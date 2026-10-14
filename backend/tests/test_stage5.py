"""Stage 5: explicit repairs, immutable history, bounded preflight and HTTP accounting."""

import json
import sys
from copy import deepcopy
from io import BytesIO

import httpx
import mido
import pytest
from fastapi.testclient import TestClient

from backend.app.composition import compile_plan, input_context, mock_plan, model_messages
from backend.app.deepseek import DeepSeekAdapter
from backend.app.main import create_app
from backend.app.model_settings import ModelSettings
from backend.app.models import RecordedNote, empty_song
from backend.tests.test_generation import (
    project_with_input,
    real_settings,
    state_with_input,
    submit,
    wait_job,
)
from scripts import evaluate_model
from scripts.evaluate_model import capture_requests, source_for


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "songs", model_settings=ModelSettings())) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        yield client


def adopted(client, preserve=False):
    project = project_with_input(client, state_with_input(lyrics=True, melody=preserve))
    job = submit(client, project)
    assert wait_job(client, project["id"], job["id"])["status"] == "completed"
    return client.post(
        f"/api/projects/{project['id']}/generations/{job['id']}/apply",
        json={"base_revision": project["current_revision"]},
    ).json()


def repair(client, project, state, ids, line=0, unit=0, **changes):
    return client.post(
        f"/api/projects/{project['id']}/lyrics-repair",
        json={
            "base_revision": project["current_revision"],
            "state": state,
            "line_index": line,
            "unit_index": unit,
            "note_ids": ids,
            **changes,
        },
    )


def test_repair_is_draft_only_and_one_word_many_notes_survives_save_undo_midi(client, tmp_path):
    project = adopted(client)
    before = deepcopy(project)
    revision_path = (
        tmp_path / "songs" / project["id"] / "revisions" / f"{project['current_revision']}.json"
    )
    original_bytes = revision_path.read_bytes()
    state = deepcopy(project["revision"]["state"])
    lead = state["score"]["tracks"][0]
    bound_id = state["composition"]["lyrics_alignment"][0]["note_ids"][0]
    index = next(i for i, note in enumerate(lead["notes"]) if note["id"] == bound_id)
    old = lead["notes"].pop(index)
    half = old["duration_tick"] // 2
    a = {**old, "id": "replacement-a", "duration_tick": half}
    b = {
        **old,
        "id": "replacement-b",
        "start_tick": old["start_tick"] + half,
        "duration_tick": old["duration_tick"] - half,
    }
    lead["notes"][index:index] = [a, b]
    result = repair(client, project, state, [a["id"], b["id"]])
    assert result.status_code == 200, result.text
    draft = result.json()
    assert draft["saved"] is False and draft["assessment"]["valid"]
    assert client.get(f"/api/projects/{project['id']}").json() == before
    assert draft["state"]["inputs"] == state["inputs"]
    assert draft["state"]["score"]["tracks"] == state["score"]["tracks"]
    response = client.put(
        f"/api/projects/{project['id']}",
        json={"base_revision": project["current_revision"], "state": draft["state"]},
    )
    response.raise_for_status()
    saved = response.json()
    review = client.get(f"/api/projects/{project['id']}/review").json()
    assert review["valid"] and review["review"] is None
    exported = client.post(
        f"/api/projects/{project['id']}/midi-export",
        json={"base_revision": saved["current_revision"]},
    )
    exported.raise_for_status()
    midi = mido.MidiFile(file=BytesIO(exported.content), charset="utf-8")
    assert [msg.text for track in midi.tracks for msg in track if msg.type == "lyrics"].count(
        "昨"
    ) == 1
    assert revision_path.read_bytes() == original_bytes
    restored = client.post(
        f"/api/projects/{project['id']}/undo", json={"base_revision": saved["current_revision"]}
    ).json()
    assert restored["revision"]["state"] == before["revision"]["state"]
    redone = client.post(
        f"/api/projects/{project['id']}/redo", json={"base_revision": restored["current_revision"]}
    ).json()
    assert redone["revision"]["state"] == saved["revision"]["state"]


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate",
        "reverse",
        "missing",
        "other_track",
        "range",
        "duration",
        "polyphony",
        "unit",
        "stale",
    ],
)
def test_wrong_repairs_are_rejected_without_any_disk_change(client, kind):
    project = adopted(client)
    state = deepcopy(project["revision"]["state"])
    lead = state["score"]["tracks"][0]
    ids = state["composition"]["lyrics_alignment"][0]["note_ids"].copy()
    bound = next(note for note in lead["notes"] if note["id"] == ids[0])
    changes = {}
    if kind == "duplicate":
        ids = state["composition"]["lyrics_alignment"][1]["note_ids"].copy()
    elif kind == "reverse":
        ids = [lead["notes"][-1]["id"]]
    elif kind == "missing":
        ids = ["no-such-note"]
    elif kind == "other_track":
        ids = [state["score"]["tracks"][1]["notes"][0]["id"]]
    elif kind == "range":
        bound["pitch"] = 47
    elif kind == "duration":
        bound["duration_tick"] = 1
    elif kind == "polyphony":
        bound["duration_tick"] += 100
    elif kind == "unit":
        changes["unit_index"] = 999
    else:
        changes["base_revision"] = "f" * 32
    response = repair(client, project, state, ids, **changes)
    assert response.status_code in (422, 409), response.text
    assert client.get(f"/api/projects/{project['id']}").json() == project


