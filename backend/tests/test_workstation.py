import json
from copy import deepcopy
from io import BytesIO

import mido
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.midi import export_midi, import_midi


def client_for(root):
    client = TestClient(create_app(root))
    client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
    return client


@pytest.fixture
def client(tmp_path):
    return client_for(tmp_path / "songs")


def create(client, name="歌曲 a"):
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def save(client, project, state, label="保存测试版本"):
    return client.put(
        f"/api/projects/{project['id']}",
        json={
            "base_revision": project["current_revision"],
            "state": state,
            "label": label,
        },
    )


def source_midi(ppq=960, type_=1, controller=None):
    file = mido.MidiFile(type=type_, ticks_per_beat=ppq, charset="utf-8")
    conductor = mido.MidiTrack(
        [
            mido.MetaMessage("set_tempo", tempo=500000),
            mido.MetaMessage("time_signature", numerator=3, denominator=4),
            mido.MetaMessage("key_signature", key="G"),
            mido.MetaMessage("set_tempo", tempo=1000000, time=ppq * 2),
            mido.MetaMessage("end_of_track", time=ppq * 6),
        ]
    )
    if type_ != 0:
        file.tracks.append(conductor)
    for name, channel, program, pitch in [
        ("键盘", 0, 4, 64),
        ("鼓", 9, 0, 36),
        ("吉他", 1, 25, 55),
        ("贝斯", 2, 33, 40),
    ][: 1 if type_ == 0 else 4]:
        track = mido.MidiTrack(
            [
                mido.MetaMessage("track_name", name=name),
                mido.Message("program_change", channel=channel, program=program),
            ]
        )
        if controller:
            track.append(
                mido.Message("control_change", channel=channel, control=controller, value=127)
            )
        track.extend(
            [
                mido.Message("note_on", channel=channel, note=pitch, velocity=83),
                mido.Message("note_on", channel=channel, note=pitch, velocity=0, time=ppq),
                mido.Message("note_on", channel=channel, note=pitch + 2, velocity=99, time=ppq),
                mido.Message("note_off", channel=channel, note=pitch + 2, time=ppq),
                mido.MetaMessage("end_of_track", time=ppq * 5),
            ]
        )
        if type_ == 0:
            track.insert(1, mido.MetaMessage("set_tempo", tempo=500000))
        file.tracks.append(track)
    stream = BytesIO()
    file.save(file=stream)
    return stream.getvalue()


def test_empty_projects_isolation_and_restart(client, tmp_path):
    first, second = create(client, "同名歌曲"), create(client, "同名歌曲")
    assert first["id"] != second["id"]
    assert [len(track["notes"]) for track in first["revision"]["state"]["score"]["tracks"]] == [
        0
    ] * 4
    state = deepcopy(first["revision"]["state"])
    state["inputs"].update(
        theme="毕业",
        emotion="明亮但不舍",
        lyrics_text="第一行\n第二行",
        selected_lines=[0, 1],
    )
    state["inputs"]["recording"] = [
        {
            "pitch": 60,
            "velocity": 90,
            "start_seconds": 0.1234567,
            "duration_seconds": 0.3456789,
            "channel": 0,
            "bank_msb": 0,
            "bank_lsb": 0,
            "track_id": state["score"]["tracks"][0]["id"],
            "family": "keyboard",
            "program": 0,
        }
    ]
    updated = save(client, first, state).json()
    restarted = client_for(tmp_path / "songs")
    assert restarted.get(f"/api/projects/{first['id']}").json()["revision"]["state"] == state
    assert (
        restarted.get(f"/api/projects/{second['id']}").json()["revision"]["state"]["inputs"][
            "theme"
        ]
        == ""
    )
    material = tmp_path / "songs" / first["id"] / "inputs" / updated["current_revision"]
    assert (material / "lyrics.txt").read_text(encoding="utf-8") == "第一行\n第二行"
    assert json.loads((material / "performance.json").read_text())[0]["start_seconds"] == 0.1234567


def test_saved_undo_redo_and_restore_survive_restart(client, tmp_path):
    original = create(client)
    state = deepcopy(original["revision"]["state"])
    state["score"]["tracks"][0]["notes"] = [
        {
            "id": "n1",
            "pitch": 64,
            "velocity": 95,
            "start_tick": 9000,
            "duration_tick": 960,
        }
    ]
    edited = save(client, original, state).json()
    restarted = client_for(tmp_path / "songs")
    undone = restarted.post(
        f"/api/projects/{original['id']}/undo",
        json={"base_revision": edited["current_revision"]},
    ).json()
    assert undone["revision"]["state"] == original["revision"]["state"]
    assert undone["can_redo"]
    redone = restarted.post(
        f"/api/projects/{original['id']}/redo",
        json={"base_revision": undone["current_revision"]},
    ).json()
    assert redone["revision"]["state"] == state
    restored = restarted.post(
        f"/api/projects/{original['id']}/restore",
        json={
            "base_revision": redone["current_revision"],
            "revision_id": original["current_revision"],
        },
    ).json()
    assert restored["revision"]["number"] == 3
    assert restored["revision"]["state"] == original["revision"]["state"]
    assert len(restarted.get(f"/api/projects/{original['id']}/history").json()) == 3


