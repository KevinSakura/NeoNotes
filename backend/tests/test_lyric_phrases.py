"""Offline v2 contracts, transactions and simulated request lifecycle; no inference."""

import json
import time
from copy import deepcopy
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.composition import (
    compile_plan,
    mock_plan,
    validate_alignment,
    validate_preservation,
)
from backend.app.deepseek import DeepSeekAdapter
from backend.app.lyric_group_edit import GroupEditRequest, calculate, draft_hash
from backend.app.lyric_phrases import (
    CONTRACT,
    compile_phrases,
    identity,
    mock_complete,
    mock_global,
    mock_phrase,
    phrase_context,
    validate_phrases,
)
from backend.app.main import create_app
from backend.app.model_settings import ModelError, ModelSettings
from backend.app.models import Note, RecordedNote, SongState, Tempo, empty_song, new_id
from backend.app.vocal_score import VocalSettings, convert_score
from backend.tests.test_generation import real_settings


def source(text="让歌声陪我到远方"):
    s = empty_song()
    s.inputs.lyrics_text = text
    s.inputs.selected_lines = [i for i, row in enumerate(text.split("\n")) if row.strip()]
    return s


def compiled(text="让歌声陪我到远方"):
    s, context = phrase_context(source(text))
    raw = mock_complete(context)
    result, _ = compile_phrases(raw, s, new_id(), True, context)
    return result


def nonrepeating_1024():
    letters = "".join(chr(0x4E00 + i) for i in range(1024))
    rows, offset = [], 0
    for i in range(64):
        length = [20, 12, 18, 14][i % 4]
        rows.append(letters[offset:offset + length])
        offset += length
    assert offset == 1024 and len(set(letters)) == 1024
    return "\n".join(rows)


def wait(client, project, job):
    for _ in range(1000):
        value = client.get(f"/api/projects/{project}/generations/{job}").json()
        if value["status"] not in ("queued", "running", "validating", "cancelling"):
            return value
        time.sleep(.01)
    raise AssertionError("Isolated task timed out.")


def save(client, text):
    p = client.post("/api/projects", json={"name": "隔离乐句测试"}).json()
    p = client.put(f"/api/projects/{p['id']}", json={"base_revision": p["current_revision"],
                   "state": source(text).model_dump(), "label": "测试输入"}).json()
    return p


def start(client, p, **fields):
    offer = client.post(f"/api/projects/{p['id']}/generations/phrase-budget",
                        json={"base_revision": p["current_revision"]}).json()
    r = client.post(f"/api/projects/{p['id']}/generations", json={
        "base_revision": p["current_revision"], "contract": CONTRACT,
        "confirm_real_call": True, "budget_hash": offer["hash"], **fields})
    assert r.status_code == 202, r.text
    return wait(client, p["id"], r.json()["id"])


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "data", model_settings=ModelSettings(mock=True))) as c:
        c.headers["X-Session-Token"] = c.get("/api/session").json()["token"]
        yield c


def test_identity_keeps_punctuation_blank_lines_codepoint_spans_and_snapshot():
    text = "\n晨风，轻轻！\n\nWhy yes　远方。\n"
    numbered = identity(text)
    assert [u.line_index for u in numbered.units] == [1] * 4 + [3] * 4
    assert [u.uid for u in numbered.units] == list(range(8))
    assert all(text[u.source_span[0]:u.source_span[1]] == u.text for u in numbered.units)
    assert numbered == identity(text)
    assert numbered.lyrics_hash != identity(text.replace("！", "。")).lyrics_hash


@pytest.mark.parametrize("text", ["晨风轻轻吹过山岗", "当晚风穿过安静的街巷我仍记得你微笑的模样",
                                  "让歌声陪我到远方", "\n晨风，轻轻。\n\n让歌声陪我到远方！"])
def test_short_long_and_punctuation_compile_complete_real_ids(text):
    s = compiled(text)
    assert s.schema_version == 2
    assert s.composition.arrangement == CONTRACT
    assert len(s.composition.lyrics_alignment) == len(identity(text).units)
    if len(identity(text).units) > 16:
        assert len(s.composition.v2.phrase_layout) > 1
    ids = {n.id for t in s.score.tracks for n in t.notes}
    assert all(n in ids for u in s.composition.lyrics_alignment for n in u.note_ids)
    assert [t.channel for t in s.score.tracks] == [0, 9, 1, 2]
    assert s.score.tracks[1].is_drum
    validate_phrases(s)