def test_multiple_missing_units_can_be_repaired_in_steps_but_cannot_be_approved_early(client):
    project = adopted(client)
    state = deepcopy(project["revision"]["state"])
    lead = state["score"]["tracks"][0]
    first_ids = [u["note_ids"][0] for u in state["composition"]["lyrics_alignment"][:2]]
    originals = [next(note for note in lead["notes"] if note["id"] == nid) for nid in first_ids]
    replacements = [{**note, "id": f"fixed-{i}"} for i, note in enumerate(originals)]
    lead["notes"] = [
        replacements[first_ids.index(note["id"])] if note["id"] in first_ids else note
        for note in lead["notes"]
    ]
    response = repair(client, project, state, [replacements[0]["id"]])
    response.raise_for_status()
    assert not response.json()["assessment"]["valid"]
    state = response.json()["state"]
    saved = client.put(
        f"/api/projects/{project['id']}",
        json={"base_revision": project["current_revision"], "state": state},
    ).json()
    assert not client.get(f"/api/projects/{project['id']}/review").json()["valid"]
    response = repair(client, saved, state, [replacements[1]["id"]], unit=1)
    response.raise_for_status()
    assert response.json()["assessment"]["valid"]


@pytest.mark.parametrize("change", ["lyrics", "selection", "theme", "preserved_anchor", "breath"])
def test_repair_does_not_bypass_input_preservation_or_breath_constraints(client, change):
    project = adopted(client, preserve=change == "preserved_anchor")
    state = deepcopy(project["revision"]["state"])
    ids = state["composition"]["lyrics_alignment"][0]["note_ids"]
    kwargs = {}
    if change == "lyrics":
        state["inputs"]["lyrics_text"] = "旧日轻轻放下\n脚步迎向朝阳"
    elif change == "selection":
        state["inputs"]["selected_lines"] = [1]
    elif change == "theme":
        state["inputs"]["theme"] = "换一个主题"
    elif change == "preserved_anchor":
        state["score"]["tracks"][0]["notes"][0]["velocity"] = 44
    else:
        unit = next(
            unit for unit in state["composition"]["lyrics_alignment"] if unit["line_index"] == 1
        )
        ids = unit["note_ids"]
        by_id = {note["id"]: note for note in state["score"]["tracks"][0]["notes"]}
        previous = state["composition"]["lyrics_alignment"][5]
        before = by_id[previous["note_ids"][-1]]
        by_id[ids[0]]["start_tick"] = before["start_tick"] + before["duration_tick"] + 48
        kwargs["line"] = 1
    result = repair(client, project, state, ids, **kwargs)
    assert result.status_code == 422, result.text
    assert client.get(f"/api/projects/{project['id']}").json() == project


