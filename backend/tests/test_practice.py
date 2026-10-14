from copy import deepcopy
from io import BytesIO

import mido
import pytest
from fastapi.testclient import TestClient

from backend.app.lyric_group_edit import draft_hash
from backend.app.main import create_app
from backend.app.model_settings import ModelSettings
from backend.app.practice_import import parse_practice, seconds_at
from backend.app.practice_judge import replay
from backend.app.practice_notation import NotationSettings, prepare_notation, reachability
from backend.app.practice_store import PracticeStore
from backend.app.storage import StorageError
from scripts.check_practice_fixture import fixture, simple_fixture


def prepared(content=None, **options):
    parsed = parse_practice(content or simple_fixture())
    settings = NotationSettings(
        track_id=parsed["tracks"][0]["id"], end_measure=2, grid=0, **options
    )
    return parsed, settings, prepare_notation(parsed, settings)


def session(store):
    material = store.import_material(simple_fixture(), "隔离练习.mid")
    _, settings, _ = prepared()
    preview = store.preview(material["source"]["id"], settings)
    revision = store.confirm(material["source"]["id"], settings, preview["preview_hash"])
    return store.create_session(
        material["source"]["id"], revision["revision"]["id"], 1, {"offset_ms": 0}, {}
    )


def corrected_events(snapshot, wrong_first=False):
    events, at = [{"kind": "recognition_start", "at": 0}], 200
    for i, target in enumerate(snapshot["targets"]["attacks"]):
        events.append({"kind": "target_ready", "at": at - 50, "target_index": i})
        if i == 0 and wrong_first:
            events += [
                {"kind": "note_down", "at": at, "pitch": 70, "instance": "wrong"},
                {"kind": "note_up", "at": at + 10, "pitch": 70, "instance": "wrong"},
                {"kind": "recognition_commit", "at": at + 110},
            ]
            at += 200
        events.extend(
            {"kind": "note_down", "at": at, "pitch": n["pitch"], "instance": f"r{i}-{j}"}
            for j, n in enumerate(target["notes"])
        )
        events.append({"kind": "recognition_commit", "at": at + 110})
        events.extend(
            {"kind": "note_up", "at": at + 150, "pitch": n["pitch"], "instance": f"r{i}-{j}"}
            for j, n in enumerate(target["notes"])
        )
        at += 250
    return events


def test_practice_parser_preserves_source_and_explicit_defaults():
    parsed = parse_practice(simple_fixture())
    assert parsed["original_ppq"] == 480 and parsed["declared"] == {
        "tempo": True,
        "meter": True,
        "key": False,
    }
    assert len(parsed["events"]) > len(parsed["tracks"][0]["notes"])
    assert parsed["tracks"][0]["instrument"]["program"] == 0
    assert parsed["tracks"][0]["instrument"]["bank_msb"] is None
    mid = mido.MidiFile(file=BytesIO(simple_fixture()))
    mid.tracks[0] = mido.MidiTrack(
        m for m in mid.tracks[0] if m.type not in ("set_tempo", "time_signature", "program_change")
    )
    stream = BytesIO()
    mid.save(file=stream)
    missing = parse_practice(stream.getvalue())
    assert missing["tracks"][0]["instrument"]["program"] is None
    assert not missing["declared"]["tempo"] and missing["tempos"][0]["tempo"] == 500000


def test_musicxml_chords_ties_repeated_onsets_tuplets_meter_and_real_tempo():
    parsed = parse_practice(fixture())
    result = prepare_notation(
        parsed, NotationSettings(track_id=parsed["tracks"][0]["id"], end_measure=8, grid=0)
    )
    assert len(result["notation"]["segments"]) == 16 and len(result["targets"]["attacks"]) == 13
    assert len(result["targets"]["attacks"][0]["notes"]) == 3
    tied = [n for a in result["targets"]["attacks"] for n in a["notes"] if n["tie_chain_id"]]
    assert len(tied) == 1 and len(tied[0]["notation_ids"]) == 2
    assert '<tie type="start"' in result["musicxml"] and '<tied type="stop"' in result["musicxml"]
    repeated = [a for a in result["targets"]["attacks"] if a["tick"] == 8400]
    assert repeated[0]["notes"][0]["tie_chain_id"] is None
    assert "<time-modification>" in result["musicxml"] and "<beat-type>8" in result["musicxml"]
    assert seconds_at(parsed["tempos"], 8160) == pytest.approx(10.666672 + 0.5)


