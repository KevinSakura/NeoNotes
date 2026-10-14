import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.app import practice_units
from backend.app.lyric_group_edit import draft_hash
from backend.app.main import create_app
from backend.app.model_settings import ModelSettings
from backend.app.practice_import import parse_practice
from backend.app.practice_store import PracticeStore
from backend.app.storage import StorageError
from backend.tests.test_practice import corrected_events

EXERCISES = [e["exercise_id"] for u in practice_units.catalog()["units"] for e in u["exercises"]]


@pytest.mark.parametrize("exercise_id", EXERCISES)
def test_every_packaged_score_is_complete_playable_and_has_actual_targets(exercise_id):
    bundle = practice_units.exercise_bundle(exercise_id)
    prepared = bundle["prepared"]
    parsed = parse_practice(bundle["midi"])
    notes = parsed["tracks"][0]["notes"]
    targets = prepared["targets"]
    assert prepared["ready"] and all(
        a["reachability"]["level"] == "simple" for a in targets["attacks"]
    )
    assert targets["end_tick"] == bundle["builtin"]["bars"] * 1920
    assert prepared["report"]["quantized_notes"] == 0
    assert prepared["report"]["excluded_notes"] == 0 and prepared["report"]["controls_ignored"] == 0
    target_notes = [n for a in targets["attacks"] for n in a["notes"]]
    assert {n["id"] for n in target_notes} == {n["id"] for n in notes}
    assert len(target_notes) == len(notes) and all(n["notation_ids"] for n in target_notes)
    assert parsed["declared"] == {"tempo": True, "meter": True, "key": True}
    assert parsed["tracks"][0]["instrument"] == {"program": 0, "bank_msb": 0, "bank_lsb": 0}
    assert practice_units.exercise_bundle(exercise_id)["midi"] == bundle["midi"]


def test_ties_rest_flat_spelling_and_repeated_onsets_are_not_confused():
    tied = practice_units.exercise_bundle("ties-across-bar")["prepared"]
    targets = tied["targets"]["attacks"]
    held = [n for a in targets for n in a["notes"] if n["tie_chain_id"]]
    assert len(held) == 1 and len(held[0]["notation_ids"]) == 2
    repeated = practice_units.exercise_bundle("melody-repeated")["prepared"]["targets"]["attacks"]
    assert repeated[0]["notes"][0]["pitch"] == repeated[1]["notes"][0]["pitch"]
    assert repeated[0]["notes"][0]["id"] != repeated[1]["notes"][0]["id"]
    assert not repeated[0]["notes"][0]["tie_chain_id"]
    assert (
        len(practice_units.exercise_bundle("rhythm-rests")["prepared"]["targets"]["silences"]) == 2
    )
    flats = practice_units.exercise_bundle("accidentals-flats")["prepared"]
    assert "<fifths>-1</fifths>" in flats["musicxml"]
    assert (
        flats["notation"]["segments"][0]["step"] == "D"
        and flats["notation"]["segments"][0]["alter"] == -1
    )


def test_catalog_is_read_only_and_unknown_ids_write_nothing(tmp_path):
    app = create_app(tmp_path, model_settings=ModelSettings(mock=True))
    with TestClient(app) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        catalogue = client.get("/api/practice/units").json()
        assert (
            len(catalogue["units"]) == 5
            and sum(len(u["exercises"]) for u in catalogue["units"]) == 15
        )
        assert not app.state.practice.root.exists()
        assert client.post("/api/practice/units/unknown/prepare", json={}).status_code == 404
        assert not app.state.practice.root.exists()
        assert client.get("/api/projects").json()["items"] == []


def test_prepare_confirm_reopen_and_records_work_without_import_or_creation(tmp_path):
    app = create_app(tmp_path, model_settings=ModelSettings(mock=True))
    with TestClient(app) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        endpoint = "/api/practice/units/reading-five-notes/prepare"
        prepared = client.post(endpoint, json={}).json()
        mid = prepared["material"]["source"]["id"]
        assert prepared["revision"] is None
        saved = client.post(
            f"/api/practice/materials/{mid}/revisions",
            json={
                "settings": prepared["settings"],
                "preview_hash": prepared["preview"]["preview_hash"],
            },
        ).json()
        rid = saved["revision"]["id"]
        state = client.post(
            "/api/practice/sessions", json={"material_id": mid, "revision_id": rid}
        ).json()
        assert state["snapshot"]["builtin"]["exercise_id"] == "reading-five-notes"
        events = corrected_events(state["snapshot"], True)
        answer = client.post(
            "/api/practice/sessions/" + state["snapshot"]["id"] + "/batches",
            json={"seq": 1, "events": events, "payload_hash": draft_hash(events)},
        ).json()
        assert (
            answer["result"]["recognition"]["complete"]
            and answer["result"]["recognition"]["first_accuracy"] == 0.875
        )
        reopened = client.post(endpoint, json={}).json()
        assert (
            reopened["material"]["source"]["id"] == mid
            and reopened["revision"]["revision"]["id"] == rid
        )
        assert len(app.state.practice.materials()) == 1
        records = client.get("/api/practice/sessions").json()["items"]
        assert (
            records[0]["builtin"]["artifact_hash"] == state["snapshot"]["builtin"]["artifact_hash"]
        )
        assert client.get("/api/projects").json()["items"] == []
        again = PracticeStore(app.state.practice.root).session(state["snapshot"]["id"])
        assert (
            again["events"] == events
            and again["snapshot"]["builtin"] == state["snapshot"]["builtin"]
        )


