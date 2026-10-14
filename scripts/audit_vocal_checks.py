"""Audit saved isolated vocal evidence and the initial protected-file inventory."""

import array
import hashlib
import json
import math
import statistics
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.models import SongState  # noqa: E402
from scripts.check_vocals import note_timing_check  # noqa: E402

CACHE = ROOT / ".cache/vocal-implementation-20261010"


def pitch_diagnostics(state, vocal):
    """YIN on short middle-of-vowel windows. Diagnostics, not a listening verdict."""
    with wave.open(str(vocal)) as stream:
        assert stream.getnchannels() == 1 and stream.getsampwidth() == 2
        rate = stream.getframerate()
        samples = array.array("h", stream.readframes(stream.getnframes()))
    lead = next(t for t in state.score.tracks if t.id == state.composition.melody_track_id)
    selected = [
        n
        for n in lead.notes
        if n.id in {"v-0-0-0", "v-0-1-0", "v-0-2-0", "v-1-0-0", "v-1-1-0", "v-2-0-0"}
    ]
    result = []
    for note in selected:
        duration = state.score.seconds_at(
            note.start_tick + note.duration_tick
        ) - state.score.seconds_at(note.start_tick)
        estimates = []
        for portion in (0.45, 0.55, 0.65):
            first = round((state.score.seconds_at(note.start_tick) + duration * portion) * rate)
            data = samples[first : first + 4096 : 4]
            if len(data) < 1024:
                continue
            rate_small = rate / 4
            lo, hi = int(rate_small / 650), int(rate_small / 180)
            differences, accumulated, normal = [0.0], 0.0, [1.0]
            for lag in range(1, hi + 2):
                value = sum((data[i] - data[i + lag]) ** 2 for i in range(768))
                differences.append(value)
                accumulated += value
                normal.append(value * lag / accumulated if accumulated else 1.0)
            minimum = next(
                (
                    i
                    for i in range(lo, hi)
                    if normal[i] < 0.15 and normal[i] <= normal[i - 1] and normal[i] < normal[i + 1]
                ),
                None,
            )
            if minimum is None:
                continue
            left, mid, right = normal[minimum - 1 : minimum + 2]
            offset = (
                (left - right) / (2 * (left - 2 * mid + right)) if left - 2 * mid + right else 0
            )
            estimates.append(rate_small / (minimum + offset))
        hz = statistics.median(estimates) if estimates else None
        result.append(
            {
                "note_id": note.id,
                "midi_pitch": note.pitch,
                "expected_hz": 440 * 2 ** ((note.pitch - 69) / 12),
                "estimated_hz": hz,
                "estimated_cents": 1200 * math.log2(hz / (440 * 2 ** ((note.pitch - 69) / 12)))
                if hz
                else None,
                "windows": len(estimates),
            }
        )
    return {
        "method": "stdlib YIN, 3 short steady windows per selected note; non-detection retained",
        "notes": result,
        "human_pitch_and_vowel_timing": "unassessed",
    }


def main():
    reports = []
    for path in sorted(CACHE.glob("*/summary.json")):
        summary = json.loads(path.read_text(encoding="utf-8"))
        if not summary.get("real_local_synthesis"):
            continue
        job = summary["jobs"][0]
        root = path.parent / "data" / summary["project_id"]
        source = root / "revisions" / f"{job['revision_id']}.json"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == summary["source_revision_hash"]
        state = SongState.model_validate(json.loads(source.read_text(encoding="utf-8"))["state"])
        assets = root / "exports/vocals" / job["id"]
        report = {
            "summary": str(path.relative_to(ROOT)),
            "song_seconds": state.score.seconds_at(state.score.end_tick),
            "units": len(state.composition.lyrics_alignment),
            "frames": job["frames"],
            "runner_requested": summary["runner"],
            "wall_seconds": job["elapsed_seconds"],
            "worker_seconds": job["engine"]["elapsed_seconds"],
            "peak_worker_ram_bytes": job["engine"]["peak_working_set_bytes"],
            "notes": note_timing_check(state, job["engine"]),
            "wav_metrics": job["metrics"],
            "human_listening": "unassessed",
        }
        for item in summary["jobs"]:
            for name, details in item["files"].items():
                with (root / "exports/vocals" / item["id"] / name).open("rb") as stream:
                    assert hashlib.file_digest(stream, "sha256").hexdigest() == details["sha256"]
        if len(state.composition.lyrics_alignment) == 24:
            report["pitch_diagnostics"] = pitch_diagnostics(state, assets / "vocal.wav")
        reports.append(report)
    original = json.loads((CACHE / "protected-before.json").read_text(encoding="utf-8"))
    current = {}
    for folder in (
        "data",
        "frontend/prototype",
        ".cache/lyric-composition-checks",
        ".cache/model-evaluations",
    ):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    current[str(path.relative_to(ROOT))] = hashlib.file_digest(
                        stream, "sha256"
                    ).hexdigest()
    protection = {
        "before_files": len(original),
        "after_files": len(current),
        "added": sorted(current.keys() - original.keys()),
        "missing": sorted(original.keys() - current.keys()),
        "changed": [
            name for name in original.keys() & current.keys() if original[name] != current[name]
        ],
    }
    assert not protection["added"] and not protection["missing"] and not protection["changed"], (
        protection
    )
    result = {
        "real_synthesis_checks": reports,
        "protected_files": protection,
        "paid_api_requests": 0,
        "env_access_by_auditor": False,
        "human_listening": "unassessed",
        "desktop_gui_reference_comparison": "unassessed",
    }
    (CACHE / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "real_sample_checks": len(reports),
                "protected_files": protection,
                "pitch_diagnostics": [
                    r["pitch_diagnostics"] for r in reports if "pitch_diagnostics" in r
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