def test_single_long_note_and_melisma_coexist_without_adding_words():
    s, ctx = phrase_context(source("让歌声陪我远行"))
    raw = mock_complete(ctx)
    p = raw["phrases"][0]
    p["groups"][2]["notes"] = [[64, 4]]
    p["groups"][-1]["rest_after_slots"] -= 2
    result, _ = compile_phrases(raw, s, new_id(), True, ctx)
    assert len(result.composition.lyrics_alignment[2].note_ids) == 1
    assert len(result.composition.lyrics_alignment[-1].note_ids) == 2
    by = {n.id: n for n in result.score.tracks[0].notes}
    assert by[result.composition.lyrics_alignment[2].note_ids[0]].duration_tick == 480


@pytest.mark.parametrize("change", ["missing", "duplicate", "reorder", "line", "words",
                                   "rest", "duration", "pitch", "fraction", "gap", "tempo"])
def test_bad_contracts_are_rejected_not_repaired(change):
    s, ctx = phrase_context(source())
    raw = mock_complete(ctx)
    p = raw["phrases"][0]
    if change == "missing":
        p["groups"].pop()
    elif change == "duplicate":
        p["groups"][1]["uid"] = 0
    elif change == "reorder":
        p["groups"][0], p["groups"][1] = p["groups"][1], p["groups"][0]
    elif change == "line":
        p["line_index"] = 9
    elif change == "words":
        p["word_groups"] = [[0]]
    elif change == "rest":
        p["groups"][-1]["rest_after_slots"] = 0
    elif change == "duration":
        p["groups"][0]["notes"][0][1] = 0
    elif change == "pitch":
        p["groups"][0]["notes"][0][0] = 85
    elif change == "fraction":
        p["groups"][0]["notes"][0][1] = 1.5
    elif change == "gap":
        raw["harmony"][0]["start_slot"] = 16
    else:
        raw["bpm"] = 161
    with pytest.raises(ModelError):
        compile_phrases(raw, s, new_id(), True, ctx)
    assert s.schema_version == 1 and not s.composition


def test_one_slot_at_fast_tempo_is_not_a_short_duration_exemption():
    s, ctx = phrase_context(source())
    raw = mock_complete(ctx)
    raw["bpm"] = 160
    raw["phrases"][0]["groups"][0]["notes"] = [[60, 1]]
    raw["phrases"][0]["groups"][-1]["rest_after_slots"] += 1
    with pytest.raises(ModelError, match="0.12"):
        compile_phrases(raw, s, new_id(), True, ctx)


def test_preserved_prefix_is_exact_and_unworded():
    s = source()
    s.inputs.melody_mode = "preserve"
    s.inputs.recording = [RecordedNote(pitch=60, start_seconds=.123456, duration_seconds=.321234,
                                      velocity=63, track_id=s.score.tracks[0].id, family="keyboard", program=4)]
    s, ctx = phrase_context(s)
    result, _ = compile_phrases(mock_complete(ctx), s, new_id(), True, ctx)
    validate_preservation(result)
    assert result.score.tracks[0].program == 4 and result.score.tracks[0].notes[0].velocity == 63
    assert result.score.tracks[0].notes[0].id not in result.composition.lyrics_alignment[0].note_ids
    raw = mock_complete(ctx)
    raw["phrases"][0]["start_slot"] = 0
    with pytest.raises(ModelError):
        compile_phrases(raw, s, new_id(), True, ctx)


def test_1024_nonrepeated_variable_length_full_text_and_note_budget():
    s = compiled(nonrepeating_1024())
    assert len(s.composition.lyrics_alignment) == 1024
    assert len(s.composition.v2.phrase_layout) > 64
    assert sum(len(t.notes) for t in s.score.tracks) <= 5000
    assert s.composition.target_seconds <= 600
    assert len(s.composition.chords) <= 256
    with pytest.raises((ModelError, ValueError)):
        compiled(nonrepeating_1024() + "字")


