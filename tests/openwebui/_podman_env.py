"""Test-side re-implementation of podman's ``--env-file`` parser.

Deliberately independent of ``hal0.openwebui.env_writer._read_existing_env``,
so a test can ask "what would podman hand the container?" without trusting the
code under test to answer.

Source: ``containers/podman`` ``pkg/env/env.go`` ``ParseFile`` / ``parseEnv``,
identical in v4.9.3, v5.4.0 and v6.1.4. The rules it applies:

* one variable per line (``bufio.Scanner``: a trailing ``\\r`` is dropped);
* leading blanks (``" \\t"``) are trimmed;
* an empty line, or one starting with ``#``, is skipped;
* the line splits on its first ``=``; the value is kept verbatim: no
  unquoting, no escapes, no trailing trim;
* a line without ``=`` imports the host variable of that name. A test file has
  no host to import from, so such lines are skipped here;
* an empty name (``=value``) is an error: podman refuses to start.
"""

from __future__ import annotations


def parse_podman_env(text: str) -> dict[str, str]:
    """Return the ``{name: value}`` map podman builds from an env file's text."""
    env: dict[str, str] = {}
    for raw in text.split("\n"):
        line = raw.removesuffix("\r").lstrip(" \t")
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep:
            continue
        if not name:
            raise ValueError(f"invalid environment variable: {line!r}")
        env[name.lstrip(" \t")] = value
    return env
