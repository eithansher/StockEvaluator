"""Stock Thesis Tracker: code decides every verdict; the model only interprets language."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Minimal .env loader (git-ignored file; real environment variables win).
if (ROOT / ".env").exists():
    for _line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

DATA = Path(os.environ.get("STT_DATA", ROOT / "data"))
