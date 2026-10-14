"""Isolated conversion/lifecycle checks. Stubs here are not singing acceptance."""

import hashlib
import json
import sys
import time
from copy import deepcopy
from threading import Event

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.resources import AudioError
from backend.app.vocal_process import owned_process
from backend.app.vocal_resources import VocalPaths, VocalResource
from backend.app.vocal_score import Pronunciation, VocalSettings, convert_score
from scripts.check_vocals import fixture


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "data")) as result:
        result.headers["X-Session-Token"] = result.get("/api/session").json()["token"]
        yield result


def saved(client, state=None):
    project = client.post("/api/projects", json={"name": "隔离人声生命周期"}).json()
    response = client.put(
        f"/api/projects/{project['id']}",
        json={
            "base_revision": project["current_revision"],
            "label": "固定谱",
            "state": (state if state is not None else fixture()).model_dump(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def config(client, project, settings=None, expected=None):
    return client.post(
        f"/api/projects/{project['id']}/vocal-settings",
        json={
            "base_revision": project["current_revision"],
            "settings": (settings or VocalSettings()).model_dump(),
            "expected_settings_hash": expected,
        },
    )


def test_converter_uses_real_ids_full_tempo_and_only_removes_bound_notes():
    state = fixture(tempo_change=True)
    original = state.model_dump()
    settings = VocalSettings(
        pronunciations=[Pronunciation(line_index=1, unit_index=0, pinyin="chong")]
    )
    request, backing, removed = convert_score(state, settings)
    assert state.model_dump() == original
    assert len(request["lines"]) == 3
    assert sum(len(line["units"]) for line in request["lines"]) == 24
    assert len(request["tempos"]) == 2
    assert request["tempos"][1]["position"] == state.score.tempo_map[1].tick
    assert request["lines"][1]["units"][0]["lyric"] == "chong"
    for line in request["lines"]:
        for unit in line["units"]:
            for note in unit["notes"]:
                assert note["start_seconds"] == state.score.seconds_at(note["position"])
                assert note["end_seconds"] == state.score.seconds_at(
                    note["position"] + note["duration"]
                )
    assert {n.id for n in backing.tracks[0].notes} == {
        "instrumental-0",
        "instrumental-1",
        "instrumental-2",
    }
    assert set(removed) == {
        note for unit in state.composition.lyrics_alignment for note in unit.note_ids
    }
    assert backing.tracks[1:] == state.score.tracks[1:]
    assert backing.tempo_map == state.score.tempo_map
    assert request["frames"] == round((state.score.seconds_at(state.score.end_tick) + 3) * 44100)


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "english", "noncontiguous", "partial", "override"]
)
def test_invalid_lyric_inputs_are_rejected_without_guessing(change):
    state, settings = fixture(), VocalSettings()
    if change == "missing":
        state.composition.lyrics_alignment[0].note_ids = ["absent"]
    elif change == "duplicate":
        state.composition.lyrics_alignment[1].note_ids = state.composition.lyrics_alignment[
            0
        ].note_ids
    elif change == "english":
        state.inputs.lyrics_text = state.inputs.lyrics_text.replace("晨", "Hello", 1)
        state.composition.lyrics_alignment[0].text = "Hello"
    elif change == "noncontiguous":
        second = next(n for n in state.score.tracks[0].notes if n.id == "v-0-2-1")
        first = next(n for n in state.score.tracks[0].notes if n.id == "v-0-2-0")
        first.duration_tick -= 120
        assert second.start_tick - first.start_tick - first.duration_tick == 120
    elif change == "partial":
        state.inputs.selected_lines = [0]
        state.composition.lyrics_alignment = state.composition.lyrics_alignment[:8]
    else:
        settings.pronunciations = [Pronunciation(line_index=4, unit_index=0, pinyin="chong")]
    with pytest.raises(AudioError):
        convert_score(state, settings)


def test_pronunciation_settings_reject_duplicate_and_unsafe_values():
    with pytest.raises(ValueError):
        VocalSettings(
            pronunciations=[Pronunciation(line_index=1, unit_index=0, pinyin="chong")] * 2
        )
    for value in ("../../bad", "chong2", "重", "chóng", "", "$(anything)"):
        with pytest.raises(ValueError):
            Pronunciation(line_index=0, unit_index=0, pinyin=value)


def test_settings_are_immutable_and_have_optimistic_conflicts(client):
    project = saved(client)
    store = client.app.state.store
    source = store.directory(project["id"]) / "revisions" / f"{project['current_revision']}.json"
    before = source.read_bytes()
    first = config(client, project)
    assert first.status_code == 200
    first_hash = first.json()["hash"]
    assert config(client, project, VocalSettings(vocal_gain=0.6)).status_code == 409
    changed = config(client, project, VocalSettings(vocal_gain=0.6), first_hash)
    assert changed.status_code == 200 and changed.json()["hash"] != first_hash
    root = store.directory(project["id"]) / "vocal-settings"
    assert json.loads((root / f"{first_hash}.json").read_text())["settings"]["vocal_gain"] == 0.8
    assert source.read_bytes() == before
    assert (
        client.post(
            f"/api/projects/{project['id']}/vocals",
            json={"base_revision": project["current_revision"], "settings_hash": first_hash},
        ).status_code
        == 409
    )


def test_mixed_lyrics_allow_settings_save_but_block_singing_before_inference(client, monkeypatch):
    state = fixture()
    lines = state.inputs.lyrics_text.split("\n")
    lines[0] = " ".join(["Why", *lines[0][1:]])
    lines[1] = " ".join(["Yes", *lines[1][1:]])
    state.inputs.lyrics_text = "\n".join(lines)
    state.composition.lyrics_alignment[0].text = "Why"
    state.composition.lyrics_alignment[8].text = "Yes"
    project = saved(client, state)
    store = client.app.state.store
    revision = store.directory(project["id"]) / "revisions" / f"{project['current_revision']}.json"
    original = revision.read_bytes()
    manager = client.app.state.vocals

    def unexpected_resource_check():
        raise AssertionError("Unsupported lyrics must be rejected before model resources/inference.")

    monkeypatch.setattr(manager.resources, "info", unexpected_resource_check)
    response = config(client, project, VocalSettings(vocal_gain=0.6))
    assert response.status_code == 200, response.text
    check = response.json()["score_check"]
    assert check["ready"] is False and check["unsupported_count"] == 2
    assert check["unsupported_units"] == [
        {"line_index": 0, "unit_index": 0, "text": "Why"},
        {"line_index": 1, "unit_index": 0, "text": "Yes"},
    ]
    assert "歌词第1行第1字词“Why”" in check["detail"]
    assert "不能替代英文演唱" in check["detail"]
    loaded = client.get(f"/api/projects/{project['id']}/vocal-settings").json()
    assert loaded["settings"]["vocal_gain"] == 0.6 and loaded["score_check"] == check
    response = client.post(
        f"/api/projects/{project['id']}/vocals",
        json={"base_revision": project["current_revision"], "settings_hash": loaded["hash"]},
    )
    assert response.status_code == 422 and response.json()["detail"] == check["detail"]
    assert client.get(f"/api/projects/{project['id']}/vocals").json()["items"] == []
    assert not (store.directory(project["id"]) / "exports/vocals").exists()
    assert revision.read_bytes() == original
    assert client.get(f"/api/projects/{project['id']}").json() == project


@pytest.mark.parametrize("change", ["noncontiguous", "partial", "missing"])
def test_settings_can_be_saved_while_score_needs_repair(client, change):
    state = fixture()
    if change == "noncontiguous":
        next(n for n in state.score.tracks[0].notes if n.id == "v-0-2-0").duration_tick -= 120
    elif change == "partial":
        state.inputs.selected_lines = [0]
        state.composition.lyrics_alignment = state.composition.lyrics_alignment[:8]
    else:
        state.composition.lyrics_alignment[0].note_ids = ["absent"]
    project = saved(client, state)
    response = config(client, project)
    assert response.status_code == 200, response.text
    assert response.json()["score_check"]["ready"] is False
    assert response.json()["score_check"]["detail"]
    response = client.post(
        f"/api/projects/{project['id']}/vocals",
        json={"base_revision": project["current_revision"], "settings_hash": response.json()["hash"]},
    )
    assert response.status_code == 422
    assert not client.get(f"/api/projects/{project['id']}/vocals").json()["items"]


def test_settings_reject_nonexistent_pronunciation_without_writing_config(client):
    project = saved(client)
    response = config(
        client,
        project,
        VocalSettings(pronunciations=[Pronunciation(line_index=99, unit_index=0, pinyin="chong")]),
    )
    assert response.status_code == 422 and "不存在的字" in response.json()["detail"]
    assert not (client.app.state.store.directory(project["id"]) / "vocal-settings").exists()


def test_missing_resources_do_not_create_fake_success(client, tmp_path):
    project = saved(client)
    manager = client.app.state.vocals
    manager.resources = VocalResource(VocalPaths(tmp_path / "missing"))
    assert not client.get("/api/vocals/resources").json()["ready"]
    settings = config(client, project).json()
    response = client.post(
        f"/api/projects/{project['id']}/vocals",
        json={"base_revision": project["current_revision"], "settings_hash": settings["hash"]},
    )
    assert response.status_code == 503
    assert client.get(f"/api/projects/{project['id']}/vocals").json()["items"] == []


def test_unknown_pinyin_is_rejected_before_inference(client):
    project = saved(client)
    response = config(
        client,
        project,
        VocalSettings(pronunciations=[Pronunciation(line_index=0, unit_index=0, pinyin="abcdef")]),
    )
    assert response.status_code == 422
    assert "歌手词典" in response.json()["detail"]
    assert client.get(f"/api/projects/{project['id']}/vocals").json()["items"] == []


def test_missing_gpu_requires_an_explicit_cpu_choice(client):
    project = saved(client)
    key = config(client, project, VocalSettings(gpu=15)).json()["hash"]
    response = client.post(
        f"/api/projects/{project['id']}/vocals",
        json={"base_revision": project["current_revision"], "settings_hash": key},
    )
    assert response.status_code == 503
    assert "CPU" in response.json()["detail"]
    assert client.get(f"/api/projects/{project['id']}/vocals").json()["items"] == []


def archived_job(client, project, settings_hash):
    """A labelled metadata stub for state checks, never an audio pass."""
    manager = client.app.state.vocals
    item = {
        "id": "a" * 32,
        "project_id": project["id"],
        "revision_id": project["current_revision"],
        "revision_number": project["revision"]["number"],
        "settings_hash": settings_hash,
        "resource": manager.resources.info(),
        "status": "completed",
        "phase": "TEST METADATA ONLY",
        "created_at": "2026-10-10T00:00:00+00:00",
        "files": {},
        "mock": True,
    }
    manager._write(item)
    return item


def test_stale_settings_revision_restart_and_recycle_bin_keep_archives(client, tmp_path):
    project = saved(client)
    first_hash = config(client, project).json()["hash"]
    item = archived_job(client, project, first_hash)
    path = f"/api/projects/{project['id']}/vocals/{item['id']}"
    assert client.get(path).json()["current"]
    changed = config(
        client,
        project,
        VocalSettings(pronunciations=[Pronunciation(line_index=1, unit_index=0, pinyin="chong")]),
        first_hash,
    )
    assert changed.status_code == 200
    assert client.get(path).json()["stale_reason"] == "演唱设置已改变"
    state = deepcopy(project["revision"]["state"])
    state["score"]["tempo_map"][0]["microseconds_per_beat"] = 600000
    response = client.put(
        f"/api/projects/{project['id']}",
        json={"base_revision": project["current_revision"], "label": "修改速度", "state": state},
    )
    assert response.status_code == 200
    current = response.json()
    assert client.get(path).json()["stale_reason"] == "非当前保存版本"
    with TestClient(create_app(tmp_path / "data")) as restarted:
        assert restarted.get(path).json()["status"] == "completed"
    directory = client.app.state.store.directory(project["id"])
    before = {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file()
    }
    response = client.request(
        "DELETE",
        f"/api/projects/{project['id']}",
        json={"base_revision": current["current_revision"]},
    )
    assert response.status_code == 200
    assert client.get(path).status_code == 404
    assert client.post(f"/api/trash/{project['id']}/restore").status_code == 200
    after = {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file()
    }
    assert after == before
    assert client.get(path).json()["status"] == "completed"


def test_restart_marks_incomplete_vocal_jobs_interrupted(client):
    project = saved(client)
    key = config(client, project).json()["hash"]
    item = archived_job(client, project, key)
    item.update(status="running", files={})
    client.app.state.vocals._write(item)
    response = client.get(f"/api/projects/{project['id']}/vocals/{item['id']}").json()
    assert response["status"] == "interrupted" and not response["files"]


def test_active_vocal_task_blocks_delete_and_cancel_releases_after_process_exit(
    client, monkeypatch
):
    project = saved(client)
    manager = client.app.state.vocals
    key = config(client, project).json()["hash"]
    entered, done = Event(), Event()

    def controlled(item, request, backing, previous, event):
        entered.set()
        event.wait(5)
        with manager.lock, manager.store.lock:
            item.update(status="cancelled", phase="unit-test cancellation", files={})
            manager._write(item)
            manager._forget(item["id"])
        done.set()

    monkeypatch.setattr(manager, "_render", controlled)
    response = client.post(
        f"/api/projects/{project['id']}/vocals",
        json={"base_revision": project["current_revision"], "settings_hash": key},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert entered.wait(3)
    assert (
        client.request(
            "DELETE",
            f"/api/projects/{project['id']}",
            json={"base_revision": project["current_revision"]},
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/projects/{project['id']}/vocals/{job['id']}/cancel").status_code == 200
    )
    assert done.wait(3)
    assert not manager.has_active_jobs(project["id"])
    assert (
        client.get(f"/api/projects/{project['id']}/vocals/{job['id']}").json()["status"]
        == "cancelled"
    )
    assert (
        client.request(
            "DELETE",
            f"/api/projects/{project['id']}",
            json={"base_revision": project["current_revision"]},
        ).status_code
        == 200
    )


def test_owned_process_stops_on_context_exit(tmp_path):
    with (tmp_path / "child.log").open("wb") as log:
        with owned_process(
            [sys.executable, "-c", "import time; time.sleep(30)"], log, log
        ) as process:
            assert process.poll() is None
        assert process.poll() is not None


def test_cancel_really_stops_owned_child(client, tmp_path):
    project = saved(client)
    manager = client.app.state.vocals
    key = config(client, project).json()["hash"]
    item = archived_job(client, project, key)
    event = Event()
    import threading

    failure = []

    def call():
        try:
            manager.command(
                item,
                [sys.executable, "-c", "import time; time.sleep(30)"],
                tmp_path,
                "cancel-check",
                event,
                60,
            )
        except Exception as error:
            failure.append(error)

    thread = threading.Thread(target=call)
    thread.start()
    deadline = time.monotonic() + 5
    while item["id"] not in manager.processes and time.monotonic() < deadline:
        time.sleep(0.01)
    process = manager.processes[item["id"]]
    event.set()
    thread.join(5)
    assert not thread.is_alive() and process.poll() is not None
    assert failure and failure[0].__class__.__name__ == "Cancelled"


def test_asset_paths_and_session_guard(client):
    project = saved(client)
    token = client.headers.pop("X-Session-Token")
    assert config(client, project).status_code == 403
    client.headers["X-Session-Token"] = token
    key = config(client, project).json()["hash"]
    item = archived_job(client, project, key)
    assert (
        client.get(
            f"/api/projects/{project['id']}/vocals/{item['id']}/assets/input.json"
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/projects/{project['id']}/vocals/{item['id']}/assets/vocal.wav"
        ).status_code
        == 409
    )
    response = client.post(
        f"/api/projects/{project['id']}/vocals",
        json={"base_revision": "0" * 32, "settings_hash": key},
    )
    assert response.status_code == 409