def test_stale_write_rejected(client):
    original = create(client)
    state = deepcopy(original["revision"]["state"])
    state["inputs"]["theme"] = "第一次保存"
    updated = save(client, original, state).json()
    state["inputs"]["theme"] = "来自旧页面的覆盖"
    response = save(client, original, state)
    assert response.status_code == 409
    assert client.get(f"/api/projects/{original['id']}").json() == updated


def test_disk_failure_keeps_committed_version(client, monkeypatch):
    original = create(client)
    store = client.app.state.store
    write_json = store.write_json

    def fail_pointer(path, content):
        if path.name == "project.json":
            raise OSError("simulated disk failure")
        return write_json(path, content)

    monkeypatch.setattr(store, "write_json", fail_pointer)
    state = deepcopy(original["revision"]["state"])
    state["inputs"]["theme"] = "不应覆盖原文件"
    assert save(client, original, state).status_code == 500
    assert client.get(f"/api/projects/{original['id']}").json() == original
    assert len(client.get(f"/api/projects/{original['id']}/history").json()) == 1


def test_lyrics_original_and_selection_validation(client, tmp_path):
    original = create(client)
    content = b"\xef\xbb\xbf" + "悲伤的内容\r\n\r\n但曲风要明亮\r\n最后一行".encode()
    response = client.post(
        f"/api/projects/{original['id']}/lyrics",
        data={"base_revision": original["current_revision"]},
        files={"file": ("words.txt", content)},
    )
    assert response.status_code == 200
    uploaded = response.json()
    inputs = uploaded["revision"]["state"]["inputs"]
    assert inputs["lyrics_text"] == "悲伤的内容\n\n但曲风要明亮\n最后一行"
    file = inputs["files"][0]
    assert (
        tmp_path / "songs" / original["id"] / "inputs" / "originals" / f"{file['id']}.txt"
    ).read_bytes() == content
    state = deepcopy(uploaded["revision"]["state"])
    state["inputs"]["selected_lines"] = [0, 2]
    assert save(client, uploaded, state).status_code == 200
    current = client.get(f"/api/projects/{original['id']}").json()
    state["inputs"]["selected_lines"] = [0, 3]
    assert save(client, current, state).status_code == 422
    assert client.get(f"/api/projects/{original['id']}").json() == current


@pytest.mark.parametrize(
    "content,name",
    [
        (b"not midi", "bad.mid"),
        (source_midi(controller=64), "pedal.mid"),
        (source_midi(type_=2), "type2.mid"),
    ],
)
def test_bad_or_unsupported_midi_does_not_overwrite(client, content, name):
    original = create(client)
    response = client.post(
        f"/api/projects/{original['id']}/midi",
        data={"base_revision": original["current_revision"]},
        files={"file": (name, content)},
    )
    assert response.status_code == 422
    assert client.get(f"/api/projects/{original['id']}").json() == original


def test_real_midi_round_trip_channels_instruments_tempo_metadata(client, tmp_path):
    original = create(client)
    source = source_midi()
    response = client.post(
        f"/api/projects/{original['id']}/midi",
        data={"base_revision": original["current_revision"]},
        files={"file": ("band.mid", source)},
    )
    assert response.status_code == 200, response.text
    imported = response.json()
    score = imported["revision"]["state"]["score"]
    assert score["ppq"] == 480
    assert [track["name"] for track in score["tracks"]] == [
        "键盘",
        "鼓",
        "吉他",
        "贝斯",
    ]
    assert [track["program"] for track in score["tracks"]] == [4, 0, 25, 33]
    assert [track["channel"] for track in score["tracks"]] == [0, 9, 1, 2]
    assert score["tracks"][1]["is_drum"] is True
    result = client.post(
        f"/api/projects/{original['id']}/midi-export",
        json={"base_revision": imported["current_revision"]},
    )
    assert result.status_code == 200 and result.content.startswith(b"MThd")
    binary = mido.MidiFile(file=BytesIO(result.content), charset="utf-8")
    assert binary.type == 1 and len(binary.tracks) == 5 and binary.ticks_per_beat == 480
    assert binary.length == pytest.approx(7.0)
    assert any(
        message.type == "program_change" and message.program == 25 and message.channel == 1
        for message in binary.tracks[3]
    )
    assert any(
        message.type == "note_on" and message.note == 36 and message.channel == 9
        for message in binary.tracks[2]
    )
    assert any(
        message.type == "key_signature" and message.key == "G" for message in binary.tracks[0]
    )
    round_trip = import_midi(result.content)
    assert round_trip.seconds_at(round_trip.end_tick) == pytest.approx(7.0)
    for expected, actual in zip(score["tracks"], round_trip.tracks, strict=True):
        assert [
            (note["pitch"], note["velocity"], note["start_tick"], note["duration_tick"])
            for note in expected["notes"]
        ] == [
            (note.pitch, note.velocity, note.start_tick, note.duration_tick)
            for note in actual.notes
        ]
    path = tmp_path / "songs" / original["id"] / "exports" / f"{imported['current_revision']}.mid"
    assert path.read_bytes() == result.content