def test_exact_ten_minutes_and_sparse_reduction_preserve_all_bound_notes():
    s, ctx = phrase_context(source("方"))
    raw = mock_complete(ctx)
    raw.update(bpm=160, total_slots=6400, accompaniment="driving")
    raw["sections"][-1]["length_slots"] = 6400 - raw["sections"][-1]["start_slot"]
    raw["harmony"] = [{"start_slot": 0, "length_slots": 6400, "pitches": [60, 64, 67]}]
    result, _ = compile_phrases(raw, s, new_id(), True, ctx)
    assert result.composition.target_seconds == 600
    assert result.composition.v2.accompaniment == "sparse"
    assert len(result.composition.lyrics_alignment) == 1
    assert sum(len(t.notes) for t in result.score.tracks) <= 5000
    raw["total_slots"] += 16
    raw["sections"][-1]["length_slots"] += 16
    raw["harmony"][0]["length_slots"] += 16
    with pytest.raises((ModelError, ValueError)):
        compile_phrases(raw, s, new_id(), True, ctx)


def test_more_than_5000_minimum_notes_rejects_instead_of_dropping_lyrics():
    s, ctx = phrase_context(source("\n".join("晨风轻轻吹过山岗让歌声陪我到远方" for _ in range(20))))
    raw = mock_complete(ctx)
    raw["bpm"] = 120
    cursor = 16
    for p in raw["phrases"]:
        p.update(start_slot=cursor, length_slots=208)
        for g in p["groups"]:
            g["notes"] = [[60 + i % 3, 1] for i in range(12)]
            g["rest_after_slots"] = 0
        p["groups"][-1]["rest_after_slots"] = 208 - sum(len(g["notes"]) for g in p["groups"])
        cursor += 208
    raw.update(total_slots=cursor + 16, harmony=[{"start_slot": 0, "length_slots": cursor + 16, "pitches": [60, 64, 67]}],
               sections=[{"name": "前奏", "start_slot": 0, "length_slots": 16},
                         {"name": "乐句", "start_slot": 16, "length_slots": cursor - 16},
                         {"name": "尾奏", "start_slot": cursor, "length_slots": 16}])
    with pytest.raises(ModelError, match="5000"):
        compile_phrases(raw, s, new_id(), True, ctx)


def edit_body(s, **params):
    return GroupEditRequest(base_revision="a" * 32, draft_hash=draft_hash(s), transaction_id=new_id(),
                            state=s, line_index=0, unit_index=0, action="add", **params)


def test_fixed_group_edit_ids_duration_and_all_other_notes_unchanged():
    s = compiled()
    original = s.model_dump()
    first = s.composition.lyrics_alignment[0].note_ids[0]
    result, impact = calculate(edit_body(s))
    assert s.model_dump() == original
    group = result.composition.lyrics_alignment[0]
    assert group.note_ids[0] == first and len(group.note_ids) == 2
    others = {n.id: n.model_dump() for t in s.score.tracks for n in t.notes if n.id != first}
    assert {n.id: n.model_dump() for t in result.score.tracks for n in t.notes if n.id in others} == others
    assert impact["later_music_delta_ticks"] == 0
    assert result.composition.target_seconds == s.composition.target_seconds
    assert len([m for m in result.score.midi_meta if m.type == "lyrics"]) == len(s.composition.lyrics_alignment)


def test_simplify_keeps_chosen_id_and_removes_extra_notes():
    s = compiled()
    unit = s.composition.lyrics_alignment[-1]
    body = GroupEditRequest(base_revision="a" * 32, draft_hash=draft_hash(s), transaction_id=new_id(),
                            state=s, line_index=0, unit_index=unit.unit_index, action="simplify", keep="last")
    result, _ = calculate(body)
    assert result.composition.lyrics_alignment[-1].note_ids == [unit.note_ids[-1]]
    assert unit.note_ids[0] not in {n.id for t in result.score.tracks for n in t.notes}


