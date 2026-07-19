# audio.py

> 18 nodes

## Key Concepts

- **audio.py** (9 connections) — `src/hal0/realtime/audio.py`
- **wav_to_pcm16()** (3 connections) — `src/hal0/realtime/audio.py`
- **looks_like_wav()** (3 connections) — `src/hal0/realtime/audio.py`
- **frame_bytes_for_ms()** (3 connections) — `src/hal0/realtime/audio.py`
- **slice_pcm_frames()** (3 connections) — `src/hal0/realtime/audio.py`
- **rms_level()** (3 connections) — `src/hal0/realtime/audio.py`
- **pcm16_to_wav()** (2 connections) — `src/hal0/realtime/audio.py`
- **b64_encode()** (2 connections) — `src/hal0/realtime/audio.py`
- **b64_decode()** (2 connections) — `src/hal0/realtime/audio.py`
- **PCM16 <-> WAV wrapping and output frame slicing for the Realtime engine.  The ba** (1 connections) — `src/hal0/realtime/audio.py`
- **Wrap raw pcm16 mono LE bytes in a RIFF/WAV container.      The STT route rejects** (1 connections) — `src/hal0/realtime/audio.py`
- **Return ``(pcm16_bytes, sample_rate)`` from a WAV container.      Used by the TTS** (1 connections) — `src/hal0/realtime/audio.py`
- **True if ``blob`` starts with a RIFF/WAVE header.** (1 connections) — `src/hal0/realtime/audio.py`
- **Bytes in ``ms`` of mono pcm16 at ``sample_rate`` (even, whole samples).** (1 connections) — `src/hal0/realtime/audio.py`
- **Slice a pcm16 blob into ``frame_ms`` frames (last frame may be shorter).      Fr** (1 connections) — `src/hal0/realtime/audio.py`
- **Base64-encode pcm bytes for a ``response.output_audio.delta`` payload.** (1 connections) — `src/hal0/realtime/audio.py`
- **Decode a base64 ``input_audio_buffer.append`` payload to pcm bytes.      Tolerat** (1 connections) — `src/hal0/realtime/audio.py`
- **Normalized RMS (0.0-1.0) of a pcm16 mono LE buffer.      Pure stdlib so the VAD** (1 connections) — `src/hal0/realtime/audio.py`

## Relationships

- [backends.py](backends.py.md) (2 shared connections)
- [EnergyVAD](EnergyVAD.md) (1 shared connections)

## Source Files

- `src/hal0/realtime/audio.py`

## Audit Trail

- EXTRACTED: 36 (92%)
- INFERRED: 3 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*