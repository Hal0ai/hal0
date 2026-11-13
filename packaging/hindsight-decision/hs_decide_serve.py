"""hal0 local System One server for hindsight-ai/hs-decide-1-flash — no vLLM.

hs-decide-1-flash is read, not generated: every question's answer is the LM-head logit
of each option code's token at that question's ':' slot (what vLLM's `--convert classify`
+ STEP pooling computes). This reproduces it with a plain transformers forward pass on
CPU, reusing the official serving package (schema / render / typed) shipped in the model
repo, so request validation, prompt rendering and answer shaping stay byte-identical.

Run: hs-decide-serve --model <snapshot dir> --port 9178 [--threads 12] [--dtype bfloat16]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import torch
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from transformers import AutoModelForCausalLM, AutoTokenizer

from hindsight_decision import schema
from hindsight_decision.identity import identity
from hindsight_decision.prompt import load_settings
from hindsight_decision.typed import classifier_ids, question_logits
from hindsight_decision.typed_render import render_request

log = logging.getLogger("hs-decide-serve")
MODEL_ID = "hs-decide-1-flash"


class CpuDecider:
    def __init__(self, folder: Path, dtype: torch.dtype, max_tokens: int | None):
        self.tokenizer = AutoTokenizer.from_pretrained(folder)
        self.settings = load_settings(folder)
        self.ids = classifier_ids(self.tokenizer)
        self.max_tokens = max_tokens or self.settings.max_request_tokens
        t = time.time()
        self.model = AutoModelForCausalLM.from_pretrained(folder, dtype=dtype).eval()
        self.ids_t = torch.tensor(self.ids)
        log.info("loaded %s (%s) in %.1fs, %d classifier ids", folder, dtype, time.time() - t, len(self.ids))
        self.lock = asyncio.Lock()
        self.stats = {"requests": 0, "questions": 0, "input_tokens": 0, "busy_s": 0.0, "errors": 0}

    def _forward(self, ids: list[int], slots: list[int]) -> list[list[float]]:
        with torch.inference_mode():
            x = torch.tensor([ids])
            hidden = self.model.model(input_ids=x).last_hidden_state[0]
            # Project only the slot rows through the LM head, and only onto the classifier
            # vocabulary — same numbers as the full logits, a fraction of the work.
            w = self.model.lm_head.weight[self.ids_t]  # [n_ids, hidden]
            rows = (hidden[slots] @ w.T).float()
        return rows.tolist()

    async def respond(self, req: schema.SystemOneRequest) -> dict:
        rendered = render_request(self.tokenizer, req, self.settings)
        n = rendered.tokens()
        if n > self.max_tokens:
            raise schema.ContextOverflow(f"{n} tokens is over this server's window of {self.max_tokens} tokens")
        slots = [q.slot for q in rendered.questions]
        async with self.lock:
            t = time.time()
            rows = await asyncio.to_thread(self._forward, rendered.ids, slots)
            dt = time.time() - t
        answers = {}
        for q, logits in zip(rendered.questions, question_logits(rows, rendered, self.ids), strict=True):
            answers[q.id] = schema.answer(req.questions[q.id], schema.softmax(logits))
        s = self.stats
        s["requests"] += 1; s["questions"] += len(slots); s["input_tokens"] += n; s["busy_s"] += dt
        log.info("systemone: %d tokens, %d questions, %.2fs", n, len(slots), dt)
        return {"model": req.model, "answers": answers, "usage": {"input_tokens": n, "output_tokens": 0}}


def build_app(folder: Path, dtype: torch.dtype, max_tokens: int | None) -> FastAPI:
    app = FastAPI(title="hal0 hs-decide-serve")
    decider = CpuDecider(folder, dtype, max_tokens)
    ident = identity(MODEL_ID, folder, None)
    started = time.time()

    @app.get("/health")
    async def health():
        return {"status": "ok", "model": MODEL_ID, "device": "cpu", "threads": torch.get_num_threads(),
                "uptime_s": round(time.time() - started), **decider.stats}

    @app.get("/v1/models")
    async def models():
        return JSONResponse({"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "hindsight-ai",
                                                         "max_model_len": decider.max_tokens}]}, headers=ident.headers())

    @app.post("/v1/systemone")
    async def systemone(raw: Request):
        try:
            body = await raw.json()
            if not isinstance(body, dict):
                return JSONResponse({"error": "the body must be a JSON object"}, status_code=400)
            return JSONResponse(await decider.respond(schema.SystemOneRequest.model_validate(body)), headers=ident.headers())
        except json.JSONDecodeError:
            return JSONResponse({"error": "the body is not valid JSON"}, status_code=400)
        except ValidationError as e:
            decider.stats["errors"] += 1
            return JSONResponse({"detail": e.errors(include_url=False)}, status_code=422)
        except schema.ContextOverflow as e:
            decider.stats["errors"] += 1
            return JSONResponse(schema.overflow_body(str(e)), status_code=400)
        except schema.Unanswerable as e:
            decider.stats["errors"] += 1
            return JSONResponse({"detail": [{"loc": ["body"], "msg": str(e), "type": "value_error"}]}, status_code=422)

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="hs-decide-1-flash snapshot directory")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9178)
    ap.add_argument("--threads", type=int, default=int(os.environ.get("HS_DECIDE_THREADS", "8")))
    ap.add_argument("--dtype", default="bfloat16", choices=["bfloat16", "float32"])
    ap.add_argument("--max-tokens", type=int, default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    torch.set_num_threads(a.threads)
    import uvicorn
    uvicorn.run(build_app(Path(a.model), getattr(torch, a.dtype), a.max_tokens), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