@pytest.mark.parametrize("conflict", ["manual", "crossing", "tempo", "prefix"])
def test_restricted_ripple_explicitly_blocks_unsafe_regions(conflict):
    s = compiled("晨风轻轻吹过山岗\n让歌声陪我到远方")
    p = s.composition.v2.phrase_layout[0]
    if conflict == "manual":
        s.score.tracks[2].notes[4].pitch += 1
    elif conflict == "crossing":
        s.score.tracks[0].notes.append(Note(pitch=73, start_tick=p.end_tick - 120, duration_tick=240))
    elif conflict == "tempo":
        s.score.tempo_map.append(Tempo(tick=p.end_tick, microseconds_per_beat=500000))
    else:
        s.composition.v2.prefix_end_tick = p.start_tick + 1
    with pytest.raises(ModelError):
        calculate(edit_body(s, time_mode="ripple", duration_ticks=960))


def test_ripple_consumes_rest_then_moves_all_later_music_by_whole_bars():
    s = compiled("晨风轻轻吹过山岗\n让歌声陪我到远方")
    p, next_p = s.composition.v2.phrase_layout[:2]
    result, impact = calculate(edit_body(s, time_mode="ripple", duration_ticks=360))
    assert impact["following_lyrics_delta_ticks"] == 120 and not impact["later_music_delta_ticks"]
    result, impact = calculate(edit_body(s, time_mode="ripple", duration_ticks=1920))
    shift = impact["later_music_delta_ticks"]
    assert shift > 0 and shift % 1920 == 0
    assert result.composition.v2.phrase_layout[1].start_tick == next_p.start_tick + shift
    for original_track, new_track in zip(s.score.tracks, result.score.tracks, strict=True):
        by = {n.id: n for n in new_track.notes}
        for old in original_track.notes:
            if old.start_tick >= p.end_tick:
                assert by[old.id].start_tick == old.start_tick + shift
    assert result.score.end_tick == s.score.end_tick + shift
    validate_alignment(result)


def test_v1_fixed_edit_and_old_contract_parsing_remain_available():
    s = source()
    from backend.app.lyric_composition import lyric_context
    s, ctx = lyric_context(s)
    v1, _ = compile_plan(mock_plan(s, ctx), s, new_id(), True, context=ctx)
    result, _ = calculate(edit_body(v1))
    assert result.schema_version == 1 and result.composition.arrangement == "lyric-motifs-v1"
    with pytest.raises(ModelError):
        calculate(edit_body(v1, time_mode="ripple"))
    with pytest.raises(ValueError):
        SongState.model_validate({**compiled().model_dump(), "schema_version": 1})


def test_v2_vocal_protocol_keeps_every_note_and_absolute_tempo_without_inference():
    s = compiled("当晚风穿过安静的街巷我仍记得你微笑的模样")
    second = s.composition.v2.phrase_layout[1]
    s.score.tempo_map.append(Tempo(tick=second.start_tick, microseconds_per_beat=500000))
    request, backing, removed = convert_score(s, VocalSettings())
    assert request["protocol"] == "neonote-vocal-v2" and "lines" not in request
    assert len(request["phrases"]) == 2 and len(request["tempos"]) == 2
    assert sum(len(u["notes"]) for p in request["phrases"] for u in p["units"]) == len(removed)
    assert not set(removed).intersection(n.id for n in backing.tracks[0].notes)
    assert backing.tracks[1:] == s.score.tracks[1:]
    for p in request["phrases"]:
        for u in p["units"]:
            for n in u["notes"]:
                assert n["start_seconds"] == s.score.seconds_at(n["position"])


def test_mock_short_and_long_parent_tasks_only_publish_whole_candidates(client):
    for text, limit in [("晨风轻轻吹过山岗", 1), (nonrepeating_1024(), 17)]:
        p = save(client, text)
        job = start(client, p)
        assert job["status"] == "completed", job
        assert job["reserved_attempts"] == limit and job["request_count"] == 0
        assert job["simulated_requests"] <= limit
        candidate = client.get(f"/api/projects/{p['id']}/generations/{job['id']}/candidate").json()
        assert candidate["schema_version"] == 2
        assert len(candidate["composition"]["lyrics_alignment"]) == len(identity(text).units)
        assert client.get(f"/api/projects/{p['id']}").json()["revision"]["state"] == p["revision"]["state"]


