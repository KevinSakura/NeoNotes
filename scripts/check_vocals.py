"""Real local singing acceptance on isolated fixed scores; never loads .env or an LLM."""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import create_app  # noqa: E402
from backend.app.models import (  # noqa: E402
    Composition,
    LyricUnit,
    Note,
    SongChord,
    SongSection,
    SongState,
    Tempo,
    empty_song,
)
from backend.app.vocal_score import Pronunciation, VocalSettings  # noqa: E402

LYRICS = "晨风轻轻吹过山岗\n重逢时你笑着说好\n让歌声陪我到远方"


def fixture(tempo_change=False):
    state = empty_song()
    state.inputs.lyrics_text = LYRICS
    state.inputs.selected_lines = [0, 1, 2]
    state.score.tempo_map = [Tempo(microseconds_per_beat=666667)]
    lead = state.score.tracks[0]
    alignment, cursor = [], 1920
    interlude = None
    for line_index, line in enumerate(LYRICS.splitlines()):
        if line_index == 1:
            interlude = cursor
            cursor += 1920
        for unit_index, text in enumerate(line):
            count = 3 if text == "方" else 2 if (line_index, unit_index) in ((0, 2), (1, 7)) else 1
            ids = []
            for index in range(count):
                note = Note(
                    id=f"v-{line_index}-{unit_index}-{index}",
                    pitch=[60, 62, 64, 65, 67, 64, 62, 60][unit_index] + index % 2 * 2,
                    start_tick=cursor,
                    duration_tick=480,
                )
                lead.notes.append(note)
                ids.append(note.id)
                cursor += 480
            alignment.append(
                LyricUnit(line_index=line_index, unit_index=unit_index, text=text, note_ids=ids)
            )
        cursor += 960
    state.score.end_tick = cursor + 1920
    if tempo_change:
        state.score.tempo_map.append(Tempo(tick=interlude, microseconds_per_beat=555556))
    for index, start in enumerate((0, interlude, cursor)):
        lead.notes.append(
            Note(id=f"instrumental-{index}", pitch=60, start_tick=start, duration_tick=480)
        )
    for track, pitch in zip(state.score.tracks[1:], (36, 55, 40), strict=True):
        step = 480 if track.is_drum else 960
        for start in range(0, state.score.end_tick, step):
            track.notes.append(
                Note(
                    id=f"{track.family}-{start}",
                    pitch=pitch,
                    start_tick=start,
                    duration_tick=min(120 if track.is_drum else 720, state.score.end_tick - start),
                )
            )
    state.composition = Composition(
        job_id="0" * 32,
        mock=True,
        title="手工固定谱三句演唱",
        key="C",
        style_summary="固定谱测试材料，不请求作曲模型",
        scope="lyrics",
        target_seconds=state.score.seconds_at(state.score.end_tick),
        melody_track_id=lead.id,
        sections=[SongSection(name="三句测试", start_tick=0, end_tick=state.score.end_tick)],
        chords=[
            SongChord(label="C", start_tick=0, end_tick=state.score.end_tick, pitches=[60, 64, 67])
        ],
        lyrics_alignment=alignment,
    )
    return state


def boundary_fixture():
    state = empty_song()
    line = "晨风轻轻吹过山岗让歌声陪我到远方"
    assert len(line) == 16
    state.inputs.lyrics_text = "\n".join([line] * 64)
    state.inputs.selected_lines = list(range(64))
    state.score.tempo_map = [Tempo(microseconds_per_beat=250000)]
    lead, cursor, alignment = state.score.tracks[0], 1920, []
    for line_index in range(64):
        for unit_index, text in enumerate(line):
            note = Note(
                id=f"boundary-{line_index}-{unit_index}",
                pitch=[60, 62, 64, 65, 67, 64, 62, 60][unit_index % 8],
                start_tick=cursor,
                duration_tick=480,
            )
            lead.notes.append(note)
            alignment.append(
                LyricUnit(
                    line_index=line_index, unit_index=unit_index, text=text, note_ids=[note.id]
                )
            )
            cursor += 480
        cursor += 960
    state.score.end_tick = cursor + 1920
    state.composition = Composition(
        job_id="0" * 32,
        mock=True,
        title="1024字词固定谱边界",
        key="C",
        style_summary="边界测试材料，不请求作曲模型",
        scope="lyrics",
        target_seconds=state.score.seconds_at(state.score.end_tick),
        melody_track_id=lead.id,
        sections=[SongSection(name="边界测试", start_tick=0, end_tick=state.score.end_tick)],
        chords=[
            SongChord(label="C", start_tick=0, end_tick=state.score.end_tick, pitches=[60, 64, 67])
        ],
        lyrics_alignment=alignment,
    )
    return state


