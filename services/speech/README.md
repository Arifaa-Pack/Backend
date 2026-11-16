# Free speech server (D52)

faster-whisper **Small** INT8 (speech-to-text) and **Piper** (text-to-speech: English, Arabic, Hindi, Chinese) on CPU, behind an OpenAI-compatible API. The voice worker uses it as the free version of the `stt` / `tts` fields, and as the automatic fallback of Deepgram / ElevenLabs in the paid version (`docs/PLATFORM_MODES.md`).

| | |
| --- | --- |
| Image | `services/speech/Dockerfile` (Python 3.12, linux/amd64), not part of the uv workspace |
| Models | downloaded on first start into the `/models` volume (≈ 0.5 GB whisper Small + 0.25 GB voices) |
| RAM | ≈ 1 GB whisper + 0.4–2.1 GB Piper (bench R2; Small itself is not measured yet: OPEN_QUESTIONS #72) |
| API | `POST /v1/audio/transcriptions`, `POST /v1/audio/speech`, `GET /healthz` |
| App setting | `KX_APP_SPEECH_URL=http://speech:8100` |

No Tamil or Sinhala Piper voice exists: Tamil calls use ElevenLabs, Sinhala stays text chat.

Local: `make up-voice` (`docs/LOCAL_DEV.md`). Measure on the real server with `resource-usage-bench/scripts/speech_bench.py` before opening free voice to buyers.