def test_group_preview_apply_undo_save_and_source_receipts_are_atomic(client):
    p = save(client, "晨风轻轻吹过山岗\n让歌声陪我到远方")
    job = start(client, p)
    p = client.post(f"/api/projects/{p['id']}/generations/{job['id']}/apply",
                    json={"base_revision": p["current_revision"]}).json()
    s = SongState.model_validate(p["revision"]["state"])
    body = edit_body(s, time_mode="ripple", duration_ticks=1920).model_copy(update={"base_revision": p["current_revision"]})
    url = f"/api/projects/{p['id']}/lyric-groups"
    preview = client.post(url, json=body.model_dump())
    assert preview.status_code == 200, preview.text
    assert preview.json()["preview"] and preview.json()["saved"] is False
    assert client.get(f"/api/projects/{p['id']}").json() == p
    applied = client.post(url, json=body.model_copy(update={"preview": False}).model_dump())
    assert applied.status_code == 200, applied.text
    assert applied.json()["state"] == preview.json()["state"]
    changed = applied.json()["state"]
    saved = client.put(f"/api/projects/{p['id']}", json={"base_revision": p["current_revision"], "state": changed}).json()
    review = client.get(f"/api/projects/{p['id']}/review").json()
    assert review["valid"], review
    undone = client.post(f"/api/projects/{p['id']}/undo", json={"base_revision": saved["current_revision"]}).json()
    assert undone["revision"]["state"] == p["revision"]["state"]
    redone = client.post(f"/api/projects/{p['id']}/redo", json={"base_revision": undone["current_revision"]}).json()
    assert redone["revision"]["state"] == saved["revision"]["state"]
    assert client.post(url, json=body.model_dump()).status_code == 409
    forged = deepcopy(changed)
    forged["composition"]["target_seconds"] += 1
    client.app.state.generator.validate_source(p["id"], SongState.model_validate(changed))
    with pytest.raises(ModelError):
        client.app.state.generator.validate_source(p["id"], SongState.model_validate(forged))


def transport_factory(log, controls=None):
    """Only in-memory HTTPX MockTransport. No socket, .env or provider requests."""
    controls = controls if controls is not None else {}

    def handle(request):
        payload = json.loads(request.content)
        ctx = json.loads(payload["messages"][-1]["content"])
        stage = ctx["stage"]
        log.append((stage, ctx.get("block_index")))
        if controls.get("entered") and stage == "global":
            controls["entered"].set()
            assert controls["release"].wait(10)
        if stage == "block" and ctx["block_index"] == controls.get("fail_index"):
            raise httpx.ReadTimeout("isolated timeout", request=request)
        if stage == "global":
            raw = mock_global(ctx)
        elif stage == "block":
            raw = {"format": "lyric-phrases-block-v2", "plan_hash": ctx["plan_hash"],
                   "block_index": ctx["block_index"], "phrases": [mock_phrase(p) for p in ctx["assigned_phrases"]]}
        else:
            raw = mock_complete(ctx)
        return httpx.Response(200, json={"model": "fake-deepseek", "id": "offline-1",
                             "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                             "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(raw)}}]})
    return lambda settings: DeepSeekAdapter(settings, httpx.MockTransport(handle))


def test_block_timeout_stops_dispatch_and_explicit_retry_reuses_verified_prefix(tmp_path):
    log, controls = [], {"fail_index": 1}
    with TestClient(create_app(tmp_path / "data", model_settings=real_settings(),
                              adapter_factory=transport_factory(log, controls))) as c:
        c.headers["X-Session-Token"] = c.get("/api/session").json()["token"]
        p = save(c, nonrepeating_1024())
        failed = start(c, p)
        assert failed["status"] == "failed" and failed["error_code"] == "timeout"
        assert log == [("global", None), ("block", 0), ("block", 1)]
        assert failed["request_count"] == 3 and failed["reserved_attempts"] == 17
        assert c.get(f"/api/projects/{p['id']}/generations/{failed['id']}/candidate").status_code == 409
        root = c.app.state.generator.directory(p["id"], failed["id"])
        assert not (root / "candidate.json").exists()
        controls["fail_index"] = None
        offer = c.post(f"/api/projects/{p['id']}/generations/{failed['id']}/retry-budget",
                       json={"base_revision": p["current_revision"]}).json()
        assert offer["reused_blocks"] == 1 and offer["request_limit"] < 17
        response = c.post(f"/api/projects/{p['id']}/generations/{failed['id']}/retry",
                          json={"base_revision": p["current_revision"], "confirm_real_call": True,
                                "budget_hash": offer["hash"]})
        assert response.status_code == 202, response.text
        completed = wait(c, p["id"], response.json()["id"])
        assert completed["status"] == "completed", completed
        assert log[3] == ("block", 1)
        assert log.count(("block", 0)) == 1
        assert c.get(f"/api/projects/{p['id']}/generations/{failed['id']}").json()["status"] == "failed"
        (root / "blocks/000/result.json").write_text("{}", encoding="utf-8")
        assert c.post(f"/api/projects/{p['id']}/generations/{failed['id']}/retry-budget",
                      json={"base_revision": p["current_revision"]}).status_code == 409


