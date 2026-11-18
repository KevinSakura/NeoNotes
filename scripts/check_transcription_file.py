"""Explicit isolated file check. No implicit retry, no human quality approval."""

# ruff: noqa: E402
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import UploadFile

from backend.app.resources import AudioPaths
from backend.app.transcription_score import ScoreSettings
from backend.app.transcription_score import TranscriptionNote as N
from backend.app.transcriptions import Save, Start, TranscriptionManager, digest
from scripts.check_transcription import command, wait


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float, default=20)
    parser.add_argument("--mode", choices=["solo", "mix"], default="mix")
    args = parser.parse_args()
    out = ROOT / ".cache/transcription-checks" / time.strftime("user-file-%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    manager = TranscriptionManager(out / "data/.transcriptions")
    evidence = {
        "input": args.input.name,
        "input_sha256": digest(args.input),
        "human_listening": "not evaluated",
        "continuation": "technical test only, not human approval",
    }
    try:
        with args.input.open("rb") as stream:
            d = asyncio.run(manager.upload(UploadFile(filename=args.input.name, file=stream)))
        pid = d["project"]["id"]
        begin = time.monotonic()
        job = manager.start(pid, Start(start=args.start, end=args.end, mode=args.mode))
        job = wait(manager, pid, job["id"])
        if job["status"] == "awaiting_review":
            manager.resume(pid, job["id"], None)
            job = wait(manager, pid, job["id"])
        evidence.update(project_id=pid, job=job, elapsed_seconds=time.monotonic() - begin)
        if job["status"] == "succeeded":
            result = manager.detail(pid)["results"][0]
            notes = [N(**n) for n in result["notes"]]
            revision = manager.save(
                pid, Save(result_id=result["id"], notes=notes, settings=ScoreSettings())
            )
            evidence.update(
                result_id=result["id"],
                notes=len(notes),
                revision_id=revision["id"],
                notation_error=revision["notation_error"],
            )
            paths = AudioPaths()
            command(
                [
                    paths.fluidsynth,
                    "-ni",
                    "-r",
                    "44100",
                    "-F",
                    out / "midi-preview.wav",
                    paths.soundfont,
                    manager.directory(pid) / "results" / result["id"] / "performance.mid",
                ]
            )
        (out / "verification.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(out, job["status"], flush=True)
        if job["status"] != "succeeded":
            raise SystemExit(1)
    finally:
        manager.close()


if __name__ == "__main__":
    main()