def test_manual_slur_remains_distinct_from_tie():
    parsed, settings, result = prepared()
    ids = [a["notes"][0]["id"] for a in result["targets"]["attacks"]]
    settings.slurs = [(ids[0], ids[1])]
    updated = prepare_notation(parsed, settings)
    assert '<slur type="start"' in updated["musicxml"]
    assert len(updated["targets"]["attacks"]) == 4


def test_musicxml_declares_initial_attributes_then_actual_changes_and_triplet_brackets():
    from xml.etree.ElementTree import fromstring

    parsed = parse_practice(fixture())
    prepared = prepare_notation(
        parsed, NotationSettings(track_id=parsed["tracks"][0]["id"], end_measure=8, grid=0)
    )
    root = fromstring(prepared["musicxml"])
    assert len(root.findall(".//clef")) == 1
    assert len(root.findall(".//time")) == 3 and len(root.findall(".//key")) == 2
    triplets = root.findall(".//tuplet")
    assert [t.get("type") for t in triplets] == ["start", "stop"]
    assert len(prepared["targets"]["attacks"]) == 13 and len(prepared["notation"]["segments"]) == 16


@pytest.mark.parametrize(
    "change", ["type2", "smpte", "bend", "sysex", "mid_meter", "empty", "overlap", "bad_pair"]
)
def test_unsupported_inputs_produce_specific_reasons_without_editing_source(change):
    mid = mido.MidiFile(file=BytesIO(simple_fixture()))
    track = mid.tracks[0]
    if change == "type2":
        mid.type = 2
    elif change == "smpte":
        mid.ticks_per_beat = -24
    elif change == "bend":
        track.insert(3, mido.Message("pitchwheel", pitch=300))
    elif change == "sysex":
        track.insert(3, mido.Message("sysex", data=[1, 2]))
    elif change == "mid_meter":
        track.insert(4, mido.MetaMessage("time_signature", numerator=3, denominator=4, time=100))
    elif change == "empty":
        mid.tracks = [mido.MidiTrack([mido.MetaMessage("end_of_track")])]
    elif change == "overlap":
        track.insert(4, mido.Message("note_on", note=60, velocity=90))
        track.insert(7, mido.Message("note_off", note=60))
    elif change == "bad_pair":
        track.insert(3, mido.Message("note_off", note=76))
    stream = BytesIO()
    mid.save(file=stream)
    content = stream.getvalue()
    if change == "overlap":
        parsed = parse_practice(content)
        result = prepare_notation(
            parsed, NotationSettings(track_id=parsed["tracks"][0]["id"], end_measure=2, grid=0)
        )
        assert not result["ready"] and "重叠起音" in result["reason"]
        return
    with pytest.raises(StorageError):
        parsed = parse_practice(content)
        prepare_notation(
            parsed, NotationSettings(track_id=parsed["tracks"][0]["id"], end_measure=2, grid=0)
        )
    assert stream.getvalue() == content


def test_grand_staff_allocates_continuous_inferred_voices_without_a_pitch_split():
    parsed = parse_practice(fixture(grand=True))
    result = prepare_notation(
        parsed,
        NotationSettings(track_id=parsed["tracks"][0]["id"], end_measure=8, grid=0, clef="grand"),
    )
    assert {s["staff"] for s in result["notation"]["segments"]} == {1, 2}
    assert "<staves>2</staves>" in result["musicxml"] and "<sign>F</sign>" in result["musicxml"]


def test_successful_batch_file_is_authoritative_when_summary_write_fails(tmp_path, monkeypatch):
    store = PracticeStore(tmp_path)
    state = session(store)
    sid = state["snapshot"]["id"]
    events = corrected_events(state["snapshot"])
    original = store.json

    def injected(path, value):
        if path.name == "summary.json":
            raise OSError("isolated failure")
        return original(path, value)

    monkeypatch.setattr(store, "json", injected)
    with pytest.raises(OSError):
        store.append(sid, 1, events, draft_hash(events))
    monkeypatch.setattr(store, "json", original)
    result = store.append(sid, 1, events, draft_hash(events))
    assert result["last_seq"] == 1 and result["result"]["recognition"]["complete"]


