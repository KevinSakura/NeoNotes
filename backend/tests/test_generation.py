"""Offline contract tests. All credentials and HTTP responses here are artificial."""

import json
import time
from copy import deepcopy
from io import BytesIO
from threading import Event

import httpx
import mido
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app.composition import compile_plan, input_context, lyric_units, mock_plan
from backend.app.deepseek import DeepSeekAdapter, strict_json
from backend.app.main import create_app
from backend.app.model_settings import ModelError, ModelSettings, load_model_settings
from backend.app.models import Inputs, RecordedNote, SongState, empty_song, new_id
from backend.app.storage import ProjectStore, now

FAKE_KEY = "not-a-real-key-for-isolated-tests"


def state_with_input(lyrics=False, melody=False):
    state = empty_song()
    state.inputs.theme = "告别昨日，走向新生活"
    state.inputs.emotion = "明亮、积极，保留一点不舍"
    if lyrics:
        state.inputs.lyrics_text = "昨日轻轻放下\n脚步迎向朝阳"
        state.inputs.selected_lines = [0, 1]
    if melody:
        state.inputs.melody_mode = "preserve"
        state.inputs.recording = [
            RecordedNote(
                pitch=pitch,
                start_seconds=index * 0.5,
                duration_seconds=0.35,
                track_id=state.score.tracks[0].id,
                family="keyboard",
            )
            for index, pitch in enumerate((60, 62, 64))
        ]
    return state


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "songs", model_settings=ModelSettings())) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        yield client


def project_with_input(client, state=None):
    project = client.post("/api/projects", json={"name": "阶段三隔离测试"}).json()
    state = state or state_with_input()
    return client.put(
        f"/api/projects/{project['id']}",
        json={
            "base_revision": project["current_revision"],
            "state": state.model_dump(),
            "label": "测试输入",
        },
    ).json()


def submit(client, project, **fields):
    response = client.post(
        f"/api/projects/{project['id']}/generations",
        json={"base_revision": project["current_revision"], **fields},
    )
    assert response.status_code == 202, response.text
    return response.json()


def submit_legacy(client, project):
    """Seed a versioned legacy job to test old workers/files, never the new entry point."""
    manager = client.app.state.generator
    settings = manager.configuration()
    assert settings.mock or settings.api_key.get_secret_value() == FAKE_KEY
    source = SongState.model_validate(project["revision"]["state"])
    context = input_context(source)
    job_id = new_id()
    item = {
        "id": job_id,
        "project_id": project["id"],
        "base_revision": project["current_revision"],
        "base_number": project["revision"]["number"],
        "operation": "generate",
        "instruction": "",
        "scope": "short",
        "target_seconds": 60,
        "duration_policy": "fixed",
        "full_source": None,
        "mock": settings.mock,
        "status": "queued",
        "phase": "legacy fixture",
        "created_at": now(),
        "updated_at": now(),
        "request_count": 0,
        "metadata": None,
        "checks": [],
        "error": None,
        "error_code": None,
        "max_output_tokens": settings.max_tokens,
    }
    directory = manager.directory(project["id"], job_id)
    manager.store.write_json(directory / "input.json", context)
    manager.store.write_json(directory / "job.json", item)
    manager._run(project["id"], job_id, source, context, settings)
    return manager.read(project["id"], job_id)


def wait_job(client, project_id, job_id):
    for _ in range(200):
        result = client.get(f"/api/projects/{project_id}/generations/{job_id}").json()
        if result["status"] in ("completed", "failed"):
            return result
        time.sleep(0.01)
    raise AssertionError("isolated job did not finish")


def real_settings(**values):
    return ModelSettings(mock=False, api_key=SecretStr(FAKE_KEY), **values)


def response_for(raw, **changes):
    result = {
        "id": "isolated-response-1",
        "model": "deepseek-flash",
        "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(raw, ensure_ascii=False)}}
        ],
        "usage": {"prompt_tokens": 300, "completion_tokens": 900, "total_tokens": 1200},
    }
    result.update(changes)
    return result