def test_parallel_prepare_does_not_duplicate_materials(tmp_path):
    store = PracticeStore(tmp_path)
    bundle = practice_units.exercise_bundle("chords-triads")
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(
            pool.map(lambda _: store.unit_material(bundle["midi"], bundle["builtin"]), range(2))
        )
    assert result[0]["source"]["id"] == result[1]["source"]["id"]
    assert len(store.materials()) == 1


def test_customized_revision_is_not_reclassified_as_standard_unit(tmp_path):
    store = PracticeStore(tmp_path)
    bundle = practice_units.exercise_bundle("reading-five-notes")
    material = store.unit_material(bundle["midi"], bundle["builtin"])
    mid = material["source"]["id"]
    settings = bundle["settings"]
    settings.transpose = 12
    preview = store.preview(mid, settings)
    assert "builtin" not in preview
    revision = store.confirm(mid, settings, preview["preview_hash"])
    assert "builtin" not in revision["revision"]
    state = store.create_session(mid, revision["revision"]["id"], 1, {}, {})
    assert "builtin" not in state["snapshot"]


def test_identical_uploaded_midi_is_not_reclassified_and_bundle_mutation_is_isolated(tmp_path):
    store = PracticeStore(tmp_path)
    bundle = practice_units.exercise_bundle("reading-five-notes")
    uploaded = store.import_material(bundle["midi"], "自选.mid")
    material = store.unit_material(bundle["midi"], bundle["builtin"])
    assert uploaded["source"]["id"] != material["source"]["id"]
    assert "builtin" not in store.material(uploaded["source"]["id"])["source"]
    bundle["prepared"]["targets"]["attacks"].clear()
    assert (
        len(practice_units.exercise_bundle("reading-five-notes")["prepared"]["targets"]["attacks"])
        == 8
    )


def test_changed_catalog_creates_new_artifact_and_retains_old_records(tmp_path, monkeypatch):
    store = PracticeStore(tmp_path / "store")
    old = practice_units.exercise_bundle("reading-five-notes")
    material = store.unit_material(old["midi"], old["builtin"])
    mid = material["source"]["id"]
    preview = store.preview(mid, old["settings"])
    revision = store.confirm(mid, old["settings"], preview["preview_hash"])
    state = store.create_session(mid, revision["revision"]["id"], 1, {}, {})
    before = deepcopy(store.material(mid))
    catalogue = deepcopy(practice_units.definitions())
    catalogue["units"][0]["exercises"][0]["sequence"][0][0] = 62
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(catalogue, ensure_ascii=False), encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(practice_units, "CATALOG_PATH", path)
        practice_units.definitions.cache_clear()
        practice_units._bundle.cache_clear()
        try:
            new = practice_units.exercise_bundle("reading-five-notes")
            assert new["builtin"]["artifact_hash"] != old["builtin"]["artifact_hash"]
            other = store.unit_material(new["midi"], new["builtin"])
            assert other["source"]["id"] != mid
            assert store.material(mid) == before
            assert store.session(state["snapshot"]["id"])["snapshot"]["builtin"] == old["builtin"]
        finally:
            practice_units.definitions.cache_clear()
            practice_units._bundle.cache_clear()


def test_missing_resources_or_corrupted_cached_midi_are_not_silently_replaced(
    tmp_path, monkeypatch
):
    store = PracticeStore(tmp_path / "store")
    bundle = practice_units.exercise_bundle("reading-five-notes")
    material = store.unit_material(bundle["midi"], bundle["builtin"])
    (store.material_path(material["source"]["id"]) / "source.mid").write_bytes(b"corrupted")
    with pytest.raises(StorageError, match="哈希"):
        store.unit_material(bundle["midi"], bundle["builtin"])
    assert len(store.materials()) == 1
    with monkeypatch.context() as patch:
        patch.setattr(practice_units, "CATALOG_PATH", tmp_path / "missing.json")
        practice_units.definitions.cache_clear()
        practice_units._bundle.cache_clear()
        try:
            with pytest.raises(StorageError, match="目录缺失"):
                practice_units.catalog()
        finally:
            practice_units.definitions.cache_clear()
            practice_units._bundle.cache_clear()
