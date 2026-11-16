"""Free speech server (D52, server plan v2 §2): faster-whisper Small INT8 (STT) + Piper (TTS) on CPU.

OpenAI-compatible, so the voice worker reaches it through `livekit-plugins-openai` (base_url = this server):

  POST /v1/audio/transcriptions   multipart: file (wav), model, language?        -> {"text": "..."}
  POST /v1/audio/speech           json: input, voice (en|ar|hi|zh), response_format (pcm|wav) -> audio
  GET  /healthz                   200 once the models are loaded

Env: MODELS (/models), STT_MODEL (small), STT_COMPUTE (int8), STT_THREADS (4), STT_WORKERS (2),
PIPER_VOICES (en,ar,hi,zh), PORT (8100). Piper voices are 22.05 kHz; PCM is resampled to 24 kHz mono 16-bit
(what the OpenAI TTS plug-in expects). No audio or text is stored: the server keeps nothing between requests.
Built from resource-usage-bench/scripts/speech_server.py (bench R2, 14 Nov).
"""

from __future__ import annotations

import asyncio
import io
import os
import wave
from pathlib import Path
from typing import Any

import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

MODELS = Path(os.environ.get("MODELS", "/models"))
VOICE_FILES = {
    "en": "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "ar": "ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx",
    "hi": "hi/hi_IN/pratham/medium/hi_IN-pratham-medium.onnx",
    "zh": "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
}
OUT_RATE = 24_000
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # one utterance; the voice worker segments speech with VAD
MAX_TEXT_CHARS = 2_000

app = FastAPI(title="Kairox speech (free version)")


@app.on_event("startup")
def load() -> None:
    from faster_whisper import WhisperModel  # noqa: PLC0415 - heavy; load at startup only
    from piper import PiperVoice  # noqa: PLC0415

    model = os.environ.get("STT_MODEL", "small")
    app.state.stt = WhisperModel(
        str(MODELS / f"faster-whisper-{model}"),
        device="cpu",
        compute_type=os.environ.get("STT_COMPUTE", "int8"),
        cpu_threads=int(os.environ.get("STT_THREADS", "4")),
        num_workers=int(os.environ.get("STT_WORKERS", "2")),
    )
    wanted = [v.strip() for v in os.environ.get("PIPER_VOICES", "en,ar,hi,zh").split(",") if v.strip()]
    app.state.voices = {
        v: PiperVoice.load(str(MODELS / "piper-voices" / VOICE_FILES[v])) for v in wanted if v in VOICE_FILES
    }


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "voices": sorted(getattr(app.state, "voices", {}))}


def wav_to_16k(src: Any) -> np.ndarray:
    """WAV -> 16 kHz mono float32 (faster-whisper's input). Decoded here: faster-whisper 1.2.1's PyAV path
    breaks with PyAV 19 (bench R2)."""
    with wave.open(src) as w:
        rate, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != 16_000 and x.size:
        x = np.interp(np.linspace(0, x.size - 1, int(x.size * 16_000 / rate)), np.arange(x.size), x)
    return x.astype(np.float32)


def _transcribe(data: bytes, language: str | None) -> str:
    segments, _ = app.state.stt.transcribe(
        wav_to_16k(io.BytesIO(data)), language=language or None, beam_size=1, vad_filter=False
    )
    return " ".join(s.text.strip() for s in segments)


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),  # noqa: B008 - FastAPI form API
    model: str = Form("small"),
    language: str | None = Form(None),
) -> JSONResponse:
    del model  # one model per server
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "audio too long")
    try:
        text = await asyncio.to_thread(_transcribe, data, (language or "")[:2] or None)
    except (wave.Error, EOFError) as exc:
        raise HTTPException(400, "send 16-bit PCM WAV") from exc
    return JSONResponse({"text": text})


def _resample(pcm: bytes, rate: int) -> bytes:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if rate == OUT_RATE or x.size == 0:
        return pcm
    n = int(x.size * OUT_RATE / rate)
    return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.int16).tobytes()


@app.post("/v1/audio/speech")
async def speech(request: Request) -> Response:
    req = await request.json()
    voices = app.state.voices
    voice = voices.get(str(req.get("voice", "en"))[:2]) or voices.get("en")
    if voice is None:
        raise HTTPException(503, "no Piper voice loaded")
    text = str(req.get("input", ""))[:MAX_TEXT_CHARS]
    rate = voice.config.sample_rate

    def chunks() -> list[bytes]:
        return [_resample(c.audio_int16_bytes, rate) for c in voice.synthesize(text)]

    parts = await asyncio.to_thread(chunks)
    if req.get("response_format", "pcm") == "wav":
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(OUT_RATE)
            w.writeframes(b"".join(parts))
        return Response(buf.getvalue(), media_type="audio/wav")
    return StreamingResponse(iter(parts), media_type="audio/pcm")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8100")), log_level="warning")  # noqa: S104