@pytest.mark.parametrize("mode", ["cancel", "version"])
def test_cancel_and_version_change_stop_after_inflight_simulated_request(tmp_path, mode):
    log, controls = [], {"entered": Event(), "release": Event()}
    with TestClient(create_app(tmp_path / "data", model_settings=real_settings(),
                              adapter_factory=transport_factory(log, controls))) as c:
        c.headers["X-Session-Token"] = c.get("/api/session").json()["token"]
        p = save(c, nonrepeating_1024())
        offer = c.post(f"/api/projects/{p['id']}/generations/phrase-budget",
                       json={"base_revision": p["current_revision"]}).json()
        response = c.post(f"/api/projects/{p['id']}/generations", json={
            "base_revision": p["current_revision"], "contract": CONTRACT,
            "confirm_real_call": True, "budget_hash": offer["hash"]})
        assert response.status_code == 202
        job = response.json()
        assert controls["entered"].wait(5)
        if mode == "cancel":
            cancelled = c.post(f"/api/projects/{p['id']}/generations/{job['id']}/cancel")
            assert cancelled.json()["status"] == "cancelling"
            assert c.request("DELETE", f"/api/projects/{p['id']}",
                             json={"base_revision": p["current_revision"]}).status_code == 409
        else:
            state = deepcopy(p["revision"]["state"])
            state["inputs"]["theme"] = "另一版"
            assert c.put(f"/api/projects/{p['id']}", json={"base_revision": p["current_revision"],
                         "state": state}).status_code == 200
        controls["release"].set()
        finished = wait(c, p["id"], job["id"])
        assert finished["status"] == ("cancelled" if mode == "cancel" else "failed")
        assert log == [("global", None)]
        assert finished["request_count"] == 1
        assert c.get(f"/api/projects/{p['id']}/generations/{job['id']}/candidate").status_code == 409


def test_budget_consent_is_separate_from_legacy_single_request_consent(tmp_path):
    log = []
    with TestClient(create_app(tmp_path / "data", model_settings=real_settings(),
                              adapter_factory=transport_factory(log))) as c:
        c.headers["X-Session-Token"] = c.get("/api/session").json()["token"]
        p = save(c, "晨风轻轻吹过山岗")
        r = c.post(f"/api/projects/{p['id']}/generations", json={
            "base_revision": p["current_revision"], "contract": CONTRACT, "confirm_real_call": True})
        assert r.status_code == 403 and r.json()["code"] == "phrase_budget_consent"
        assert not log
        job = start(c, p)
        assert job["status"] == "completed" and job["request_count"] == 1