def test_isolated_env_loader_and_secret_allowlist(tmp_path):
    path = tmp_path / "fake-config.env"
    path.write_text(
        f"MODEL_MOCK=false\nMODEL_API_KEY={FAKE_KEY}\nMODEL_MAX_TOKENS=8000\nMODEL_MAX_RETRIES=0\nSOUNDFONT_PATH=unused\n",
        encoding="utf-8",
    )
    settings = load_model_settings(path)
    assert settings.api_key.get_secret_value() == FAKE_KEY
    assert settings.max_tokens == 8000
    assert FAKE_KEY not in repr(settings) + json.dumps(settings.public())
    path.write_text(f"MODEL_MAX_TOKENS={FAKE_KEY}\n", encoding="utf-8")
    with pytest.raises(ModelError) as caught:
        load_model_settings(path)
    assert FAKE_KEY not in str(caught.value)


def test_mock_candidate_does_not_change_song_and_real_midi(client):
    project = project_with_input(client, state_with_input(lyrics=True))
    job = submit(client, project)
    item = wait_job(client, project["id"], job["id"])
    assert item["status"] == "completed", item
    assert item["mock"] and item["request_count"] == 0
    assert (
        client.get(f"/api/projects/{project['id']}").json()["current_revision"]
        == project["current_revision"]
    )
    candidate = SongState.model_validate(
        client.get(f"/api/projects/{project['id']}/generations/{job['id']}/candidate").json()
    )
    assert candidate.composition.scope == "lyrics"
    assert candidate.score.seconds_at(candidate.score.end_tick) == pytest.approx(
        candidate.composition.target_seconds, abs=0.001
    )
    assert [unit.text for unit in candidate.composition.lyrics_alignment] == lyric_units(
        "昨日轻轻放下脚步迎向朝阳"
    )
    midi = mido.MidiFile(
        file=BytesIO(
            client.get(f"/api/projects/{project['id']}/generations/{job['id']}/midi").content
        ),
        charset="utf-8",
    )
    assert midi.type == 1 and len(midi.tracks) == 5
    assert any(
        event.type == "note_on" and event.channel == 9 for track in midi.tracks for event in track
    )
    assert any(event.type == "lyrics" for track in midi.tracks for event in track)
    before = len(client.get(f"/api/projects/{project['id']}/history").json())
    url = f"/api/projects/{project['id']}/generations/{job['id']}/apply"
    applied = client.post(url, json={"base_revision": project["current_revision"]}).json()
    assert applied["revision"]["state"]["composition"]["mock"]
    retry = client.post(url, json={"base_revision": applied["current_revision"]}).json()
    assert retry["current_revision"] == applied["current_revision"]
    assert len(client.get(f"/api/projects/{project['id']}/history").json()) == before + 1


def test_stale_candidate_and_cross_song_are_rejected(client):
    project = project_with_input(client)
    job = submit(client, project)
    assert wait_job(client, project["id"], job["id"])["status"] == "completed"
    state = deepcopy(project["revision"]["state"])
    state["inputs"]["theme"] = "新要求"
    current = client.put(
        f"/api/projects/{project['id']}",
        json={"base_revision": project["current_revision"], "state": state},
    ).json()
    response = client.post(
        f"/api/projects/{project['id']}/generations/{job['id']}/apply",
        json={"base_revision": current["current_revision"]},
    )
    assert response.status_code == 409
    second = project_with_input(client)
    assert (
        client.get(f"/api/projects/{second['id']}/generations/{job['id']}/candidate").status_code
        == 404
    )
    assert client.get(f"/api/projects/{project['id']}/generations/../candidate").status_code != 200


