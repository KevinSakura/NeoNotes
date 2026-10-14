"""Stage 4 regressions: exact input anchors, review diagnostics and no live API."""

from copy import deepcopy
from io import BytesIO

import mido
import pytest
from fastapi.testclient import TestClient

from backend.app.composition import (
    compile_plan,
    input_context,
    mock_plan,
    validate_preservation,
)
from backend.app.main import create_app
from backend.app.model_settings import ModelError, ModelSettings
from backend.app.models import RecordedNote, SongState, Tempo
from backend.tests.test_generation import (
    project_with_input,
    real_settings,
    state_with_input,
    submit,
    wait_job,
)


def compiled(source):
    return compile_plan(mock_plan(source, input_context(source)), source, "a" * 32, True)[0]


def test_non_grid_seconds_velocity_instrument_and_multiple_families_survive_model_errors():
    source = state_with_input(melody=True)
    for index, note in enumerate(source.inputs.recording):
        note.start_seconds += 0.123456
        note.duration_seconds = 0.291234
        note.velocity = 64 + index * 11
        note.program = 4
    source.inputs.recording.append(
        RecordedNote(
            pitch=36,
            start_seconds=0.12567,
            duration_seconds=0.12345,
            velocity=51,
            track_id="original-drum",
            family="drums",
            program=8,
        )
    )
    context = input_context(source)
    assert context["locked_prefix"]["keyboard"]["note_count"] == 3
    raw = mock_plan(source, context)
    # Drop erroneous/crossing prefix events, lock even a conflicting model instrument.
    raw["tracks"][0]["notes"].insert(0, [0, 77, 5, 127])
    raw["tracks"][0]["program"], raw["tracks"][0]["bank_msb"] = 80, 5
    result, _ = compile_plan(raw, source, "a" * 32, False)
    lock = result.composition.reference_lock
    assert lock.note_count == 4 and lock.filtered_model_notes == 1
    assert lock.families == ["keyboard", "drums"]
    for original in source.inputs.recording:
        track = next(track for track in result.score.tracks if track.family == original.family)
        assert (track.program, track.bank_msb, track.bank_lsb) == (
            original.program,
            original.bank_msb,
            original.bank_lsb,
        )
        note = next(
            note
            for note in track.notes
            if note.pitch == original.pitch and note.velocity == original.velocity
        )
        tick_seconds = result.score.tempo_map[0].microseconds_per_beat / 480 / 1e6
        assert (
            abs(result.score.seconds_at(note.start_tick) - original.start_seconds) <= tick_seconds
        )
        actual_duration = result.score.seconds_at(
            note.start_tick + note.duration_tick
        ) - result.score.seconds_at(note.start_tick)
        assert abs(actual_duration - original.duration_seconds) <= tick_seconds
    validate_preservation(result)


def test_anchor_count_is_included_in_final_note_limit():
    source = state_with_input(melody=True)
    raw = mock_plan(source, input_context(source))
    # Same-start distinct pitches are not added: limit must fail before overlap checks.
    total = sum(len(track["notes"]) for track in raw["tracks"])
    raw["tracks"][1]["notes"] += [[50, 36, 0.1, 70]] * (1200 - total)
    with pytest.raises(ModelError) as caught:
        compile_plan(raw, source, "a" * 32, True)
    assert caught.value.code == "note_count"


@pytest.mark.parametrize(
    "change", ["pitch", "velocity", "duration", "time", "instrument", "tempo", "extra"]
)
def test_current_version_cannot_claim_preservation_after_manual_changes(change):
    source = state_with_input(melody=True)
    state = compiled(source)
    note = state.score.tracks[0].notes[1]
    if change == "pitch":
        note.pitch += 1
    elif change == "velocity":
        note.velocity -= 1
    elif change == "duration":
        note.duration_tick += 12
    elif change == "time":
        note.start_tick += 12
    elif change == "instrument":
        state.score.tracks[0].bank_msb = 1
    elif change == "tempo":
        # A later tempo event changes original seconds even if MIDI ticks stay fixed.
        state.score.tempo_map.append(Tempo(tick=200, microseconds_per_beat=800000))
    else:
        extra = note.model_copy(update={"id": "extra-anchor", "pitch": 61, "start_tick": 250})
        state.score.tracks[0].notes.append(extra)
    with pytest.raises(ModelError) as caught:
        validate_preservation(state)
    assert caught.value.code in ("preserve", "preserve_window")