def test_exact_5000_note_candidate_and_5001_rejection():
    text = "\n".join("".join(chr(0x4E00 + row * 64 + i) for i in range(64)) for row in range(8))
    s, ctx = phrase_context(source(text))
    raw = mock_complete(ctx)
    phrases = []
    for row in range(8):
        groups = [{"uid": row * 64 + i, "notes": [[60 + j % 3, 1] for j in range(4 if i < 47 else 3)],
                   "rest_after_slots": 0, "stress": "none"} for i in range(64)]
        groups[-1]["rest_after_slots"] = 17
        phrases.append({"phrase_id": "limit-" + str(row), "line_index": row, "start_slot": 16 + row * 256,
                        "length_slots": 256, "pickup_slots": 0, "word_groups": [[g["uid"]] for g in groups], "groups": groups})
    raw.update(bpm=120, total_slots=4800, phrases=phrases, accompaniment="sparse",
               harmony=[{"start_slot": 0, "length_slots": 4800, "pitches": [60, 64, 67]}],
               sections=[{"name": "前奏", "start_slot": 0, "length_slots": 16},
                         {"name": "乐句", "start_slot": 16, "length_slots": 2048},
                         {"name": "尾奏", "start_slot": 2064, "length_slots": 2736}])
    result, _ = compile_phrases(raw, s, new_id(), True, ctx)
    assert sum(len(t.notes) for t in result.score.tracks) == 5000
    assert result.composition.target_seconds == 600
    raw["phrases"][0]["groups"][0]["notes"].append([62, 1])
    raw["phrases"][0]["groups"][-1]["rest_after_slots"] -= 1
    with pytest.raises(ModelError, match="5000"):
        compile_phrases(raw, s, new_id(), True, ctx)


def test_later_manual_accompaniment_stays_manual_after_an_earlier_ripple():
    s = compiled("晨风轻轻吹过山岗\n让歌声陪我到远方")
    next_p = s.composition.v2.phrase_layout[1]
    edited = next(n for n in s.score.tracks[2].notes if n.start_tick >= next_p.start_tick)
    edited.pitch += 1
    original_id, pitch = edited.id, edited.pitch
    shifted, impact = calculate(edit_body(s, time_mode="ripple", duration_ticks=1920))
    assert impact["later_music_bars"] == 1
    assert next(n for n in shifted.score.tracks[2].notes if n.id == original_id).pitch == pitch
    body = edit_body(shifted, time_mode="ripple", duration_ticks=960).model_copy(update={"line_index": 1})
    with pytest.raises(ModelError, match="手动"):
        calculate(body)


def test_integer_ratio_distribution_has_no_tick_drift():
    from backend.app.lyric_composition import lyric_context
    s, ctx = lyric_context(source())
    old, _ = compile_plan(mock_plan(s, ctx), s, new_id(), True, context=ctx)
    body = edit_body(old).model_copy(update={"action": "set", "unit_index": 7, "pitches": [60, 62, 60], "weights": [2, 3, 2]})
    edited, _ = calculate(body)
    unit = edited.composition.lyrics_alignment[7]
    by_id = {n.id: n for n in edited.score.tracks[0].notes}
    assert [by_id[n].duration_tick for n in unit.note_ids] == [274, 412, 274]
    assert sum(by_id[n].duration_tick for n in unit.note_ids) == 960
    assert "v2" not in old.model_dump()["composition"]


def test_restarted_v2_task_is_interrupted_without_dispatch_and_can_retry(client):
    p = save(client, nonrepeating_1024())
    job = start(client, p)
    manager = client.app.state.generator
    manager.futures[job["id"]].result(timeout=5)
    root = manager.directory(p["id"], job["id"])
    record = manager.store.read_json(root / "job.json")
    record["status"] = "running"
    record["blocks"][-1]["status"] = "running"
    manager.store.write_json(root / "job.json", record)
    interrupted = client.get(f"/api/projects/{p['id']}/generations/{job['id']}").json()
    assert interrupted["status"] == "interrupted" and interrupted["blocks"][-1]["status"] == "interrupted"
    offer = client.post(f"/api/projects/{p['id']}/generations/{job['id']}/retry-budget",
                        json={"base_revision": p["current_revision"]})
    assert offer.status_code == 200
    changed = deepcopy(p["revision"]["state"])
    changed["inputs"]["theme"] = "改稿"
    saved = client.put(f"/api/projects/{p['id']}", json={"base_revision": p["current_revision"], "state": changed}).json()
    assert client.post(f"/api/projects/{p['id']}/generations/{job['id']}/retry-budget",
                       json={"base_revision": saved["current_revision"]}).status_code == 409