def test_type_zero_import():
    score = import_midi(source_midi(type_=0, ppq=96))
    assert len(score.tracks) == 1 and score.tracks[0].notes[0].duration_tick == 480


def test_instrument_name_and_unicode_metadata_round_trip():
    file = mido.MidiFile(file=BytesIO(source_midi()), charset="utf-8")
    file.tracks[1].insert(1, mido.MetaMessage("instrument_name", name="温暖电钢琴"))
    file.tracks[1].insert(2, mido.MetaMessage("lyrics", text="悲伤的词，明亮的音乐"))
    stream = BytesIO()
    file.save(file=stream)
    score = import_midi(stream.getvalue())
    exported = mido.MidiFile(file=BytesIO(export_midi(score)), charset="utf-8")
    assert any(
        message.type == "instrument_name" and message.name == "温暖电钢琴"
        for message in exported.tracks[1]
    )
    assert any(
        message.type == "lyrics" and message.text == "悲伤的词，明亮的音乐"
        for message in exported.tracks[1]
    )


def test_session_origin_host_and_path_protection(client):
    assert (
        client.post(
            "/api/projects", headers={"X-Session-Token": "invalid"}, json={"name": "x"}
        ).status_code
        == 403
    )
    assert client.get("/api/session", headers={"Origin": "https://example.com"}).status_code == 403
    assert client.get("/api/session", headers={"Host": "example.com"}).status_code == 400
    assert client.get("/api/projects/not-a-song-id").status_code == 404
    assert client.get("/.env").status_code == 404
    assert client.get("/backend/app/main.py").status_code in (404, 503)
    assert client.get("/api/health").json()["ai_enabled"] is True
    assert client.get("/api/health").json()["stage"] == 6
    assert client.get("/api/health").json()["model_mode"] == "mock"
    assert (
        client.get("/api/health").json()["mp3_enabled"]
        == client.get("/api/audio/resources").json()["render_ready"]
    )


@pytest.mark.parametrize("mutation", ["drum", "pitch", "nan", "too_long", "duplicate"])
def test_invalid_music_not_committed(client, mutation):
    original = create(client)
    state = deepcopy(original["revision"]["state"])
    tracks = state["score"]["tracks"]
    if mutation == "drum":
        tracks[1]["channel"] = 3
    elif mutation == "pitch":
        tracks[0]["notes"] = [
            {
                "id": "bad",
                "pitch": 128,
                "velocity": 90,
                "start_tick": 0,
                "duration_tick": 480,
            }
        ]
    elif mutation == "nan":
        state["inputs"]["selected_lines"] = [True]
    elif mutation == "too_long":
        state["score"]["end_tick"] = 1000000
    else:
        tracks[1]["id"] = tracks[0]["id"]
    assert save(client, original, state).status_code == 422
    assert client.get(f"/api/projects/{original['id']}").json() == original


def test_empty_export_and_bad_lyrics(client):
    original = create(client)
    assert (
        client.post(
            f"/api/projects/{original['id']}/midi-export",
            json={"base_revision": original["current_revision"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/projects/{original['id']}/lyrics",
            data={"base_revision": original["current_revision"]},
            files={"file": ("bad.txt", b"\xff\xfe\x00")},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/projects/{original['id']}/lyrics",
            data={"base_revision": original["current_revision"]},
            files={"file": ("too-big.txt", b"a" * 512001)},
        ).status_code
        == 413
    )


def test_history_cannot_restore_other_song(client):
    first, second = create(client), create(client)
    response = client.post(
        f"/api/projects/{first['id']}/restore",
        json={
            "base_revision": first["current_revision"],
            "revision_id": second["current_revision"],
        },
    )
    assert response.status_code == 404
    assert client.get(f"/api/projects/{first['id']}").json() == first
