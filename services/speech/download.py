"""Fetch the models once into the /models volume (idempotent): faster-whisper Small (CTranslate2) and
the Piper voices listed in PIPER_VOICES. Official Hugging Face repos, the same ones the bench used."""

from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download

MODELS = Path(os.environ.get("MODELS", "/models"))
STT_REPO = {
    "small": "Systran/faster-whisper-small",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
PIPER = {
    "en": "en/en_US/lessac/medium",
    "ar": "ar/ar_JO/kareem/medium",
    "hi": "hi/hi_IN/pratham/medium",
    "zh": "zh/zh_CN/huayan/medium",
}


def main() -> None:
    model = os.environ.get("STT_MODEL", "small")
    stt_dir = MODELS / f"faster-whisper-{model}"
    if not (stt_dir / "model.bin").exists():
        snapshot_download(repo_id=STT_REPO[model], local_dir=stt_dir)
    voices = [v.strip() for v in os.environ.get("PIPER_VOICES", "en,ar,hi,zh").split(",") if v.strip()]
    missing = [v for v in voices if v in PIPER and not (MODELS / "piper-voices" / PIPER[v]).exists()]
    if missing:
        snapshot_download(
            repo_id="rhasspy/piper-voices",
            local_dir=MODELS / "piper-voices",
            allow_patterns=[f"{PIPER[v]}/*" for v in missing],
        )
    print(f"models ready: whisper {model}, piper {', '.join(voices)}", flush=True)


if __name__ == "__main__":
    main()
