"""Offline checks and protected-file receipts for packaged beginner exercises."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROTECTED = [
    "data",
    "frontend/prototype",
    ".cache/model-evaluations",
    ".cache/lyric-composition-checks",
    ".cache/vocal-implementation-20261010",
    ".cache/vocal-settings-fix-20261010",
    ".cache/lyric-phrases-evaluation",
    ".cache/practice-evaluation",
]


def fingerprints():
    result = {}
    for folder in PROTECTED:
        for path in (ROOT / folder).rglob("*"):
            if path.is_file():
                with path.open("rb") as handle:
                    result[path.relative_to(ROOT).as_posix()] = hashlib.file_digest(
                        handle, "sha256"
                    ).hexdigest()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / ".cache/practice-unit-checks" / uuid4().hex))
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--verify-protected", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).absolute()
    if not output.resolve().is_relative_to((ROOT / ".cache/practice-unit-checks").resolve()):
        raise SystemExit("Checks write only to .cache/practice-unit-checks.")
    output.mkdir(parents=True, exist_ok=True)
    if args.baseline:
        destination = output / "protected-before.json"
        if destination.exists():
            raise SystemExit("Never replace a protection baseline.")
        before = fingerprints()
        destination.write_text(json.dumps(before, indent=2), encoding="utf-8")
        print(json.dumps({"path": str(output), "protected_files": len(before)}))
    elif args.verify_protected:
        before = json.loads((output / "protected-before.json").read_text(encoding="utf-8"))
        after = fingerprints()
        report = {
            "before_count": len(before),
            "after_count": len(after),
            "added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()),
            "changed": sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
        }
        (output / "protected-verification.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report))
        if any(report[k] for k in ("added", "removed", "changed")):
            raise SystemExit(1)
    else:
        from backend.app.practice_units import catalog, exercise_bundle

        summaries = []
        for unit in catalog()["units"]:
            for exercise in unit["exercises"]:
                bundle = exercise_bundle(exercise["exercise_id"])
                directory = output / exercise["exercise_id"]
                directory.mkdir(exist_ok=False)
                (directory / "source.mid").write_bytes(bundle["midi"])
                (directory / "score.musicxml").write_text(
                    bundle["prepared"]["musicxml"], encoding="utf-8"
                )
                (directory / "targets.json").write_text(
                    json.dumps(bundle["prepared"]["targets"], indent=2), encoding="utf-8"
                )
                summaries.append(bundle["builtin"])
        (output / "catalog-check.json").write_text(
            json.dumps(
                {"exercises": summaries, "model_requests": 0, "singing_inferences": 0},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "output": str(output),
                    "units": len(catalog()["units"]),
                    "exercises": len(summaries),
                    "model_requests": 0,
                    "singing_inferences": 0,
                }
            )
        )