@pytest.mark.parametrize("kind", ["silence", "tiny"])
def test_unavoidable_reference_failures_create_no_task_or_http_dispatch(tmp_path, kind):
    calls = []
    source = state_with_input(melody=True)
    if kind == "silence":
        source.inputs.recording = [
            RecordedNote(
                pitch=36 if family in ("drums", "bass") else 60,
                start_seconds=4,
                duration_seconds=0.3,
                track_id=family,
                family=family,
            )
            for family in ("keyboard", "drums", "guitar", "bass")
        ]
    else:
        source.inputs.recording = [
            RecordedNote(
                pitch=60,
                start_seconds=i * 0.00001,
                duration_seconds=0.000005,
                track_id="tiny",
                family="keyboard",
            )
            for i in range(2)
        ]
    with TestClient(
        create_app(
            tmp_path / "songs",
            model_settings=real_settings(),
            adapter_factory=lambda settings: calls.append(settings),
        )
    ) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client, source)
        response = client.post(
            f"/api/projects/{project['id']}/generations",
            json={"base_revision": project["current_revision"], "confirm_real_call": True},
        )
        assert response.status_code == 422 and response.json()["code"] == "preserve_feasibility"
        assert not calls
        assert client.get(f"/api/projects/{project['id']}/generations").json()["items"] == []


def test_rounding_feasibility_is_tempo_specific_and_unlocked_tracks_can_fill_gaps():
    source = state_with_input(melody=True)
    source.inputs.recording = [
        RecordedNote(
            pitch=60,
            start_seconds=start,
            duration_seconds=duration,
            track_id="tiny",
            family="keyboard",
        )
        for start, duration in ((0, 0.0005), (0.00055, 0.2))
    ]
    context = input_context(source)
    assert 96 not in context["feasible_bpms"] and 160 in context["feasible_bpms"]
    assert context["lyric_prefix_eligible_indices"] == [1]
    source.inputs.recording[0].start_seconds = 10
    source.inputs.recording = [source.inputs.recording[0]]
    assert input_context(source)["feasible_bpms"]  # Three unlocked instruments can start at zero.


@pytest.mark.parametrize("kind", ["short", "breath", "dense"])
def test_mock_can_keep_ineligible_reference_as_intro_and_leave_lyric_breaths(kind):
    source = state_with_input(lyrics=True, melody=True)
    if kind == "short":
        source.inputs.recording[0].duration_seconds = 0.05
    elif kind == "breath":
        source.inputs.lyrics_text = "向\n前进"
        source.inputs.recording[1].start_seconds = 0.4
    else:
        source.inputs.lyrics_text = "\n".join(["向前走向前走向前" for _ in range(15)])
        source.inputs.selected_lines = list(range(15))
        source.inputs.recording = [
            RecordedNote(
                pitch=60, start_seconds=0, duration_seconds=30, track_id="long", family="keyboard"
            )
        ]
    state, _ = compile_plan(mock_plan(source, input_context(source)), source, "a" * 32, True)
    assert state.composition.reference_lock.note_count == len(source.inputs.recording)
    if kind in ("short", "dense"):
        assert (
            state.composition.lyrics_alignment[0].note_ids[0] != state.score.tracks[0].notes[0].id
        )


def test_adapter_initialization_failure_is_not_counted_as_a_sent_request(tmp_path):
    def unavailable(settings):
        raise OSError("isolated adapter construction error")

    with TestClient(
        create_app(tmp_path / "songs", model_settings=real_settings(), adapter_factory=unavailable)
    ) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client)
        job = wait_job(client, project["id"], submit(client, project, confirm_real_call=True)["id"])
        assert job["status"] == "failed" and job["request_count"] == 0