def test_lyrics_review_is_version_bound_and_requires_singing(client):
    project = project_with_input(client, state_with_input(lyrics=True))
    job = submit(client, project)
    assert wait_job(client, project["id"], job["id"])["status"] == "completed"
    project = client.post(
        f"/api/projects/{project['id']}/generations/{job['id']}/apply",
        json={"base_revision": project["current_revision"]},
    ).json()
    url = f"/api/projects/{project['id']}/review"
    body = {
        "base_revision": project["current_revision"],
        "approved": True,
        "melody": True,
        "arrangement": True,
        "style": True,
    }
    assert client.post(url, json=body).status_code == 422
    body["lyrics_sung"] = True
    result = client.post(url, json=body).json()
    assert result["review"]["approved"] and result["review"]["mock"]
    assert result["full_song_enabled"] is False  # Already covers the full lyric box.
    state = project["revision"]["state"]
    state["score"]["tracks"][1]["volume"] = 65
    current = client.put(
        f"/api/projects/{project['id']}",
        json={"base_revision": project["current_revision"], "state": state},
    ).json()
    assert client.get(url).json()["review"] is None
    assert client.get(url).json()["full_song_enabled"] is False
    assert client.post(url, json=body).status_code == 409
    body["base_revision"] = current["current_revision"]
    assert client.post(url, json=body).status_code == 200
    # Removing an aligned melody note invalidates approval instead of silently dropping words.
    first_bound = state["composition"]["lyrics_alignment"][0]["note_ids"][0]
    state["score"]["tracks"][0]["notes"] = [
        note for note in state["score"]["tracks"][0]["notes"] if note["id"] != first_bound
    ]
    current = client.put(
        f"/api/projects/{project['id']}",
        json={"base_revision": current["current_revision"], "state": state},
    ).json()
    body["base_revision"] = current["current_revision"]
    assert client.post(url, json=body).status_code == 422
    assert client.get(url).json()["valid"] is False


def test_preserve_and_lyrics_use_program_owned_prefix_and_record_filtered_model_notes():
    source = state_with_input(lyrics=True, melody=True)
    context = input_context(source)
    raw = mock_plan(source, context)
    result, _ = compile_plan(raw, source, "a" * 32, True)
    assert result.composition.lyrics_alignment
    assert "逐音" in result.composition.checks[-1]
    # A hallucinated prefix and instrument are ignored explicitly; original MIDI events survive.
    raw["tracks"][0]["notes"].insert(0, [0, 75, 0.1, 23])
    raw["tracks"][0]["program"] = 80
    result, _ = compile_plan(raw, source, "a" * 32, False)
    assert result.composition.reference_lock.filtered_model_notes == 1
    assert result.score.tracks[0].program == source.inputs.recording[0].program
    assert [note.pitch for note in result.score.tracks[0].notes[:3]] == [60, 62, 64]
    assert result.composition.lyrics_alignment[0].note_ids == [result.score.tracks[0].notes[0].id]


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing_word", "alignment"),
        ("duplicate_note", "alignment_duplicate"),
        ("silence", "ending"),
        ("bad_track", "schema"),
        ("bad_duration", "schema"),
    ],
)
def test_invalid_music_is_not_a_success(change, code):
    source = state_with_input(lyrics=True)
    raw = mock_plan(source, input_context(source))
    if change == "missing_word":
        raw["lyrics_alignment"].pop()
    elif change == "duplicate_note":
        raw["lyrics_alignment"][1]["note_indices"] = [0]
    elif change == "silence":
        for track in raw["tracks"]:
            track["notes"] = [note for note in track["notes"] if note[0] + note[2] < 80]
        raw["lyrics_alignment"] = []
    elif change == "bad_track":
        raw["tracks"][1]["family"] = "keyboard"
    else:
        raw["bpm"] = 97
    with pytest.raises(ModelError) as caught:
        compile_plan(raw, source, "a" * 32, False)
    assert caught.value.code == code


def test_real_adapter_request_contract_metadata_and_no_retry():
    source = state_with_input()
    raw = mock_plan(source, input_context(source))
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer " + FAKE_KEY
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["max_tokens"] == 32768 and payload["stream"] is False
        assert FAKE_KEY not in request.content.decode()
        return httpx.Response(200, json=response_for(raw))

    result, metadata = DeepSeekAdapter(real_settings(), httpx.MockTransport(handler)).generate(
        [{"role": "user", "content": "json test"}]
    )
    assert result == raw and len(requests) == 1
    assert metadata["usage"]["total_tokens"] == 1200
    assert FAKE_KEY not in json.dumps(metadata)