def test_same_pitch_midpoint_windows_do_not_double_match(tmp_path):
    state = session(PracticeStore(tmp_path))
    s = state["snapshot"]
    for a in s["targets"]["attacks"][:3]:
        a["notes"][0]["pitch"] = 60
    events = corrected_events(s) + [
        {"kind": "segment_start", "at": 2000, "expected_start": 2500, "from_tick": 0, "speed": 1},
        {"kind": "note_down", "at": 2750, "pitch": 60, "instance": "midpoint"},
        {"kind": "segment_end", "at": 7000, "completed": True},
    ]
    result = replay(s, events)["rhythm"][0]
    assert (
        len(result["matched"]) == 1
        and result["matched"][0]["target_id"] == s["targets"]["attacks"][1]["notes"][0]["id"]
    )


def test_rules_and_source_tampering_are_rejected(tmp_path):
    store = PracticeStore(tmp_path)
    state = session(store)
    sid = state["snapshot"]["id"]
    path = store.session_path(sid) / "session.json"
    document = store.read(path)
    document["rules"]["match_ms"] = 9000
    store.json(path, document)
    with pytest.raises(StorageError, match="快照"):
        store.session(sid)
    material = store.materials()[0]
    source = store.material_path(material["id"]) / "source.mid"
    source.write_bytes(b"not midi")
    with pytest.raises(StorageError, match="哈希"):
        store.material(material["id"])


def test_initial_instrument_is_per_channel_not_other_channels_active_notes():
    mid = mido.MidiFile(type=0)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.extend(
        [
            mido.Message("note_on", channel=0, note=60, velocity=90),
            mido.Message("program_change", channel=1, program=4),
            mido.Message("control_change", channel=1, control=0, value=2),
            mido.Message("note_on", channel=1, note=64, velocity=90),
            mido.Message("note_off", channel=0, note=60, time=480),
            mido.Message("note_off", channel=1, note=64),
        ]
    )
    output = BytesIO()
    mid.save(file=output)
    parsed = parse_practice(output.getvalue())
    assert parsed["tracks"][1]["instrument"] == {"program": 4, "bank_msb": 2, "bank_lsb": None}
    assert not parsed["tracks"][1]["controls"]


def test_resume_rejects_cutting_a_held_note_and_musicxml_is_hashed(tmp_path):
    store = PracticeStore(tmp_path)
    state = session(store)
    sid = state["snapshot"]["id"]
    mid = state["snapshot"]["material_id"]
    rid = state["snapshot"]["revision_id"]
    events = corrected_events(state["snapshot"]) + [
        {"kind": "segment_start", "at": 2000, "expected_start": 3000, "from_tick": 120, "speed": 1}
    ]
    with pytest.raises(StorageError, match="切断"):
        store.append(sid, 1, events, draft_hash(events))
    assert not store.batches(sid)
    path = store.material_path(mid) / "revisions" / rid / "score.musicxml"
    path.write_text("<fake/>", encoding="utf-8")
    with pytest.raises(StorageError, match="哈希"):
        store.revision(mid, rid)


def test_note_level_first_and_cumulative_accuracy_do_not_erase_errors(tmp_path):
    state = session(PracticeStore(tmp_path))
    reading = replay(state["snapshot"], corrected_events(state["snapshot"], True))["recognition"]
    assert reading["first_accuracy"] == 0.75
    assert reading["first_pitch_accuracy"] == pytest.approx(5 / 7)
    assert reading["cumulative_pitch_accuracy"] == pytest.approx(6 / 8)


def test_controller_ignoring_is_an_explicit_practice_choice():
    mid = mido.MidiFile(file=BytesIO(simple_fixture()))
    mid.tracks[0].insert(3, mido.Message("control_change", control=64, value=127))
    stream = BytesIO()
    mid.save(file=stream)
    parsed = parse_practice(stream.getvalue())
    settings = NotationSettings(track_id=parsed["tracks"][0]["id"], end_measure=2, grid=0)
    with pytest.raises(StorageError, match="确认"):
        prepare_notation(parsed, settings)
    settings.acknowledge_controls = True
    result = prepare_notation(parsed, settings)
    assert result["report"]["controls_ignored"] == 1


def test_quantization_and_pickup_are_explicit_immutable_previews():
    parsed, settings, _ = prepared()
    before = deepcopy(parsed)
    parsed["tracks"][0]["notes"][0]["start_tick"] = 50
    settings.grid = 120
    result = prepare_notation(parsed, settings)
    assert result["report"]["quantized_notes"] == 1 and result["report"]["max_shift_ms"] > 0
    assert parsed["tracks"][0]["notes"][0]["start_tick"] == 50
    parsed = before
    settings.grid = 0
    settings.pickup_ticks = 480
    result = prepare_notation(parsed, settings)
    assert (
        result["notation"]["measures"][0]["pickup"] and result["targets"]["attacks"][0]["tick"] == 0
    )


