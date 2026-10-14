import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .generation import (
    CandidateAction,
    GenerationManager,
    GenerationRequest,
    LyricRepairRequest,
    ReviewRequest,
)
from .lyric_group_edit import GroupEditRequest, transaction
from .midi import MidiError, export_midi, import_midi
from .model_settings import ModelError, ModelSettings, load_model_settings
from .models import (
    CreateProject,
    RestoreRevision,
    RevisionAction,
    SaveProject,
    SongState,
)
from .phrase_tasks import BudgetRequest
from .practice_api import routes as practice_routes
from .practice_store import PracticeStore
from .render import RenderManager, RenderRequest
from .resources import AudioError, AudioPaths, SoundFontResource
from .storage import ProjectStore, StorageError
from .vocal_resources import VocalResource
from .vocal_score import VocalRequest, VocalSettingsRequest
from .vocals import VocalManager

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_ORIGINS = {
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}


def create_app(
    data_dir: Path | None = None,
    audio_paths: AudioPaths | None = None,
    model_settings: ModelSettings | None = None,
    adapter_factory=None,
    vocal_resource: VocalResource | None = None,
) -> FastAPI:
    store = ProjectStore(data_dir if data_dir is not None else ROOT / "data")
    resource = SoundFontResource(audio_paths or AudioPaths())
    renderer = RenderManager(store, resource)
    # Isolated stores always default to a fake configuration. Imports never read .env.
    settings_source = (
        (lambda: model_settings)
        if model_settings is not None
        else (load_model_settings if data_dir is None else ModelSettings)
    )
    generator = GenerationManager(store, resource, settings_source, adapter_factory)
    vocals = VocalManager(store, resource, vocal_resource)
    practice = PracticeStore(store.root / '.practice')

    @asynccontextmanager
    async def lifespan(app):
        yield
        vocals.close()
        renderer.close()
        generator.close()

    app = FastAPI(
        title="NeoNote · 本地音乐工作台",
        version="0.7.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    token = secrets.token_urlsafe(32)
    app.state.store = store
    app.state.renderer = renderer
    app.state.generator = generator
    app.state.vocals = vocals
    app.state.practice = practice
    app.include_router(practice_routes(practice, store))
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def protect_local_writes(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "仅允许本地工作台访问。"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and not secrets.compare_digest(
            request.headers.get("x-session-token", ""), token
        ):
            return JSONResponse(
                {"detail": "本地会话已失效，请下载未保存草稿并刷新页面。"},
                status_code=403,
            )
        length = request.headers.get("content-length", "0")
        try:
            if int(length) > 8 * 1024 * 1024:
                return JSONResponse({"detail": "请求文件过大。"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "请求长度无效。"}, status_code=400)
        response = await call_next(request)
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api/") else "no-cache"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(StorageError)
    async def storage_error(request, exception):
        return JSONResponse({"detail": str(exception)}, status_code=exception.status)

    @app.exception_handler(MidiError)
    async def midi_error(request, exception):
        return JSONResponse({"detail": str(exception)}, status_code=422)

    @app.exception_handler(AudioError)
    async def audio_error(request, exception):
        return JSONResponse({"detail": str(exception)}, status_code=exception.status)

    @app.exception_handler(ModelError)
    async def model_error(request, exception):
        return JSONResponse(
            {"detail": str(exception), "code": exception.code}, status_code=exception.status
        )

    @app.exception_handler(OSError)
    async def disk_error(request, exception):
        return JSONResponse(
            {"detail": "文件读写失败；已有保存版本仍保留，请检查磁盘空间与目录权限。"},
            status_code=500,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exception):
        errors = []
        for error in exception.errors()[:4]:
            location = ".".join(str(part) for part in error["loc"] if part != "body")
            errors.append(f"{location}：{error['msg']}")
        return JSONResponse({"detail": "数据未通过检查。" + "；".join(errors)}, status_code=422)

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "stage": 6,
            "full_song_available": True,
            "ai_enabled": True,
            "model_mode": generator.status()["mode"],
            "mp3_enabled": resource.status()["render_ready"],
            "data_directory": "data/",
        }

    @app.get("/api/session")
    def session():
        return {"token": token}

    @app.get("/api/model/status")
    def model_status():
        return generator.status()

    @app.get("/api/projects/{project_id}/generations")
    def generation_list(project_id: str):
        return {"items": generator.list_jobs(project_id)}

    @app.post("/api/projects/{project_id}/generations", status_code=202)
    def generation_start(project_id: str, body: GenerationRequest):
        return generator.start(project_id, body)

    @app.post("/api/projects/{project_id}/generations/phrase-budget")
    def phrase_budget(project_id: str, body: BudgetRequest):
        return generator.phrase_runner.preview(project_id, body)

    @app.post("/api/projects/{project_id}/lyric-groups")
    def lyric_group_edit(project_id: str, body: GroupEditRequest):
        return transaction(generator, project_id, body)

    @app.post("/api/projects/{project_id}/generations/{job_id}/cancel")
    def phrase_cancel(project_id: str, job_id: str):
        return generator.phrase_runner.cancel(project_id, job_id)

    @app.post("/api/projects/{project_id}/generations/{job_id}/retry-budget")
    def phrase_retry_budget(project_id: str, job_id: str, body: CandidateAction):
        return generator.phrase_runner.retry_preview(project_id, job_id, body)

    @app.post("/api/projects/{project_id}/generations/{job_id}/retry", status_code=202)
    def phrase_retry(project_id: str, job_id: str, body: GenerationRequest):
        return generator.retry_phrases(project_id, job_id, body)

    @app.get("/api/projects/{project_id}/generations/{job_id}")
    def generation_status(project_id: str, job_id: str):
        return generator.read(project_id, job_id)

    @app.get("/api/projects/{project_id}/generations/{job_id}/candidate")
    def generation_candidate(project_id: str, job_id: str):
        return generator.candidate(project_id, job_id).model_dump()

    @app.get("/api/projects/{project_id}/generations/{job_id}/midi")
    def generation_midi(project_id: str, job_id: str):
        return FileResponse(
            generator.midi_file(project_id, job_id),
            media_type="audio/midi",
            filename=f"neonote-candidate-{job_id[:8]}.mid",
        )

    @app.post("/api/projects/{project_id}/generations/{job_id}/apply")
    def generation_apply(project_id: str, job_id: str, body: CandidateAction):
        return generator.apply(project_id, job_id, body.base_revision)

    @app.get("/api/projects/{project_id}/review")
    def generation_review_status(project_id: str):
        return generator.review_status(project_id)

    @app.post("/api/projects/{project_id}/review")
    def generation_review(project_id: str, body: ReviewRequest):
        return generator.review(project_id, body)

    @app.post("/api/projects/{project_id}/lyrics-repair")
    def repair_lyrics(project_id: str, body: LyricRepairRequest):
        return generator.repair_alignment(project_id, body)

    @app.get("/api/audio/resources")
    def audio_resources():
        return resource.status()

    @app.get("/api/audio/soundfont")
    def soundfont(sha256: str):
        if sha256 != resource.info()["sha256"]:
            raise AudioError("音色资源已改变，请刷新页面后重新加载。", 409)
        return FileResponse(resource.paths.soundfont, media_type="audio/x-sf2")

    @app.get("/api/projects")
    def list_projects():
        return store.list()

    @app.post("/api/projects", status_code=201)
    def create_project(body: CreateProject):
        return store.create(body.name)

    @app.get("/api/trash")
    def list_deleted_projects():
        return store.list_trash()

    @app.post("/api/trash/{project_id}/restore")
    def restore_deleted_project(project_id: str):
        return store.restore_deleted(project_id)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return store.get(project_id)

    @app.put("/api/projects/{project_id}")
    def save_project(project_id: str, body: SaveProject):
        return store.save(project_id, body.base_revision, body.state, body.label)

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str, body: RevisionAction):
        # Keep the same manager -> store lock order as task creation and adoption.
        # Check and move under these locks so a new task cannot start in between.
        with generator.lock, renderer.lock, vocals.lock, store.lock:
            store.check_base(store.header(project_id), body.base_revision)
            if (
                generator.has_active_jobs(project_id)
                or renderer.has_active_jobs(project_id)
                or vocals.has_active_jobs(project_id)
            ):
                raise StorageError("这首歌正在生成或渲染，请等待任务结束后再删除。", 409)
            return store.delete(project_id, body.base_revision)

    @app.get("/api/projects/{project_id}/history")
    def history(project_id: str):
        return store.history(project_id)

    @app.post("/api/projects/{project_id}/undo")
    def undo(project_id: str, body: RevisionAction):
        return store.move(project_id, body.base_revision, "undo")

    @app.post("/api/projects/{project_id}/redo")
    def redo(project_id: str, body: RevisionAction):
        return store.move(project_id, body.base_revision, "redo")

    @app.post("/api/projects/{project_id}/restore")
    def restore(project_id: str, body: RestoreRevision):
        return store.restore(project_id, body.base_revision, body.revision_id)

    async def read_upload(file: UploadFile, extension: tuple[str, ...], limit: int):
        name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not name or len(name) > 255 or not name.lower().endswith(extension):
            raise HTTPException(422, "文件扩展名不受支持。")
        content = await file.read(limit + 1)
        await file.close()
        if not content or len(content) > limit:
            raise HTTPException(413, "文件为空或超过上传大小限制。")
        return name, content

    @app.post("/api/projects/{project_id}/lyrics")
    async def upload_lyrics(
        project_id: str, base_revision: Annotated[str, Form()], file: Annotated[UploadFile, File()]
    ):
        name, content = await read_upload(file, (".txt",), 512000)
        try:
            text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
            if "\x00" in text:
                raise ValueError
        except (UnicodeError, ValueError):
            raise HTTPException(422, "请上传 UTF-8 编码的纯文本歌词；原作品未改变。") from None

        def transform(state):
            state.inputs.lyrics_text = text
            state.inputs.selected_lines = [
                i for i, line in enumerate(text.split("\n")) if line.strip()
            ]

        return store.upload(project_id, base_revision, content, name, "lyrics", transform)

    @app.post("/api/projects/{project_id}/midi")
    async def upload_midi(
        project_id: str, base_revision: Annotated[str, Form()], file: Annotated[UploadFile, File()]
    ):
        name, content = await read_upload(file, (".mid", ".midi"), 2 * 1024 * 1024)
        score = import_midi(content)

        def transform(state):
            state.score = score
            state.composition = None

        return store.upload(project_id, base_revision, content, name, "midi", transform)

    @app.post("/api/projects/{project_id}/midi-export")
    def midi_export(project_id: str, body: RevisionAction):
        project = store.get(project_id)
        store.check_base(project, body.base_revision)
        state = SongState.model_validate(project["revision"]["state"])
        if not any(track.notes for track in state.score.tracks):
            raise HTTPException(422, "没有音符可导出，请先录制并加入编曲，或导入 MIDI。")
        content = export_midi(state.score)
        store.export(project_id, body.base_revision, content)
        return Response(
            content,
            media_type="audio/midi",
            headers={
                "Content-Disposition": f'attachment; filename="neonote-{project_id[:8]}-v{project["revision"]["number"]}.mid"'
            },
        )

    @app.get("/api/projects/{project_id}/renders")
    def render_list(project_id: str):
        return {"items": renderer.list_jobs(project_id)}

    @app.post("/api/projects/{project_id}/renders", status_code=202)
    def render_start(project_id: str, body: RenderRequest):
        return renderer.start(project_id, body)

    @app.get("/api/projects/{project_id}/renders/{job_id}")
    def render_status(project_id: str, job_id: str):
        return renderer.read(project_id, job_id)

    @app.get("/api/projects/{project_id}/renders/{job_id}/audio/{extension}")
    def rendered_audio(project_id: str, job_id: str, extension: str, download: bool = False):
        path, item = renderer.audio_file(project_id, job_id, extension)
        return FileResponse(
            path,
            media_type="audio/wav" if extension == "wav" else "audio/mpeg",
            filename=f"neonote-{project_id[:8]}-v{item['revision_number']}-{item['mode']}.{extension}",
            content_disposition_type="attachment" if download else "inline",
        )

    @app.get("/api/vocals/resources")
    def vocal_resources():
        return vocals.resources.status()

    @app.get("/api/projects/{project_id}/vocal-settings")
    def vocal_settings(project_id: str):
        return vocals.settings_status(project_id)

    @app.post("/api/projects/{project_id}/vocal-settings")
    def vocal_settings_save(project_id: str, body: VocalSettingsRequest):
        return vocals.save_settings(project_id, body)

    @app.get("/api/projects/{project_id}/vocals")
    def vocal_list(project_id: str):
        return {"items": vocals.list_jobs(project_id)}

    @app.post("/api/projects/{project_id}/vocals", status_code=202)
    def vocal_start(project_id: str, body: VocalRequest):
        return vocals.start(project_id, body)

    @app.get("/api/projects/{project_id}/vocals/{job_id}")
    def vocal_job(project_id: str, job_id: str):
        return vocals.read(project_id, job_id)

    @app.post("/api/projects/{project_id}/vocals/{job_id}/cancel")
    def vocal_cancel(project_id: str, job_id: str):
        return vocals.cancel(project_id, job_id)

    @app.get("/api/projects/{project_id}/vocals/{job_id}/assets/{name}")
    def vocal_asset(project_id: str, job_id: str, name: str, download: bool = False):
        content, item = vocals.asset_content(project_id, job_id, name)
        media = (
            "audio/wav"
            if name.endswith(".wav")
            else "audio/mpeg"
            if name.endswith(".mp3")
            else "application/octet-stream"
        )
        disposition = "attachment" if download else "inline"
        return Response(
            content,
            media_type=media,
            headers={
                "Content-Disposition": f'{disposition}; filename="neonote-{project_id[:8]}-v{item["revision_number"]}-{name}"'
            },
        )

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/") or any(part.startswith(".") for part in path.split("/")):
            raise HTTPException(404, "接口不存在。")
        distribution = ROOT / "frontend" / "app" / "dist"
        if not distribution.is_dir():
            raise HTTPException(503, "正式前端尚未构建，请运行 scripts/start_local.ps1。")
        candidate = (distribution / (path or "index.html")).resolve()
        if not candidate.is_relative_to(distribution.resolve()):
            raise HTTPException(404, "文件不存在。")
        if candidate.is_file():
            return FileResponse(candidate)
        if Path(path).suffix:
            raise HTTPException(404, "文件不存在。")
        return FileResponse(distribution / "index.html")

    return app


app = create_app()
