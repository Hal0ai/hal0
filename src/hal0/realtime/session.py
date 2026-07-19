"""Per-connection Realtime turn state machine (spec §4b, user decisions 1-3).

Transport-agnostic: the WS route (:mod:`hal0.api.routes.realtime`) hands this an
``emit`` coroutine (send one event dict) and feeds it raw client frames; the
session owns everything else — session state, the input-audio buffer, server VAD
turn detection, STT/LLM/TTS orchestration, sentence-chunked audio out, and
cancellation. Every path is guarded so a malformed frame becomes a typed
``error`` event, never an unhandled exception that tears down the socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import structlog

from hal0.realtime import audio, events
from hal0.realtime.backends import STEWARD_MODEL, ChatChunk, RealtimeBackends
from hal0.realtime.vad import EnergyVAD

log = structlog.get_logger(__name__)

# Sentence-boundary characters that flush the pending TTS buffer incrementally.
_SENTENCE_ENDINGS = ".!?\n"


class RealtimeSession:
    """One ``/v1/realtime`` connection's lifecycle."""

    def __init__(
        self,
        *,
        emit,
        backends: RealtimeBackends,
        cfg: Any,
        model: str,
        auth: str | None = None,
    ) -> None:
        self._raw_emit = emit
        self._emit_lock = asyncio.Lock()
        self.backends = backends
        self.cfg = cfg
        self.auth = auth

        self.session_id = events.new_id("sess")
        self.model = model or getattr(cfg, "default_model", "") or ""
        # Demo hardcodes server_vad (user decision 1); none-mode also supported.
        self.turn_detection = "server_vad"
        self.voice = getattr(cfg, "tts_voice", "") or ""
        self.instructions = ""
        self.tools: list[dict[str, Any]] = []

        self.messages: list[dict[str, Any]] = []
        self._buffer = bytearray()
        self._vad = self._build_vad()

        self._response_task: asyncio.Task | None = None
        self._cancel = asyncio.Event()
        self._closed = False

    # ── emit + lifecycle ─────────────────────────────────────────────────────

    async def _emit(self, ev: dict[str, Any]) -> None:
        if self._closed:
            return
        async with self._emit_lock:
            with contextlib.suppress(Exception):
                await self._raw_emit(ev)

    def _build_vad(self) -> EnergyVAD:
        cfg = self.cfg
        return EnergyVAD(
            sample_rate=int(getattr(cfg, "sample_rate", audio.DEFAULT_SAMPLE_RATE)),
            energy_threshold=float(getattr(cfg, "vad_energy_threshold", 0.02)),
            silence_ms=int(getattr(cfg, "vad_silence_ms", 500)),
            min_speech_ms=int(getattr(cfg, "vad_min_speech_ms", 200)),
            window_ms=int(getattr(cfg, "vad_window_ms", 20)),
        )

    def _session_payload(self) -> dict[str, Any]:
        return {
            "id": self.session_id,
            "model": self.model,
            "modalities": ["audio", "text"],
            "voice": self.voice,
            "instructions": self.instructions,
            "turn_detection": (
                None if self.turn_detection == "none" else {"type": self.turn_detection}
            ),
            "tools": self.tools,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
        }

    async def start(self) -> None:
        """Emit ``session.created`` (handshake open)."""
        await self._emit(events.event("session.created", session=self._session_payload()))

    async def aclose(self) -> None:
        """Cancel any in-flight response and mark closed."""
        self._closed = True
        await self._abort_response()

    # ── frame dispatch ───────────────────────────────────────────────────────

    async def handle_raw(self, raw: str) -> None:
        """Parse + dispatch one client text frame. Never raises."""
        try:
            frame = json.loads(raw)
        except ValueError:
            await self._emit(events.error_event("frame is not valid JSON"))
            return
        if not isinstance(frame, dict):
            await self._emit(events.error_event("frame must be a JSON object"))
            return
        await self.handle(frame)

    async def handle(self, frame: dict[str, Any]) -> None:
        etype = frame.get("type")
        if not isinstance(etype, str):
            await self._emit(events.error_event("event missing 'type'"))
            return
        if etype in events.REJECTED_CLIENT_EVENTS:
            await self._emit(
                events.error_event(
                    f"event '{etype}' is not implemented in this increment",
                    code="unsupported_event",
                    event_type=etype,
                )
            )
            return
        if etype not in events.ACCEPTED_CLIENT_EVENTS:
            await self._emit(
                events.error_event(
                    f"unknown event type '{etype}'",
                    code="unknown_event",
                    event_type=etype,
                )
            )
            return
        try:
            await self._dispatch(etype, frame)
        except Exception as exc:
            log.warning("realtime.handler_error", event=etype, error=str(exc))
            await self._emit(
                events.error_event(f"internal error handling '{etype}': {exc}", event_type=etype)
            )

    async def _dispatch(self, etype: str, frame: dict[str, Any]) -> None:
        if etype == "session.update":
            await self._on_session_update(frame)
        elif etype == "input_audio_buffer.append":
            await self._on_append(frame)
        elif etype == "input_audio_buffer.commit":
            await self._on_commit()
        elif etype == "conversation.item.create":
            await self._on_item_create(frame)
        elif etype == "response.create":
            await self._start_response(barge_in=False)
        elif etype == "response.cancel":
            await self._on_cancel()

    # ── session.update ───────────────────────────────────────────────────────

    async def _on_session_update(self, frame: dict[str, Any]) -> None:
        sess = frame.get("session")
        if not isinstance(sess, dict):
            await self._emit(
                events.error_event(
                    "session.update missing 'session' object", event_type="session.update"
                )
            )
            return
        if isinstance(sess.get("model"), str) and sess["model"]:
            self.model = sess["model"]
        if isinstance(sess.get("voice"), str):
            self.voice = sess["voice"]
        if isinstance(sess.get("instructions"), str):
            self.instructions = sess["instructions"]
        if isinstance(sess.get("tools"), list):
            self.tools = [t for t in sess["tools"] if isinstance(t, dict)]
        if "turn_detection" in sess:
            td = sess["turn_detection"]
            if td is None:
                self.turn_detection = "none"
            elif isinstance(td, dict):
                self.turn_detection = str(td.get("type") or "server_vad")
                self._reconfigure_vad(td)
        await self._emit(events.event("session.updated", session=self._session_payload()))

    def _reconfigure_vad(self, td: dict[str, Any]) -> None:
        # Optional per-session VAD tuning (OpenAI: threshold / silence_duration_ms).
        if isinstance(td.get("threshold"), int | float):
            self._vad.energy_threshold = float(td["threshold"])
        if isinstance(td.get("silence_duration_ms"), int):
            self._vad.silence_ms = int(td["silence_duration_ms"])

    # ── input audio buffer ───────────────────────────────────────────────────

    async def _on_append(self, frame: dict[str, Any]) -> None:
        payload = frame.get("audio")
        if not isinstance(payload, str):
            await self._emit(
                events.error_event(
                    "input_audio_buffer.append missing base64 'audio'",
                    event_type="input_audio_buffer.append",
                )
            )
            return
        try:
            pcm = audio.b64_decode(payload)
        except ValueError as exc:
            await self._emit(events.error_event(str(exc), event_type="input_audio_buffer.append"))
            return
        self._append_pcm(pcm)
        if self.turn_detection == "server_vad":
            await self._run_vad(pcm)

    def _append_pcm(self, pcm: bytes) -> None:
        self._buffer.extend(pcm)
        cap = int(
            float(getattr(self.cfg, "max_buffer_seconds", 30.0))
            * int(getattr(self.cfg, "sample_rate", audio.DEFAULT_SAMPLE_RATE))
            * audio.SAMPLE_WIDTH_BYTES
        )
        if cap and len(self._buffer) > cap:
            # Drop the oldest audio rather than growing unbounded (DoS guard).
            del self._buffer[: len(self._buffer) - cap]

    async def _run_vad(self, pcm: bytes) -> None:
        for decision in self._vad.feed(pcm):
            if decision.kind == "speech_started":
                await self._emit(events.event("input_audio_buffer.speech_started"))
                # Barge-in: a new utterance cancels any response still speaking.
                if self._response_running():
                    await self._abort_response(emit_done=True)
            elif decision.kind == "speech_stopped":
                await self._emit(events.event("input_audio_buffer.speech_stopped"))
                if decision.committable:
                    await self._on_commit()

    async def _on_commit(self) -> None:
        pcm = bytes(self._buffer)
        self._buffer.clear()
        self._vad.reset()
        if not pcm:
            await self._emit(
                events.error_event(
                    "input_audio_buffer.commit with empty buffer",
                    code="input_audio_buffer_commit_empty",
                    event_type="input_audio_buffer.commit",
                )
            )
            return
        wav = audio.pcm16_to_wav(
            pcm, sample_rate=int(getattr(self.cfg, "sample_rate", audio.DEFAULT_SAMPLE_RATE))
        )
        stt_model = getattr(self.cfg, "stt_model", "") or self.model
        try:
            text = await self.backends.transcribe(wav, model=stt_model, auth=self.auth)
        except Exception as exc:
            await self._emit(events.error_event(f"transcription failed: {exc}", code="stt_error"))
            return
        item_id = events.new_id("item")
        self.messages.append({"role": "user", "content": text})
        await self._emit(
            events.event(
                "conversation.item.input_audio_transcription.completed",
                item_id=item_id,
                transcript=text,
            )
        )
        await self._start_response(barge_in=False)

    # ── conversation.item.create (tool results / injected text) ──────────────

    async def _on_item_create(self, frame: dict[str, Any]) -> None:
        item = frame.get("item")
        if not isinstance(item, dict):
            await self._emit(
                events.error_event(
                    "conversation.item.create missing 'item'", event_type="conversation.item.create"
                )
            )
            return
        itype = item.get("type")
        if itype == "function_call_output":
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(item.get("call_id") or ""),
                    "content": str(item.get("output") or ""),
                }
            )
        elif itype == "message":
            role = item.get("role") or "user"
            text = _extract_item_text(item)
            if text:
                self.messages.append({"role": role, "content": text})
        # Item accepted silently; the client drives response.create next (demo flow).

    # ── response lifecycle ───────────────────────────────────────────────────

    def _response_running(self) -> bool:
        return self._response_task is not None and not self._response_task.done()

    async def _start_response(self, *, barge_in: bool) -> None:
        if self._response_running():
            if barge_in:
                await self._abort_response(emit_done=True)
            else:
                return  # one response at a time; ignore the redundant trigger
        self._cancel = asyncio.Event()
        self._response_task = asyncio.create_task(self._run_response())

    async def _on_cancel(self) -> None:
        if self._response_running():
            await self._abort_response(emit_done=True)
        else:
            # Nothing running — acknowledge with a cancelled done for symmetry.
            await self._emit(events.event("response.done", response={"status": "cancelled"}))

    async def _abort_response(self, *, emit_done: bool = False) -> None:
        self._cancel.set()
        task = self._response_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._response_task = None
        if emit_done:
            await self._emit(events.event("response.done", response={"status": "cancelled"}))

    async def _run_response(self) -> None:
        response_id = events.new_id("resp")
        await self._emit(events.event("response.created", response={"id": response_id}))
        pending = ""  # sentence buffer
        assistant_text = ""
        try:
            async for chunk in self._llm_chunks():
                if self._cancel.is_set():
                    break
                if chunk.kind == "text":
                    assistant_text += chunk.text
                    pending += chunk.text
                    pending = await self._flush_sentences(pending)
                elif chunk.kind == "approval":
                    await self._speak(chunk.text)
                elif chunk.kind == "tool_call":
                    await self._emit(
                        events.event(
                            "response.function_call_arguments.done",
                            call_id=chunk.call_id or events.new_id("call"),
                            name=chunk.name,
                            arguments=chunk.arguments,
                        )
                    )
                elif chunk.kind == "error":
                    await self._emit(events.error_event(chunk.text, code="llm_error"))
                    break
                elif chunk.kind == "done":
                    break
            if not self._cancel.is_set() and pending.strip():
                await self._speak(pending)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("realtime.response_error", error=str(exc))
            await self._emit(events.error_event(f"response failed: {exc}", code="response_error"))
        finally:
            if assistant_text.strip():
                self.messages.append({"role": "assistant", "content": assistant_text})
            if not self._cancel.is_set():
                await self._emit(
                    events.event(
                        "response.done", response={"id": response_id, "status": "completed"}
                    )
                )

    async def _llm_chunks(self) -> AsyncIterator[ChatChunk]:
        """Select + drive the LLM leg; bound the steward approval wait for voice."""
        if self.model == STEWARD_MODEL:
            payload = {"messages": self.messages, "model": STEWARD_MODEL, "stream": True}
            source = self.backends.chat_steward(payload=payload, auth=self.auth)
            async for chunk in self._bounded_after_approval(source):
                yield chunk
        else:
            source = self.backends.chat_plain(
                messages=self.messages,
                model=self.model,
                tools=self.tools or None,
                auth=self.auth,
            )
            async for chunk in source:
                yield chunk

    async def _bounded_after_approval(
        self, source: AsyncIterator[ChatChunk]
    ) -> AsyncIterator[ChatChunk]:
        """Pass chunks through; once an approval notice is seen, cap the wait for
        the next chunk at ``approval_wait_s`` so a gated steward tool can't leave
        the voice session in up to 300s of silence (spec §2b, user decision 3)."""
        wait_s = float(getattr(self.cfg, "approval_wait_s", 20.0))
        it = source.__aiter__()
        approved_deadline_active = False
        while True:
            try:
                if approved_deadline_active:
                    chunk = await asyncio.wait_for(it.__anext__(), timeout=wait_s)
                else:
                    chunk = await it.__anext__()
            except StopAsyncIteration:
                return
            except TimeoutError:
                yield ChatChunk(
                    "text",
                    text="I'm still waiting on that approval — I'll stop here. "
                    "Approve it at the bell and ask me again.",
                )
                with contextlib.suppress(Exception):
                    await it.aclose()  # type: ignore[attr-defined]
                yield ChatChunk("done")
                return
            if chunk.kind == "approval":
                approved_deadline_active = True
            yield chunk

    async def _flush_sentences(self, pending: str) -> str:
        """Speak every complete sentence in ``pending``; return the remainder."""
        while True:
            idx = _first_sentence_end(pending)
            if idx is None:
                return pending
            sentence, pending = pending[: idx + 1], pending[idx + 1 :]
            if sentence.strip():
                await self._speak(sentence.strip())
            if self._cancel.is_set():
                return ""

    async def _speak(self, text: str) -> None:
        """TTS one text span -> transcript delta + framed pcm16 audio deltas."""
        if self._cancel.is_set() or not text.strip():
            return
        await self._emit(events.event("response.output_audio_transcript.delta", delta=text))
        tts_model = getattr(self.cfg, "tts_model", "") or self.model
        try:
            pcm = await self.backends.synthesize(
                text, model=tts_model, voice=self.voice or None, auth=self.auth
            )
        except Exception as exc:
            await self._emit(events.error_event(f"synthesis failed: {exc}", code="tts_error"))
            return
        sr = int(getattr(self.cfg, "sample_rate", audio.DEFAULT_SAMPLE_RATE))
        frame_ms = int(getattr(self.cfg, "frame_ms", 20))
        for frame_pcm in audio.slice_pcm_frames(pcm, frame_ms=frame_ms, sample_rate=sr):
            if self._cancel.is_set():
                return
            await self._emit(
                events.event("response.output_audio.delta", delta=audio.b64_encode(frame_pcm))
            )


def _first_sentence_end(text: str) -> int | None:
    for i, ch in enumerate(text):
        if ch in _SENTENCE_ENDINGS:
            return i
    return None


def _extract_item_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") in ("input_text", "text")
        ]
        return " ".join(p for p in parts if p)
    return ""


__all__ = ["RealtimeSession"]