def test_v2_task_and_edit_receipts_survive_whole_song_trash_restore(client):
    p = save(client, "晨风轻轻吹过山岗")
    job = start(client, p)
    p = client.post(f"/api/projects/{p['id']}/generations/{job['id']}/apply",
                    json={"base_revision": p["current_revision"]}).json()
    body = edit_body(SongState.model_validate(p["revision"]["state"])).model_copy(update={"base_revision": p["current_revision"], "preview": False})
    edited = client.post(f"/api/projects/{p['id']}/lyric-groups", json=body.model_dump()).json()["state"]
    p = client.put(f"/api/projects/{p['id']}", json={"base_revision": p["current_revision"], "state": edited}).json()
    manager = client.app.state.generator
    directory = manager.store.directory(p["id"])
    import hashlib
    before = {f.relative_to(directory).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest() for f in directory.rglob("*") if f.is_file()}
    assert client.request("DELETE", f"/api/projects/{p['id']}", json={"base_revision": p["current_revision"]}).status_code == 200
    assert client.post(f"/api/trash/{p['id']}/restore").status_code == 200
    assert {f.relative_to(directory).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest() for f in directory.rglob("*") if f.is_file()} == before
    assert client.get(f"/api/projects/{p['id']}/review").json()["valid"]


def test_design_examples_have_valid_exact_container_arithmetic():
    from backend.app.lyric_phrases import design_examples
    for key in ("complete", "split_long_line"):
        case = design_examples()[key]
        text = "让歌声陪我远行" if key == "complete" else "当晚风穿过安静的街巷我仍记得你微笑的模样"
        s, ctx = phrase_context(source(text))
        result, _ = compile_phrases(case["output"], s, new_id(), True, ctx)
        assert len(result.composition.lyrics_alignment) == len(identity(text).units)
    p = design_examples()["word_group_pause"]["phrase"]
    assert sum(d for g in p["groups"] for _, d in g["notes"]) + sum(g["rest_after_slots"] for g in p["groups"]) == 32


def test_old_vocal_readings_reject_changed_lyric_identity_without_inference(client):
    p = save(client, "晨风轻轻吹过山岗")
    job = start(client, p)
    p = client.post(f"/api/projects/{p['id']}/generations/{job['id']}/apply",
                    json={"base_revision": p["current_revision"]}).json()
    url = f"/api/projects/{p['id']}/vocal-settings"
    loaded = client.get(url).json()
    state = deepcopy(p["revision"]["state"])
    state["inputs"]["lyrics_text"] = state["inputs"]["lyrics_text"].replace("晨", "春", 1)
    changed = client.put(f"/api/projects/{p['id']}", json={"base_revision": p["current_revision"], "state": state}).json()
    response = client.post(url, json={"base_revision": changed["current_revision"], "settings": VocalSettings().model_dump(),
                                     "lyrics_identity_hash": loaded["lyrics_identity_hash"]})
    assert response.status_code == 409 and "歌词身份" in response.json()["detail"]
    assert client.get(f"/api/projects/{p['id']}/vocals").json()["items"] == []


def test_saved_group_edits_mark_old_vocal_metadata_stale_and_keep_success_record(client):
    # Metadata stub only, never audio or singing acceptance.
    from backend.tests.test_vocals import archived_job, config
    p = save(client, "晨风轻轻吹过山岗")
    job = start(client, p)
    p = client.post(f"/api/projects/{p['id']}/generations/{job['id']}/apply",
                    json={"base_revision": p["current_revision"]}).json()
    settings = config(client, p).json()
    record = archived_job(client, p, settings["hash"])
    path = client.app.state.vocals.directory(p["id"], record["id"]) / "job.json"
    original = path.read_bytes()
    body = edit_body(SongState.model_validate(p["revision"]["state"])).model_copy(
        update={"base_revision": p["current_revision"], "preview": False})
    response = client.post(f"/api/projects/{p['id']}/lyric-groups", json=body.model_dump())
    assert response.status_code == 200
    client.put(f"/api/projects/{p['id']}", json={"base_revision": p["current_revision"], "state": response.json()["state"]})
    old = client.get(f"/api/projects/{p['id']}/vocals/{record['id']}").json()
    assert old["status"] == "completed" and old["stale_reason"] == "非当前保存版本" and not old["current"]
    assert path.read_bytes() == original
