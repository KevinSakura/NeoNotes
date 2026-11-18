"""Isolated offline contracts; fake inference is never accuracy evidence."""

import io
import shutil
import time
import wave
from pathlib import Path

import mido
import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.storage import StorageError
from backend.app.transcription_score import (
    ScoreSettings,
    clip_midi_bytes,
    midi_bytes,
    read_midi,
    score,
)
from backend.app.transcription_score import (
    TranscriptionNote as N,
)
from backend.app.transcriptions import Save, Start, TranscriptionManager


def audio_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
        target.writeframes(b"\0" * 44100 * 4 * 2)
    return output.getvalue()


def fake(operation, source, output, cancel):
    if operation == "separate":
        shutil.copyfile(source, output)
    else:
        output.write_bytes(
            midi_bytes(
                [
                    N(pitch=60, onset=0.1, offset=0.5),
                    N(pitch=64, onset=0.1, offset=0.5),
                    N(pitch=60, onset=0.7, offset=1.2),
                ]
            )
        )


def wait(manager, pid, jid):
    until = time.monotonic() + 10
    while time.monotonic() < until:
        if manager.active is None:
            return manager.job(pid, jid)
        time.sleep(0.01)
    raise AssertionError("task did not finish")


@pytest.fixture
def manager(tmp_path):
    m = TranscriptionManager(tmp_path / "data/.transcriptions", worker=fake)
    yield m
    m.close()


async def uploaded(manager):
    return (
        await manager.upload(UploadFile(filename="sample.wav", file=io.BytesIO(audio_bytes())))
    )["project"]["id"]


def new_project(manager):
    import asyncio

    return asyncio.run(uploaded(manager))


def test_solo_roundtrip_versions_downloads_and_conflict(manager):
    pid = new_project(manager)
    job = manager.start(pid, Start(end=2))
    done = wait(manager, pid, job["id"])
    assert done["status"] == "succeeded"
    result = manager.detail(pid)["results"][0]
    original = (manager.directory(pid) / "results" / result["id"] / "performance.mid").read_bytes()
    notes = [N(**n) for n in result["notes"]]
    notes[0].pitch = 61
    rev = manager.save(pid, Save(result_id=result["id"], notes=notes, settings=ScoreSettings()))
    assert rev["score"] and rev["parent"] is None
    assert (
        original
        == (manager.directory(pid) / "results" / result["id"] / "performance.mid").read_bytes()
    )
    with pytest.raises(StorageError, match="修订已改变"):
        manager.save(pid, Save(result_id=result["id"], notes=notes))
    rev2 = manager.save(pid, Save(base=rev["id"], result_id=result["id"], notes=notes))
    assert rev2["parent"] == rev["id"]
    assert len(manager.detail(pid)["revisions"]) == 2
    root = manager.store.root
    manager.close()
    reopened = TranscriptionManager(root, worker=fake)
    assert reopened.header(pid)["current_revision"] == rev2["id"]
    reopened.close()


def test_mix_requires_explicit_confirmation_and_immutable_source(manager):
    pid = new_project(manager)
    job = manager.start(pid, Start(start=0.25, end=1.75, mode="mix"))
    done = wait(manager, pid, job["id"])
    assert done["status"] == "awaiting_review"
    assert not manager.detail(pid)["results"]
    manager.resume(pid, job["id"], None)
    done = wait(manager, pid, job["id"])
    assert done["status"] == "succeeded"
    result = manager.detail(pid)["results"][0]
    assert result["clip_start"] == 0.25 and "piano.wav" in result["files"]
    with pytest.raises(StorageError):
        manager.resume(pid, job["id"], None)


