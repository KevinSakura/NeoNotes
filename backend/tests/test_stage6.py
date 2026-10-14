"""Isolated full-song checks; no real credentials, network or quality approval."""

import json
from copy import deepcopy
from io import BytesIO

import httpx
import mido
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.composition import compile_plan, input_context, mock_plan
from backend.app.deepseek import DeepSeekAdapter
from backend.app.full_song import full_context, full_inputs
from backend.app.main import create_app
from backend.app.model_settings import ModelSettings
from backend.app.models import SongState
from backend.tests.test_generation import (
    FAKE_KEY,
    project_with_input,
    real_settings,
    response_for,
    state_with_input,
    submit,
    submit_legacy,
    wait_job,
)


def test_output_limit_accepts_32768_and_rejects_larger_settings():
    assert ModelSettings().max_tokens == 32768
    assert ModelSettings(max_tokens=32768).public()["full_song_max_tokens"] == 32768
    with pytest.raises(ValidationError):
        ModelSettings(max_tokens=32769)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "songs", model_settings=ModelSettings())) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        yield client


def adopt(client, project, job):
    result = client.post(
        f"/api/projects/{project['id']}/generations/{job['id']}/apply",
        json={"base_revision": project["current_revision"]},
    )
    assert result.status_code == 200, result.text
    return result.json()


