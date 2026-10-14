"""Instrumental passages and exact lyric-index failures; isolated fake HTTP only."""

import json
from copy import deepcopy
from io import BytesIO

import httpx
import mido
import pytest
from fastapi.testclient import TestClient

from backend.app.composition import compile_plan, input_context, mock_plan, model_messages
from backend.app.deepseek import DeepSeekAdapter
from backend.app.full_song import full_context
from backend.app.main import create_app
from backend.app.midi import export_midi, import_midi
from backend.app.model_settings import ModelError
from backend.tests.test_generation import (
    FAKE_KEY,
    project_with_input,
    real_settings,
    response_for,
    state_with_input,
    submit_legacy,
    wait_job,
)


def instrumental_plan(source, context):
    raw = mock_plan(source, context)
    total = raw["bpm"] * raw["duration_seconds"] / 60
    lead = next(track for track in raw["tracks"] if track["family"] == "keyboard")
    lead["notes"] = [
        [0, 62, 0.5, 80],
        [1, 64, 0.5, 80],
        [2, 64, 0.5, 80],
        [3, 62, 0.5, 80],
        [8, 62, 1, 90],
        [10, 64, 0.5, 90],
        [11, 65, 1, 80],
        [42, 67, 1, 80],
        [48, 64, 0.75, 90],
        [50, 62, 0.75, 90],
        [total - 2, 60, 2, 80],
    ]
    raw["sections"] = [
        {"name": name, "start": start, "end": end}
        for name, start, end in [
            ("前奏", 0, 8),
            ("主歌", 8, 40),
            ("间奏", 40, 48),
            ("第二段演唱", 48, total - 8),
            ("尾奏", total - 8, total),
        ]
    ]
    raw["lyrics_alignment"] = [
        {"line_index": 0, "unit_index": 0, "note_indices": [4, 5]},
        {"line_index": 1, "unit_index": 0, "note_indices": [8]},
        {"line_index": 1, "unit_index": 1, "note_indices": [9]},
    ]
    return raw


def passage_source():
    source = state_with_input()
    source.inputs.lyrics_text = "向\n前走"
    source.inputs.selected_lines = [0, 1]
    return source


@pytest.mark.parametrize("full", [False, True])
def test_instrumental_passages_and_one_word_many_notes_survive_complete_midi(full):
    source = passage_source()
    context = input_context(source)
    raw = instrumental_plan(source, context)
    if full:
        short, _ = compile_plan(raw, source, "a" * 32, True)
        source, context = full_context(short, "b" * 32, "c" * 32)
        raw = instrumental_plan(source, context)
    result, _ = compile_plan(raw, source, "d" * 32, True, "full" if full else "generate", context)
    lead = result.score.tracks[0]
    bound = {nid for unit in result.composition.lyrics_alignment for nid in unit.note_ids}
    assert len(lead.notes) == 11 and len(bound) == 4
    assert len(result.composition.lyrics_alignment) == 3
    assert len(result.composition.lyrics_alignment[0].note_ids) == 2
    assert lead.notes[0].id not in bound
    assert lead.notes[7].id not in bound
    assert lead.notes[-1].id not in bound
    assert result.score.seconds_at(result.score.end_tick) == pytest.approx(
        raw["duration_seconds"], abs=0.001
    )
    data = export_midi(result.score)
    restored = next(track for track in import_midi(data).tracks if track.family == "keyboard")
    assert [(n.pitch, n.start_tick, n.duration_tick) for n in restored.notes] == [
        (n.pitch, n.start_tick, n.duration_tick) for n in lead.notes
    ]
    parsed = mido.MidiFile(file=BytesIO(data), charset="utf-8")
    assert sum(event.type == "lyrics" for track in parsed.tracks for event in track) == 3


def indexed_source():
    source = state_with_input()
    source.inputs.lyrics_text = "未选\n向前\n\n夢想"
    source.inputs.selected_lines = [1, 3]
    return source


def break_reference(raw, kind):
    target = raw["lyrics_alignment"][0]
    if kind == "line_index":
        target["line_index"] = 4
    elif kind == "unselected_line":
        target["line_index"] = 0
    elif kind == "unit_index":
        target["unit_index"] = 2
    else:
        lead = next(track for track in raw["tracks"] if track["family"] == "keyboard")
        target["note_indices"] = [len(lead["notes"])]


