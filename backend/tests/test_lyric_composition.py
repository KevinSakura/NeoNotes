"""Lyric rewrite regressions, with injected configuration and no network requests."""

import json
from copy import deepcopy
from io import BytesIO

import httpx
import mido
import pytest
from fastapi.testclient import TestClient

from backend.app.assessment import validate_planning
from backend.app.composition import (
    compile_plan,
    lyric_units,
    model_messages,
    validate_alignment,
    validate_preservation,
)
from backend.app.deepseek import DeepSeekAdapter
from backend.app.lyric_composition import CONTRACT, lyric_context, mock_lyric_plan
from backend.app.main import create_app
from backend.app.midi import export_midi
from backend.app.model_settings import ModelError
from backend.app.models import RecordedNote, empty_song
from backend.tests.test_generation import (
    project_with_input,
    real_settings,
    response_for,
    submit,
    wait_job,
)

EXAMPLE_LYRICS = """去追逐著夢想吧   一切都將有所不同
到底是為什麼呢?
要用能夠拯救別人的力量
去與誰爭執著
說實在的
大家都是在同一個宇宙中出生於世
應該要能夠互相了解的
(你能夠做到什麼?)不管幾次
(你能夠完成什麼?)讓我們開始吧
去迎接 (變得更加溫柔)
嶄新的明天吧   (變得更加美好)
去追逐夢想吧     一切都將有所不同
無論何時我都用心在看著你
愛究竟存在於哪裡 從那個答案就可以知道
你一定能夠找到只屬於你的勇氣"""


def lyrics_source(text=EXAMPLE_LYRICS):
    source = empty_song()
    source.inputs.lyrics_text = text
    source.inputs.selected_lines = [0]  # An old selection is explicitly expanded on a new request.
    source.inputs.theme = "去追逐梦想，相互理解，迎接明天"
    source.inputs.emotion = "积极明亮的乐队演奏，歌词表达自然，主旋律便于试唱"
    return source


def compiled(text=EXAMPLE_LYRICS, change=None):
    source, context = lyric_context(lyrics_source(text))
    raw = mock_lyric_plan(context)
    if change:
        change(raw)
    state, plan = compile_plan(raw, source, "a" * 32, True, context=context)
    return state, plan, context


def test_actual_lyrics_full_coverage_without_provider_note_indices():
    state, plan, context = compiled()
    expected = [unit for line in EXAMPLE_LYRICS.split("\n") for unit in lyric_units(line)]
    assert len(expected) == 171
    assert [u.text for u in state.composition.lyrics_alignment] == expected
    assert state.inputs.selected_lines == list(range(15))
    assert state.composition.scope == "lyrics"
    assert state.score.seconds_at(state.score.end_tick) > 60
    assert context["target_seconds"] is None
    assert "note_indices" not in json.dumps(plan.model_dump())
    assert "lyrics_alignment" not in plan.model_dump()
    assert all(
        set(p) == {"line_index", "section", "motif", "cadence", "chords", "interlude_bars"}
        for p in plan.model_dump()["phrases"]
    )
    validate_alignment(state)
    validate_planning(state)
    lead = state.score.tracks[0]
    bound = {nid for unit in state.composition.lyrics_alignment for nid in unit.note_ids}
    assert len(lead.notes) > len(bound)  # Instrumental intro/outro are preserved.
    assert lead.notes[-1].start_tick + lead.notes[-1].duration_tick == state.score.end_tick
    assert "lyric-motifs-v1" in model_messages(context)[0]["content"]


def test_one_word_multiple_notes_and_no_fixed_minimum_duration():
    state, _, _ = compiled("夢！")
    assert len(state.composition.lyrics_alignment) == 1
    assert len(state.composition.lyrics_alignment[0].note_ids) == 2
    assert 1 < state.composition.target_seconds < 60
    validate_alignment(state)
    midi = mido.MidiFile(
        file=BytesIO(export_midi(state.score)),
        charset="utf-8",
    )
    assert [m.text for track in midi.tracks for m in track if m.type == "lyrics"] == ["夢"]
    assert {m.channel for track in midi.tracks for m in track if m.type == "note_on"} == {
        0,
        1,
        2,
        9,
    }