def approved_short(client, source=None):
    project = project_with_input(client, source)
    job = (
        submit_legacy(client, project)
        if source and source.inputs.selected_lines
        else submit(client, project)
    )
    assert wait_job(client, project["id"], job["id"])["status"] == "completed"
    project = adopt(client, project, job)
    result = client.post(
        f"/api/projects/{project['id']}/review",
        json={
            "base_revision": project["current_revision"],
            "approved": True,
            "melody": True,
            "arrangement": True,
            "style": True,
            "lyrics_sung": True,
            "comment": "隔离模拟门槛检查，不代表音乐效果验收。",
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["full_song_enabled"]
    return project, result.json()


def test_full_is_explicit_and_requires_current_short_review(client):
    project = project_with_input(client)
    url = f"/api/projects/{project['id']}/generations"
    response = client.post(
        url, json={"base_revision": project["current_revision"], "operation": "full"}
    )
    assert response.status_code == 409
    assert client.get(url).json()["items"] == []
    project, _ = approved_short(client)
    url = f"/api/projects/{project['id']}/generations"
    assert len(client.get(url).json()["items"]) == 1  # Review never starts a task.
    state = deepcopy(project["revision"]["state"])
    state["score"]["tracks"][1]["volume"] = 55
    saved = client.put(
        f"/api/projects/{project['id']}",
        json={
            "base_revision": project["current_revision"],
            "state": state,
        },
    ).json()
    assert (
        client.post(
            url, json={"base_revision": saved["current_revision"], "operation": "full"}
        ).status_code
        == 409
    )
    assert len(client.get(url).json()["items"]) == 1


def test_instrumental_full_195_seconds_real_midi_new_version_undo_and_review(client):
    project, status = approved_short(client)
    base = deepcopy(project)
    job = submit(client, project, operation="full")
    item = wait_job(client, project["id"], job["id"])
    assert item["status"] == "completed", item
    assert item["request_count"] == 0 and item["max_output_tokens"] == 32768
    assert item["result_seconds"] == 195
    assert client.get(f"/api/projects/{project['id']}").json() == base
    state = SongState.model_validate(
        client.get(f"/api/projects/{project['id']}/generations/{job['id']}/candidate").json()
    )
    assert state.composition.scope == "full"
    assert state.composition.full_source.review_id == status["review"]["id"]
    assert state.score.seconds_at(state.score.end_tick) == pytest.approx(195, abs=0.001)
    midi = mido.MidiFile(
        file=BytesIO(
            client.get(f"/api/projects/{project['id']}/generations/{job['id']}/midi").content
        )
    )
    assert len(midi.tracks) == 5 and midi.length == pytest.approx(195, abs=0.001)
    assert any(
        message.type == "note_on" and message.channel == 9
        for track in midi.tracks
        for message in track
    )
    project = adopt(client, project, job)
    report = client.get(f"/api/projects/{project['id']}/review").json()
    assert report["valid"] and report["review"] is None and not report["full_song_enabled"]
    undone = client.post(
        f"/api/projects/{project['id']}/undo", json={"base_revision": project["current_revision"]}
    ).json()
    assert undone["current_revision"] == base["current_revision"]
    assert client.get(f"/api/projects/{project['id']}/review").json()["full_song_enabled"]


def test_full_uses_whole_lyrics_original_line_order_and_new_duration(client):
    source = state_with_input(lyrics=True)
    source.inputs.lyrics_text = "迎着微风出发\n昨日轻轻放下\n脚步迎向朝阳\n让每一段旅程都有回答"
    source.inputs.selected_lines = [1, 2]
    project, _ = approved_short(client, source)
    job = submit(client, project, operation="full")
    item = wait_job(client, project["id"], job["id"])
    assert item["status"] == "completed", item
    assert item["target_seconds"] is None and item["duration_policy"] == "lyrics"
    state = SongState.model_validate(
        client.get(f"/api/projects/{project['id']}/generations/{job['id']}/candidate").json()
    )
    assert state.inputs.selected_lines == [0, 1, 2, 3]
    assert state.inputs.lyrics_text == source.inputs.lyrics_text
    assert state.composition.target_seconds != 195 and state.composition.target_seconds > 0
    assert sorted({unit.line_index for unit in state.composition.lyrics_alignment}) == [0, 1, 2, 3]
    project = adopt(client, project, job)
    report = client.get(f"/api/projects/{project['id']}/review").json()
    assert report["valid"], report
    assert len(report["assessment"]["lyric_lines"]) == 4


@pytest.mark.parametrize("kind", ["ending", "lyrics", "duration", "motif"])
def test_incomplete_full_plan_is_rejected_without_synthetic_repair(kind):
    initial = state_with_input(lyrics=True)
    short, _ = compile_plan(mock_plan(initial, input_context(initial)), initial, "a" * 32, True)
    source, context = full_context(short, "b" * 32, "c" * 32)
    raw = mock_plan(source, context)
    if kind == "ending":
        for track in raw["tracks"]:
            track["notes"] = [note for note in track["notes"] if note[0] + note[2] <= raw["bpm"]]
    elif kind == "lyrics":
        raw["lyrics_alignment"].pop()
    elif kind == "duration":
        context["target_seconds"] = 195
    else:
        for track in raw["tracks"]:
            if track["family"] == "keyboard":
                for note in track["notes"]:
                    note[1] = 72
    from backend.app.model_settings import ModelError

    with pytest.raises(ModelError):
        compile_plan(raw, source, "d" * 32, True, "full", context)


def test_review_retraction_blocks_full_candidate_adoption(client):
    project, _ = approved_short(client)
    job = submit(client, project, operation="full")
    assert wait_job(client, project["id"], job["id"])["status"] == "completed"
    client.post(
        f"/api/projects/{project['id']}/review",
        json={
            "base_revision": project["current_revision"],
            "approved": False,
        },
    )
    response = client.post(
        f"/api/projects/{project['id']}/generations/{job['id']}/apply",
        json={
            "base_revision": project["current_revision"],
        },
    )
    assert response.status_code == 409
    assert (
        client.get(f"/api/projects/{project['id']}").json()["current_revision"]
        == project["current_revision"]
    )


def test_whole_full_modification_preserves_duration_source_and_mixer(client):
    project, _ = approved_short(client, state_with_input(lyrics=True, melody=True))
    job = submit(client, project, operation="full")
    assert wait_job(client, project["id"], job["id"])["status"] == "completed"
    project = adopt(client, project, job)
    state = deepcopy(project["revision"]["state"])
    state["score"]["tracks"][1]["mute"] = True
    state["score"]["tracks"][0]["solo"] = True
    project = client.put(
        f"/api/projects/{project['id']}",
        json={
            "base_revision": project["current_revision"],
            "state": state,
        },
    ).json()
    job = submit(client, project, operation="modify", instruction="整体更轻快，保持原歌词")
    item = wait_job(client, project["id"], job["id"])
    assert item["status"] == "completed", item
    candidate = client.get(
        f"/api/projects/{project['id']}/generations/{job['id']}/candidate"
    ).json()
    assert candidate["composition"]["arrangement"] == "lyric-motifs-v1"
    assert candidate["composition"]["target_seconds"] > 0  # New lyric phrases may change length.
    assert candidate["composition"]["full_source"] == state["composition"]["full_source"]
    assert candidate["score"]["tracks"][1]["mute"] and candidate["score"]["tracks"][0]["solo"]
    project = adopt(client, project, job)
    report = client.get(f"/api/projects/{project['id']}/review").json()
    assert report["valid"], report


@pytest.mark.parametrize("truncated", [False, True])
def test_fake_http_full_uses_32768_with_legacy_config_and_never_retries(tmp_path, truncated):
    calls = []
    initial = state_with_input()
    holder = {}

    def handler(request):
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][1]["content"])
        calls.append({"operation": context["operation"], "max_tokens": payload["max_tokens"]})
        if context["operation"] == "full":
            source = SongState.model_validate(holder["project"]["revision"]["state"])
            raw = mock_plan(full_inputs(source), context)
            envelope = response_for(raw)
            if truncated:
                envelope["choices"][0]["finish_reason"] = "length"
                envelope["choices"][0]["message"]["content"] = '{"title":'
        else:
            envelope = response_for(mock_plan(initial, context))
        return httpx.Response(200, json=envelope)

    def factory(settings):
        return DeepSeekAdapter(settings, httpx.MockTransport(handler))

    with TestClient(
        create_app(
            tmp_path / "songs",
            model_settings=real_settings(max_tokens=12000),
            adapter_factory=factory,
        )
    ) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client, initial)
        job = submit(client, project, confirm_real_call=True)
        assert wait_job(client, project["id"], job["id"])["status"] == "completed"
        project = adopt(client, project, job)
        status = client.post(
            f"/api/projects/{project['id']}/review",
            json={
                "base_revision": project["current_revision"],
                "approved": True,
                "melody": True,
                "arrangement": True,
                "style": True,
                "comment": "假HTTP隔离测试；并非真实模型或音乐验收。",
            },
        ).json()
        assert status["full_song_enabled"]
        holder["project"] = deepcopy(project)
        assert (
            client.post(
                f"/api/projects/{project['id']}/generations",
                json={
                    "base_revision": project["current_revision"],
                    "operation": "full",
                },
            ).status_code
            == 403
        )
        assert len(calls) == 1
        job = submit(client, project, operation="full", confirm_real_call=True)
        item = wait_job(client, project["id"], job["id"])
        assert item["status"] == ("failed" if truncated else "completed"), item
        assert calls == [
            {"operation": "generate", "max_tokens": 12000},
            {"operation": "full", "max_tokens": 32768},
        ]
        assert item["request_count"] == 1 and item["metadata"]["usage"]["total_tokens"] == 1200
        assert FAKE_KEY not in json.dumps(item)
        assert client.get(f"/api/projects/{project['id']}").json() == holder["project"]
        if truncated:
            assert item["error_code"] == "incomplete"
            assert (
                client.get(
                    f"/api/projects/{project['id']}/generations/{job['id']}/candidate"
                ).status_code
                == 409
            )


