"""Load the repo .env into os.environ (values never printed)."""
import os, pathlib
def load(path: str | None = None) -> None:
    p = pathlib.Path(path or pathlib.Path(__file__).resolve().parents[2] / ".env")
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split("  #", 1)[0].strip()
        os.environ.setdefault(k.strip(), v)