def test_failed_and_cancelled_jobs_preserve_success(manager):
    pid = new_project(manager)
    job = manager.start(pid, Start(end=2))
    wait(manager, pid, job["id"])
    saved = manager.detail(pid)["results"]

    def failure(*args):
        raise ValueError("private untrusted exception")

    manager.worker = failure
    job = manager.start(pid, Start(end=2))
    assert wait(manager, pid, job["id"])["status"] == "failed"
    assert "private" not in manager.job(pid, job["id"])["error"]

    def slow(operation, source, output, cancel):
        cancel.wait(5)

    manager.worker = slow
    job = manager.start(pid, Start(end=2))
    manager.cancel(pid, job["id"])
    assert wait(manager, pid, job["id"])["status"] == "cancelled"
    assert manager.detail(pid)["results"] == saved


def test_restart_interrupts_and_concurrency_rejects(manager):
    pid = new_project(manager)

    def slow(operation, source, output, cancel):
        cancel.wait(5)

    manager.worker = slow
    job = manager.start(pid, Start(end=2))
    with pytest.raises(StorageError, match="已有扒谱任务"):
        manager.start(pid, Start(end=2))
    manager.cancel(pid, job["id"])
    wait(manager, pid, job["id"])
    done = manager.job(pid, job["id"])
    done["status"] = "transcribing"
    manager.write_job(pid, done)
    other = TranscriptionManager(manager.store.root, worker=fake)
    other.recover()
    assert other.job(pid, job["id"])["status"] == "interrupted"
    other.close()


@pytest.mark.parametrize("start,end", [(0, 61), (1, 1), (0, 3)])
def test_clip_limits(manager, start, end):
    pid = new_project(manager)
    with pytest.raises(StorageError):
        manager.start(pid, Start(start=start, end=end))


def test_bad_format_upload_and_paths(manager):
    import asyncio

    for filename, content in [("fake.wav", b"not audio"), ("fake.txt", audio_bytes())]:
        with pytest.raises(StorageError):
            asyncio.run(manager.upload(UploadFile(filename=filename, file=io.BytesIO(content))))
    assert manager.list() == []
    pid = new_project(manager)
    with pytest.raises(StorageError):
        manager.bounded_path(pid, "../../secret")


def test_full_range_chords_repeats_ties_pickup_and_hands():
    notes = [
        N(id="low", pitch=21, onset=0, offset=3, hand="left"),
        N(id="a", pitch=108, onset=0, offset=0.5, hand="right"),
        N(id="b", pitch=108, onset=0.5, offset=1, hand="right"),
        N(id="c", pitch=100, onset=0.5, offset=1, hand="right"),
    ]
    out, _ = score(notes, ScoreSettings(bpm=120, first_downbeat=0.5))
    assert out["notation"]["measures"][0]["pickup"]
    assert {s["pitch"] for s in out["notation"]["segments"]} == {21, 108, 100}
    low = [s for s in out["notation"]["segments"] if s["source_id"] == "low"]
    assert len(low) > 1 and low[0]["tie_start"] and low[-1]["tie_stop"]
    assert not any(
        s["tie_start"] or s["tie_stop"]
        for s in out["notation"]["segments"]
        if s["source_id"] in ("a", "b")
    )
    assert "<sign>F</sign>" in out["musicxml"] and "<sign>G</sign>" in out["musicxml"]


def test_notation_failure_still_saves_performance(manager):
    pid = new_project(manager)
    job = manager.start(pid, Start(end=2))
    wait(manager, pid, job["id"])
    r = manager.detail(pid)["results"][0]
    rev = manager.save(
        pid,
        Save(
            result_id=r["id"],
            notes=[N(pitch=60, onset=0.01, offset=0.03)],
            settings=ScoreSettings(grid=480),
        ),
    )
    assert rev["notation_error"] and not rev["score"]
    assert set(rev["files"]) == {"performance.mid"}


def test_seconds_mapping_and_midi_repeat(tmp_path):
    notes = [N(pitch=60, onset=0.25, offset=0.5), N(pitch=60, onset=0.5, offset=0.75)]
    f = tmp_path / "raw.mid"
    f.write_bytes(midi_bytes(notes))
    parsed, _ = read_midi(f, 1)
    assert [(n.onset, n.offset) for n in parsed] == [(0.25, 0.5), (0.5, 0.75)]
    out, mid = score(notes, ScoreSettings(bpm=60, first_downbeat=-0.5, grid=120))
    assert out["report"]["clip_zero_tick"] == 240
    assert out["notation"]["notes"][0]["start_tick"] == 360
    assert len([m for m in mido.MidiFile(file=io.BytesIO(mid)) if m.type == "note_on"]) == 2