def test_dispatched_timeout_is_counted_once_without_retry(tmp_path):
    calls = []

    def timeout(request):
        calls.append(True)
        raise httpx.ConnectTimeout("fake timeout")

    with TestClient(
        create_app(
            tmp_path / "songs",
            model_settings=real_settings(),
            adapter_factory=lambda settings: DeepSeekAdapter(
                settings, httpx.MockTransport(timeout)
            ),
        )
    ) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client)
        job = wait_job(client, project["id"], submit(client, project, confirm_real_call=True)["id"])
        assert job["status"] == "failed" and job["request_count"] == 1
        assert calls == [True]


def test_evaluation_reservations_preflight_and_actual_attempts_are_distinct():
    result = {"request_count": 0}
    summary = {"reserved_attempts": 2, "cases": [result, {"request_count": 1}]}
    capture_requests(summary, result, {"request_count": 0, "error_code": "processing"})
    assert summary["actual_requests"] == summary["live_attempts"] == 1
    assert summary["reserved_attempts"] == 2
    a = source_for("preserve_lyrics", empty_song().model_dump())
    b = source_for("extend_lyrics", a.model_dump())
    assert a.inputs.model_dump(exclude={"melody_mode"}) == b.inputs.model_dump(
        exclude={"melody_mode"}
    )
    assert a.inputs.melody_mode == "preserve" and b.inputs.melody_mode == "extend"


def test_failed_preserve_evaluation_skips_dependent_modification_without_replacement(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(evaluate_model, "ROOT", tmp_path)
    monkeypatch.setattr(
        "backend.app.generation.mock_plan",
        lambda source, context: {"title": "broken isolated fixture"},
    )
    monkeypatch.setattr(
        sys, "argv", ["evaluate_model.py", "--mock", "--cases", "preserve_lyrics", "modify"]
    )
    monkeypatch.setattr(
        evaluate_model,
        "load_model_settings",
        lambda: pytest.fail("Mock evaluation must not load .env"),
    )
    assert evaluate_model.main() == 1
    path = next((tmp_path / ".cache/model-evaluations").glob("*/summary.json"))
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert [case["status"] for case in summary["cases"]] == ["failed", "skipped"]
    assert summary["actual_requests"] == summary["reserved_attempts"] == 0
    assert len(list(path.parent.rglob("job.json"))) == 1
    assert summary["cases"][0]["error_code"] == "lyric_schema"
    assert "job_id" not in summary["cases"][1]


@pytest.mark.parametrize(
    "args",
    [
        ["--allow-real", "--max-requests", "1", "--cases", "preserve_lyrics", "modify"],
        ["--allow-real", "--max-requests", "2", "--cases", "preserve_lyrics", "modify"],
        ["--mock", "--cases", "modify", "preserve_lyrics"],
        ["--mock", "--cases", "lyrics", "lyrics"],
    ],
)
def test_invalid_evaluation_scope_stops_before_configuration_or_requests(args, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["evaluate_model.py", *args])
    monkeypatch.setattr(
        evaluate_model,
        "load_model_settings",
        lambda: pytest.fail("No configuration may load for invalid scope"),
    )
    with pytest.raises(SystemExit) as error:
        evaluate_model.main()
    assert error.value.code == 2


@pytest.mark.parametrize("kind", ["ending", "breath"])
def test_real_failure_boundaries_keep_strict_checks_and_report_numeric_location(kind):
    source = state_with_input(lyrics=True)
    context = input_context(source)
    raw = mock_plan(source, context)
    if kind == "ending":
        for track in raw["tracks"]:
            track["notes"] = [
                event for event in track["notes"] if event[0] + event[2] <= raw["bpm"] - 2
            ]
    else:
        previous = raw["tracks"][0]["notes"][5]
        current = raw["tracks"][0]["notes"][6]
        current[0] = previous[0] + previous[2] + 0.08 * raw["bpm"] / 60
    from backend.app.model_settings import ModelError

    with pytest.raises(ModelError) as error:
        compile_plan(raw, source, "b" * 32, False)
    assert error.value.code == kind
    assert ("最后发声结束于" if kind == "ending" else "第2行句前间隔为") in str(error.value)
    messages = model_messages(context)
    assert "max(start+duration)>=bpm-bpm/240" in messages[0]["content"]
    assert "0.16*bpm/60" in messages[0]["content"]
