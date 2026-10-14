"""Noncommercial local singing tasks, independent assets and immutable configuration."""

import hashlib
import json
import math
import re
import shutil
import time
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock

from .midi import export_midi
from .models import SongState, new_id
from .render import RenderManager
from .resources import AudioError, SoundFontResource
from .storage import ProjectStore, StorageError, now
from .vocal_process import owned_process
from .vocal_resources import VocalResource
from .vocal_score import (
    SAMPLE_RATE,
    VocalRequest,
    VocalSettings,
    VocalSettingsRequest,
    convert_score,
    fingerprint,
    reading_identity,
    score_check,
    validate_pronunciation_targets,
)

ACTIVE = ("queued", "running", "cancelling")
TERMINAL = ("completed", "failed", "cancelled", "interrupted")


def digest_file(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class Cancelled(Exception):
    pass


class VocalManager:
    def __init__(self, store: ProjectStore, audio: SoundFontResource, resources=None):
        self.store, self.audio = store, audio
        self.resources = resources or VocalResource()
        self.lock = RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="song-vocal")
        self.active = {}
        self.job_projects = {}
        self.cancellations = {}
        self.processes = {}
        self.closed = False

    def has_active_jobs(self, project_id):
        with self.lock:
            return project_id in self.job_projects.values()

    def close(self):
        with self.lock:
            self.closed = True
            for event in self.cancellations.values():
                event.set()
            for process in self.processes.values():
                if process.poll() is None:
                    process.kill()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def directory(self, project_id, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise AudioError("演唱任务不存在。", 404)
        directory = self.store.directory(project_id) / "exports/vocals" / job_id
        if directory.resolve() != directory or any(
            p.is_symlink() for p in [directory, *directory.parents]
        ):
            raise AudioError("演唱保存路径无效。", 400)
        return directory

    def _settings_root(self, project_id):
        path = self.store.directory(project_id) / "vocal-settings"
        if path.resolve() != path or any(p.is_symlink() for p in [path, *path.parents]):
            raise AudioError("演唱配置路径无效。", 400)
        return path

    def settings(self, project_id, revision_id=None):
        with self.lock, self.store.lock:
            project = self.store.get(project_id)
            revision_id = revision_id or project["current_revision"]
            if revision_id not in self.store.header(project_id)["revision_ids"]:
                raise AudioError("歌曲版本不存在。", 404)
            root = self._settings_root(project_id)
            index = (
                self.store.read_json(root / "index.json") if (root / "index.json").is_file() else {}
            )
            key = index.get(revision_id)
            if key:
                if not re.fullmatch(r"[a-f0-9]{64}", key):
                    raise AudioError("演唱配置记录损坏。", 500)
                item = self.store.read_json(root / f"{key}.json")
                if fingerprint(item["settings"]) != key:
                    raise AudioError("演唱配置校验失败。", 500)
                VocalSettings.model_validate(item["settings"])
                return item
            return {
                "revision_id": revision_id,
                "hash": None,
                "settings": VocalSettings().model_dump(),
            }

    def settings_status(self, project_id):
        with self.lock, self.store.lock:
            project = self.store.get(project_id)
            selected = self.settings(project_id)
            settings = VocalSettings.model_validate(selected["settings"])
            state = SongState.model_validate(project["revision"]["state"])
            return {
                **selected,
                "revision_id": project["current_revision"],
                "score_check": score_check(state, settings),
                "lyrics_identity_hash": reading_identity(state),
            }

    def save_settings(self, project_id, body: VocalSettingsRequest):
        with self.lock, self.store.lock:
            project = self.store.get(project_id)
            self.store.check_base(project, body.base_revision)
            previous = self.settings(project_id)
            if previous["hash"] != body.expected_settings_hash:
                raise AudioError("演唱设置已在其他页面改变，请重新打开后再保存。", 409)
            state = SongState.model_validate(project["revision"]["state"])
            # A valid configuration can be saved before the score is ready to sing.
            if body.lyrics_identity_hash and body.lyrics_identity_hash != reading_identity(state):
                raise AudioError("歌词身份或字词范围已改变，旧读音不能套到新字；请重新选择读音。", 409)
            # Score conversion remains mandatory when a singing task is started.
            validate_pronunciation_targets(state, body.settings)
            self.resources.validate_readings(body.settings)
            payload = body.settings.model_dump()
            key = fingerprint(payload)
            root = self._settings_root(project_id)
            item = {"hash": key, "settings": payload, "created_at": now()}
            if not (root / f"{key}.json").exists():
                self.store.write_json(root / f"{key}.json", item)
            else:
                item = self.store.read_json(root / f"{key}.json")
            index = (
                self.store.read_json(root / "index.json") if (root / "index.json").is_file() else {}
            )
            index[body.base_revision] = key
            self.store.write_json(root / "index.json", index)
            return {
                **item,
                "revision_id": body.base_revision,
                "score_check": score_check(state, body.settings),
                "lyrics_identity_hash": reading_identity(state),
            }

    def _read(self, project_id, job_id):
        item = self.store.read_json(self.directory(project_id, job_id) / "job.json")
        if item.get("id") != job_id or item.get("project_id") != project_id:
            raise AudioError("演唱记录损坏。", 500)
        if item["status"] in ACTIVE and job_id not in self.active:
            item.update(
                status="interrupted",
                phase="服务曾中断",
                error="服务曾中断，未自动重试；已有成功结果保留。",
                updated_at=now(),
                files={},
            )
            self._write(item)
        return item

    def read(self, project_id, job_id):
        with self.lock, self.store.lock:
            self.store.get(project_id)
            item = self._read(project_id, job_id)
            settings = self.settings(project_id)
            same_revision = item["revision_id"] == self.store.header(project_id)["current_revision"]
            same_settings = item["settings_hash"] == settings["hash"]
            resource = self.resources.status()
            same_resource = (
                resource["ready"]
                and item["resource"]["resource_hash"] == resource["singers"][0]["resource_hash"]
            )
            return {
                **item,
                "current": same_revision and same_settings and same_resource,
                "stale_reason": None
                if same_revision and same_settings and same_resource
                else "非当前保存版本"
                if not same_revision
                else "演唱设置已改变"
                if not same_settings
                else "模型资源已改变或缺失",
            }

    def list_jobs(self, project_id):
        with self.lock, self.store.lock:
            self.store.get(project_id)
            root = self.store.directory(project_id) / "exports/vocals"
            if root.resolve() != root:
                raise AudioError("演唱路径无效。", 400)
            if not root.is_dir():
                return []
            jobs = [
                self.read(project_id, p.name)
                for p in root.iterdir()
                if re.fullmatch(r"[a-f0-9]{32}", p.name) and (p / "job.json").is_file()
            ]
            return sorted(jobs, key=lambda j: j["created_at"], reverse=True)

    def _write(self, item):
        self.store.header(item["project_id"])  # Never recreate a deleted project.
        self.store.write_json(self.directory(item["project_id"], item["id"]) / "job.json", item)

    def start(self, project_id, body: VocalRequest):
        with self.lock, self.store.lock:
            if self.closed:
                raise AudioError("服务正在停止。", 503)
            project = self.store.get(project_id)
            self.store.check_base(project, body.base_revision)
            selected = self.settings(project_id)
            if selected["hash"] != body.settings_hash:
                raise AudioError("请先保存当前演唱设置；其他页面更改时须重新打开。", 409)
            settings = VocalSettings.model_validate(selected["settings"])
            state = SongState.model_validate(project["revision"]["state"])
            request, accompaniment, removed = convert_score(state, settings)
            resource = self.resources.info()
            if request["protocol"] not in resource.get("protocols", [resource["protocol"]]):
                raise AudioError("当前人声宿主未支持这个协议，请构建新版宿主；原演唱保留。", 503)
            self.resources.validate_readings(settings)
            if settings.runner == "DirectML" and settings.gpu not in {
                device["id"] for device in resource["devices"]
            }:
                raise AudioError("选择的显卡当前不可用，请重新选择显卡或明确改用 CPU。", 503)
            font = self.audio.info()
            if not all(
                p.is_file()
                for p in (
                    self.audio.paths.fluidsynth,
                    self.audio.paths.ffmpeg,
                    self.audio.paths.ffprobe,
                )
            ):
                raise AudioError("伴奏/混音工具缺失，请检查 tools。", 503)
            if len(self.active) >= 2:
                raise AudioError("演唱队列已满，请等待或取消已有任务。", 429)
            if self.has_active_jobs(project_id):
                raise AudioError("这首歌已有演唱任务，请等待或取消后再生成。", 409)
            # Exact same-version dry stems can be reused for a gain-only remix.
            vocal_hash = fingerprint(
                {
                    "request": request,
                    "model": resource["resource_hash"],
                    "runner": settings.runner,
                    "gpu": settings.gpu,
                }
            )
            backing_hash = fingerprint(
                {"score": accompaniment.model_dump(), "font": font["sha256"]}
            )
            previous = next(
                (
                    j
                    for j in self.list_jobs(project_id)
                    if j["status"] == "completed"
                    and j["revision_id"] == body.base_revision
                    and j["vocal_hash"] == vocal_hash
                    and j["backing_hash"] == backing_hash
                    and self._valid_files(j)
                ),
                None,
            )
            job_id = new_id()
            directory = self.directory(project_id, job_id)
            item = {
                "id": job_id,
                "project_id": project_id,
                "revision_id": body.base_revision,
                "revision_number": project["revision"]["number"],
                "settings_hash": body.settings_hash,
                "vocal_hash": vocal_hash,
                "backing_hash": backing_hash,
                "input_hash": fingerprint(state.model_dump()),
                "resource": resource,
                "soundfont_hash": font["sha256"],
                "status": "queued",
                "phase": "等待本地演唱",
                "settings": settings.model_dump(),
                "created_at": now(),
                "updated_at": now(),
                "error": None,
                "files": {},
                "human_review": "unassessed",
                "mock": False,
                "timeline_seconds": state.score.seconds_at(state.score.end_tick),
                "frames": request["frames"],
                "duration_seconds": request["frames"] / SAMPLE_RATE,
                "removed_guide_note_ids": removed,
                "reused_from": previous["id"] if previous else None,
            }
            self.store.write_json(
                directory / "input.json",
                {"state": state.model_dump(), "settings": settings.model_dump()},
            )
            self._write(item)
            event = Event()
            self.active[job_id] = None
            self.cancellations[job_id] = event
            self.job_projects[job_id] = project_id
            self.active[job_id] = self.executor.submit(
                self._render, item, request, accompaniment, previous, event
            )
            return dict(item)

    def cancel(self, project_id, job_id):
        with self.lock, self.store.lock:
            self.store.get(project_id)
            item = self._read(project_id, job_id)
            if job_id not in self.active:
                return item
            self.cancellations[job_id].set()
            future = self.active[job_id]
            if future and future.cancel():
                item.update(status="cancelled", phase="已取消", files={}, updated_at=now())
                self._forget(job_id)
            else:
                item.update(status="cancelling", phase="正在取消演唱", updated_at=now())
            self._write(item)
            return dict(item)

    def _forget(self, job_id):
        self.active.pop(job_id, None)
        self.job_projects.pop(job_id, None)
        self.cancellations.pop(job_id, None)
        self.processes.pop(job_id, None)

    def phase(self, item, name):
        with self.lock, self.store.lock:
            if self.cancellations[item["id"]].is_set():
                raise Cancelled
            item.update(status="running", phase=name, updated_at=now())
            self._write(item)

    def command(self, item, args, work, name, event, timeout=120):
        if event.is_set():
            raise Cancelled
        start = time.monotonic()
        output = work / f"{name}.stdout.log"
        with (
            output.open("wb") as stdout,
            (work / f"{name}.stderr.log").open("wb") as stderr,
            owned_process(args, stdout, stderr) as process,
        ):
            with self.lock:
                self.processes[item["id"]] = process
            phase = ""
            while process.poll() is None:
                if event.wait(0.15):
                    raise Cancelled
                if time.monotonic() - start > timeout:
                    raise AudioError("演唱或音频处理超时，实际进程已停止；可检查后重新发起。", 504)
                if name == "worker":
                    content = output.read_text(encoding="utf-8", errors="replace").splitlines()
                    for line in content[-2:]:
                        try:
                            update = json.loads(line)
                            text = f"生成演唱片段 {update['completed'] + 1}/{update['total']}"
                            if text != phase:
                                phase = text
                                self.phase(item, text)
                        except (ValueError, KeyError, TypeError):
                            pass
            if event.is_set():
                raise Cancelled
            if process.returncode:
                raise AudioError(
                    f"本地{name}处理失败（退出码{process.returncode}）；检查该任务工具日志，已有结果保留。",
                    500,
                )
        with self.lock:
            self.processes.pop(item["id"], None)

    @staticmethod
    def metrics(path, channels, frames, allow_silence=False):
        count, peak, clipped, squares = 0, 0, 0, 0
        with wave.open(str(path), "rb") as source:
            if (
                source.getnchannels() != channels
                or source.getframerate() != SAMPLE_RATE
                or source.getsampwidth() != 2
                or source.getnframes() != frames
            ):
                raise AudioError("演唱资产格式或采样时间轴不一致。", 500)
            while block := source.readframes(32768):
                for value in array("h", block):
                    count += 1
                    peak = max(peak, abs(value))
                    clipped += abs(value) >= 32767
                    squares += value * value
        if not count or clipped or (not peak and not allow_silence):
            raise AudioError("演唱资产为空白或出现削波，未发布结果。", 500)
        return {
            "frames": frames,
            "channels": channels,
            "peak": peak / 32768,
            "rms": math.sqrt(squares / count) / 32768,
            "clipped_samples": clipped,
        }

    def _valid_files(self, item):
        directory = self.directory(item["project_id"], item["id"])
        return bool(item.get("files")) and all(
            (directory / name).is_file()
            and not (directory / name).is_symlink()
            and digest_file(directory / name) == details["sha256"]
            for name, details in item["files"].items()
        )

    def _render(self, item, request, backing, previous, event):
        directory = self.directory(item["project_id"], item["id"])
        work = directory / "work"
        started = time.monotonic()
        try:
            work.mkdir()
            paths = self.audio.paths
            if self.resources.info()["resource_hash"] != item["resource"]["resource_hash"]:
                raise AudioError("演唱资源在排队期间改变。", 409)
            self.phase(item, "检查歌词并准备演唱")
            if previous:
                original = self.directory(item["project_id"], previous["id"])
                for name in (
                    "vocal.wav",
                    "accompaniment.wav",
                    "singing.ustx",
                    "worker-report.json",
                    "accompaniment.mid",
                ):
                    shutil.copyfile(original / name, work / name)
                self.phase(item, "复用相同版本的干声与伴奏，仅重新混音")
            else:
                request.update(
                    resource_root=str(self.resources.paths.root),
                    runner=item["settings"]["runner"],
                    gpu=item["settings"]["gpu"],
                )
                self.store.write_json(work / "request.json", request)
                self.command(
                    item,
                    [
                        str(self.resources.paths.dotnet),
                        str(self.resources.paths.worker),
                        "--request",
                        str(work / "request.json"),
                    ],
                    work,
                    "worker",
                    event,
                    timeout=1800,
                )
                self.phase(item, "整理独立人声")
                common = [str(paths.ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
                fade = f"alimiter=limit=0.95:level=0:latency=1,apad,atrim=end_sample={item['frames']},afade=t=out:st={item['duration_seconds'] - 0.05:.9f}:d=0.05,asetpts=N/SR/TB"
                self.command(
                    item,
                    [
                        *common,
                        "-i",
                        str(work / "vocal-raw.wav"),
                        "-af",
                        fade,
                        "-ar",
                        str(SAMPLE_RATE),
                        "-ac",
                        "1",
                        "-c:a",
                        "pcm_s16le",
                        str(work / "vocal.wav"),
                    ],
                    work,
                    "vocal-wav",
                    event,
                )
                self.phase(item, "生成移除导唱后的伴奏")
                (work / "accompaniment.mid").write_bytes(export_midi(backing))
                if any(track.notes and track.volume > 0 for track in backing.tracks):
                    self.audio.validate_score(backing, "full")
                    self.command(
                        item,
                        [
                            str(paths.fluidsynth),
                            "-ni",
                            "-F",
                            str(work / "backing-raw.wav"),
                            "-T",
                            "wav",
                            "-O",
                            "float",
                            "-r",
                            str(SAMPLE_RATE),
                            "-g",
                            "0.2",
                            "-R",
                            "0",
                            "-C",
                            "0",
                            "-o",
                            "synth.midi-bank-select=gs",
                            "-o",
                            "player.reset-synth=0",
                            str(paths.soundfont),
                            str(work / "accompaniment.mid"),
                        ],
                        work,
                        "fluidsynth",
                        event,
                    )
                    self.command(
                        item,
                        [
                            *common,
                            "-i",
                            str(work / "backing-raw.wav"),
                            "-af",
                            fade,
                            "-ar",
                            str(SAMPLE_RATE),
                            "-ac",
                            "2",
                            "-c:a",
                            "pcm_s16le",
                            str(work / "accompaniment.wav"),
                        ],
                        work,
                        "backing-wav",
                        event,
                    )
                else:
                    # A score containing only sung notes legitimately has a silent backing.
                    with wave.open(str(work / "accompaniment.wav"), "wb") as target:
                        target.setparams((2, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                        remaining = item["frames"]
                        while remaining:
                            size = min(remaining, 32768)
                            target.writeframes(b"\0" * size * 4)
                            remaining -= size
            self.phase(item, "混合人声与伴奏")
            common = [str(paths.ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
            gains = item["settings"]
            mix_filter = f"[0:a]volume={gains['vocal_gain']}[v];[1:a]volume={gains['accompaniment_gain']}[a];[v][a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95:level=0:latency=1,apad,atrim=end_sample={item['frames']},asetpts=N/SR/TB[out]"
            self.command(
                item,
                [
                    *common,
                    "-i",
                    str(work / "vocal.wav"),
                    "-i",
                    str(work / "accompaniment.wav"),
                    "-filter_complex",
                    mix_filter,
                    "-map",
                    "[out]",
                    "-ar",
                    str(SAMPLE_RATE),
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s16le",
                    str(work / "mix.wav"),
                ],
                work,
                "mix-wav",
                event,
            )
            self.command(
                item,
                [
                    *common,
                    "-i",
                    str(work / "mix.wav"),
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    "-write_xing",
                    "1",
                    str(work / "mix.mp3"),
                ],
                work,
                "mix-mp3",
                event,
            )
            self.command(
                item,
                [*common[:-1], "-i", str(work / "mix.mp3"), "-f", "null", "-"],
                work,
                "mp3-decode",
                event,
            )
            metrics = {
                name: self.metrics(
                    work / name,
                    channels,
                    item["frames"],
                    allow_silence=name == "accompaniment.wav"
                    or name == "mix.wav"
                    and gains["vocal_gain"] == 0,
                )
                for name, channels in (("vocal.wav", 1), ("accompaniment.wav", 2), ("mix.wav", 2))
            }
            if (
                self.resources.info()["resource_hash"] != item["resource"]["resource_hash"]
                or self.audio.info()["sha256"] != item["soundfont_hash"]
            ):
                raise AudioError("模型或音色在生成期间改变，未发布结果。", 409)
            report = json.loads((work / "worker-report.json").read_text(encoding="utf-8"))
            warnings = previous.get("warnings", []) if previous else []
            if not previous and "falling back to CPU" in (work / "worker.stderr.log").read_text(
                encoding="utf-8", errors="replace"
            ):
                warnings = ["部分演唱模型使用了 CPU 兼容推理，生成时间可能增加。"]
            if report["frames"] != item["frames"] or report["protocol"] != request["protocol"]:
                raise AudioError("演唱引擎返回的来源或采样时间轴不一致。", 500)
            self.phase(item, "检查与保存演唱文件")
            files = {}
            for name in (
                "vocal.wav",
                "accompaniment.wav",
                "mix.wav",
                "mix.mp3",
                "singing.ustx",
                "worker-report.json",
                "accompaniment.mid",
            ):
                if event.is_set():
                    raise Cancelled
                source, target = work / name, directory / name
                RenderManager.copy(self, source, target)
                files[name] = {"bytes": target.stat().st_size, "sha256": digest_file(target)}
            with self.lock, self.store.lock:
                if event.is_set():
                    raise Cancelled
                item.update(
                    status="completed",
                    phase="演唱已保存，听感待人工确认",
                    files=files,
                    metrics=metrics,
                    engine=report,
                    warnings=warnings,
                    elapsed_seconds=time.monotonic() - started,
                )
        except Cancelled:
            item.update(status="cancelled", phase="演唱已取消", files={}, error=None)
        except (
            AudioError,
            OSError,
            ValueError,
            KeyError,
            IndexError,
            wave.Error,
            StorageError,
        ) as error:
            item.update(
                status="failed",
                phase="演唱失败",
                files={},
                error=str(error)
                if isinstance(error, AudioError)
                else "本地演唱或文件校验失败，请查看任务工具日志；旧结果保留。",
            )
        finally:
            item["updated_at"] = now()
            with self.lock, self.store.lock:
                try:
                    self._write(item)
                finally:
                    self._forget(item["id"])

    def asset(self, project_id, job_id, name):
        if name not in ("vocal.wav", "accompaniment.wav", "mix.wav", "mix.mp3", "singing.ustx"):
            raise AudioError("演唱导出格式不存在。", 404)
        with self.lock, self.store.lock:
            item = self.read(project_id, job_id)
            if item["status"] != "completed":
                raise AudioError("演唱尚未成功生成。", 409)
            path = self.directory(project_id, job_id) / name
            if (
                not path.is_file()
                or path.is_symlink()
                or digest_file(path) != item["files"][name]["sha256"]
            ):
                raise AudioError("演唱文件缺失或校验失败，旧记录保留。", 409)
            return path, item

    def asset_content(self, project_id, job_id, name):
        # Close the file before sending HTTP bytes: an aborted Windows stream must
        # not keep a song locked when the user immediately sends it to the trash.
        with self.lock, self.store.lock:
            path, item = self.asset(project_id, job_id, name)
            if path.stat().st_size > 110 * 1024 * 1024:
                raise AudioError("演唱文件超过当前10分钟音频读取上限，请检查任务记录。", 413)
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != item["files"][name]["sha256"]:
                raise AudioError("演唱文件在读取期间改变，未返回结果。", 409)
            return content, item
