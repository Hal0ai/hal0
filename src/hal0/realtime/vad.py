"""In-process energy-threshold server VAD (user decision 1, spec §4c-1).

**Why energy, not silero/webrtc for inc-1**: the shipped venv has ``numpy`` and
``soundfile`` but *not* ``onnxruntime``, ``webrtcvad``, or ``silero-vad``. A
silero-onnx VAD would pull ``onnxruntime`` (a heavyweight dep) *and* a multi-file
ONNX model that hits the exact "doesn't fit the single-file pull path" problem
the curated ``*-streaming`` rows already have (spec §2d). An energy-RMS detector
is zero-new-dependency, in-process, CPU-only, needs no slot, and its thresholds
are config-tunable — the minimal-dependency choice the brief asks for. The class
is deliberately a plain interface (``feed`` -> decisions) so a silero backend can
replace it in increment 2 without touching the session state machine.

Semantics: the demo hardcodes ``turn_detection: {type: server_vad}`` and never
sends ``commit`` (spec §3). This VAD watches appended pcm16 frames, emits
``speech_started`` on voiced onset and ``speech_stopped`` after
``silence_ms`` of trailing silence; the session auto-commits + auto-``response``
only when the ended segment carried at least ``min_speech_ms`` of voiced audio
(the ``committable`` flag), so a cough or a click doesn't fire a turn.
"""

from __future__ import annotations

from dataclasses import dataclass

from hal0.realtime.audio import SAMPLE_WIDTH_BYTES, rms_level

try:  # numpy is present in the shipped venv; fall back to stdlib if not.
    import numpy as _np
except ImportError:  # pragma: no cover — numpy absent
    _np = None


@dataclass(frozen=True)
class VadDecision:
    """A single VAD state transition emitted from :meth:`EnergyVAD.feed`."""

    kind: str  # "speech_started" | "speech_stopped"
    committable: bool = True  # speech_stopped only: segment met min_speech_ms


def _window_rms(pcm: bytes) -> float:
    """Normalized RMS of one window — numpy fast path, stdlib reference else."""
    if _np is not None:
        arr = _np.frombuffer(pcm, dtype="<i2")
        if arr.size == 0:
            return 0.0
        # float64 to avoid int16 overflow in the square.
        return float(_np.sqrt(_np.mean(arr.astype(_np.float64) ** 2))) / 32768.0
    return rms_level(pcm)


class EnergyVAD:
    """Streaming energy-threshold voice-activity detector.

    Feed appended pcm16 mono LE bytes; get back an ordered list of
    :class:`VadDecision` transitions. Stateful across calls (frames arrive in
    small ``input_audio_buffer.append`` chunks), so it keeps a residue buffer of
    the trailing partial window between feeds.
    """

    def __init__(
        self,
        *,
        sample_rate: int,
        energy_threshold: float = 0.02,
        silence_ms: int = 500,
        min_speech_ms: int = 200,
        window_ms: int = 20,
    ) -> None:
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.silence_ms = silence_ms
        self.min_speech_ms = min_speech_ms
        self.window_ms = window_ms
        self._window_bytes = max(
            SAMPLE_WIDTH_BYTES, round(sample_rate * window_ms / 1000) * SAMPLE_WIDTH_BYTES
        )
        self._residue = b""
        self._in_speech = False
        self._speech_ms = 0
        self._silence_run_ms = 0

    def reset(self) -> None:
        """Clear all state (called on commit / cancel / new turn)."""
        self._residue = b""
        self._in_speech = False
        self._speech_ms = 0
        self._silence_run_ms = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def feed(self, pcm: bytes) -> list[VadDecision]:
        """Process appended pcm; return the transitions it produced (may be empty)."""
        decisions: list[VadDecision] = []
        buf = self._residue + pcm
        n_windows = len(buf) // self._window_bytes
        for i in range(n_windows):
            window = buf[i * self._window_bytes : (i + 1) * self._window_bytes]
            voiced = _window_rms(window) >= self.energy_threshold
            if not self._in_speech:
                if voiced:
                    self._in_speech = True
                    self._speech_ms = self.window_ms
                    self._silence_run_ms = 0
                    decisions.append(VadDecision("speech_started"))
            else:
                if voiced:
                    self._speech_ms += self.window_ms
                    self._silence_run_ms = 0
                else:
                    self._silence_run_ms += self.window_ms
                    if self._silence_run_ms >= self.silence_ms:
                        committable = self._speech_ms >= self.min_speech_ms
                        decisions.append(VadDecision("speech_stopped", committable=committable))
                        self._in_speech = False
                        self._speech_ms = 0
                        self._silence_run_ms = 0
        self._residue = buf[n_windows * self._window_bytes :]
        return decisions


__all__ = ["EnergyVAD", "VadDecision"]
