"""Independent local audio projects. No song imports, model API or .env access."""

import hashlib
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, RLock
from typing import Annotated, Literal

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field

from .models import Model, new_id
from .resources import ROOT, AudioPaths
from .storage import ProjectStore, StorageError, now
from .transcription_score import (
    ScoreSettings,
    TranscriptionNote,
    clip_midi_bytes,
    midi_bytes,
    read_midi,
    score,
    validate_notes,
)
from .vocal_process import owned_process

ACTIVE = {"queued", "decoding", "separating", "transcribing", "validating", "cancelling"}
ASSETS = ROOT / "tools/transcription"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resources():
    try:
        manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
        for asset in manifest["assets"]:
            path = ASSETS / asset["file"]
            if (
                not path.resolve().is_relative_to(ASSETS)
                or not path.is_file()
                or path.stat().st_size != asset["bytes"]
            ):
                raise ValueError()
        return {"ready": True, "manifest": manifest, "error": None}
    except (OSError, ValueError, KeyError):
        return {
            "ready": False,
            "manifest": None,
            "error": "扒谱资源尚未准备，请按说明运行安装及 prepare_transcription.py。",
        }


class Start(Model):
    base: str | None = None
    start: float = Field(default=0, ge=0, lt=600)
    end: float = Field(gt=0, le=600)
    mode: Literal["solo", "mix"] = "solo"


class Base(Model):
    base: str | None = None


class Save(Base):
    result_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    notes: list[TranscriptionNote] = Field(min_length=1, max_length=5000)
    settings: ScoreSettings | None = None


class Cancelled(Exception):
    pass