def test_fragment_tie_boundaries_are_rejected_or_explicitly_expanded():
    parsed = parse_practice(fixture())
    settings = NotationSettings(
        track_id=parsed["tracks"][0]["id"], start_measure=5, end_measure=5, grid=0
    )
    with pytest.raises(StorageError, match="切断"):
        prepare_notation(parsed, settings)
    settings.expand_boundaries = True
    result = prepare_notation(parsed, settings)
    assert result["notation"]["start_tick"] == 5760


@pytest.mark.parametrize(
    "pitches,level",
    [
        ([60], "simple"),
        ([61], "simple"),
        ([62, 74], "simple"),
        ([72, 84], "simple"),
        ([36], "blocked"),
        ([86], "blocked"),
        ([74, 86], "simple"),
        ([96], "blocked"),
        ([84, 96], "simple"),
        ([48, 64], "blocked"),
        ([48, 60, 64, 67], "blocked"),
        ([60, 74], "blocked"),
    ],
)
def test_reachability_uses_exact_octave_outputs(pitches, level):
    assert reachability(pitches)["level"] == level


def test_recognition_correction_preserves_first_accuracy_and_physical_reaction(tmp_path):
    state = session(PracticeStore(tmp_path))
    events = corrected_events(state["snapshot"], True)
    result = replay(state["snapshot"], events)
    reading = result["recognition"]
    assert (
        reading["complete"] and reading["first_accuracy"] == 0.75 and len(reading["attempts"]) == 5
    )
    assert reading["attempts"][0]["actual"] == [70] and not reading["attempts"][0]["correct"]
    assert (
        reading["attempts"][1]["correct"] and reading["attempts"][1]["final_correction_ms"] == 250
    )


def test_chord_attempts_do_not_accumulate_and_require_simultaneous_keys(tmp_path):
    state = session(PracticeStore(tmp_path))
    snapshot = state["snapshot"]
    snapshot["targets"]["attacks"] = snapshot["targets"]["attacks"][-1:]
    events = [{"kind": "recognition_start", "at": 0}]
    for i, pitch in enumerate((60, 64, 67)):
        at = 200 + i * 200
        events.extend(
            [
                {"kind": "note_down", "at": at, "pitch": pitch, "instance": str(i)},
                {"kind": "note_up", "at": at + 10, "pitch": pitch, "instance": str(i)},
                {"kind": "recognition_commit", "at": at + 110},
            ]
        )
    result = replay(snapshot, events)
    assert not result["recognition"]["complete"] and len(result["recognition"]["attempts"]) == 3


def test_rhythm_wide_matching_early_late_missed_extra_rest_and_release_hints(tmp_path):
    state = session(PracticeStore(tmp_path))
    s = state["snapshot"]
    events = corrected_events(s)
    events.append(
        {"kind": "segment_start", "at": 2000, "expected_start": 2500, "from_tick": 0, "speed": 1}
    )
    events.extend(
        [
            {"kind": "note_down", "at": 2300, "pitch": 60, "instance": "t0"},
            {"kind": "note_up", "at": 2400, "pitch": 60, "instance": "t0"},
            {"kind": "note_down", "at": 3200, "pitch": 62, "instance": "t1"},
            {"kind": "note_up", "at": 3500, "pitch": 62, "instance": "t1"},
            {"kind": "note_down", "at": 3900, "pitch": 76, "instance": "extra"},
            {"kind": "segment_end", "at": 7000, "completed": True},
        ]
    )
    report = replay(s, events)["rhythm"][0]
    assert [m["timing"] for m in report["matched"]] == ["early", "late"]
    assert len(report["missed"]) == 4 and len(report["extras"]) == 1 and report["rest_errors"] == 1
    assert all(m["release_warning"] for m in report["matched"])


def test_pause_clock_cutoff_does_not_create_whole_fragment_misses(tmp_path):
    state = session(PracticeStore(tmp_path))
    s = state["snapshot"]
    events = corrected_events(s)
    events += [
        {"kind": "segment_start", "at": 2000, "expected_start": 2500, "from_tick": 0, "speed": 1},
        {"kind": "pause", "at": 20000, "last_valid_at": 2400, "reason": "clock_interruption"},
    ]
    report = replay(s, events)["rhythm"][0]
    assert report["interrupted"] and not report["missed"] and not report["complete"]


