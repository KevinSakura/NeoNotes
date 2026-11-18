"""Replay existing failed artifacts in isolated copies; never infer or edit data/."""

# ruff: noqa: E402
import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import UploadFile

from backend.app.transcription_score import ScoreSettings, TranscriptionNote
from backend.app.transcriptions import Save, Start, TranscriptionManager
from scripts.check_transcription import wait


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    out = args.output or ROOT / ".cache/transcription-fixes" / uuid4().hex
    if not out.resolve().is_relative_to((ROOT / ".cache/transcription-fixes").resolve()):
        raise SystemExit("Isolated output required")
    out.mkdir(parents=True, exist_ok=False)
    report = {"real_inferences": 0, "cases": [], "human_listening": "not evaluated"}
    # Revalidate raw bytes through a new isolated task, preserving old failed jobs.
    for file in (ROOT / "data/.transcriptions").glob("*/jobs/*/job.json"):
        job = json.loads(file.read_text(encoding="utf-8"))
        if job.get("status") != "failed" or "时间超出片段" not in (job.get("error") or ""):
            continue
        raw = file.parent / "performance.mid"
        project_dir = file.parents[2]
        project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        source = project_dir / "source" / project["source"]["file"]

        def cached(operation, source_file, output, cancel, raw=raw, work=file.parent):
            shutil.copyfile(raw if operation == "transcribe" else work / "piano.wav", output)

        manager = TranscriptionManager(out / uuid4().hex / "data/.transcriptions", worker=cached)
        try:
            with source.open("rb") as stream:
                detail = asyncio.run(manager.upload(UploadFile(filename=source.name, file=stream)))
            pid = detail["project"]["id"]
            p = job["parameters"]
            new = manager.start(pid, Start(start=p["start"], end=p["end"], mode=p["mode"]))
            done = wait(manager, pid, new["id"])
            if done["status"] == "awaiting_review":
                manager.resume(pid, new["id"], None)
                done = wait(manager, pid, new["id"])
            result = manager.detail(pid)["results"][0] if done["status"] == "succeeded" else None
            report["cases"].append(
                {
                    "kind": "cached-model-output",
                    "source_job": str(file.relative_to(ROOT)),
                    "new_job": done["id"],
                    "status": done["status"],
                    "notes": len(result["notes"]) if result else 0,
                    "boundary_report": result["boundary_report"] if result else None,
                    "raw_preserved": bool(
                        result
                        and hashlib.sha256(raw.read_bytes()).hexdigest()
                        == result["files"]["performance.mid"]
                    ),
                }
            )
        finally:
            manager.close()
    # Save a new revision of every reported polyphony failure in a disposable copy.
    for file in (ROOT / "data/.transcriptions").glob("*/revisions/*/revision.json"):
        old = json.loads(file.read_text(encoding="utf-8"))
        if "每手超过2个" not in (old.get("notation_error") or ""):
            continue
        source = file.parents[2]
        pid = source.name
        dest = out / uuid4().hex / "data/.transcriptions"
        shutil.copytree(source, dest / pid)
        manager = TranscriptionManager(dest)
        try:
            revision = manager.save(
                pid,
                Save(
                    base=manager.header(pid)["current_revision"],
                    result_id=old["result_id"],
                    notes=[TranscriptionNote(**n) for n in old["notes"]],
                    settings=ScoreSettings(**old["settings"]),
                ),
            )
            report["cases"].append(
                {
                    "kind": "polyphonic-revision",
                    "source_revision": str(file.relative_to(ROOT)),
                    "status": "succeeded" if revision["score"] else "failed",
                    "notes": len(revision["notes"]),
                    "voices": revision["score"]["report"]["voices_per_hand"]
                    if revision["score"]
                    else None,
                    "notation_error": revision["notation_error"],
                    "revision_id": revision["id"],
                    "same_notes": revision["notes"] == old["notes"],
                    "directory": str((dest / pid / "revisions" / revision["id"]).relative_to(ROOT)),
                }
            )
        finally:
            manager.close()
    (out / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(out)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["cases"] or any(c["status"] != "succeeded" for c in report["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
