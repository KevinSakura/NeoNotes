"""Explicit, isolated local real inference on original SoundFont-rendered material."""
# ruff: noqa: E402

import argparse
import asyncio
import io
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mido
from fastapi import UploadFile

from backend.app.resources import AudioPaths
from backend.app.transcription_score import ScoreSettings, midi_bytes
from backend.app.transcription_score import TranscriptionNote as N
from backend.app.transcriptions import Save, Start, TranscriptionManager, digest


def command(args):
    subprocess.run([str(x) for x in args], check=True, capture_output=True, timeout=120)


def wait(manager, pid, jid):
    while manager.active:
        time.sleep(0.5)
    return manager.job(pid, jid)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", required=True)
    parser.parse_args()
    out = ROOT / ".cache/transcription-checks" / time.strftime("real-%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    paths = AudioPaths()
    notes = [
        N(pitch=p, onset=0.5 + i * 0.5, offset=0.9 + i * 0.5, velocity=85)
        for i, p in enumerate([60, 62, 64, 60, 67, 67, 65, 64, 62, 60])
    ]
    notes.extend(
        [
            N(pitch=p, onset=5.5, offset=6.5, velocity=80, hand="left" if p < 60 else "right")
            for p in [48, 60, 64, 67]
        ]
    )
    (out / "reference.mid").write_bytes(midi_bytes(notes))
    command(
        [
            paths.fluidsynth,
            "-ni",
            "-r",
            "44100",
            "-g",
            ".6",
            "-F",
            out / "piano-full.wav",
            paths.soundfont,
            out / "reference.mid",
        ]
    )
    command(
        [
            paths.ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            out / "piano-full.wav",
            "-t",
            "8",
            "-ac",
            "2",
            out / "piano.wav",
        ]
    )
    # Original bass + guitar arrangement, not a commercial recording.
    midi = mido.MidiFile(
        file=io.BytesIO(
            midi_bytes(
                [
                    N(pitch=36 + i % 3 * 7, onset=0.5 + i * 0.5, offset=0.85 + i * 0.5, velocity=80)
                    for i in range(12)
                ]
            )
        )
    )
    midi.tracks[1][1] = mido.Message("program_change", program=33)
    midi.save(out / "bass.mid")
    command(
        [
            paths.fluidsynth,
            "-ni",
            "-r",
            "44100",
            "-g",
            ".35",
            "-F",
            out / "bass.wav",
            paths.soundfont,
            out / "bass.mid",
        ]
    )
    command(
        [
            paths.ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            out / "piano.wav",
            "-i",
            out / "bass.wav",
            "-filter_complex",
            "amix=inputs=2:duration=first:normalize=0",
            "-t",
            "8",
            out / "mix.wav",
        ]
    )
    command([paths.ffmpeg, "-v", "error", "-y", "-i", out / "piano.wav", out / "piano.mp3"])
    evidence = {
        "fixture": "original rendered piano + electric bass, no vocals; not a real recorded song",
        "reference_notes": [n.model_dump() for n in notes],
        "soundfont_sha256": digest(paths.soundfont),
        "human_listening": "not evaluated",
        "cases": [],
    }
    manager = TranscriptionManager(out / "data/.transcriptions")
    try:
        for mode, filename in [("solo", "piano.mp3"), ("mix", "mix.wav")]:
            before = time.monotonic()
            d = asyncio.run(
                manager.upload(
                    UploadFile(filename=filename, file=io.BytesIO((out / filename).read_bytes()))
                )
            )
            pid = d["project"]["id"]
            job = manager.start(pid, Start(end=8, mode=mode))
            job = wait(manager, pid, job["id"])
            if job["status"] == "awaiting_review":
                # Technical test continuation only, never classified as human listening.
                manager.resume(pid, job["id"], None)
                job = wait(manager, pid, job["id"])
            item = {
                "mode": mode,
                "project_id": pid,
                "job": job,
                "elapsed_seconds": time.monotonic() - before,
            }
            if job["status"] == "succeeded":
                r = manager.detail(pid)["results"][0]
                ns = [N(**n) for n in r["notes"]]
                item.update(
                    notes=len(ns), pitch_range=[min(n.pitch for n in ns), max(n.pitch for n in ns)]
                )
                rev = manager.save(pid, Save(result_id=r["id"], notes=ns, settings=ScoreSettings()))
                item["revision_id"] = rev["id"]
                item["notation_error"] = rev["notation_error"]
                command(
                    [
                        paths.fluidsynth,
                        "-ni",
                        "-r",
                        "44100",
                        "-F",
                        out / f"{mode}-midi-preview.wav",
                        paths.soundfont,
                        manager.directory(pid) / "results" / r["id"] / "performance.mid",
                    ]
                )
            evidence["cases"].append(item)
            (out / "verification.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(mode, job["status"], flush=True)
    finally:
        manager.close()
    print(out)
    if any(c["job"]["status"] != "succeeded" for c in evidence["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