def test_session_batches_are_idempotent_and_unfinished_survives_restart(tmp_path):
    store = PracticeStore(tmp_path)
    state = session(store)
    sid = state["snapshot"]["id"]
    events = corrected_events(state["snapshot"], True)
    result = store.append(sid, 1, events, draft_hash(events))
    assert result["status"] == "recognition_complete"
    assert store.append(sid, 1, events, draft_hash(events))["events"] == events
    changed = deepcopy(events)
    changed[0]["at"] = 1
    with pytest.raises(StorageError, match="不同内容"):
        store.append(sid, 1, changed, draft_hash(changed))
    assert len(PracticeStore(tmp_path).session(sid)["events"]) == len(events)
    assert (
        not (store.session_path(sid) / "summary.json")
        .read_text(encoding="utf-8")
        .find('"completed"')
        >= 0
    )


def test_invalid_completion_or_unlocked_rhythm_is_rejected_before_batch_publish(tmp_path):
    store = PracticeStore(tmp_path)
    state = session(store)
    sid = state["snapshot"]["id"]
    events = [{"kind": "segment_start", "at": 0, "expected_start": 0, "from_tick": 0, "speed": 1}]
    with pytest.raises(StorageError, match="认音"):
        store.append(sid, 1, events, draft_hash(events))
    assert store.batches(sid) == []
    events = corrected_events(state["snapshot"]) + [
        {"kind": "segment_start", "at": 2000, "expected_start": 3000, "from_tick": 0, "speed": 1},
        {"kind": "segment_end", "at": 3100, "completed": True},
    ]
    with pytest.raises(StorageError, match="尚未结束"):
        store.append(sid, 1, events, draft_hash(events))
    assert store.batches(sid) == []


def test_api_practice_is_independent_of_song_history_and_recycle_bin(tmp_path):
    app = create_app(tmp_path, model_settings=ModelSettings(mock=True))
    with TestClient(app) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        assert client.get("/api/projects").json()["items"] == []
        project = client.post("/api/projects", json={"name": "隔离来源"}).json()
        # Add an isolated legal score without invoking any generator.
        score = project["revision"]["state"]
        score["score"]["tracks"][0]["notes"] = [
            {
                "id": "practice-origin",
                "pitch": 60,
                "velocity": 90,
                "start_tick": 0,
                "duration_tick": 480,
            }
        ]
        score["score"]["end_tick"] = 1920
        project = client.put(
            "/api/projects/" + project["id"],
            json={"base_revision": project["current_revision"], "state": score},
        ).json()
        directory = app.state.store.directory(project["id"])
        before = {
            p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob("*")
            if p.is_file()
        }
        copied = client.post(
            "/api/practice/from-song",
            json={"song_id": project["id"], "revision_id": project["current_revision"]},
        )
        assert copied.status_code == 201
        mid = copied.json()["source"]["id"]
        assert client.get("/api/practice/materials/" + mid).status_code == 200
        assert before == {
            p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob("*")
            if p.is_file()
        }
        assert (
            client.request(
                "DELETE",
                "/api/projects/" + project["id"],
                json={"base_revision": project["current_revision"]},
            ).status_code
            == 200
        )
        assert client.get("/api/practice/materials/" + mid).status_code == 200
        assert client.get("/api/projects").json()["items"] == []
        assert client.post("/api/trash/" + project["id"] + "/restore").status_code == 200
        assert (
            client.get("/api/practice/materials/" + mid).json()["source"]["association"][
                "revision_id"
            ]
            == project["current_revision"]
        )


def test_restart_marks_active_unfinished_session_interrupted(tmp_path):
    store = PracticeStore(tmp_path)
    state = session(store)
    sid = state["snapshot"]["id"]
    events = corrected_events(state["snapshot"])[:5]
    result = store.append(sid, 1, events, draft_hash(events))
    assert result["status"] == "recognition" and not result["result"]["recognition"]["complete"]
    reopened = PracticeStore(tmp_path).session(sid)
    assert reopened["status"] == "interrupted" and reopened["events"] == events
    assert not reopened["result"]["recognition"]["complete"]


def test_buffer_arrival_order_can_differ_from_original_key_time_but_not_record_order(tmp_path):
    store = PracticeStore(tmp_path)
    state = session(store)
    sid = state["snapshot"]["id"]
    events = [
        {"kind": "recognition_start", "at": 0, "recorded_at": 1},
        {"kind": "note_down", "at": 100, "recorded_at": 110, "pitch": 60, "instance": "delayed"},
        {"kind": "raw_down", "at": 95, "recorded_at": 111, "code": "KeyS"},
        {"kind": "recognition_commit", "at": 210, "recorded_at": 212},
    ]
    assert (
        store.append(sid, 1, events, draft_hash(events))["result"]["recognition"]["next_index"] == 1
    )
    next_events = [{"kind": "checkpoint", "at": 213, "recorded_at": 211}]
    with pytest.raises(StorageError, match="时间顺序"):
        store.append(sid, 2, next_events, draft_hash(next_events))