def test_ten_minute_lyric_plan_tolerates_integer_midi_tempo_rounding():
    initial = state_with_input(lyrics=True)
    short, _ = compile_plan(mock_plan(initial, input_context(initial)), initial, "a" * 32, True)
    source, context = full_context(short, "b" * 32, "c" * 32)
    context["target_seconds"] = 600
    raw = mock_plan(source, context)
    ratio = 108 / raw["bpm"]
    raw["bpm"] = 108  # MIDI integer tempo rounds upward, giving 600.00048 seconds.
    for track in raw["tracks"]:
        for note in track["notes"]:
            note[0] *= ratio
            note[2] *= ratio
    for segments in (raw["sections"], raw["chords"]):
        for segment in segments:
            segment["start"] *= ratio
            segment["end"] *= ratio
    full, _ = compile_plan(raw, source, "d" * 32, True, "full", context)
    assert full.score.seconds_at(full.score.end_tick) == pytest.approx(600, abs=0.001)
    from backend.app.midi import export_midi, import_midi

    imported = import_midi(export_midi(full.score))
    assert imported.seconds_at(imported.end_tick) == pytest.approx(600, abs=0.001)


def test_mock_review_never_enables_real_full_dispatch(client):
    project, _ = approved_short(client)
    generator = client.app.state.generator
    generator.settings = real_settings()

    def forbidden(settings):
        pytest.fail("Mock source must stop before any adapter is initialized.")

    generator.adapter_factory = forbidden
    response = client.post(
        f"/api/projects/{project['id']}/generations",
        json={
            "base_revision": project["current_revision"],
            "operation": "full",
            "confirm_real_call": True,
        },
    )
    assert response.status_code == 409 and "模拟短曲" in response.text
    assert len(client.get(f"/api/projects/{project['id']}/generations").json()["items"]) == 1


