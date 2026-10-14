"""Prepare an isolated MIDI/notation fixture. No env, model requests or singing."""

import json
import sys
from io import BytesIO
from pathlib import Path

import mido

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.practice_import import parse_practice  # noqa: E402
from backend.app.practice_notation import NotationSettings, prepare_notation  # noqa: E402


def fixture(grand=False):
    mid = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf-8")
    meta = mido.MidiTrack()
    mid.tracks.append(meta)
    meta.extend(
        [
            mido.MetaMessage("set_tempo", tempo=666667),
            mido.MetaMessage("time_signature", numerator=4, denominator=4),
            mido.MetaMessage("set_tempo", tempo=500000, time=7680),
            mido.MetaMessage("time_signature", numerator=3, denominator=4),
            mido.MetaMessage("key_signature", key="Bb"),
            mido.MetaMessage("time_signature", numerator=6, denominator=8, time=2880),
            mido.MetaMessage("end_of_track", time=2880),
        ]
    )
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.extend(
        [
            mido.MetaMessage("track_name", name="OSMD固定隔离谱"),
            mido.Message("program_change", program=0),
        ]
    )
    notes = [(0, 480, p) for p in (60, 64, 67)] + [
        (480, 720, 61),
        (960, 1200, 62),
        (1920, 2400, 65),
        (3840, 4320, 64),
        (5760, 6240, 67),
        (7200, 8400, 60),
        (8400, 8640, 60),
        (9120, 9600, 70),
        (10560, 10720, 62),
        (10720, 10880, 64),
        (10880, 11040, 65),
        (12000, 12480, 60),
    ]
    if grand:
        notes.append((0, 960, 48))
    events = [(a, 1, mido.Message("note_on", note=p, velocity=90)) for a, b, p in notes]
    events += [(b, 0, mido.Message("note_off", note=p)) for a, b, p in notes]
    previous = 0
    for tick, _, event in sorted(events, key=lambda e: (e[0], e[1], e[2].note)):
        track.append(event.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=13440 - previous))
    output = BytesIO()
    mid.save(file=output)
    return output.getvalue()


def simple_fixture():
    mid = mido.MidiFile(type=0, ticks_per_beat=480, charset="utf-8")
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.extend(
        [
            mido.MetaMessage("set_tempo", tempo=500000),
            mido.MetaMessage("time_signature", numerator=4, denominator=4),
            mido.Message("program_change", program=0),
        ]
    )
    notes = [(0, 240, 60), (480, 720, 62), (960, 1200, 61)] + [
        (1440, 1680, p) for p in (60, 64, 67)
    ]
    events = [(a, 1, mido.Message("note_on", note=p, velocity=90)) for a, b, p in notes]
    events += [(b, 0, mido.Message("note_off", note=p)) for a, b, p in notes]
    previous = 0
    for tick, _, event in sorted(events, key=lambda e: (e[0], e[1], e[2].note)):
        track.append(event.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=3840 - previous))
    output = BytesIO()
    mid.save(file=output)
    return output.getvalue()


if __name__ == "__main__":
    output = ROOT / ".cache/practice-evaluation/implementation-20261013/fixed-score"
    output.mkdir(parents=True, exist_ok=True)
    content = fixture()
    parsed = parse_practice(content)
    n = parsed["tracks"][0]["notes"]
    settings = NotationSettings(
        track_id=parsed["tracks"][0]["id"], end_measure=8, grid=0, slurs=[(n[0]["id"], n[3]["id"])]
    )
    result = prepare_notation(parsed, settings)
    (output / "source.mid").write_bytes(content)
    (output / "preview.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "score.musicxml").write_text(result["musicxml"], encoding="utf-8")
    print(
        json.dumps(
            {
                "path": str(output),
                "attacks": len(result["targets"]["attacks"]),
                "segments": len(result["notation"]["segments"]),
                "api_requests": 0,
                "singing_inferences": 0,
            }
        )
    )
    simple = output.parent / "simple-score"
    simple.mkdir(exist_ok=True)
    (simple / "source.mid").write_bytes(simple_fixture())
    grand = output.parent / "grand-score"
    grand.mkdir(exist_ok=True)
    (grand / "source.mid").write_bytes(fixture(grand=True))