@pytest.mark.parametrize("kind", ["line_index", "unselected_line", "unit_index", "note_index"])
def test_bad_references_report_exact_original_lyric_and_zero_based_array_location(kind):
    source = indexed_source()
    raw = mock_plan(source, input_context(source))
    break_reference(raw, kind)
    before = deepcopy(raw)
    with pytest.raises(ModelError) as caught:
        compile_plan(raw, source, "a" * 32, False)
    error = caught.value
    assert error.code == "alignment_reference"
    assert error.diagnostic["alignment_index"] == 0
    assert error.diagnostic["kind"] == ("line_index" if kind == "unselected_line" else kind)
    assert "未采用候选" in str(error)
    if kind in ("line_index", "unselected_line"):
        assert error.diagnostic["selected_line_indices"] == [1, 3]
        assert ("不存在的第5行" if kind == "line_index" else "未选中的第1行") in str(error)
    elif kind == "unit_index":
        assert error.diagnostic["unit_count"] == 2
        assert "第2行第3个字词" in str(error) and "有效编号为0–1" in str(error)
    else:
        count = len(raw["tracks"][0]["notes"])
        assert error.diagnostic["note_index"] == error.diagnostic["lead_note_count"] == count
        assert "第2行第1个字词" in str(error)
        assert f"有效编号为0–{count - 1}" in str(error)
    assert raw == before  # Invalid references are never guessed or shifted.


def test_preserve_diagnostics_use_actual_prefix_and_filtered_keyboard_array():
    source = state_with_input(lyrics=True, melody=True)
    context = input_context(source)
    raw = mock_plan(source, context)
    baseline, _ = compile_plan(raw, source, "a" * 32, True)
    count = len(baseline.score.tracks[0].notes)
    raw["tracks"][0]["notes"].insert(0, [0, 77, 5, 90])
    raw["lyrics_alignment"][0]["note_indices"] = [count]
    with pytest.raises(ModelError) as caught:
        compile_plan(raw, source, "b" * 32, False)
    diagnostic = caught.value.diagnostic
    assert diagnostic["lead_note_count"] == count
    assert diagnostic["locked_keyboard_note_count"] == 3
    assert diagnostic["filtered_keyboard_note_count"] == 1


@pytest.mark.parametrize("full", [False, True])
def test_fake_http_receives_explicit_instrumental_and_original_zero_based_index_rules(full):
    source = indexed_source()
    context = input_context(source)
    if full:
        short, _ = compile_plan(mock_plan(source, context), source, "a" * 32, True)
        source, context = full_context(short, "b" * 32, "c" * 32)
    messages = model_messages(context)
    calls = []

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        system = payload["messages"][0]["content"]
        assert "允许无词的前奏、间奏和尾奏" in system
        assert "不要求所有键盘音符都有歌词" in system
        assert "unit_index从0开始" in system and "0..N-1" in system
        user = json.loads(payload["messages"][1]["content"])
        assert [line["line_index"] for line in user["selected_lyrics"]] == (
            [0, 1, 3] if full else [1, 3]
        )
        assert user["selected_lyrics"][-1]["units"] == ["夢", "想"]
        assert FAKE_KEY not in request.content.decode()
        return httpx.Response(200, json=response_for(mock_plan(source, context)))

    DeepSeekAdapter(real_settings(), httpx.MockTransport(handler)).generate(messages)
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["line_index", "unit_index", "note_index"])
def test_failed_job_saves_safe_reference_diagnostic_and_usage_without_overwriting_song(
    tmp_path, kind
):
    source = indexed_source()
    raw = mock_plan(source, input_context(source))
    raw["title"] = "RAW_MODEL_TITLE_MUST_NOT_APPEAR_IN_DIAGNOSTICS"
    break_reference(raw, kind)
    calls = []

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
        directory = tmp_path / "songs" / project["id"]
        header = (directory / "project.json").read_bytes()
        job = submit_legacy(client, project)
        item = wait_job(client, project["id"], job["id"])
        assert item["status"] == "failed" and item["error_code"] == "alignment_reference"
        assert item["request_count"] == 1 and len(calls) == 1
        assert item["diagnostic"]["kind"] == kind
        assert item["metadata"]["usage"]["total_tokens"] == 1200
        assert FAKE_KEY not in json.dumps(item, ensure_ascii=False)
        assert raw["title"] not in json.dumps(item, ensure_ascii=False)
        failed_dir = directory / "jobs" / "composition" / job["id"]
        persisted = json.loads((failed_dir / "job.json").read_text(encoding="utf-8"))
        assert persisted["diagnostic"] == item["diagnostic"]
        assert not (failed_dir / "plan.json").exists()
        assert not (failed_dir / "candidate.json").exists()
        assert (directory / "project.json").read_bytes() == header
        assert (
            client.get(f"/api/projects/{project['id']}").json()["revision"]["state"]
            == project["revision"]["state"]
        )
        assert (
            client.get(
                f"/api/projects/{project['id']}/generations/{job['id']}/candidate"
            ).status_code
            == 409
        )