def test_full_origin_forgery_cannot_pass_review_or_lyric_repair(client):
    project, _ = approved_short(client, state_with_input(lyrics=True))
    job = submit(client, project, operation="full")
    assert wait_job(client, project["id"], job["id"])["status"] == "completed"
    project = adopt(client, project, job)
    state = deepcopy(project["revision"]["state"])
    state["composition"]["full_source"]["review_id"] = "f" * 32
    saved = client.put(
        f"/api/projects/{project['id']}",
        json={
            "base_revision": project["current_revision"],
            "state": state,
        },
    ).json()
    report = client.get(f"/api/projects/{project['id']}/review").json()
    assert not report["valid"] and not report["full_song_enabled"]
    target = state["composition"]["lyrics_alignment"][0]
    response = client.post(
        f"/api/projects/{project['id']}/lyrics-repair",
        json={
            "base_revision": saved["current_revision"],
            "state": state,
            "line_index": target["line_index"],
            "unit_index": target["unit_index"],
            "note_ids": target["note_ids"],
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("kind", ["history_mismatch", "malformed_id"])
def test_damaged_review_cannot_start_full_job(client, kind):
    project, status = approved_short(client)
    store = client.app.state.store
    directory = store.directory(project["id"]) / "reviews"
    damaged = deepcopy(status["review"])
    if kind == "history_mismatch":
        damaged["approved"] = False
        path = directory / "history" / (damaged["id"] + ".json")
    else:
        damaged["id"] = None
        path = directory / (project["current_revision"] + ".json")
    store.write_json(path, damaged)
    report = client.get(f"/api/projects/{project['id']}/review")
    assert report.status_code == 200 and not report.json()["full_song_enabled"]
    response = client.post(
        f"/api/projects/{project['id']}/generations",
        json={
            "base_revision": project["current_revision"],
            "operation": "full",
        },
    )
    assert response.status_code == 409
    assert len(client.get(f"/api/projects/{project['id']}/generations").json()["items"]) == 1


@pytest.mark.parametrize("kind", ["missing_melody", "wrong_family", "oversize_txt"])
def test_invalid_full_reference_and_large_txt_return_clear_preflight(client, kind):
    source = state_with_input(lyrics=kind == "oversize_txt")
    if kind == "oversize_txt":
        source.inputs.lyrics_text = "词\n" * 11000
        source.inputs.selected_lines = [0, 1]
    project = project_with_input(client, source)
    job = submit_legacy(client, project) if kind == "oversize_txt" else submit(client, project)
    assert wait_job(client, project["id"], job["id"])["status"] == "completed"
    project = adopt(client, project, job)
    if kind != "oversize_txt":
        state = deepcopy(project["revision"]["state"])
        state["composition"]["melody_track_id"] = (
            "missing" if kind == "missing_melody" else state["score"]["tracks"][1]["id"]
        )
        response = client.put(
            f"/api/projects/{project['id']}",
            json={"base_revision": project["current_revision"], "state": state},
        )
        assert response.status_code == 200, response.text
        project = response.json()
    report = client.get(f"/api/projects/{project['id']}/review")
    assert report.status_code == 200, report.text
    assert not report.json()["full_song_enabled"]
    assert ("64个非空行" if kind == "oversize_txt" else "主旋律参考") in report.json()[
        "full_song_reason"
    ]
    response = client.post(
        f"/api/projects/{project['id']}/generations",
        json={"base_revision": project["current_revision"], "operation": "full"},
    )
    assert response.status_code == 409
    assert len(client.get(f"/api/projects/{project['id']}/generations").json()["items"]) == 1
