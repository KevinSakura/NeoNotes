"""Lyrics input recovery and expanded limits; fake settings/HTTP only."""

import json
from io import BytesIO

import httpx
import mido
import pytest
from fastapi.testclient import TestClient

from backend.app.composition import compile_plan, input_context, mock_plan
from backend.app.deepseek import DeepSeekAdapter
from backend.app.full_song import full_context
from backend.app.main import create_app
from backend.app.model_settings import ModelError, ModelSettings
from backend.app.models import SongState
from backend.tests.test_generation import (
    project_with_input,
    real_settings,
    response_for,
    state_with_input,
    submit,
    wait_job,
)


def lyrics_state(count):
    state = state_with_input()
    chunks = ["词" * min(128, count - offset) for offset in range(0, count, 128)]
    state.inputs.lyrics_text = "\n".join(chunks)
    state.inputs.selected_lines = list(range(len(chunks)))
    return state


@pytest.mark.parametrize("count", [173, 256])
def test_expanded_short_limit_compiles_all_words_and_real_midi(count):
    source = lyrics_state(count)
    context = input_context(source)
    result, _ = compile_plan(mock_plan(source, context), source, "a" * 32, True)
    assert len(result.composition.lyrics_alignment) == count
    from backend.app.midi import export_midi, import_midi

    midi = export_midi(result.score)
    assert len(import_midi(midi).tracks) == 4
    parsed = mido.MidiFile(file=BytesIO(midi))
    assert sum(message.type == "lyrics" for track in parsed.tracks for message in track) == count
    assert parsed.length == pytest.approx(60, abs=0.001)


def test_full_1024_words_survive_plan_composition_and_midi():
    original = lyrics_state(1024)
    original.inputs.selected_lines = [0]
    short, _ = compile_plan(mock_plan(original, input_context(original)), original, "a" * 32, True)
    source, context = full_context(short, "b" * 32, "c" * 32)
    result, _ = compile_plan(mock_plan(source, context), source, "d" * 32, True, "full", context)
    assert len(result.composition.lyrics_alignment) == 1024
    assert SongState.model_validate(result.model_dump()).composition.scope == "full"
    from backend.app.midi import export_midi, import_midi

    imported = import_midi(export_midi(result.score))
    assert sum(meta.type == "lyrics" for meta in imported.midi_meta) == 1024
    assert imported.seconds_at(imported.end_tick) == pytest.approx(600, abs=0.001)


@pytest.mark.parametrize("full,count", [(False, 257), (True, 1025)])
def test_one_word_over_limit_is_rejected_before_dispatch(full, count):
    source = lyrics_state(count)
    with pytest.raises(ModelError) as caught:
        input_context(source, "full" if full else "generate")
    assert caught.value.code == "lyrics_size"
    assert str(count) in str(caught.value)


@pytest.mark.parametrize("entry", ["paste", "txt"])
def test_unselected_lyrics_keep_source_and_allow_feeling_only_same_song(tmp_path, entry):
    app = create_app(tmp_path / "songs", model_settings=ModelSettings())
    with TestClient(app) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        source = lyrics_state(1025)
        text = source.inputs.lyrics_text
        project = project_with_input(client, source)
        if entry == "txt":
            project = client.post(
                f"/api/projects/{project['id']}/lyrics",
                data={"base_revision": project["current_revision"]},
                files={"file": ("words.txt", text.encode("utf-8"), "text/plain")},
            ).json()
            selected = project["revision"]["state"]
            selected["inputs"]["selected_lines"] = source.inputs.selected_lines
            project = client.put(
                f"/api/projects/{project['id']}",
                json={"base_revision": project["current_revision"], "state": selected},
            ).json()
        url = f"/api/projects/{project['id']}/generations"
        failed = client.post(url, json={"base_revision": project["current_revision"]})
        assert failed.status_code == 422 and failed.json()["code"] == "lyrics_size"
        assert client.get(url).json()["items"] == []
        revised = project["revision"]["state"]
        revised["inputs"]["selected_lines"] = []
        revised["inputs"]["theme"] = ""  # Music feeling alone is sufficient.
        files = revised["inputs"]["files"]
        project = client.put(
            f"/api/projects/{project['id']}",
            json={"base_revision": project["current_revision"], "state": revised},
        ).json()
        job = submit(client, project)
        assert wait_job(client, project["id"], job["id"])["status"] == "completed"
        candidate = client.get(url + f"/{job['id']}/candidate").json()
        assert candidate["inputs"]["lyrics_text"] == text
        assert candidate["inputs"]["selected_lines"] == []
        assert candidate["inputs"]["files"] == files
        assert candidate["composition"]["lyrics_alignment"] == []
        saved = client.post(
            url + f"/{job['id']}/apply", json={"base_revision": project["current_revision"]}
        ).json()
        assert saved["revision"]["state"]["inputs"]["lyrics_text"] == text
        if entry == "txt":
            directory = tmp_path / "songs" / project["id"] / "inputs/originals"
            assert next(directory.glob("*.txt")).read_bytes() == text.encode("utf-8")


def test_lyrics_only_without_selection_still_requires_explicit_lines(tmp_path):
    with TestClient(create_app(tmp_path / "songs", model_settings=ModelSettings())) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        source = lyrics_state(12)
        source.inputs.theme = source.inputs.emotion = ""
        source.inputs.selected_lines = []
        project = project_with_input(client, source)
        response = client.post(
            f"/api/projects/{project['id']}/generations",
            json={"base_revision": project["current_revision"]},
        )
        assert response.status_code == 422 and response.json()["code"] == "lyrics_selection"


def test_unused_lyrics_are_not_sent_to_fake_http_model(tmp_path):
    source = lyrics_state(1025)
    marker = source.inputs.lyrics_text
    calls = []

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][-1]["content"])
        assert context["selected_lyrics"] == []
        assert marker not in json.dumps(context, ensure_ascii=False)
        return httpx.Response(200, json=response_for(mock_plan(source, context)))

    app = create_app(
        tmp_path / "songs",
        model_settings=real_settings(),
        adapter_factory=lambda settings: DeepSeekAdapter(settings, httpx.MockTransport(handler)),
    )
    with TestClient(app) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client, source)
        url = f"/api/projects/{project['id']}/generations"
        response = client.post(
            url, json={"base_revision": project["current_revision"], "confirm_real_call": True}
        )
        assert response.status_code == 422 and not calls
        source.inputs.selected_lines = []
        project = client.put(
            f"/api/projects/{project['id']}",
            json={"base_revision": project["current_revision"], "state": source.model_dump()},
        ).json()
        job = submit(client, project, confirm_real_call=True)
        result = wait_job(client, project["id"], job["id"])
        assert result["status"] == "completed", result
        assert len(calls) == result["request_count"] == 1  # Fake transport, no network.