@pytest.mark.parametrize("status", [401, 402, 429, 503, 302])
def test_real_adapter_http_failures_do_not_echo_keys_or_follow_redirects(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=FAKE_KEY, headers={"Location": "https://example.com/"})

    with pytest.raises(ModelError) as caught:
        DeepSeekAdapter(real_settings(), httpx.MockTransport(handler)).generate([])
    assert len(calls) == 1 and FAKE_KEY not in str(caught.value)


def test_timeout_and_truncated_usage_are_retained_without_retry():
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout(FAKE_KEY)

    with pytest.raises(ModelError) as caught:
        DeepSeekAdapter(real_settings(), httpx.MockTransport(timeout)).generate([])
    assert caught.value.code == "timeout" and FAKE_KEY not in str(caught.value) and len(calls) == 1
    envelope = response_for({})
    envelope["choices"][0]["finish_reason"] = "length"
    with pytest.raises(ModelError) as caught:
        DeepSeekAdapter(
            real_settings(), httpx.MockTransport(lambda request: httpx.Response(200, json=envelope))
        ).generate([])
    assert (
        caught.value.code == "incomplete" and caught.value.metadata["usage"]["total_tokens"] == 1200
    )


def test_real_job_requires_explicit_consent_and_one_fake_http_request(tmp_path):
    calls = []
    source = state_with_input()
    raw = mock_plan(source, input_context(source))

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_for(raw))

    app = create_app(
        tmp_path / "songs",
        model_settings=real_settings(),
        adapter_factory=lambda settings: DeepSeekAdapter(settings, httpx.MockTransport(handler)),
    )
    with TestClient(app) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client, source)
        url = f"/api/projects/{project['id']}/generations"
        assert (
            client.post(url, json={"base_revision": project["current_revision"]}).status_code == 403
        )
        assert not calls
        job = submit(client, project, confirm_real_call=True)
        item = wait_job(client, project["id"], job["id"])
        assert item["status"] == "completed" and item["request_count"] == 1 and not item["mock"]
        assert len(calls) == 1
        for path in (tmp_path / "songs").rglob("*.json"):
            assert FAKE_KEY not in path.read_text(encoding="utf-8")
        assert FAKE_KEY not in client.get("/api/model/status").text


def test_failure_keeps_existing_song_and_retains_consumption(tmp_path):
    source = state_with_input()
    raw = mock_plan(source, input_context(source))
    raw["tracks"].pop()
    app = create_app(
        tmp_path / "songs",
        model_settings=real_settings(),
        adapter_factory=lambda settings: DeepSeekAdapter(
            settings,
            httpx.MockTransport(lambda request: httpx.Response(200, json=response_for(raw))),
        ),
    )
    with TestClient(app) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client, source)
        job = submit(client, project, confirm_real_call=True)
        item = wait_job(client, project["id"], job["id"])
        assert item["status"] == "failed" and item["metadata"]["usage"]["total_tokens"] == 1200
        assert (
            client.get(f"/api/projects/{project['id']}").json()["current_revision"]
            == project["current_revision"]
        )
        assert (
            client.get(f"/api/projects/{project['id']}/generations/{job['id']}/midi").status_code
            == 409
        )


def test_natural_language_modification_is_a_new_candidate_and_can_undo(client):
    project = project_with_input(client)
    job = submit(client, project)
    assert wait_job(client, project["id"], job["id"])["status"] == "completed"
    project = client.post(
        f"/api/projects/{project['id']}/generations/{job['id']}/apply",
        json={"base_revision": project["current_revision"]},
    ).json()
    bad = client.post(
        f"/api/projects/{project['id']}/generations",
        json={
            "base_revision": project["current_revision"],
            "operation": "modify",
            "instruction": "只修改贝斯声部",
        },
    )
    assert bad.status_code == 422
    changed = submit(client, project, operation="modify", instruction="整体更轻快")
    assert wait_job(client, project["id"], changed["id"])["status"] == "completed"
    after = client.post(
        f"/api/projects/{project['id']}/generations/{changed['id']}/apply",
        json={"base_revision": project["current_revision"]},
    ).json()
    assert (
        after["revision"]["state"]["score"]["tempo_map"][0]["microseconds_per_beat"]
        < project["revision"]["state"]["score"]["tempo_map"][0]["microseconds_per_beat"]
    )
    undone = client.post(
        f"/api/projects/{project['id']}/undo", json={"base_revision": after["current_revision"]}
    ).json()
    assert undone["current_revision"] == project["current_revision"]