class TranscriptionManager:
    def __init__(self, root, paths=None, worker=None):
        self.store = ProjectStore(root)
        self.paths = paths or AudioPaths()
        self.lock = RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcription")
        self.active = None
        self.cancel_event = Event()
        self.process = None
        self.closed = False
        self.worker = worker  # dependency injection only; production has no mock entry

    def recover(self):
        """Only the selected app's startup recovers jobs; importing main never does."""
        for path in self.store.root.glob("*/jobs/*/job.json"):
            job = self.store.read_json(path)
            if job["status"] in ACTIVE:
                job.update(
                    status="interrupted", error="服务重启中断，请明确重新处理。", updated_at=now()
                )
                self.store.write_json(path, job)

    def directory(self, pid):
        path = self.store.directory(pid)
        if not (path / "project.json").is_file():
            raise StorageError("扒谱工程不存在。", 404)
        return path

    def header(self, pid):
        return self.store.read_json(self.directory(pid) / "project.json")

    def check(self, pid, base):
        project = self.header(pid)
        if project["current_revision"] != base:
            raise StorageError("扒谱修订已改变，请重新打开工程并保留草稿。", 409)
        return project

    def bounded_path(self, pid, relative):
        base = self.directory(pid)
        path = base / relative
        if (
            not path.resolve().is_relative_to(base)
            or path.is_symlink()
            or any(p.is_symlink() for p in path.parents)
        ):
            raise StorageError("文件路径无效。", 400)
        return path

    def list(self):
        return sorted(
            [self.store.read_json(p) for p in self.store.root.glob("*/project.json")],
            key=lambda p: p["created_at"],
            reverse=True,
        )

    def detail(self, pid):
        with self.lock:
            directory = self.directory(pid)
            return {
                "project": self.header(pid),
                "jobs": [
                    self.store.read_json(p) for p in sorted(directory.glob("jobs/*/job.json"))
                ],
                "results": [
                    self.store.read_json(p) for p in directory.glob("results/*/result.json")
                ],
                "revisions": [
                    self.store.read_json(p) for p in directory.glob("revisions/*/revision.json")
                ],
            }

    def probe(self, path):
        try:
            result = subprocess.run(
                [
                    str(self.paths.ffprobe),
                    "-v",
                    "error",
                    "-show_format",
                    "-show_streams",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                timeout=30,
                check=True,
            )
            info = json.loads(result.stdout)
            stream = next(s for s in info["streams"] if s["codec_type"] == "audio")
            duration = float(info["format"]["duration"])
            if (
                not 0 < duration <= 600
                or info["format"]["format_name"] not in ("mp3", "wav")
                or not 8000 <= int(stream["sample_rate"]) <= 192000
                or stream["channels"] not in (1, 2)
                or stream["codec_name"]
                not in (
                    "mp3",
                    "pcm_s16le",
                    "pcm_s24le",
                    "pcm_s32le",
                    "pcm_f32le",
                    "pcm_u8",
                    "pcm_f64le",
                )
            ):
                raise ValueError()
            return {
                "duration": duration,
                "channels": stream["channels"],
                "sample_rate": int(stream["sample_rate"]),
                "codec": stream["codec_name"],
            }
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration):
            raise StorageError(
                "文件不是支持的MP3/PCM WAV，或超过10分钟/双声道；请转换后重试。", 422
            ) from None

    async def upload(self, file):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".mp3", ".wav"):
            raise StorageError("请上传MP3或WAV文件。", 422)
        pid = new_id()
        directory = self.store.directory(pid)
        source = directory / "source" / ("original" + suffix)
        source.parent.mkdir(parents=True)
        try:
            count = 0
            with source.open("xb") as target:
                while data := await file.read(1024 * 1024):
                    count += len(data)
                    if count > 100 * 1024 * 1024:
                        raise StorageError("上传文件超过100 MiB。", 413)
                    target.write(data)
            info = self.probe(source)
            project = {
                "id": pid,
                "name": Path(file.filename.replace("\\", "/")).name[:160],
                "created_at": now(),
                "current_revision": None,
                "source": {"file": source.name, "sha256": digest(source), "bytes": count, **info},
                "extensions": {"copy_to_practice": False, "copy_to_creation": False},
            }
            self.store.write_json(directory / "project.json", project)
            return self.detail(pid)
        except Exception:
            # Only the just-created private upload directory, never existing projects.
            if directory.resolve().parent == self.store.root and not directory.is_symlink():
                shutil.rmtree(directory)
            raise
        finally:
            await file.close()

    def job(self, pid, jid):
        if len(jid) != 32 or any(c not in "0123456789abcdef" for c in jid):
            raise StorageError("任务不存在。", 404)
        path = self.directory(pid) / "jobs" / jid / "job.json"
        if not path.exists():
            raise StorageError("任务不存在。", 404)
        return self.store.read_json(path)

    def write_job(self, pid, job):
        job["updated_at"] = now()
        self.store.write_json(self.directory(pid) / "jobs" / job["id"] / "job.json", job)

    def start(self, pid, request):
        with self.lock:
            project = self.check(pid, request.base)
            if any(j["status"] == "awaiting_review" for j in self.detail(pid)["jobs"]):
                raise StorageError("这个工程已有分离结果等待试听，请继续或取消该任务。", 409)
            if (
                not 0.05 <= request.end - request.start <= 60
                or request.end > project["source"]["duration"] + 0.001
            ):
                raise StorageError("请选择文件内0.05–60秒的片段。", 422)
            ready = resources()
            if not self.worker and not ready["ready"]:
                raise StorageError(ready["error"], 503)
            job = {
                "id": new_id(),
                "project_id": pid,
                "base": request.base,
                "parameters": request.model_dump(),
                "status": "queued",
                "created_at": now(),
                "error": None,
                "result_id": None,
                "models": ready["manifest"],
                "pipeline": {
                    "version": "neonote-transcription-v2",
                    "boundary_policy": "clip-intersection-v1",
                    "worker_sha256": digest(Path(__file__).with_name("transcription_worker.py")),
                    "separator": {"shifts": 0, "overlap": 0.25},
                    "transkun_window": "official config 16s / hop8s",
                    "device": "cpu",
                    "threads": 4,
                },
            }
            self.dispatch(pid, job)
            return job

    def dispatch(self, pid, job):
        if self.closed or self.active:
            raise StorageError("已有扒谱任务正在处理，请完成或取消后重试。", 409)
        self.active = (pid, job["id"])
        self.cancel_event = Event()
        self.write_job(pid, job)
        self.executor.submit(self.run, pid, job["id"])

    def resume(self, pid, jid, base):
        with self.lock:
            self.check(pid, base)
            job = self.job(pid, jid)
            if job["status"] != "awaiting_review" or job["base"] != base:
                raise StorageError("只可继续当前版本且等待试听确认的分离任务。", 409)
            job["confirmed_at"] = now()
            job["status"] = "queued"
            self.dispatch(pid, job)
            return job

    def cancel(self, pid, jid):
        with self.lock:
            job = self.job(pid, jid)
            if self.active == (pid, jid):
                self.cancel_event.set()
                job["status"] = "cancelling"
                if self.process and self.process.poll() is None:
                    self.process.kill()
            elif job["status"] == "awaiting_review":
                job["status"] = "cancelled"
            self.write_job(pid, job)
            return job

    def stage(self, pid, jid, status):
        with self.lock:
            if self.cancel_event.is_set():
                raise Cancelled()
            job = self.job(pid, jid)
            job["status"] = status
            self.write_job(pid, job)

    def command(self, args, cwd, log):
        with log.open("ab") as stream, owned_process(args, stdout=stream, stderr=stream) as process:
            with self.lock:
                self.process = process
                if self.cancel_event.is_set():
                    process.kill()
            try:
                deadline = time.monotonic() + 1800
                while process.poll() is None:
                    if self.cancel_event.wait(0.1):
                        process.kill()
                        raise Cancelled()
                    if time.monotonic() > deadline:
                        process.kill()
                        raise StorageError("处理超过30分钟；请缩短片段后重新处理。", 422)
                if self.cancel_event.is_set():
                    raise Cancelled()
                if process.returncode:
                    raise StorageError(
                        "本地处理进程失败；请检查任务日志及资源，已有结果保留。", 422
                    )
            finally:
                with self.lock:
                    self.process = None

    def infer(self, operation, input, output, work, log):
        if self.worker:
            self.worker(operation, input, output, self.cancel_event)
            return
        info = resources()
        if not info["ready"]:
            raise StorageError(info["error"], 503)
        job = self.store.read_json(work / "job.json")
        if info["manifest"] != job["models"]:
            raise StorageError("资源准备清单已变化，请明确新建任务；不混用模型版本。", 409)
        for asset in info["manifest"]["assets"]:
            if digest(ASSETS / asset["file"]) != asset["sha256"]:
                raise StorageError("模型权重哈希改变，请重新准备并检查资源。", 422)
        request = {
            "operation": operation,
            "input": str(input),
            "output": str(output),
            "assets": str(ASSETS),
        }
        file = work / (operation + ".json")
        self.store.write_json(file, request)
        self.command(
            [sys.executable, str(Path(__file__).with_name("transcription_worker.py")), str(file)],
            ROOT,
            log,
        )

    def run(self, pid, jid):
        work = self.directory(pid) / "jobs" / jid
        log = work / "worker.log"
        try:
            job = self.job(pid, jid)
            p = job["parameters"]
            duration = p["end"] - p["start"]
            clip = work / "clip.wav"
            piano = work / "piano.wav"
            if not job.get("confirmed_at"):
                self.stage(pid, jid, "decoding")
                source = self.directory(pid) / "source" / self.header(pid)["source"]["file"]
                self.command(
                    [
                        str(self.paths.ffmpeg),
                        "-nostdin",
                        "-v",
                        "error",
                        "-y",
                        "-ss",
                        str(p["start"]),
                        "-i",
                        str(source),
                        "-t",
                        str(duration),
                        "-vn",
                        "-ar",
                        "44100",
                        "-ac",
                        "2",
                        "-c:a",
                        "pcm_s16le",
                        str(clip),
                    ],
                    ROOT,
                    log,
                )
                if abs(self.probe(clip)["duration"] - duration) > 0.05:
                    raise StorageError("解码片段长度不符。", 422)
                if p["mode"] == "mix":
                    self.stage(pid, jid, "separating")
                    self.infer("separate", clip, piano, work, log)
                    if abs(self.probe(piano)["duration"] - duration) > 0.05:
                        raise StorageError("分离音频长度不符。", 422)
                    with self.lock:
                        if self.cancel_event.is_set():
                            raise Cancelled()
                        job = self.job(pid, jid)
                        job.update(status="awaiting_review", separation_sha256=digest(piano))
                        self.write_job(pid, job)
                    return
            if p["mode"] == "mix" and digest(piano) != job["separation_sha256"]:
                raise StorageError("分离音频已变化，不能继续。", 422)
            self.stage(pid, jid, "transcribing")
            raw = work / "performance.mid"
            self.infer("transcribe", piano if p["mode"] == "mix" else clip, raw, work, log)
            self.stage(pid, jid, "validating")
            boundary_report = {}
            notes, controls = read_midi(raw, duration, boundary_report)
            rid = new_id()
            temporary = work / "publish"
            temporary.mkdir()
            shutil.copyfile(raw, temporary / "performance.mid")
            self.store.write_bytes(temporary / "clip.mid", clip_midi_bytes(raw, duration))
            shutil.copyfile(clip, temporary / "clip.wav")
            if p["mode"] == "mix":
                shutil.copyfile(piano, temporary / "piano.wav")
            result = {
                "id": rid,
                "job_id": jid,
                "created_at": now(),
                "clip_start": p["start"],
                "duration": duration,
                "mode": p["mode"],
                "notes": [n.model_dump() for n in notes],
                "controls": controls,
                "boundary_report": boundary_report,
                "source_sha256": self.header(pid)["source"]["sha256"],
                "models": job["models"],
                "pipeline": job["pipeline"],
                "alignment": {
                    "basis": "clip_seconds",
                    "source_offset": p["start"],
                    "decoder_offset": 0,
                    "separator_offset": 0,
                    "model_offset": 0,
                    "offset_status": "no additional correction measured",
                },
                "files": {f.name: digest(f) for f in temporary.iterdir()},
            }
            self.store.write_json(temporary / "result.json", result)
            with self.lock:
                if self.cancel_event.is_set():
                    raise Cancelled()
                self.check(pid, job["base"])
                dest = self.directory(pid) / "results" / rid
                dest.parent.mkdir(exist_ok=True)
                temporary.rename(dest)
                job = self.job(pid, jid)
                job.update(status="succeeded", result_id=rid)
                self.write_job(pid, job)
        except Exception as error:
            with self.lock:
                job = self.job(pid, jid)
                job.update(
                    status="cancelled"
                    if isinstance(error, Cancelled) or self.cancel_event.is_set()
                    else "failed",
                    error=None
                    if isinstance(error, Cancelled)
                    else str(error)
                    if isinstance(error, StorageError)
                    else "本地处理失败，请检查任务日志；旧结果仍保留。",
                )
                self.write_job(pid, job)
        finally:
            with self.lock:
                self.active = None

    def save(self, pid, request):
        with self.lock:
            project = self.check(pid, request.base)
            if self.active and self.active[0] == pid:
                raise StorageError("请等待任务结束再保存校对。", 409)
            base = self.bounded_path(pid, f"results/{request.result_id}/result.json")
            if not base.exists():
                raise StorageError("转录结果不存在。", 404)
            result = self.store.read_json(base)
            validate_notes(request.notes, result["duration"])
            revision = {
                "id": new_id(),
                "parent": request.base,
                "result_id": request.result_id,
                "created_at": now(),
                "notes": [n.model_dump() for n in request.notes],
                "settings": request.settings.model_dump() if request.settings else None,
                "score": None,
                "notation_error": None,
                "compiler_sha256": digest(Path(__file__).with_name("transcription_score.py")),
            }
            files = {"performance.mid": midi_bytes(request.notes)}
            if request.settings:
                try:
                    prepared, midi = score(request.notes, request.settings)
                    revision["score"] = prepared
                    files.update(
                        {"score.mid": midi, "score.musicxml": prepared["musicxml"].encode("utf-8")}
                    )
                except StorageError as error:
                    revision["notation_error"] = str(error)
            directory = self.directory(pid) / "revisions" / revision["id"]
            revision["files"] = {
                name: hashlib.sha256(data).hexdigest() for name, data in files.items()
            }
            for name, data in files.items():
                self.store.write_bytes(directory / name, data)
            self.store.write_json(directory / "revision.json", revision)
            project["current_revision"] = revision["id"]
            self.store.write_json(self.directory(pid) / "project.json", project)
            return revision

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel_event.set()
            if self.process and self.process.poll() is None:
                self.process.kill()
        self.executor.shutdown(wait=True, cancel_futures=True)