def test_api_protects_session_downloads_and_songs(tmp_path):
    with TestClient(create_app(tmp_path / "data")) as client:
        client.app.state.transcriptions.worker = fake
        headers = {"x-session-token": client.get("/api/session").json()["token"]}
        assert (
            client.post("/api/transcriptions", files={"file": ("x.wav", audio_bytes())}).status_code
            == 403
        )
        d = client.post(
            "/api/transcriptions", files={"file": ("x.wav", audio_bytes())}, headers=headers
        ).json()
        pid = d["project"]["id"]
        assert client.get("/api/projects").json()["items"] == []
        j = client.post(f"/api/transcriptions/{pid}/jobs", json={"end": 2}, headers=headers).json()
        done = wait(client.app.state.transcriptions, pid, j["id"])
        rid = done["result_id"]
        assert client.get(
            f"/api/transcriptions/{pid}/files/results/{rid}/performance.mid"
        ).content.startswith(b"MThd")
        assert (
            client.get(
                f"/api/transcriptions/{pid}/files/jobs/{j['id']}/performance.mid"
            ).status_code
            == 404
        )
        assert client.get(f"/api/transcriptions/{pid}/source").status_code == 200


def test_cancellation_reaps_real_owned_process(manager):
    import sys

    pid = new_project(manager)

    def child(operation, source, output, cancel):
        manager.command(
            [sys.executable, "-c", "import time;time.sleep(60)"],
            Path.cwd(),
            output.with_suffix(".log"),
        )

    manager.worker = child
    job = manager.start(pid, Start(end=2))
    deadline = time.monotonic() + 5
    while manager.process is None and time.monotonic() < deadline:
        time.sleep(0.01)
    process = manager.process
    assert process is not None
    manager.cancel(pid, job["id"])
    assert wait(manager, pid, job["id"])["status"] == "cancelled"
    assert process.poll() is not None


def test_corrupt_download_and_missing_resources_are_explicit(tmp_path, monkeypatch):
    from backend.app import transcriptions

    with TestClient(create_app(tmp_path / "data")) as client:
        m = client.app.state.transcriptions
        m.worker = fake
        headers = {"x-session-token": client.get("/api/session").json()["token"]}
        pid = client.post(
            "/api/transcriptions", files={"file": ("x.wav", audio_bytes())}, headers=headers
        ).json()["project"]["id"]
        j = m.start(pid, Start(end=2))
        done = wait(m, pid, j["id"])
        rid = done["result_id"]
        (m.directory(pid) / "results" / rid / "performance.mid").write_bytes(b"bad")
        assert (
            client.get(f"/api/transcriptions/{pid}/files/results/{rid}/performance.mid").status_code
            == 409
        )
        monkeypatch.setattr(transcriptions, "ASSETS", tmp_path / "missing")
        m.worker = None
        response = client.post(f"/api/transcriptions/{pid}/jobs", json={"end": 2}, headers=headers)
        assert response.status_code == 503
        assert len(m.detail(pid)["jobs"]) == 1


