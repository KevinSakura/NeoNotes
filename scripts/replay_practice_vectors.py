"""Cross-language replay receipt; read isolated vectors, never settings or songs."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.practice_judge import replay  # noqa: E402

path = Path(sys.argv[1]).resolve()
if not any(
    path.is_relative_to((ROOT / folder).resolve())
    for folder in (".cache/practice-evaluation", ".cache/practice-unit-checks")
):
    raise SystemExit("Test vectors must be isolated under an approved practice cache directory")
vectors = json.loads(path.read_text(encoding="utf-8"))
results = [replay(v["snapshot"], v["events"]) for v in vectors]
print(json.dumps(results, ensure_ascii=False, allow_nan=False))