def test_blank_rows_and_english_words_keep_original_line_numbers():
    state, _, _ = compiled("夢\n\nWe can fly, don't stop!\n明天")
    assert state.inputs.selected_lines == [0, 2, 3]
    assert [(u.line_index, u.text) for u in state.composition.lyrics_alignment] == [
        (0, "夢"),
        (2, "We"),
        (2, "can"),
        (2, "fly"),
        (2, "don't"),
        (2, "stop"),
        (3, "明"),
        (3, "天"),
    ]
    validate_alignment(state)


def test_full_1024_units_and_64_lines_fit_declared_bounds():
    state, _, _ = compiled("\n".join(["词" * 16] * 64))
    assert len(state.composition.lyrics_alignment) == 1024
    assert 60 < state.composition.target_seconds <= 600
    assert sum(len(t.notes) for t in state.score.tracks) <= 5000
    assert len(state.composition.sections) <= 24
    validate_alignment(state)


@pytest.mark.parametrize(
    "text,code",
    [
        ("词" * 1025, "lyrics_size"),
        ("\n".join(["词"] * 65), "lyrics_size"),
        ("可唱\n?!", "lyric_empty_line"),
    ],
)
def test_input_overflow_or_punctuation_only_rejected_before_dispatch(text, code):
    with pytest.raises(ModelError) as caught:
        lyric_context(lyrics_source(text))
    assert caught.value.code == code
    assert "审核短曲" not in str(caught.value)


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda raw: raw["phrases"].pop(), "lyric_lines"),
        (lambda raw: raw["phrases"].reverse(), "lyric_lines"),
        (lambda raw: raw["phrases"][0].update(line_index=999), "lyric_lines"),
        (lambda raw: raw.update(lyrics_alignment=[{"note_indices": [57]}]), "lyric_schema"),
        (lambda raw: raw["phrases"][0].update(motif=[[[60, 0.1]]]), "lyric_schema"),
        (lambda raw: raw.update(bpm=97), "lyric_tempo"),
        (lambda raw: raw["instruments"][0].update(family="drums"), "lyric_instruments"),
    ],
)
def test_invalid_new_contract_is_rejected_without_guessing_or_retry(change, code):
    with pytest.raises(ModelError) as caught:
        compiled(change=change)
    assert caught.value.code == code


def test_overlong_motif_expansion_is_not_silently_truncated():
    def slow(raw):
        for phrase in raw["phrases"]:
            phrase["motif"] = [[[60, 4]]]

    with pytest.raises(ModelError) as caught:
        compiled("词" * 1024, slow)
    assert caught.value.code == "lyric_duration"


def test_locked_instrumental_prefix_does_not_enter_lyric_numbering():
    source = lyrics_source("梦想向前\n明日更好")
    source.inputs.melody_mode = "preserve"
    source.inputs.recording = [
        RecordedNote(
            pitch=40,
            velocity=53,
            start_seconds=0.123,
            duration_seconds=0.537,
            program=4,
            family="keyboard",
            track_id=source.score.tracks[0].id,
        )
    ]
    source, context = lyric_context(source)
    raw = mock_lyric_plan(context)
    state, _ = compile_plan(raw, source, "b" * 32, True, context=context)
    assert state.score.tracks[0].program == 4
    assert state.score.tracks[0].notes[0].pitch == 40
    assert state.score.tracks[0].notes[0].velocity == 53
    assert state.composition.reference_lock.filtered_model_notes == 0
    assert state.score.tracks[0].notes[0].id not in {
        nid for u in state.composition.lyrics_alignment for nid in u.note_ids
    }
    validate_preservation(state)
    validate_alignment(state)