def routes(manager):
    router = APIRouter(prefix="/api/transcriptions")

    @router.get("/resources")
    def ready():
        return resources()

    @router.get("")
    def listing():
        return manager.list()

    @router.post("")
    async def upload(file: Annotated[UploadFile, File()]):
        return await manager.upload(file)

    @router.get("/{pid}")
    def detail(pid: str):
        return manager.detail(pid)

    @router.get("/{pid}/source")
    def source(pid: str):
        project = manager.header(pid)
        path = manager.bounded_path(pid, "source/" + project["source"]["file"])
        return FileResponse(path, media_type="audio/mpeg" if path.suffix == ".mp3" else "audio/wav")

    @router.post("/{pid}/jobs")
    def start(pid: str, body: Start):
        return manager.start(pid, body)

    @router.post("/{pid}/jobs/{jid}/continue")
    def resume(pid: str, jid: str, body: Base):
        return manager.resume(pid, jid, body.base)

    @router.post("/{pid}/jobs/{jid}/cancel")
    def cancel(pid: str, jid: str):
        return manager.cancel(pid, jid)

    @router.post("/{pid}/revisions")
    def save(pid: str, body: Save):
        return manager.save(pid, body)

    @router.get("/{pid}/files/{kind}/{identity}/{name}")
    def download(pid: str, kind: str, identity: str, name: str):
        if kind not in ("results", "revisions", "jobs") or name not in (
            "performance.mid",
            "clip.mid",
            "score.mid",
            "score.musicxml",
            "clip.wav",
            "piano.wav",
        ):
            raise StorageError("文件不可下载。", 404)
        with manager.lock:
            path = manager.bounded_path(pid, f"{kind}/{identity}/{name}")
            if kind == "jobs":
                job = manager.job(pid, identity)
                if job["status"] != "awaiting_review" or name not in ("clip.wav", "piano.wav"):
                    raise StorageError("中间文件不可下载。", 404)
            elif not (
                path.parent / ("result.json" if kind == "results" else "revision.json")
            ).is_file():
                raise StorageError("完整结果尚未发布。", 404)
            if not path.is_file():
                raise StorageError("该版本没有此文件。", 404)
            if kind != "jobs":
                manifest = manager.store.read_json(
                    path.parent / ("result.json" if kind == "results" else "revision.json")
                )
                if manifest["files"].get(name) != digest(path):
                    raise StorageError("版本文件校验失败，拒绝下载。", 409)
            return FileResponse(path, filename=f"neonote-{identity[:8]}-{name}")

    return router
