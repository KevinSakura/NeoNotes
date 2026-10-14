from typing import Annotated, Literal

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response
from pydantic import Field

from .midi import export_midi
from .models import Model, SongState
from .practice_notation import NotationSettings
from .practice_units import catalog, exercise_bundle
from .storage import StorageError


class Confirmation(Model):
    settings: NotationSettings
    preview_hash: str = Field(pattern="^[a-f0-9]{64}$")


class PracticeSessionRequest(Model):
    material_id: str = Field(pattern="^[a-f0-9]{32}$")
    revision_id: str = Field(pattern="^[a-f0-9]{32}$")
    speed: Literal[0.5, 0.75, 1] = 0.75
    calibration: dict = Field(default_factory=lambda: {"status": "uncalibrated", "offset_ms": 0})
    self_test: dict = Field(default_factory=dict)


class Batch(Model):
    seq: int = Field(ge=1, le=100000)
    events: list[dict] = Field(min_length=1, max_length=512)
    payload_hash: str = Field(pattern="^[a-f0-9]{64}$")


class SongCopy(Model):
    song_id: str = Field(pattern="^[a-f0-9]{32}$")
    revision_id: str = Field(pattern="^[a-f0-9]{32}$")


def routes(practice, songs):
    router = APIRouter(prefix="/api/practice")

    @router.get("/units")
    def units():
        return catalog()

    @router.post("/units/{exercise_id}/prepare")
    def unit_preview(exercise_id: str):
        bundle = exercise_bundle(exercise_id)
        with practice.lock:
            material = practice.unit_material(bundle["midi"], bundle["builtin"])
            mid = material["source"]["id"]
            preview = practice.preview(mid, bundle["settings"])
            matching = next(
                (
                    r
                    for r in reversed(material["revisions"])
                    if r.get("builtin", {}).get("artifact_hash")
                    == bundle["builtin"]["artifact_hash"]
                ),
                None,
            )
            revision = practice.revision(mid, matching["id"]) if matching else None
            return {
                "material": material,
                "settings": bundle["settings"].model_dump(),
                "preview": preview,
                "revision": revision,
            }

    @router.get("/materials")
    def materials():
        return {"items": practice.materials()}

    @router.post("/materials", status_code=201)
    async def upload(file: Annotated[UploadFile, File()]):
        content = await file.read(2 * 1024 * 1024 + 1)
        await file.close()
        return practice.import_material(content, file.filename or "practice.mid")

    @router.post("/from-song", status_code=201)
    def from_song(body: SongCopy):
        with songs.lock:
            header = songs.header(body.song_id)
            if body.revision_id not in header["revision_ids"]:
                raise StorageError("请选择歌曲已保存的明确版本。", 409)
            revision = songs.revision(body.song_id, body.revision_id)
            state = SongState.model_validate(revision["state"])
            return practice.import_material(
                export_midi(state.score),
                header["name"] + ".mid",
                {"song_id": body.song_id, "revision_id": body.revision_id},
            )

    @router.get("/materials/{mid}")
    def material(mid: str):
        return practice.material(mid)

    @router.get("/materials/{mid}/source")
    def source(mid: str):
        practice.material(mid)
        return Response(
            practice.safe(practice.material_path(mid) / "source.mid").read_bytes(),
            media_type="audio/midi",
        )

    @router.post("/materials/{mid}/preview")
    def preview(mid: str, body: NotationSettings):
        return practice.preview(mid, body)

    @router.post("/materials/{mid}/revisions", status_code=201)
    def confirm(mid: str, body: Confirmation):
        return practice.confirm(mid, body.settings, body.preview_hash)

    @router.get("/materials/{mid}/revisions/{rid}")
    def revision(mid: str, rid: str):
        return practice.revision(mid, rid)

    @router.post("/sessions", status_code=201)
    def start(body: PracticeSessionRequest):
        return practice.create_session(
            body.material_id, body.revision_id, body.speed, body.calibration, body.self_test
        )

    @router.get("/sessions")
    def sessions():
        return {"items": practice.sessions()}

    @router.get("/sessions/{sid}")
    def session(sid: str):
        return practice.session(sid)

    @router.post("/sessions/{sid}/batches")
    def append(sid: str, body: Batch):
        return practice.append(sid, body.seq, body.events, body.payload_hash)

    return router