def test_missing_or_wrong_lyrics_after_prefix_is_not_silently_repaired():
    source = state_with_input(lyrics=True, melody=True)
    raw = mock_plan(source, input_context(source))
    raw["lyrics_alignment"][1]["note_indices"] = [0]
    with pytest.raises(ModelError) as caught:
        compile_plan(raw, source, "a" * 32, False)
    assert caught.value.code == "alignment_duplicate"


@pytest.mark.parametrize(
    "change,code",
    [("range", "preserve_range"), ("overlap", "preserve_overlap"), ("bank", "reference_resource")],
)
def test_invalid_pinned_input_stops_before_task_or_http_request(tmp_path, change, code):
    calls = []

    def adapter(settings):
        calls.append(settings)
        raise AssertionError("No request allowed for invalid source")

    source = state_with_input(melody=True)
    if change == "range":
        source.inputs.recording[0].pitch = 1
    elif change == "overlap":
        source.inputs.recording[1].pitch = 60
        source.inputs.recording[0].duration_seconds = 1
    else:
        for note in source.inputs.recording:
            note.bank_lsb = 1
    with TestClient(
        create_app(tmp_path / "songs", model_settings=real_settings(), adapter_factory=adapter)
    ) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client, source)
        response = client.post(
            f"/api/projects/{project['id']}/generations",
            json={"base_revision": project["current_revision"], "confirm_real_call": True},
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == code
        assert not calls
        assert client.get(f"/api/projects/{project['id']}/generations").json()["items"] == []


def test_review_report_rechecks_score_and_pins_assessment_snapshot(tmp_path):
    with TestClient(create_app(tmp_path / "songs", model_settings=ModelSettings())) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client, state_with_input(lyrics=True, melody=True))
        job = submit(client, project)
        assert wait_job(client, project["id"], job["id"])["status"] == "completed"
        project = client.post(
            f"/api/projects/{project['id']}/generations/{job['id']}/apply",
            json={"base_revision": project["current_revision"]},
        ).json()
        url = f"/api/projects/{project['id']}/review"
        status = client.get(url).json()
        assert status["valid"] and len(status["assessment"]["checks"]) == 6
        assert status["assessment"]["singability"] == "requires_human_trial"
        assert [row["text"] for row in status["assessment"]["lyric_lines"]] == [
            "昨日轻轻放下",
            "脚步迎向朝阳",
        ]
        body = {
            "base_revision": project["current_revision"],
            "approved": True,
            "melody": True,
            "arrangement": True,
            "style": True,
            "lyrics_sung": True,
        }
        approved = client.post(url, json=body).json()
        assert approved["review"]["assessment"]["valid"]
        assert approved["review"]["assessment"]["reference_lock"]["note_count"] == 3
        state = deepcopy(project["revision"]["state"])
        state["score"]["tracks"][0]["notes"][0]["velocity"] = 44
        project = client.put(
            f"/api/projects/{project['id']}",
            json={"base_revision": project["current_revision"], "state": state},
        ).json()
        status = client.get(url).json()
        assert not status["valid"] and status["review"] is None
        preserve = next(
            check for check in status["assessment"]["checks"] if check["id"] == "preserve"
        )
        assert not preserve["passed"] and preserve["code"] == "preserve"
        body["base_revision"] = project["current_revision"]
        assert client.post(url, json=body).status_code == 422
        # Recording rejection is still available, with evidence, even for a damaged score.
        body["approved"] = False
        assert client.post(url, json=body).json()["review"]["assessment"]["valid"] is False


def test_old_composition_without_reference_lock_loads_and_checks_actual_notes():
    source = state_with_input(melody=True)
    raw = compiled(source).model_dump()
    raw["composition"].pop("reference_lock")
    state = SongState.model_validate(raw)
    assert state.composition.reference_lock is None
    validate_preservation(state)