def test_ppq_conversion_and_shared_channel_tracks_have_independent_ids():
    mid = mido.MidiFile(type=1, ticks_per_beat=960)
    for pitch in (60, 64):
        mid.tracks.append(
            mido.MidiTrack(
                [
                    mido.Message("note_on", note=pitch, velocity=90, time=1),
                    mido.Message("note_off", note=pitch, time=960),
                ]
            )
        )
    output = BytesIO()
    mid.save(file=output)
    parsed = parse_practice(output.getvalue())
    assert len(parsed["tracks"]) == 2 and all(t["channel"] == 0 for t in parsed["tracks"])
    a, b = (t["notes"][0] for t in parsed["tracks"])
    assert a["id"] != b["id"]
    assert a["raw_start"] == 1 and a["start_tick"] == 1 and a["end_tick"] == 481
    assert parsed["max_rounding_ticks"] == 0.5


@pytest.mark.parametrize("count,accepted", [(5000, True), (5001, False)])
def test_source_note_count_boundaries(count, accepted):
    mid = mido.MidiFile(type=0)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    for _ in range(count):
        track.extend(
            [
                mido.Message("note_on", note=60, velocity=90),
                mido.Message("note_off", note=60, time=40),
            ]
        )
    output = BytesIO()
    mid.save(file=output)
    if accepted:
        assert parse_practice(output.getvalue())["tracks"][0]["note_count"] == 5000
    else:
        with pytest.raises(StorageError, match="5000"):
            parse_practice(output.getvalue())


@pytest.mark.parametrize("end_tick,accepted", [(576000, True), (576002, False)])
def test_original_ten_minute_boundary(end_tick, accepted):
    mid = mido.MidiFile(file=BytesIO(simple_fixture()))
    track = mid.tracks[0]
    track[-1] = mido.MetaMessage("end_of_track", time=end_tick - 1680)
    output = BytesIO()
    mid.save(file=output)
    if accepted:
        assert parse_practice(output.getvalue())["seconds"] == 600
    else:
        with pytest.raises(StorageError, match="10分钟"):
            parse_practice(output.getvalue())


def test_rhythm_chord_spread_calibration_tie_and_slur_hints_are_replayable(tmp_path):
    state = session(PracticeStore(tmp_path))
    s = state["snapshot"]
    s["rules"]["calibration"]["offset_ms"] = 40
    first = s["targets"]["attacks"][0]["notes"][0]
    second = s["targets"]["attacks"][1]["notes"][0]
    first["tie_chain_id"] = "held-chain"
    s["targets"]["slurs"] = [[first["id"], second["id"]]]
    events = corrected_events(s) + [
        {"kind": "segment_start", "at": 2000, "expected_start": 2500, "from_tick": 0, "speed": 1}
    ]
    for i, attack in enumerate(s["targets"]["attacks"]):
        for j, note in enumerate(attack["notes"]):
            when = 2500 + seconds_at(s["targets"]["tempos"], attack["tick"]) * 1000 + 40 + j * 60
            release = 2500 + seconds_at(s["targets"]["tempos"], note["end_tick"]) * 1000 + 40
            events.extend(
                [
                    {
                        "kind": "note_down",
                        "at": when,
                        "pitch": note["pitch"],
                        "instance": f"t{i}-{j}",
                    },
                    {
                        "kind": "note_up",
                        "at": release,
                        "pitch": note["pitch"],
                        "instance": f"t{i}-{j}",
                    },
                ]
            )
    # Arrival order is preserved; chord events above intentionally arrive out of timestamp order.
    events.append({"kind": "segment_end", "at": 7000, "completed": True})
    report = replay(s, events)["rhythm"][0]
    assert len(report["matched"]) == 6 and report["matched"][0]["release_delta_ms"] == 0
    assert report["matched"][0]["tie_chain_id"] == "held-chain"
    assert report["slur_feedback"][0]["gap_ms"] == 250 and report["slur_feedback"][0]["warning"]
    assert (
        report["chord_feedback"][0]["spread_ms"] == 120
        and not report["chord_feedback"][0]["together"]
    )