def test_new_lyrics_requests_apply_modify_and_review_without_short_gate(tmp_path):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        context = json.loads(body["messages"][1]["content"])
        calls.append(body)
        return httpx.Response(200, json=response_for(mock_lyric_plan(context)))

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
        project = project_with_input(client, lyrics_source())
        original_revision = project["current_revision"]
        for operation in ("generate", "modify"):
            job = submit(
                client,
                project,
                operation=operation,
                instruction="整体稍轻快，保留全部歌词" if operation == "modify" else "",
                confirm_real_call=True,
            )
            item = wait_job(client, project["id"], job["id"])
            assert item["status"] == "completed", item
            assert item["contract"] == CONTRACT and item["scope"] == "lyrics"
            assert item["request_count"] == 1 and item["max_output_tokens"] == 32768
            project = client.post(
                f"/api/projects/{project['id']}/generations/{job['id']}/apply",
                json={"base_revision": project["current_revision"]},
            ).json()
            review = client.get(f"/api/projects/{project['id']}/review").json()
            assert review["valid"], review
            assert not review["full_song_enabled"]
            assert "全文歌词作品" in review["full_song_reason"]
            assert review["review"] is None
            assert len(project["revision"]["state"]["composition"]["lyrics_alignment"]) == 171
        assert len(calls) == 2
        assert all(body["max_tokens"] == 32768 for body in calls)
        assert project["current_revision"] != original_revision
        # Input changes still invalidate review, even though alignment is locally generated.
        state = deepcopy(project["revision"]["state"])
        state["inputs"]["selected_lines"] = [0]
        project = client.put(
            f"/api/projects/{project['id']}",
            json={"base_revision": project["current_revision"], "state": state},
        ).json()
        assert not client.get(f"/api/projects/{project['id']}/review").json()["valid"]


@pytest.mark.parametrize("mode", ["--mock", "--allow-real"])
def test_acceptance_cli_never_reads_configuration_as_lyrics(tmp_path, monkeypatch, mode):
    import sys

    from scripts import check_lyric_composition

    fake_config = tmp_path / "fake-config.env"
    fake_config.write_text("not lyric input", encoding="utf-8")
    monkeypatch.setattr(
        check_lyric_composition,
        "load_model_settings",
        lambda: pytest.fail("Rejected inputs must not load configuration"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_lyric_composition.py",
            mode,
            "--lyrics-file",
            str(fake_config),
            *(["--max-requests", "2"] if mode == "--allow-real" else []),
        ],
    )
    with pytest.raises(SystemExit) as caught:
        check_lyric_composition.main()
    assert caught.value.code == 2


def test_real_acceptance_failure_consumes_one_attempt_and_skips_dependent_modify(
    tmp_path, monkeypatch
):
    import sys

    from backend.app.generation import GenerationManager
    from scripts import check_lyric_composition

    path = tmp_path / "lyrics.txt"
    path.write_text("向前\n明天", encoding="utf-8")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_for({"format": "invalid isolated fixture"}))

    monkeypatch.setattr(check_lyric_composition, "ROOT", tmp_path)
    monkeypatch.setattr(check_lyric_composition, "load_model_settings", real_settings)
    monkeypatch.setattr(
        check_lyric_composition,
        "GenerationManager",
        lambda store, resource, source: GenerationManager(
            store,
            resource,
            source,
            adapter_factory=lambda settings: DeepSeekAdapter(
                settings, httpx.MockTransport(handler)
            ),
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_lyric_composition.py",
            "--allow-real",
            "--max-requests",
            "2",
            "--lyrics-file",
            str(path),
        ],
    )
    assert check_lyric_composition.main() == 1
    summary = json.loads(
        next((tmp_path / ".cache/lyric-composition-checks").glob("*/summary.json")).read_text(
            encoding="utf-8"
        )
    )
    assert [c["status"] for c in summary["cases"]] == ["failed", "skipped"]
    assert summary["actual_requests"] == summary["reserved_attempts"] == len(calls) == 1
    assert summary["cases"][0]["error_code"] == "lyric_schema"
    assert "job_id" not in summary["cases"][1]
