import hashlib
import json
import subprocess
import time
import wave
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from threading import Event

import mido
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.render import RenderManager
from backend.app.resources import AudioError, AudioPaths


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "songs")) as result:
        result.headers["X-Session-Token"] = result.get("/api/session").json()["token"]
        yield result


def song(client, family=None):
    project = client.post("/api/projects", json={"name": "真实渲染验收"}).json()
    state = deepcopy(project["revision"]["state"])
    state["score"]["end_tick"] = 1920
    state["score"]["tempo_map"] = [
        {"tick": 0, "microseconds_per_beat": 500000},
        {"tick": 960, "microseconds_per_beat": 750000},
    ]
    for index, (track, pitch) in enumerate(
        zip(state["score"]["tracks"], (60, 36, 55, 40), strict=True)
    ):
        if family is None or track["family"] == family:
            track["notes"] = [
                {
                    "id": f"note-{index}",
                    "pitch": pitch,
                    "velocity": 90,
                    "start_tick": 0,
                    "duration_tick": 1440,
                }
            ]
    return saved(client, project, state)


def saved(client, project, state):
    response = client.put(
        f"/api/projects/{project['id']}",
        json={
            "base_revision": project["current_revision"],
            "state": state,
            "label": "音频测试修改",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def start(client, project, mode="mix"):
    response = client.post(
        f"/api/projects/{project['id']}/renders",
        json={
            "base_revision": project["current_revision"],
            "mode": mode,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def completed(client, project, job):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{project['id']}/renders/{job['id']}")
        assert response.status_code == 200, response.text
        item = response.json()
        if item["status"] == "completed":
            return item
        assert item["status"] != "failed", item
        time.sleep(0.05)
    pytest.fail("真实渲染没有在 40 秒内完成")


def test_local_resource_and_hash_checked_download(client):
    result = client.get("/api/audio/resources").json()
    assert result["ready"] and result["render_ready"], "本机阶段二验收必须具备音色和工具"
    font = result["soundfont"]
    presets = {(item["bank"], item["program"]) for item in font["presets"]}
    assert {(0, 0), (0, 24), (0, 33), (128, 0)} <= presets
    response = client.get(f"/api/audio/soundfont?sha256={font['sha256']}")
    assert response.status_code == 200
    assert response.content[:4] == b"RIFF" and response.content[8:12] == b"sfbk"
    assert hashlib.sha256(response.content).hexdigest() == font["sha256"]
    assert client.get("/api/audio/soundfont?sha256=old-resource").status_code == 409


def test_real_mix_and_full_render_formats_revision_range_and_restart(client):
    project = song(client)
    state = deepcopy(project["revision"]["state"])
    state["score"]["tracks"][1]["solo"] = True
    project = saved(client, project, state)
    mix = completed(client, project, start(client, project))
    full = completed(client, project, start(client, project, "full"))
    assert mix["tracks"][0]["channel"] == 9 and len(mix["tracks"]) == 1
    assert len(full["tracks"]) == 4
    assert mix["timeline_seconds"] == 2.5 and mix["duration_seconds"] == 5.5
    assert mix["wav"]["frames"] == 242550 and mix["wav"]["peak"] > 0.001
    assert mix["wav"]["clipped_samples"] == 0
    assert full["files"]["wav"]["sha256"] != mix["files"]["wav"]["sha256"]
    assert start(client, project)["id"] == mix["id"]  # Reuse exact saved result.
    directory = client.app.state.store.directory(project["id"]) / "exports" / "audio" / mix["id"]
    parsed = mido.MidiFile(directory / "mix.mid")
    assert {
        message.channel for track in parsed.tracks for message in track if message.type == "note_on"
    } == {9}
    url = f"/api/projects/{project['id']}/renders/{mix['id']}/audio"
    response = client.get(url + "/wav")
    assert response.status_code == 200 and response.headers["content-type"].startswith("audio/wav")
    assert hashlib.sha256(response.content).hexdigest() == mix["files"]["wav"]["sha256"]
    with wave.open(BytesIO(response.content)) as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (44100, 2, 2)
    partial = client.get(url + "/wav", headers={"Range": "bytes=0-43"})
    assert partial.status_code == 206 and len(partial.content) == 44
    assert (
        client.get(url + "/mp3?download=true")
        .headers["content-disposition"]
        .startswith("attachment")
    )
    assert client.get(url + "/../render.log").status_code == 404
    assert client.get(url + "/exe").status_code == 404
    other = client.post("/api/projects", json={"name": "另一首"}).json()
    assert client.get(f"/api/projects/{other['id']}/renders/{mix['id']}").status_code == 404
    changed = deepcopy(project["revision"]["state"])
    changed["score"]["tracks"][1]["notes"][0]["pitch"] = 38
    newer = saved(client, project, changed)
    stale = client.post(
        f"/api/projects/{project['id']}/renders",
        json={"base_revision": project["current_revision"]},
    )
    assert stale.status_code == 409
    assert client.get(url + "/mp3").status_code == 200  # Prior version remains usable.
    with TestClient(create_app(client.app.state.store.root)) as restarted:
        items = restarted.get(f"/api/projects/{project['id']}/renders").json()["items"]
        assert len(items) == 2 and all(item["status"] == "completed" for item in items)
        assert (
            restarted.get(f"/api/projects/{project['id']}").json()["current_revision"]
            == newer["current_revision"]
        )


@pytest.mark.parametrize("family", ["keyboard", "drums", "guitar", "bass"])
def test_each_initial_instrument_produces_real_non_silent_audio(client, family):
    project = song(client, family)
    result = completed(client, project, start(client, project))
    assert result["wav"]["rms"] > 0.0001
    assert result["wav"]["peak"] < 0.96 and result["wav"]["clipped_samples"] == 0
    assert result["files"]["mp3"]["bytes"] > 10000
    assert result["revision_id"] == project["current_revision"]


def test_minute_timeline_retains_silence_and_three_second_tail(client):
    project = song(client, "keyboard")
    state = deepcopy(project["revision"]["state"])
    state["score"]["end_tick"] = 57600
    state["score"]["tempo_map"] = [{"tick": 0, "microseconds_per_beat": 500000}]
    project = saved(client, project, state)
    result = completed(client, project, start(client, project))
    assert result["duration_seconds"] == 63
    assert result["wav"]["frames"] == 2778300


def test_failed_rerender_preserves_prior_audio_and_score(client, monkeypatch):
    project = song(client, "keyboard")
    good = completed(client, project, start(client, project))
    old_path, _ = client.app.state.renderer.audio_file(project["id"], good["id"], "wav")
    checksum = hashlib.sha256(old_path.read_bytes()).hexdigest()
    state = deepcopy(project["revision"]["state"])
    state["score"]["tracks"][0]["notes"][0]["pitch"] = 62
    newer = saved(client, project, state)

    def fail(*args):
        raise AudioError("测试：编码失败，原文件保留。", 500)

    monkeypatch.setattr(client.app.state.renderer, "command", fail)
    job = start(client, newer)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(f"/api/projects/{project['id']}/renders/{job['id']}").json()
        if result["status"] == "failed":
            break
        time.sleep(0.02)
    assert result["status"] == "failed" and not result["files"]
    assert hashlib.sha256(old_path.read_bytes()).hexdigest() == checksum
    assert (
        client.get(f"/api/projects/{project['id']}").json()["current_revision"]
        == newer["current_revision"]
    )
    assert (
        client.get(f"/api/projects/{project['id']}/renders/{job['id']}/audio/mp3").status_code
        == 409
    )


def test_empty_silent_or_unavailable_preset_rejected_without_jobs(client):
    blank = client.post("/api/projects", json={"name": "空白"}).json()
    url = f"/api/projects/{blank['id']}/renders"
    assert client.post(url, json={"base_revision": blank["current_revision"]}).status_code == 422
    project = song(client, "keyboard")
    state = deepcopy(project["revision"]["state"])
    state["score"]["tracks"][0]["mute"] = True
    project = saved(client, project, state)
    url = f"/api/projects/{project['id']}/renders"
    assert client.post(url, json={"base_revision": project["current_revision"]}).status_code == 422
    state["score"]["tracks"][0].update(mute=False, bank_lsb=1)
    project = saved(client, project, state)
    assert client.post(url, json={"base_revision": project["current_revision"]}).status_code == 422
    state["score"]["tracks"][0].update(bank_lsb=0, bank_msb=127)
    project = saved(client, project, state)
    assert client.post(url, json={"base_revision": project["current_revision"]}).status_code == 422
    assert client.get(url).json()["items"] == []


def test_missing_and_corrupt_font_is_explicit(tmp_path):
    paths = replace(AudioPaths(), soundfont=tmp_path / "absent.sf2")
    with TestClient(create_app(tmp_path / "songs", paths)) as client:
        assert not client.get("/api/audio/resources").json()["ready"]
        assert client.get("/api/audio/soundfont?sha256=x").status_code == 503
        paths.soundfont.write_bytes(b"not-a-soundfont")
        assert "SF2" in client.get("/api/audio/resources").json()["error"]


def test_tool_timeout_is_converted_and_not_published(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 120)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(AudioError, match="超时"):
        RenderManager.command(["owned-local-tool"], [])


def test_stage_one_recording_without_bank_metadata_opens_unchanged(client):
    project = client.post("/api/projects", json={"name": "第一阶段旧稿"}).json()
    revision_path = (
        client.app.state.store.directory(project["id"])
        / "revisions"
        / f"{project['current_revision']}.json"
    )
    legacy = json.loads(revision_path.read_text(encoding="utf-8"))
    legacy["state"]["inputs"]["recording"] = [
        {
            "pitch": 36,
            "velocity": 90,
            "start_seconds": 0.1234567,
            "duration_seconds": 0.3456789,
            "track_id": legacy["state"]["score"]["tracks"][1]["id"],
            "family": "drums",
            "program": 0,
        }
    ]
    revision_path.write_text(json.dumps(legacy), encoding="utf-8")
    original_bytes = revision_path.read_bytes()
    response = client.get(f"/api/projects/{project['id']}")
    assert response.status_code == 200
    assert (
        response.json()["revision"]["state"]["inputs"]["recording"]
        == legacy["state"]["inputs"]["recording"]
    )
    assert revision_path.read_bytes() == original_bytes
    state = response.json()["revision"]["state"]
    state["inputs"]["recording"][0]["channel"] = 0
    invalid = client.put(
        f"/api/projects/{project['id']}",
        json={"base_revision": project["current_revision"], "state": state},
    )
    assert invalid.status_code == 422 and revision_path.read_bytes() == original_bytes


def test_abandoned_job_after_restart_is_marked_failed(client):
    project = song(client)
    job_id = "b" * 32
    directory = client.app.state.renderer.directory(project["id"], job_id)
    client.app.state.store.write_json(
        directory / "render.json",
        {
            "id": job_id,
            "project_id": project["id"],
            "revision_id": project["current_revision"],
            "status": "rendering",
            "created_at": "2026-10-05",
            "files": {},
        },
    )
    result = client.get(f"/api/projects/{project['id']}/renders/{job_id}").json()
    assert result["status"] == "failed" and "中断" in result["error"]
    assert json.loads((directory / "render.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_bounded_queue_keeps_project_api_usable(client, monkeypatch):
    manager = client.app.state.renderer
    release = Event()

    def blocked(item, score):
        release.wait(10)
        with manager.lock:
            manager.active.pop(item["id"], None)

    monkeypatch.setattr(manager, "_render", blocked)
    try:
        project = song(client)
        for index in range(4):
            start(client, project)
            state = deepcopy(project["revision"]["state"])
            state["inputs"]["theme"] = f"队列中的修改 {index}"
            project = saved(client, project, state)
        response = client.post(
            f"/api/projects/{project['id']}/renders",
            json={"base_revision": project["current_revision"]},
        )
        assert response.status_code == 429
        assert client.get(f"/api/projects/{project['id']}").status_code == 200
    finally:
        release.set()
