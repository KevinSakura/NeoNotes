"""Isolated deletion/restore checks; no real songs or model requests."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.storage import StorageError


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "songs")) as result:
        result.headers["X-Session-Token"] = result.get("/api/session").json()["token"]
        yield result


def create(client, name="同名作品"):
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()


def save_notes(client, project):
    state = deepcopy(project["revision"]["state"])
    state["inputs"]["theme"] = "删除与恢复检查"
    state["score"]["tracks"][0]["notes"] = [
        {"id": "note-a", "pitch": 60, "velocity": 90, "start_tick": 0, "duration_tick": 480}
    ]
    response = client.put(
        f"/api/projects/{project['id']}",
        json={"base_revision": project["current_revision"], "state": state, "label": "保存音符"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def delete(client, project):
    return client.request(
        "DELETE",
        f"/api/projects/{project['id']}",
        json={"base_revision": project["current_revision"]},
    )


def fingerprints(directory):
    return {
        file.relative_to(directory).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in directory.rglob("*")
        if file.is_file()
    }


def test_complete_song_move_restart_restore_preserves_bytes_and_other_song(client, tmp_path):
    first, other = create(client), create(client)
    first = client.post(
        f"/api/projects/{first['id']}/lyrics",
        data={"base_revision": first["current_revision"]},
        files={"file": ("歌词.txt", "第一行\n第二行".encode())},
    ).json()
    first = save_notes(client, first)
    midi = client.post(
        f"/api/projects/{first['id']}/midi-export",
        json={"base_revision": first["current_revision"]},
    )
    assert midi.status_code == 200 and midi.content[:4] == b"MThd"
    store = client.app.state.store
    directory = store.directory(first["id"])
    for relative, content in {
        "reviews/example.json": b'{"approved":false}',
        "jobs/composition/example/candidate.json": b'{"test":"opaque archived material"}',
        "exports/audio/example/mix.mp3": b"audio archive test bytes",
        "exports/audio/example/mix.wav": b"wav archive test bytes",
    }.items():
        store.write_bytes(directory / relative, content)
    original, other_files = fingerprints(directory), fingerprints(store.directory(other["id"]))
    history = client.get(f"/api/projects/{first['id']}/history").json()
    response = delete(client, first)
    assert response.status_code == 200, response.text
    assert response.json()["directory"] == f"data/.trash/{first['id']}"
    assert not directory.exists()
    assert fingerprints(store.trash_directory(first["id"])) == original
    assert [item["id"] for item in client.get("/api/projects").json()["items"]] == [other["id"]]
    assert client.get(f"/api/projects/{first['id']}").status_code == 404

    # Deleted-song writes must fail instead of recreating a directory.
    assert client.post(
        f"/api/projects/{first['id']}/undo",
        json={"base_revision": first["current_revision"]},
    ).status_code == 404
    assert client.post(
        f"/api/projects/{first['id']}/midi-export",
        json={"base_revision": first["current_revision"]},
    ).status_code == 404
    assert delete(client, first).status_code == 404
    assert not directory.exists()
    assert fingerprints(store.directory(other["id"])) == other_files

    with TestClient(create_app(tmp_path / "songs")) as restarted:
        restarted.headers["X-Session-Token"] = restarted.get("/api/session").json()["token"]
        assert [item["id"] for item in restarted.get("/api/trash").json()["items"]] == [
            first["id"]
        ]
        restored = restarted.post(f"/api/trash/{first['id']}/restore")
        assert restored.status_code == 200, restored.text
        assert restored.json() == first
        assert restarted.get(f"/api/projects/{first['id']}/history").json() == history
        assert restarted.get("/api/trash").json() == {"items": [], "warnings": []}
        assert restarted.post(f"/api/trash/{first['id']}/restore").status_code == 404
    assert fingerprints(directory) == original
    assert fingerprints(store.directory(other["id"])) == other_files


def test_stale_confirmation_cannot_delete_newer_revision(client):
    old = create(client)
    current = save_notes(client, old)
    assert delete(client, old).status_code == 409
    assert client.get(f"/api/projects/{old['id']}").json() == current
    assert client.get("/api/trash").json()["items"] == []
    assert delete(client, current).status_code == 200


def test_delete_and_restore_require_local_session(client):
    project = create(client)
    token = client.headers.pop("X-Session-Token")
    assert delete(client, project).status_code == 403
    assert client.get(f"/api/projects/{project['id']}").status_code == 200
    client.headers["X-Session-Token"] = token
    assert delete(client, project).status_code == 200
    client.headers.pop("X-Session-Token")
    assert client.post(f"/api/trash/{project['id']}/restore").status_code == 403
    assert len(client.get("/api/trash").json()["items"]) == 1


@pytest.mark.parametrize("project_id", ["../outside", "..", "ABC", "g" * 32, "a" * 31])
def test_invalid_ids_do_not_touch_songs_or_parent(client, tmp_path, project_id):
    project = create(client)
    marker = tmp_path / "outside.txt"
    marker.write_bytes(b"outside remains")
    store = client.app.state.store
    with pytest.raises(StorageError):
        store.delete(project_id, project["current_revision"])
    with pytest.raises(StorageError):
        store.restore_deleted(project_id)
    assert marker.read_bytes() == b"outside remains"
    assert client.get(f"/api/projects/{project['id']}").status_code == 200


@pytest.mark.parametrize("action", ["delete", "restore"])
def test_directory_move_failure_keeps_original_location(client, monkeypatch, action):
    project = create(client)
    store = client.app.state.store
    if action == "restore":
        assert delete(client, project).status_code == 200
    directory = (
        store.directory(project["id"])
        if action == "delete"
        else store.trash_directory(project["id"])
    )
    original = fingerprints(directory)

    def fail_move(*args):
        raise OSError("isolated permission failure")

    monkeypatch.setattr(os, "rename", fail_move)
    response = (
        delete(client, project)
        if action == "delete"
        else client.post(f"/api/trash/{project['id']}/restore")
    )
    assert response.status_code == 500
    assert fingerprints(directory) == original
    other_location = (
        store.trash_directory(project["id"])
        if action == "delete"
        else store.directory(project["id"])
    )
    assert not other_location.exists()


@pytest.mark.parametrize("action", ["delete", "restore"])
def test_destination_conflict_never_overwrites_existing_directory(client, action):
    project = create(client)
    store = client.app.state.store
    if action == "restore":
        assert delete(client, project).status_code == 200
    destination = (
        store.trash_directory(project["id"])
        if action == "delete"
        else store.directory(project["id"])
    )
    destination.mkdir(parents=True)
    sentinel = destination / "keep.txt"
    sentinel.write_bytes(b"do not overwrite")
    source = (
        store.directory(project["id"])
        if action == "delete"
        else store.trash_directory(project["id"])
    )
    original = fingerprints(source)
    response = (
        delete(client, project)
        if action == "delete"
        else client.post(f"/api/trash/{project['id']}/restore")
    )
    assert response.status_code == 409
    assert sentinel.read_bytes() == b"do not overwrite"
    assert fingerprints(source) == original


def test_linked_trash_path_rejected_before_move(client, tmp_path, monkeypatch):
    project = create(client)
    store = client.app.state.store
    outside = tmp_path / "outside"
    outside.mkdir()
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        # Emulate a Windows junction without requiring symlink privileges.
        if path == store.root / ".trash":
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    response = delete(client, project)
    assert response.status_code == 400
    assert client.get(f"/api/projects/{project['id']}").status_code == 200
    assert list(outside.iterdir()) == []


def test_active_generation_blocks_only_its_song_and_completed_task_allows_delete(
    client, monkeypatch
):
    project, other = save_notes(client, create(client)), create(client)
    manager = client.app.state.generator
    started, release = Event(), Event()

    def held_worker(*args):
        started.set()
        assert release.wait(10), "isolated worker was not released"

    monkeypatch.setattr(manager, "_run", held_worker)
    try:
        response = client.post(
            f"/api/projects/{project['id']}/generations",
            json={"base_revision": project["current_revision"], "operation": "generate"},
        )
        assert response.status_code == 202, response.text
        job = response.json()
        assert started.wait(3)
        assert delete(client, project).status_code == 409
        assert delete(client, other).status_code == 200
        assert client.get(f"/api/projects/{project['id']}").status_code == 200
    finally:
        release.set()
        for future in manager.futures.values():
            future.result(timeout=5)
    # A queued record from an interrupted process is not a live model request.
    original_job = (manager.directory(project["id"], job["id"]) / "job.json").read_bytes()
    assert delete(client, project).status_code == 200
    assert (
        client.app.state.store.trash_directory(project["id"])
        / "jobs" / "composition" / job["id"] / "job.json"
    ).read_bytes() == original_job


def test_active_render_blocks_deletion_until_last_file_is_written(client, monkeypatch):
    project = save_notes(client, create(client))
    manager = client.app.state.renderer
    started, release = Event(), Event()

    def held_render(item, score):
        started.set()
        try:
            assert release.wait(10), "isolated render was not released"
            with manager.lock:
                manager.store.write_bytes(
                    manager.directory(project["id"], item["id"]) / "last-file.txt", b"finished"
                )
        finally:
            with manager.lock:
                manager.active.pop(item["id"], None)
                manager.job_projects.pop(item["id"], None)

    monkeypatch.setattr(manager, "_render", held_render)
    try:
        response = client.post(
            f"/api/projects/{project['id']}/renders",
            json={"base_revision": project["current_revision"], "mode": "mix"},
        )
        assert response.status_code == 202, response.text
        job = response.json()
        assert started.wait(3)
        future = manager.active[job["id"]]
        assert delete(client, project).status_code == 409
    finally:
        release.set()
        if "future" in locals():
            future.result(timeout=5)
    assert delete(client, project).status_code == 200
    assert (
        manager.store.trash_directory(project["id"])
        / "exports" / "audio" / job["id"] / "last-file.txt"
    ).read_bytes() == b"finished"
