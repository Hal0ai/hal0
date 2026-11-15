#!/usr/bin/env python3
"""Regenerate ``src/hal0/slots/xdna_archs.py`` from the xdna runner's llama.cpp fork.

The set of ``general.architecture`` names the xdna runner can load is the
``LLM_ARCH_NAMES`` table in ``src/llama-arch.cpp`` of Tether's ggml-xdna fork
at the commit the runner image is built from (``[source].ref`` in the
runner-images repo's ``runners/xdna/manifest.toml``). Re-run this whenever
that pin moves:

  python scripts/gen_xdna_archs.py <full-40-char-fork-commit>

It prints nothing on success and rewrites the module in place.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

REPO = "tetherto/qvac-fabric-llm.cpp"
OUT = Path(__file__).resolve().parent.parent / "src" / "hal0" / "slots" / "xdna_archs.py"
_ENTRY = re.compile(r'\{\s*LLM_ARCH_[A-Z0-9_]+,\s*"([^"]+)"\s*\}')


def parse_arch_names(source: str) -> list[str]:
    """The names in the ``LLM_ARCH_NAMES`` map, minus the ``(unknown)`` sentinel."""
    start = source.index("LLM_ARCH_NAMES = {")
    end = source.index("\n};", start)
    names = {m.group(1) for m in _ENTRY.finditer(source[start:end])}
    names.discard("(unknown)")
    return sorted(names)


def render(ref: str, names: list[str]) -> str:
    body = "".join(f'        "{n}",\n' for n in names)
    return f'''"""Architectures the xdna runner's llama.cpp build can load (GENERATED).

Do not edit by hand: run ``python scripts/gen_xdna_archs.py <fork-commit>``.

Source: the ``LLM_ARCH_NAMES`` table in ``src/llama-arch.cpp`` of
https://github.com/{REPO} at commit {ref}
(the ``[source].ref`` of the xdna runner image; hal0 pins that image in
``manifest.json``). {len(names)} names. A GGUF whose ``general.architecture``
is outside this set dies in ``llama_model_load`` with "unknown model
architecture", so the slot layer refuses it up front.
"""

from __future__ import annotations

XDNA_RUNNER_LLAMA_CPP_REF = "{ref}"

XDNA_KNOWN_ARCHS: frozenset[str] = frozenset(
    {{
{body}    }}
)
'''


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not re.fullmatch(r"[0-9a-f]{40}", argv[0]):
        print(__doc__, file=sys.stderr)
        return 2
    ref = argv[0]
    url = f"https://raw.githubusercontent.com/{REPO}/{ref}/src/llama-arch.cpp"
    with urllib.request.urlopen(url, timeout=60) as resp:
        names = parse_arch_names(resp.read().decode())
    if not names:
        print("no architectures parsed", file=sys.stderr)
        return 1
    OUT.write_text(render(ref, names), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