def test_bounded_queue_and_restart_do_not_resend(tmp_path):
    blocked = Event()
    source = state_with_input()
    raw = mock_plan(source, input_context(source))

    class BlockedAdapter:
        def __init__(self, settings):
            pass

        def generate(self, messages):
            assert blocked.wait(5)
            return raw, {"request_id": "fake", "returned_model": "fake", "usage": None}

    app = create_app(
        tmp_path / "songs", model_settings=real_settings(), adapter_factory=BlockedAdapter
    )
    with TestClient(app) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        projects = [project_with_input(client) for _ in range(3)]
        jobs = [submit(client, project, confirm_real_call=True) for project in projects[:2]]
        response = client.post(
            f"/api/projects/{projects[2]['id']}/generations",
            json={"base_revision": projects[2]["current_revision"], "confirm_real_call": True},
        )
        assert response.status_code == 429
        assert client.get("/api/projects").status_code == 200
        blocked.set()
        for project, job in zip(projects, jobs, strict=False):
            assert wait_job(client, project["id"], job["id"])["status"] == "completed"
    first = projects[0]
    directory = tmp_path / "songs" / first["id"] / "jobs" / "composition" / jobs[0]["id"]
    item = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    item["status"] = "running"
    ProjectStore(tmp_path / "songs").write_json(directory / "job.json", item)
    with TestClient(create_app(tmp_path / "songs")) as restarted:
        result = restarted.get(f"/api/projects/{first['id']}/generations/{item['id']}").json()
        assert result["status"] == "failed" and result["error_code"] == "interrupted"


def test_json_duplicates_empty_and_secret_echo_are_rejected():
    with pytest.raises(ValueError):
        strict_json('{"a":1,"a":2}')
    with pytest.raises(ValueError):
        strict_json('{"a":NaN}')
    for envelope in (
        response_for({}, model=FAKE_KEY),
        response_for({}, choices=[{"finish_reason": "stop", "message": {"content": ""}}]),
    ):
        with pytest.raises(ModelError) as caught:
            DeepSeekAdapter(
                real_settings(),
                httpx.MockTransport(
                    lambda request, envelope=envelope: httpx.Response(200, json=envelope)
                ),
            ).generate([])
        assert FAKE_KEY not in str(caught.value)


def test_whole_song_modify_accepts_instruction_only_and_retains_mixer():
    source = state_with_input()
    compiled, _ = compile_plan(mock_plan(source, input_context(source)), source, "1" * 32, True)
    compiled.inputs = Inputs()
    compiled.score.tracks[0].mute = True
    compiled.score.tracks[1].solo = True
    context = input_context(compiled, "modify", "整体改为轻快的曲风")
    changed, _ = compile_plan(mock_plan(compiled, context), compiled, "2" * 32, True, "modify")
    for before, after in zip(compiled.score.tracks, changed.score.tracks, strict=True):
        assert (before.id, before.channel, before.mute, before.solo) == (
            after.id,
            after.channel,
            after.mute,
            after.solo,
        )


def test_json_escaped_secret_is_never_returned_or_saved():
    envelope = response_for({"title": FAKE_KEY})
    envelope["choices"][0]["message"]["content"] = (
        '{"title":"' + "".join(f"\\u{ord(character):04x}" for character in FAKE_KEY) + '"}'
    )
    with pytest.raises(ModelError) as caught:
        DeepSeekAdapter(
            real_settings(), httpx.MockTransport(lambda request: httpx.Response(200, json=envelope))
        ).generate([])
    assert FAKE_KEY not in str(caught.value)
