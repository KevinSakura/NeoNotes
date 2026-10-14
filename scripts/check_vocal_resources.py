"""Read-only installed singer readiness check; no .env or model API access."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.vocal_resources import VocalResource  # noqa: E402

if __name__ == "__main__":
    status = VocalResource().status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    raise SystemExit(0 if status["ready"] else 1)