def note_timing_check(state, engine):
    track = next(t for t in state.score.tracks if t.id == state.composition.melody_track_id)
    expected = [
        next(n for n in track.notes if n.id == key)
        for unit in state.composition.lyrics_alignment
        for key in unit.note_ids
    ]
    actual = [note for phrase in engine["phrases"] for note in phrase["notes"]]
    assert len(actual) == len(expected)
    errors = []
    for source, rendered in zip(expected, actual, strict=True):
        start = state.score.seconds_at(source.start_tick)
        duration = state.score.seconds_at(source.start_tick + source.duration_tick) - start
        assert rendered["tone"] == source.pitch
        errors.extend(
            (
                abs(rendered["positionMs"] / 1000 - start),
                abs(rendered["durationMs"] / 1000 - duration),
            )
        )
    assert max(errors) < 0.000001, max(errors)
    return {
        "notes": len(expected),
        "maximum_coordinate_error_seconds": max(errors),
        "audible_vowel_sync": "manual assessment pending",
    }


def wait(client, project, item, timeout=1800):
    started, previous = time.monotonic(), None
    while time.monotonic() - started < timeout:
        response = client.get(f"/api/projects/{project['id']}/vocals/{item['id']}")
        if response.status_code != 200:
            raise RuntimeError(response.text)
        item = response.json()
        if item["phase"] != previous:
            print(item["phase"], flush=True)
            previous = item["phase"]
        if item["status"] == "completed":
            return item
        if item["status"] in ("failed", "cancelled", "interrupted"):
            raise RuntimeError(json.dumps(item, ensure_ascii=False))
        time.sleep(0.25)
    raise RuntimeError("Real vocal check timed out.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", choices=["DirectML", "CPU"], default="DirectML")
    parser.add_argument("--tempo-change", action="store_true")
    parser.add_argument("--remix", action="store_true")
    parser.add_argument("--label", default="three-lines")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--boundary", action="store_true")
    args = parser.parse_args()
    if not args.label.replace("-", "").isalnum():
        raise SystemExit("Invalid isolated run label.")
    destination = ROOT / ".cache/vocal-implementation-20261010" / f"{args.label}-{time.time_ns()}"
    destination.mkdir(parents=True)
    if args.state_file:
        if (
            args.boundary
            or args.state_file.suffix.lower() != ".json"
            or ".env" in args.state_file.parts
        ):
            raise SystemExit("Supply only a saved song revision JSON.")
        payload = json.loads(args.state_file.read_text(encoding="utf-8"))
        state = SongState.model_validate(payload.get("state", payload))
    else:
        state = boundary_fixture() if args.boundary else fixture(args.tempo_change)
    settings = VocalSettings(
        runner=args.runner,
        pronunciations=[]
        if args.state_file or args.boundary
        else [
            Pronunciation(line_index=1, unit_index=0, pinyin="chong"),
            Pronunciation(line_index=1, unit_index=5, pinyin="zhe"),
        ],
    )
    with TestClient(create_app(destination / "data")) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        resources = client.get("/api/vocals/resources").json()
        if not resources["ready"]:
            raise RuntimeError(resources)
        project = client.post("/api/projects", json={"name": "隔离真实演唱验证"}).json()
        # Preserve lyric text and notes, but re-register file provenance in the isolated song.
        # Historical source file IDs cannot be attached to a different project through PUT.
        if args.state_file and state.inputs.files:
            uploaded = client.post(
                f"/api/projects/{project['id']}/lyrics",
                data={"base_revision": project["current_revision"]},
                files={
                    "file": (
                        "isolated-lyrics.txt",
                        state.inputs.lyrics_text.encode("utf-8"),
                        "text/plain",
                    )
                },
            )
            if uploaded.status_code != 200:
                raise RuntimeError(uploaded.text)
            project = uploaded.json()
            state.inputs.files = SongState.model_validate(project["revision"]["state"]).inputs.files
        response = client.put(
            f"/api/projects/{project['id']}",
            json={
                "base_revision": project["current_revision"],
                "state": state.model_dump(),
                "label": "保存固定三句谱",
            },
        )
        if response.status_code != 200:
            raise RuntimeError(response.text)
        project = response.json()
        source = (
            destination
            / "data"
            / project["id"]
            / "revisions"
            / f"{project['current_revision']}.json"
        )
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        config = client.post(
            f"/api/projects/{project['id']}/vocal-settings",
            json={
                "base_revision": project["current_revision"],
                "settings": settings.model_dump(),
                "expected_settings_hash": None,
            },
        )
        if config.status_code != 200:
            raise RuntimeError(config.text)
        response = client.post(
            f"/api/projects/{project['id']}/vocals",
            json={
                "base_revision": project["current_revision"],
                "settings_hash": config.json()["hash"],
            },
        )
        if response.status_code != 202:
            raise RuntimeError(response.text)
        item = wait(client, project, response.json())
        jobs = [item]
        if args.remix:
            settings.vocal_gain = 0.6
            config = client.post(
                f"/api/projects/{project['id']}/vocal-settings",
                json={
                    "base_revision": project["current_revision"],
                    "settings": settings.model_dump(),
                    "expected_settings_hash": config.json()["hash"],
                },
            )
            response = client.post(
                f"/api/projects/{project['id']}/vocals",
                json={
                    "base_revision": project["current_revision"],
                    "settings_hash": config.json()["hash"],
                },
            )
            if response.status_code != 202:
                raise RuntimeError(response.text)
            remix = wait(client, project, response.json())
            assert remix["reused_from"] == item["id"]
            assert remix["files"]["vocal.wav"]["sha256"] == item["files"]["vocal.wav"]["sha256"]
            assert (
                remix["files"]["accompaniment.wav"]["sha256"]
                == item["files"]["accompaniment.wav"]["sha256"]
            )
            jobs.append(remix)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        for job in jobs:
            for name in ("vocal.wav", "accompaniment.wav", "mix.wav", "mix.mp3", "singing.ustx"):
                response = client.get(
                    f"/api/projects/{project['id']}/vocals/{job['id']}/assets/{name}"
                )
                assert response.status_code == 200, response.text
                assert hashlib.sha256(response.content).hexdigest() == job["files"][name]["sha256"]
        report = {
            "root": str(destination),
            "project_id": project["id"],
            "source_revision_hash": source_hash,
            "lyrics": state.inputs.lyrics_text,
            "fixture": "isolated copy of historical revision"
            if args.state_file
            else "1024-unit hand-authored boundary score"
            if args.boundary
            else "hand-authored fixed score",
            "tempo_change": len(state.score.tempo_map) > 1,
            "runner": args.runner,
            "real_local_synthesis": True,
            "paid_api_requests": 0,
            "jobs": jobs,
            "note_coordinates": note_timing_check(state, item["engine"]),
            "human_listening": "unassessed",
            "desktop_reference": "USTX supplied for manual OpenUtau comparison; no GUI automation",
        }
        (destination / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "root": str(destination),
                    "jobs": len(jobs),
                    "frames": item["frames"],
                    "elapsed_seconds": item["elapsed_seconds"],
                    "worker_seconds": item["engine"]["elapsed_seconds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
