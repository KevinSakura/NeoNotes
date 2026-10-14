"""Bounded local rendering jobs, pinned to immutable song revisions."""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Literal

from .midi import export_midi
from .models import RevisionAction, Score, SongState, new_id
from .resources import AudioError, SoundFontResource
from .storage import ProjectStore, now

SAMPLE_RATE = 44100
TAIL_SECONDS = 3
TIMEOUT_SECONDS = 120
MAX_PENDING = 4
RECIPE_VERSION = 1


class RenderRequest(RevisionAction):
    mode: Literal["mix", "full"] = "mix"


class RenderManager:
    def __init__(self, store: ProjectStore, resource: SoundFontResource):
        self.store, self.resource = store, resource
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="song-render")
        self.lock = RLock()
        self.active: dict[str, object] = {}
        self.job_projects: dict[str, str] = {}
        self.closed = False

    def has_active_jobs(self, project_id: str):
        with self.lock:
            return any(self.job_projects.get(job_id) == project_id for job_id in self.active)

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)

    def directory(self, project_id: str, job_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise AudioError("渲染记录不存在。", 404)
        directory = self.store.directory(project_id) / "exports" / "audio" / job_id
        if not directory.resolve().is_relative_to(self.store.root):
            raise AudioError("渲染路径无效。", 400)
        if directory.is_symlink() or any(parent.is_symlink() for parent in directory.parents):
            raise AudioError("渲染目录不能使用链接。", 400)
        return directory

    def read(self, project_id: str, job_id: str):
        self.store.get(project_id)
        with self.lock:
            item = self.store.read_json(self.directory(project_id, job_id) / "render.json")
            if item.get("id") != job_id or item.get("project_id") != project_id:
                raise AudioError("渲染记录损坏。", 500)
            if item["status"] in ("queued", "rendering") and job_id not in self.active:
                item.update(
                    status="failed", error="服务曾中断，渲染未完成；请重新发起。", updated_at=now()
                )
                self.store.write_json(self.directory(project_id, job_id) / "render.json", item)
        return item

    def list_jobs(self, project_id: str):
        self.store.get(project_id)
        root = self.store.directory(project_id) / "exports" / "audio"
        if not root.exists():
            return []
        if root.is_symlink() or not root.resolve().is_relative_to(self.store.root):
            raise AudioError("渲染路径无效。", 400)
        result = []
        for path in root.iterdir():
            if re.fullmatch(r"[a-f0-9]{32}", path.name) and (path / "render.json").is_file():
                result.append(self.read(project_id, path.name))
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def start(self, project_id: str, body: RenderRequest):
        with self.lock, self.store.lock:
            if self.closed:
                raise AudioError("服务正在停止，请重新启动后渲染。", 503)
            project = self.store.get(project_id)
            self.store.check_base(project, body.base_revision)
            score = SongState.model_validate(project["revision"]["state"]).score
            audible = self.resource.validate_score(score, body.mode)
            paths = self.resource.paths
            if not all(path.is_file() for path in (paths.fluidsynth, paths.ffmpeg, paths.ffprobe)):
                raise AudioError("缺少 FluidSynth、FFmpeg 或 FFprobe，请检查 tools。", 503)
            font = self.resource.info()
            for previous in self.list_jobs(project_id):
                same_render = (
                    previous.get("recipe_version") == RECIPE_VERSION
                    and previous["revision_id"] == body.base_revision
                    and previous["mode"] == body.mode
                    and previous["soundfont"]["sha256"] == font["sha256"]
                    and previous["status"] in ("queued", "rendering", "completed")
                )
                if same_render and (
                    previous["status"] != "completed"
                    or all(
                        (self.directory(project_id, previous["id"]) / f"mix.{extension}").is_file()
                        for extension in ("wav", "mp3", "mid")
                    )
                ):
                    return previous
            if len(self.active) >= MAX_PENDING:
                raise AudioError("本地渲染队列已满，请等待已有任务完成。", 429)
            job_id = new_id()
            item = {
                "id": job_id,
                "recipe_version": RECIPE_VERSION,
                "project_id": project_id,
                "revision_id": body.base_revision,
                "revision_number": project["revision"]["number"],
                "mode": body.mode,
                "status": "queued",
                "phase": "等待本地渲染",
                "error": None,
                "created_at": now(),
                "updated_at": now(),
                "soundfont": {key: font[key] for key in ("name", "sha256", "bytes")},
                "settings": {
                    "sample_rate": SAMPLE_RATE,
                    "channels": 2,
                    "tail_seconds": TAIL_SECONDS,
                    "gain": 0.2,
                    "reverb": False,
                    "chorus": False,
                    "bank_select": "gs",
                    "mp3_bitrate_kbps": 192,
                    "limiter": 0.95,
                    "end_fade_ms": 50,
                },
                "timeline_seconds": score.seconds_at(score.end_tick),
                "duration_seconds": score.seconds_at(score.end_tick) + TAIL_SECONDS,
                "tracks": [
                    {
                        "id": track.id,
                        "name": track.name,
                        "channel": track.channel,
                        "program": track.program,
                        "bank_msb": track.bank_msb,
                        "bank_lsb": track.bank_lsb,
                        "is_drum": track.is_drum,
                        "volume": track.volume,
                    }
                    for track in audible
                ],
                "files": {},
            }
            directory = self.directory(project_id, job_id)
            self.store.write_json(directory / "render.json", item)
            self.active[job_id] = None
            self.job_projects[job_id] = project_id
            try:
                self.active[job_id] = self.executor.submit(self._render, item, score)
            except RuntimeError:
                self.active.pop(job_id, None)
                self.job_projects.pop(job_id, None)
                item.update(status="failed", error="无法启动渲染任务，请重新启动服务。")
                self.store.write_json(directory / "render.json", item)
                raise AudioError(item["error"], 503) from None
            return dict(item)

    def phase(self, item, label):
        with self.lock:
            item.update(status="rendering", phase=label, updated_at=now())
            self.store.write_json(
                self.directory(item["project_id"], item["id"]) / "render.json", item
            )

    @staticmethod
    def command(args: list[str], log: list[str]):
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            raise AudioError("本地音频工具超时，任务已停止；已有音频保留。", 504) from None
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        log.append(output[-16000:])
        if result.returncode:
            raise AudioError(
                f"本地音频工具执行失败（退出码 {result.returncode}），已有音频保留。", 500
            )
        return result.stdout

    def copy(self, source: Path, target: Path):
        # Atomic publication of each file. render.json is the final completion marker.
        if target.is_symlink() or not target.resolve().is_relative_to(self.store.root):
            raise AudioError("音频保存路径无效。", 400)
        descriptor, temporary = tempfile.mkstemp(prefix=".writing-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle, source.open("rb") as original:
                shutil.copyfileobj(original, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def wav_metrics(path: Path):
        count, peak, squares, clipped = 0, 0, 0, 0
        with wave.open(str(path), "rb") as source:
            if (
                source.getframerate() != SAMPLE_RATE
                or source.getnchannels() != 2
                or source.getsampwidth() != 2
            ):
                raise AudioError("WAV 格式不符合预期。", 500)
            frames = source.getnframes()
            while block := source.readframes(32768):
                values = array("h", block)
                count += len(values)
                for value in values:
                    peak = max(peak, abs(value))
                    squares += value * value
                    clipped += abs(value) >= 32767
        if not count or not peak:
            raise AudioError("渲染得到空白或静音音频；未发布结果，请检查音符和音色。", 500)
        return {
            "frames": frames,
            "duration_seconds": frames / SAMPLE_RATE,
            "peak": peak / 32768,
            "rms": math.sqrt(squares / count) / 32768,
            "clipped_samples": clipped,
        }

    def _render(self, item, score: Score):
        directory = self.directory(item["project_id"], item["id"])
        log = []
        try:
            # No user filename or shell interpolation is passed to tools.
            with tempfile.TemporaryDirectory(prefix=".render-", dir=directory) as temporary:
                work = Path(temporary)
                if not work.resolve().is_relative_to(directory.resolve()):
                    raise AudioError("渲染临时目录路径无效。", 400)
                midi, raw, wav, mp3 = (
                    work / name for name in ("mix.mid", "raw.wav", "mix.wav", "mix.mp3")
                )
                midi.write_bytes(export_midi(score, audible_only=item["mode"] == "mix"))
                if self.resource.info()["sha256"] != item["soundfont"]["sha256"]:
                    raise AudioError("音色文件在排队期间改变，请重新发起渲染。")
                paths = self.resource.paths
                item["tools"] = {
                    "fluidsynth": self.command([str(paths.fluidsynth), "-V"], log)
                    .decode("utf-8", errors="replace")
                    .splitlines()[0],
                    "ffmpeg": self.command([str(paths.ffmpeg), "-version"], log)
                    .decode("utf-8", errors="replace")
                    .splitlines()[0],
                }
                self.phase(item, "使用 SoundFont 生成声音")
                self.command(
                    [
                        str(paths.fluidsynth),
                        "-ni",
                        "-F",
                        str(raw),
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
                        str(midi),
                    ],
                    log,
                )
                self.phase(item, "整理 WAV 与尾音")
                audio_filter = (
                    "alimiter=limit=0.95:level=0:latency=1,apad,"
                    f"atrim=duration={item['duration_seconds']:.9f},"
                    f"afade=t=out:st={item['duration_seconds'] - 0.05:.9f}:d=0.05,asetpts=N/SR/TB"
                )
                self.command(
                    [
                        str(paths.ffmpeg),
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(raw),
                        "-af",
                        audio_filter,
                        "-ar",
                        str(SAMPLE_RATE),
                        "-ac",
                        "2",
                        "-c:a",
                        "pcm_s16le",
                        str(wav),
                    ],
                    log,
                )
                metrics = self.wav_metrics(wav)
                if (
                    abs(metrics["duration_seconds"] - item["duration_seconds"])
                    > 1 / SAMPLE_RATE + 1e-6
                ):
                    raise AudioError("音频时长检查失败，未发布结果。", 500)
                self.phase(item, "编码 MP3 并检查文件")
                self.command(
                    [
                        str(paths.ffmpeg),
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(wav),
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        "192k",
                        "-write_xing",
                        "1",
                        "-id3v2_version",
                        "3",
                        str(mp3),
                    ],
                    log,
                )
                probe = json.loads(
                    self.command(
                        [
                            str(paths.ffprobe),
                            "-v",
                            "error",
                            "-show_entries",
                            "stream=codec_name,sample_rate,channels:format=duration",
                            "-of",
                            "json",
                            str(mp3),
                        ],
                        log,
                    )
                )
                stream = probe["streams"][0]
                if (
                    stream["codec_name"] != "mp3"
                    or stream["channels"] != 2
                    or int(stream["sample_rate"]) != SAMPLE_RATE
                ):
                    raise AudioError("MP3 格式检查失败，未发布结果。", 500)
                if abs(float(probe["format"]["duration"]) - item["duration_seconds"]) > 0.1:
                    raise AudioError("MP3 时长检查失败，未发布结果。", 500)
                if self.resource.info()["sha256"] != item["soundfont"]["sha256"]:
                    raise AudioError("音色文件在渲染期间改变，请重新发起渲染。")
                for source in (midi, wav, mp3):
                    self.copy(source, directory / source.name)
                    item["files"][source.suffix[1:]] = {
                        "bytes": source.stat().st_size,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                item.update(
                    status="completed",
                    phase="音频已保存",
                    wav=metrics,
                    mp3_duration_seconds=float(probe["format"]["duration"]),
                )
        except (AudioError, OSError, ValueError, KeyError, IndexError, wave.Error) as error:
            log.append(f"{type(error).__name__}: {error}")
            item.update(
                status="failed",
                phase="渲染失败",
                files={},
                error=str(error)
                if isinstance(error, AudioError)
                else "渲染或磁盘检查失败；已有歌曲与音频保留，请检查工具和磁盘。",
            )
        finally:
            item["updated_at"] = now()
            with self.lock:
                try:
                    self.store.write_bytes(directory / "render.log", "\n".join(log).encode("utf-8"))
                    self.store.write_json(directory / "render.json", item)
                finally:
                    self.active.pop(item["id"], None)
                    self.job_projects.pop(item["id"], None)

    def audio_file(self, project_id: str, job_id: str, extension: str):
        if extension not in ("wav", "mp3"):
            raise AudioError("音频格式不存在。", 404)
        item = self.read(project_id, job_id)
        if item["status"] != "completed":
            raise AudioError("音频尚未成功生成。", 409)
        path = self.directory(project_id, job_id) / f"mix.{extension}"
        if path.is_symlink() or not path.is_file():
            raise AudioError("音频文件缺失，请重新渲染。", 404)
        return path, item
