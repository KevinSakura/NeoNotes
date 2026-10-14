"""Versioned, original beginner scores. No model, remote resources or song writes."""

import hashlib
import json
import re
from copy import deepcopy
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import mido

from .practice_import import parse_practice
from .practice_notation import NotationSettings, hash_json, prepare_notation
from .storage import StorageError

CATALOG_PATH = Path(__file__).resolve().parents[1] / "resources/practice-units-v1.json"
COMPILER_VERSION = "neonote-practice-unit-compiler-v1"


def require(condition):
    if not condition:
        raise ValueError("Invalid packaged practice definition")


@lru_cache(maxsize=1)
def definitions():
    try:
        document = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        require(document["id"] == "neonote-beginner-v1" and document["version"] == 1)
        require(1 <= len(document["units"]) <= 12)
        seen = set()
        for unit in document["units"]:
            require(isinstance(unit["title"], str) and unit["title"])
            require(1 <= len(unit["exercises"]) <= 50)
            require(unit["id"] not in seen)
            seen.add(unit["id"])
            for exercise in unit["exercises"]:
                require(re.fullmatch(r"[a-z][a-z0-9-]{1,60}", exercise["id"]))
                require(exercise["id"] not in seen)
                seen.add(exercise["id"])
                require(type(exercise["bars"]) is int and 2 <= exercise["bars"] <= 4)
                require(type(exercise["bpm"]) is int and 40 <= exercise["bpm"] <= 120)
                require(exercise["key"] in ("C", "F", "G"))
                total, has_notes = 0, False
                for pitches, beats in exercise["sequence"]:
                    require(type(beats) in (int, float) and beats in (0.5, 1, 2, 3, 4))
                    total += beats
                    if pitches is not None:
                        has_notes = True
                        chord = pitches if isinstance(pitches, list) else [pitches]
                        require(chord and len(chord) == len(set(chord)))
                        require(all(type(p) is int and 60 <= p <= 72 for p in chord))
                require(has_notes and total == exercise["bars"] * 4)
        return document
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        raise StorageError(
            "本地基础练习目录缺失或不合法，请检查安装资源；没有创建替代练习。", 503
        ) from None


@lru_cache(maxsize=32)
def _bundle(exercise_id):
    document = definitions()
    for unit in document["units"]:
        exercise = next((e for e in unit["exercises"] if e["id"] == exercise_id), None)
        if exercise is not None:
            break
    else:
        raise StorageError("这条本地练习不存在，请刷新单元列表。", 404)
    midi = mido.MidiFile(type=0, ticks_per_beat=480, charset="utf-8")
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend(
        [
            mido.MetaMessage("track_name", name=exercise["title"]),
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(exercise["bpm"])),
            mido.MetaMessage("time_signature", numerator=4, denominator=4),
            mido.MetaMessage("key_signature", key=exercise["key"]),
            mido.Message("control_change", control=0, value=0),
            mido.Message("control_change", control=32, value=0),
            mido.Message("program_change", program=0),
        ]
    )
    at, events = 0, []
    for pitches, beats in exercise["sequence"]:
        duration = int(beats * 480)
        if pitches is not None:
            for pitch in pitches if isinstance(pitches, list) else [pitches]:
                events.extend([(at, 1, pitch), (at + duration, 0, pitch)])
        at += duration
    previous = 0
    for tick, on, pitch in sorted(events):
        track.append(
            mido.Message(
                "note_on" if on else "note_off",
                note=pitch,
                velocity=85 if on else 0,
                time=tick - previous,
            )
        )
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=at - previous))
    content = BytesIO()
    midi.save(file=content)
    binary = content.getvalue()
    parsed = parse_practice(binary)
    settings = NotationSettings(
        track_id=parsed["tracks"][0]["id"], grid=0, end_measure=exercise["bars"], clef="treble"
    )
    prepared = prepare_notation(parsed, settings)
    if (
        not prepared["ready"]
        or prepared["report"]["blocked_targets"]
        or prepared["report"]["difficult_targets"]
    ):
        raise StorageError("本地练习无法按当前键位或记谱规则完成，请检查教材定义。", 503)
    definition_hash = hash_json(
        {
            "catalog": document["id"],
            "version": document["version"],
            "unit_id": unit["id"],
            "unit_title": unit["title"],
            "exercise": exercise,
            "compiler": COMPILER_VERSION,
        }
    )
    identity = {
        "catalog_id": document["id"],
        "catalog_version": document["version"],
        "unit_id": unit["id"],
        "unit_title": unit["title"],
        "exercise_id": exercise["id"],
        "exercise_title": exercise["title"],
        "goal": exercise["goal"],
        "instructions": exercise["instructions"],
        "bars": exercise["bars"],
        "bpm": exercise["bpm"],
        "start_tick": 0,
        "definition_hash": definition_hash,
        "midi_hash": hashlib.sha256(binary).hexdigest(),
        "settings_hash": hash_json(settings.model_dump()),
        "targets_hash": hash_json(prepared["targets"]),
        "notation_hash": hash_json(prepared["notation"]),
        "musicxml_hash": hashlib.sha256(prepared["musicxml"].encode()).hexdigest(),
        "compiler": COMPILER_VERSION,
    }
    identity["artifact_hash"] = hash_json(identity)
    return {"builtin": identity, "settings": settings, "prepared": prepared, "midi": binary}


def exercise_bundle(exercise_id):
    # A caller must not mutate the cached source or the identities used by later calls.
    return deepcopy(_bundle(exercise_id))


def catalog():
    document = definitions()
    units = []
    for unit in document["units"]:
        exercises = []
        for exercise in unit["exercises"]:
            identity = _bundle(exercise["id"])["builtin"]
            exercises.append(
                {
                    k: deepcopy(identity[k])
                    for k in (
                        "exercise_id",
                        "exercise_title",
                        "goal",
                        "instructions",
                        "bars",
                        "bpm",
                        "artifact_hash",
                    )
                }
            )
        units.append(
            {
                "id": unit["id"],
                "title": unit["title"],
                "description": unit["description"],
                "exercises": exercises,
            }
        )
    return {
        "id": document["id"],
        "version": document["version"],
        "author": document["author"],
        "units": units,
    }