@pytest.mark.parametrize(
    "change,check_id", [("inputs", "source"), ("planning", "planning"), ("instrument", "soundfont")]
)
def test_review_identifies_different_current_version_failures(tmp_path, change, check_id):
    with TestClient(create_app(tmp_path / "songs", model_settings=ModelSettings())) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client, state_with_input())
        job = submit(client, project)
        assert wait_job(client, project["id"], job["id"])["status"] == "completed"
        project = client.post(
            f"/api/projects/{project['id']}/generations/{job['id']}/apply",
            json={"base_revision": project["current_revision"]},
        ).json()
        state = deepcopy(project["revision"]["state"])
        if change == "inputs":
            state["inputs"]["theme"] = "已改变创作要求"
        elif change == "planning":
            state["composition"]["chords"][-1]["end_tick"] -= 480
        else:
            state["score"]["tracks"][0]["bank_lsb"] = 1
        client.put(
            f"/api/projects/{project['id']}",
            json={"base_revision": project["current_revision"], "state": state},
        ).raise_for_status()
        status = client.get(f"/api/projects/{project['id']}/review").json()
        assert not status["valid"]
        check = next(check for check in status["assessment"]["checks"] if check["id"] == check_id)
        assert not check["passed"] and check["message"]


def test_moving_or_deleting_lyric_notes_refreshes_midi_text_without_rewriting_history(tmp_path):
    with TestClient(create_app(tmp_path / "songs", model_settings=ModelSettings())) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        project = project_with_input(client, state_with_input(lyrics=True))
        job = submit(client, project)
        assert wait_job(client, project["id"], job["id"])["status"] == "completed"
        project = client.post(
            f"/api/projects/{project['id']}/generations/{job['id']}/apply",
            json={"base_revision": project["current_revision"]},
        ).json()
        old_revision = project["current_revision"]
        revision_path = tmp_path / "songs" / project["id"] / "revisions" / f"{old_revision}.json"
        old_bytes = revision_path.read_bytes()
        state = deepcopy(project["revision"]["state"])
        bound_id = state["composition"]["lyrics_alignment"][0]["note_ids"][0]
        bound_note = next(
            note for note in state["score"]["tracks"][0]["notes"] if note["id"] == bound_id
        )
        new_tick = bound_note["start_tick"] + 48
        bound_note["start_tick"] = new_tick
        project = client.put(
            f"/api/projects/{project['id']}",
            json={"base_revision": project["current_revision"], "state": state},
        ).json()
        meta = next(
            meta
            for meta in project["revision"]["state"]["score"]["midi_meta"]
            if meta["type"] == "lyrics" and meta["text"] == "昨"
        )
        assert meta["tick"] == new_tick and revision_path.read_bytes() == old_bytes
        content = client.post(
            f"/api/projects/{project['id']}/midi-export",
            json={"base_revision": project["current_revision"]},
        )
        content.raise_for_status()
        midi = mido.MidiFile(file=BytesIO(content.content), charset="utf-8")
        positions = []
        for track in midi.tracks:
            tick = 0
            for event in track:
                tick += event.time
                if event.type == "lyrics" and event.text == "昨":
                    positions.append(tick)
        assert positions == [new_tick]
        state = deepcopy(project["revision"]["state"])
        state["score"]["tracks"][0]["notes"] = [
            note for note in state["score"]["tracks"][0]["notes"] if note["id"] != bound_id
        ]
        project = client.put(
            f"/api/projects/{project['id']}",
            json={"base_revision": project["current_revision"], "state": state},
        ).json()
        assert project["revision"]["state"]["composition"]["lyrics_alignment"][0]["text"] == "昨"
        assert not any(
            meta["type"] == "lyrics" and meta["text"] == "昨"
            for meta in project["revision"]["state"]["score"]["midi_meta"]
        )
        assert not client.get(f"/api/projects/{project['id']}/review").json()["valid"]
        assert revision_path.read_bytes() == old_bytes