def test_model_padding_intersection_preserves_original_and_continuous_notes(tmp_path):
    raw = tmp_path / "raw.mid"
    # Construct model output directly: unbounded model times are not user note data.
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend(
        [
            mido.MetaMessage("set_tempo", tempo=500000),
            mido.Message("program_change", channel=2, program=4),
            mido.Message("control_change", channel=2, control=64, value=127),
            mido.Message("note_on", channel=2, note=81, velocity=99, time=28541),
            mido.Message("note_on", channel=2, note=83, velocity=80, time=451),
            mido.Message("note_off", channel=2, note=81, time=176),
            mido.Message("note_off", channel=2, note=83, time=96),
            mido.Message("control_change", channel=2, control=64, value=0, time=1),
        ]
    )
    midi.save(raw)
    original = raw.read_bytes()
    report = {}
    notes, controls = read_midi(raw, 30, report)
    assert len(notes) == 1 and notes[0].pitch == 81 and notes[0].offset == 30
    assert notes[0].onset == pytest.approx(28541 / 960)
    assert (
        notes[0].velocity == 99
        and len(report["clipped_notes"]) == 1
        and len(report["outside_notes"]) == 1
    )
    clipped = tmp_path / "clip.mid"
    clipped.write_bytes(clip_midi_bytes(raw, 30))
    normalized, _ = read_midi(clipped, 30)
    assert len(normalized) == 1 and normalized[0].offset == 30
    msgs = list(mido.MidiFile(clipped))
    assert sum(m.time for m in msgs) == pytest.approx(30)
    assert any(m.type == "program_change" and m.program == 4 and m.channel == 2 for m in msgs)
    assert any(m.type == "control_change" and m.control == 64 and m.value == 0 for m in msgs)
    assert len(controls) == 2 and raw.read_bytes() == original


def test_clip_midi_respects_tempo_changes_and_repeated_note_onsets(tmp_path):
    raw = tmp_path / "tempo.mid"
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend(
        [
            mido.MetaMessage("set_tempo", tempo=500000),
            mido.Message("note_on", note=60, velocity=80),
            mido.Message("note_off", note=60, time=240),
            mido.Message("note_on", note=60, velocity=90),
            mido.MetaMessage("set_tempo", tempo=1000000, time=240),
            mido.Message("note_off", note=60, time=960),
        ]
    )
    midi.save(raw)
    out = tmp_path / "clip.mid"
    out.write_bytes(clip_midi_bytes(raw, 1.5))
    notes, _ = read_midi(out, 1.5)
    assert [(n.onset, n.offset) for n in notes] == [(0, 0.25), (0.25, 1.5)]
    assert [m.tempo for m in mido.MidiFile(out) if m.type == "set_tempo"] == [500000, 1000000]


@pytest.mark.parametrize("kind", ["unmatched", "no-end", "huge-padding", "zero"])
def test_malformed_model_midi_still_rejected(tmp_path, kind):
    raw = tmp_path / "bad.mid"
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    if kind == "unmatched":
        track.append(mido.Message("note_off", note=60))
    else:
        track.append(mido.Message("note_on", note=60, velocity=80))
        if kind != "no-end":
            track.append(mido.Message("note_off", note=60, time=0 if kind == "zero" else 20000))
    midi.save(raw)
    with pytest.raises(StorageError):
        read_midi(raw, 2)


def test_three_voices_each_hand_have_distinct_xml_voice_ids():
    from xml.etree import ElementTree as ET

    notes = [
        N(id=f"{hand}{i}", pitch=pitch + i * 3, onset=i * 0.25, offset=3.5 - i * 0.25, hand=hand)
        for hand, pitch in [("right", 72), ("left", 48)]
        for i in range(3)
    ]
    out, mid = score(notes, ScoreSettings())
    assert out["report"]["voices_per_hand"] == {"right": 3, "left": 3}
    assert out["notation"]["voice_stride"] == 3
    assert (
        len(out["notation"]["notes"]) == 6 and len({n["id"] for n in out["notation"]["notes"]}) == 6
    )
    root = ET.fromstring(out["musicxml"])
    voices = {1: set(), 2: set()}
    for n in root.findall(".//measure/note"):
        voices[int(n.findtext("staff"))].add(int(n.findtext("voice")))
    assert voices == {1: {1, 2, 3}, 2: {4, 5, 6}}
    assert not out["notation"]["slurs"]
    assert any(s["tie_start"] for s in out["notation"]["segments"])
    assert len([m for m in mido.MidiFile(file=io.BytesIO(mid)) if m.type == "note_on"]) == 6
