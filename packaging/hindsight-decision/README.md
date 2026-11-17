# hal0-hs-decide packaging (Hindsight decision model)

This directory ships the CPU "System One" server for
`hindsight-ai/hs-decide-1-flash`.

- `hs_decide_serve.py` is the server. It runs a plain `transformers` forward
  pass on CPU and reuses the official `hindsight_decision` serving package
  shipped in the model repo's `serving/` directory, so request validation,
  prompt rendering and answer shaping stay identical to upstream.

## Install

The server is the opt-in `memory-decision` component
(`src/hal0/memory/decision_component.py`, which holds the model revision and
package pins). Nothing installs it by default:

```
hal0 memory decision install     # build, start, and turn the lane on
hal0 memory decision status
hal0 memory decision disable     # provider = "off"; the server keeps running
hal0 memory decision remove      # delete unit + venv; --purge-model drops the model
```

`install` builds the venv at `/var/lib/hal0/memory/hindsight-decision/.venv`
with exact versions (torch from the PyTorch CPU index only, the rest from
PyPI, all held by a constraints file), pulls the pinned revision into the
memory engine's Hugging Face cache, installs that snapshot's `serving/`
package (code from the Hugging Face repo that runs as `hal0`; the full-sha
revision pin is what fixes it), copies this script next to the venv, and
installs `hal0-hs-decide.service`. The unit is rendered by the
`hal0-systemctl` wrapper's `write-hs-decide-unit` verb from four validated
values; there is no unit file in the tree to install by hand.

Measured on one box: venv 640 MB, model 1.5 GB, 2.7 GB peak RSS, about 5 s
per request of 3,000 to 5,000 input tokens on 8 threads.
