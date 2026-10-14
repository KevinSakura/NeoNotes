"""Version-pinned candidates. Generation never overwrites the current song."""

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal

from pydantic import Field

from .assessment import assess
from .composition import (
    CHANNELS,
    FAMILIES,
    AlignmentReferenceError,
    compile_plan,
    input_context,
    mock_plan,
    model_messages,
    reference_events,
)
from .deepseek import DeepSeekAdapter
from .full_song import full_context, reviewed_reference
from .lyric_composition import CONTRACT, lyric_context
from .lyric_edit import bind_notes
from .midi import export_midi
from .model_settings import FULL_SONG_MAX_TOKENS, ModelError
from .models import Identifier, Model, Score, SongState, Track, new_id
from .resources import AudioError
from .storage import StorageError, now


class GenerationRequest(Model):
    base_revision: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    operation: Literal["generate", "modify", "full"] = "generate"
    instruction: str = Field(default="", max_length=2000)
    confirm_real_call: bool = False
    contract: Literal["lyric-phrases-v2"] | None = None
    budget_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CandidateAction(Model):
    base_revision: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class ReviewRequest(CandidateAction):
    approved: bool
    melody: bool = False
    arrangement: bool = False
    style: bool = False
    lyrics_sung: bool = False
    comment: str = Field(default="", max_length=2000)


class LyricRepairRequest(CandidateAction):
    state: SongState
    line_index: Annotated[int, Field(strict=True, ge=0)]
    unit_index: Annotated[int, Field(strict=True, ge=0)]
    note_ids: list[Identifier] = Field(min_length=1, max_length=12)


class GenerationManager:
    def __init__(self, store, resource, settings_source, adapter_factory=None):
        self.store, self.resource = store, resource
        self.settings_source = settings_source
        self.adapter_factory = adapter_factory or DeepSeekAdapter
        self.lock = RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="composition")
        self.futures = {}
        self.job_projects = {}
        self.settings = None
        self.closed = False
        from .phrase_tasks import PhraseTaskRunner
        self.phrase_runner = PhraseTaskRunner(self)

    def close(self):
        with self.lock:
            self.closed = True
            for event in self.phrase_runner.events.values():
                event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def has_active_jobs(self, project_id):
        with self.lock:
            return any(
                self.job_projects.get(job_id) == project_id and not future.done()
                for job_id, future in self.futures.items()
            )

    def configuration(self):
        with self.lock:
            if self.settings is None:
                self.settings = self.settings_source()
            return self.settings

    def status(self):
        try:
            return self.configuration().public()
        except ModelError:
            return {
                "mode": "unavailable",
                "provider": "deepseek",
                "model": "",
                "ready": False,
                "error": "本地模型配置无法加载，请检查 .env；歌曲编辑仍可使用。",
                "max_tokens": 0,
                "requests_per_job": 0,
                "automatic_retries": 0,
                "full_song_enabled": False,
                "full_song_available": True,
                "full_song_max_tokens": FULL_SONG_MAX_TOKENS,
            }

    def directory(self, project_id, job_id) -> Path:
        self.store.header(project_id)
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise StorageError("编曲任务不存在。", 404)
        directory = self.store.directory(project_id) / "jobs" / "composition" / job_id
        if not directory.resolve().is_relative_to(self.store.root) or any(
            parent.is_symlink() for parent in (directory, *directory.parents)
        ):
            raise StorageError("编曲任务目录无效。", 400)
        return directory

    def read(self, project_id, job_id):
        with self.lock:
            directory = self.directory(project_id, job_id)
            item = self.store.read_json(directory / "job.json")
            if (
                not isinstance(item, dict)
                or item.get("id") != job_id
                or item.get("project_id") != project_id
            ):
                raise StorageError("编曲任务记录损坏。")
            if item["status"] in ("queued", "running", "validating", "cancelling") and (
                job_id not in self.futures or self.futures[job_id].done()
            ):
                item.update(
                    status="interrupted" if item.get("contract") == "lyric-phrases-v2" else "failed",
                    phase="已中断",
                    error="服务中断，无法确认原请求是否已计费；未自动重试。",
                    error_code="interrupted",
                    updated_at=now(),
                )
                if item.get("contract") == "lyric-phrases-v2":
                    for block in item.get("blocks", []):
                        if block["status"] == "running":
                            block["status"] = "interrupted"
                        elif block["status"] == "pending":
                            block["status"] = "skipped"
                self.store.write_json(directory / "job.json", item)
            return item

    def list_jobs(self, project_id):
        with self.lock:
            self.store.header(project_id)
            root = self.store.directory(project_id) / "jobs" / "composition"
            items = (
                [
                    self.read(project_id, item.name)
                    for item in root.iterdir()
                    if item.is_dir() and re.fullmatch(r"[a-f0-9]{32}", item.name)
                ]
                if root.is_dir()
                else []
            )
            return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def start(self, project_id, body: GenerationRequest):
        with self.lock, self.store.lock:
            if self.closed:
                raise ModelError("服务正在关闭，请稍后重新启动。", 503, "closed")
            settings = self.configuration()
            settings.ensure_available()
            if not settings.mock and not body.confirm_real_call:
                raise ModelError(
                    "请明确确认这次请求会发送所选创作资料并消耗 DeepSeek API 额度。", 403, "consent"
                )
            if sum(not future.done() for future in self.futures.values()) >= 2:
                raise ModelError("已有两个编曲任务运行或排队，请等待完成。", 429, "queue")
            if any(
                item["status"] in ("queued", "running", "validating", "cancelling")
                for item in self.list_jobs(project_id)
            ):
                raise ModelError("这首歌已有编曲任务，请等待完成后再生成。", 409, "busy")
            project = self.store.get(project_id)
            self.store.check_base(project, body.base_revision)
            source = SongState.model_validate(project["revision"]["state"])
            if body.contract == "lyric-phrases-v2":
                return self.phrase_runner.start(project_id, body, settings)
            if body.operation == "full":
                if body.instruction.strip():
                    raise ModelError(
                        "生成整曲沿用已审核输入；其他要求请通过整曲修改提出。", code="instruction"
                    )
                status = self.review_status(project_id)
                if not status["full_song_enabled"]:
                    raise ModelError(status["full_song_reason"], 409, "full_review")
                source, context = full_context(
                    source, body.base_revision, status["review"]["id"], flexible_lyrics=True
                )
            else:
                if source.inputs.selected_lines:
                    source, context = lyric_context(source, body.operation, body.instruction)
                else:
                    context = input_context(source, body.operation, body.instruction)
            full = context["scope"] == "full"
            if full:
                if body.operation == "modify":
                    self.validate_source(project_id, source)
                    origin, _ = self.full_origin(project_id, source)
                    context.update(
                        full_source=source.composition.full_source.model_dump(),
                        approved_short_song=reviewed_reference(origin),
                        lyric_scope="all_nonempty_lines",
                        approach="rearrange_from_reviewed_short",
                    )
                    if not settings.mock and source.composition.mock:
                        raise ModelError(
                            "模拟整曲只能进行模拟修改；请先生成真实整曲。", 409, "mock_source"
                        )
                # Explicit stage-6 task limit; never edit the user's real .env.
                settings = settings.model_copy(update={"max_tokens": FULL_SONG_MAX_TOKENS})
            if context.get("contract") == CONTRACT:
                settings = settings.model_copy(update={"max_tokens": FULL_SONG_MAX_TOKENS})
            # Reject unusable pinned instruments before any paid request or task is created.
            try:
                self.resource.info()
                pinned = []
                for family in FAMILIES:
                    recorded, notes = reference_events(source, family, 500000)
                    if notes:
                        pinned.append(
                            Track(
                                name=family,
                                family=family,
                                channel=CHANNELS[family],
                                is_drum=family == "drums",
                                program=recorded[0].program,
                                bank_msb=recorded[0].bank_msb,
                                bank_lsb=recorded[0].bank_lsb,
                                notes=notes,
                            )
                        )
                if pinned:
                    self.resource.validate_score(
                        Score(
                            tracks=pinned,
                            end_tick=max(
                                note.start_tick + note.duration_tick
                                for track in pinned
                                for note in track.notes
                            ),
                        ),
                        "full",
                    )
            except AudioError as error:
                raise ModelError(str(error), error.status, "reference_resource") from None
            job_id = new_id()
            directory = self.directory(project_id, job_id)
            timestamp = now()
            item = {
                "id": job_id,
                "project_id": project_id,
                "base_revision": body.base_revision,
                "base_number": project["revision"]["number"],
                "operation": body.operation,
                "scope": context["scope"],
                "contract": context.get("contract", "events"),
                "target_seconds": context["target_seconds"],
                "duration_policy": context["duration_policy"],
                "max_output_tokens": settings.max_tokens,
                "full_source": context.get("full_source"),
                "instruction": body.instruction.strip(),
                "status": "queued",
                "phase": "等待编曲",
                "mock": settings.mock,
                "provider": "mock" if settings.mock else settings.provider,
                "model": "deterministic-demo" if settings.mock else settings.name,
                "created_at": timestamp,
                "updated_at": timestamp,
                "error": None,
                "error_code": None,
                "request_count": 0,
                "metadata": None,
                "checks": [],
            }
            self.store.write_json(directory / "input.json", context)
            self.store.write_json(directory / "job.json", item)
            self.job_projects[job_id] = project_id
            self.futures[job_id] = self.executor.submit(
                self._run, project_id, job_id, source, context, settings
            )
            return item

    def _update(self, project_id, job_id, **changes):
        with self.lock:
            path = self.directory(project_id, job_id) / "job.json"
            item = self.store.read_json(path)
            item.update(**changes, updated_at=now())
            self.store.write_json(path, item)
            return item

    def retry_phrases(self, project_id, job_id, body):
        with self.lock, self.store.lock:
            settings = self.configuration()
            settings.ensure_available()
            if not settings.mock and not body.confirm_real_call:
                raise ModelError("显式重试可能产生新的API费用，请确认请求预算。", 403, "consent")
            if self.closed or sum(not f.done() for f in self.futures.values()) >= 2:
                raise ModelError("任务队列不可用，请等待。", 429, "queue")
            if self.has_active_jobs(project_id):
                raise ModelError("本歌曲仍有活动任务，请等待取消/请求结束。", 409, "busy")
            old = self.read(project_id, job_id)
            body = body.model_copy(update={"contract": "lyric-phrases-v2",
                                          "operation": old["operation"], "instruction": old["instruction"]})
            return self.phrase_runner.start(project_id, body, settings, retry_of=job_id)

    def _run(self, project_id, job_id, source, context, settings):
        try:
            self._update(
                project_id,
                job_id,
                status="running",
                phase="模拟编曲" if settings.mock else "等待 DeepSeek 响应",
                request_count=0,
            )
            if settings.mock:
                raw = mock_plan(source, context)
                metadata = {
                    "requested_model": "deterministic-demo",
                    "returned_model": "deterministic-demo",
                    "request_id": None,
                    "usage": None,
                    "elapsed_seconds": None,
                    "finish_reason": "mock",
                }
            else:
                adapter = self.adapter_factory(settings)
                adapter.on_request = lambda: self._update(project_id, job_id, request_count=1)
                raw, metadata = adapter.generate(model_messages(context))
            self._update(
                project_id,
                job_id,
                status="validating",
                phase="检查时间、声部与歌词对应",
                metadata=metadata,
            )
            state, plan = compile_plan(
                raw, source, job_id, settings.mock, context["operation"], context
            )
            self.resource.validate_score(state.score, "full")
            if state.composition.scope == "full":
                self.full_origin(project_id, state)
            directory = self.directory(project_id, job_id)
            with self.lock:
                self.store.write_json(directory / "plan.json", plan.model_dump())
                self.store.write_json(directory / "candidate.json", state.model_dump())
                self.store.write_bytes(directory / "candidate.mid", export_midi(state.score))
                self._update(
                    project_id,
                    job_id,
                    status="completed",
                    phase="候选已保存，等待你采用",
                    checks=state.composition.checks,
                    result_seconds=state.composition.target_seconds,
                )
        except (ModelError, StorageError) as error:
            changes = {
                "status": "failed",
                "phase": "未生成可采用的候选",
                "error": str(error),
                "error_code": getattr(error, "code", "storage"),
            }
            if hasattr(error, "metadata"):
                changes["metadata"] = error.metadata
            if isinstance(error, AlignmentReferenceError):
                changes["diagnostic"] = error.diagnostic
            self._update(project_id, job_id, **changes)
        except Exception:
            # Never log arbitrary exceptions or raw HTTP/configuration objects containing keys.
            self._update(
                project_id,
                job_id,
                status="failed",
                phase="生成失败",
                error="编曲或文件处理失败，已有作品保留；请检查资源与磁盘后手动再试。",
                error_code="processing",
            )

    def candidate(self, project_id, job_id):
        with self.lock:
            item = self.read(project_id, job_id)
            if item["status"] != "completed":
                raise ModelError("候选尚未通过检查，不能采用或下载。", 409, "pending")
            return SongState.model_validate(
                self.store.read_json(self.directory(project_id, job_id) / "candidate.json")
            )

    def midi_file(self, project_id, job_id):
        self.candidate(project_id, job_id)
        path = self.directory(project_id, job_id) / "candidate.mid"
        if not path.is_file() or not path.resolve().is_relative_to(self.store.root):
            raise StorageError("候选 MIDI 文件缺失。", 404)
        return path

    def apply(self, project_id, job_id, base_revision):
        with self.lock, self.store.lock:
            project = self.store.get(project_id)
            self.store.check_base(project, base_revision)
            item = self.read(project_id, job_id)
            current = SongState.model_validate(project["revision"]["state"])
            if (
                current.composition
                and current.composition.job_id == job_id
                and project["revision"]["parent_revision"] == item["base_revision"]
            ):
                return project  # Retry after a lost response does not create another version.
            if item["base_revision"] != base_revision:
                raise ModelError(
                    "候选基于旧版本，不能覆盖新保存的歌曲；请以当前输入重新生成。", 409, "stale"
                )
            if item["operation"] == "full":
                status = self.review_status(project_id)
                if (
                    not status["full_song_enabled"]
                    or status["review"]["id"] != item["full_source"]["review_id"]
                ):
                    raise ModelError(
                        "短曲审核已改变，不能采用旧审核下的整曲；请重新确认后生成。",
                        409,
                        "full_review_changed",
                    )
            state = self.candidate(project_id, job_id)
            label = ("模拟" if item["mock"] else "AI") + (
                "整曲修改"
                if item["operation"] == "modify"
                else "整曲候选"
                if item["operation"] == "full"
                else "全文歌词候选"
                if item.get("scope") == "lyrics"
                else "60秒候选"
            )
            return self.store.save(project_id, base_revision, state, label)

    def validate_source(self, project_id, state):
        if not state.composition:
            raise ModelError("请先采用一个短曲候选。", code="review_source")
        item = self.read(project_id, state.composition.job_id)
        if item["status"] != "completed" or item["mock"] != state.composition.mock:
            raise ModelError("候选来源未完成或模拟标记改变，不能审核通过。", code="review_source")
        original = self.store.read_json(self.directory(project_id, item["id"]) / "input.json")
        if original.get("contract") in (CONTRACT, "lyric-phrases-v2"):
            all_lines = [
                i for i, line in enumerate(state.inputs.lyrics_text.split("\n")) if line.strip()
            ]
            if state.inputs.selected_lines != all_lines:
                raise ModelError("全文歌词范围已改变，请恢复全文或重新生成。", code="review_inputs")
            if original.get("contract") == "lyric-phrases-v2":
                from .lyric_phrases import phrase_context
                _, current = phrase_context(state, item["operation"], item["instruction"])
                if current["lyrics_identity"] != original["lyrics_identity"]:
                    raise ModelError("歌词原文、标点或分词身份已改变。", code="review_inputs")
            else:
                _, current = lyric_context(state, item["operation"], item["instruction"])
        else:
            current = input_context(state, item["operation"], item["instruction"])
        keys = ("theme", "music_style", "selected_lyrics", "melody_mode", "reference_notes")
        if any(original.get(key) != current.get(key) for key in keys):
            raise ModelError(
                "创作输入已改变，请按新输入重新生成；不要沿用旧候选的对应与说明。",
                code="review_inputs",
            )
        candidate = self.candidate(project_id, item["id"])
        if state.composition.arrangement == "lyric-phrases-v2":
            from .lyric_group_edit import validate_edit_history
            validate_edit_history(self, project_id, state, candidate)
        for field in ("scope", "target_seconds", "full_source", "arrangement"):
            if field == "target_seconds" and state.composition.arrangement == "lyric-phrases-v2":
                continue
            if getattr(state.composition, field) != getattr(candidate.composition, field):
                raise ModelError(
                    "候选的生成范围、时长或审核来源已改变，请恢复或重新生成。", code="review_source"
                )
        if state.composition.scope == "full":
            self.full_origin(project_id, state)

    def full_origin(self, project_id, state):
        source = state.composition.full_source
        if source is None:
            raise ModelError("整曲缺少已审核短曲来源。", code="full_source")
        revision = self.store.revision(project_id, source.revision_id)
        short = SongState.model_validate(revision["state"])
        if not short.composition or short.composition.scope != "short":
            raise ModelError("整曲引用的来源不是60秒短曲。", code="full_source")
        review = self.store.read_json(
            self.store.directory(project_id) / "reviews" / "history" / (source.review_id + ".json")
        )
        if (
            not self.approved_review(review, source.revision_id, short)
            or review.get("id") != source.review_id
            or source.motif_pitches != reviewed_reference(short)["motif_pitches"]
            or (not state.composition.mock and short.composition.mock)
        ):
            raise ModelError("整曲的短曲审核来源或旋律参考无效。", code="full_source")
        self.validate_source(project_id, short)
        if not assess(short, self.resource)["valid"]:
            raise ModelError("整曲引用的短曲当前结构核对失败。", code="full_source")
        if state.inputs.lyrics_text != short.inputs.lyrics_text or any(
            getattr(state.inputs, field) != getattr(short.inputs, field)
            for field in ("theme", "emotion", "melody_mode", "recording", "files")
        ):
            raise ModelError("整曲输入与审核短曲不一致，请恢复或重新生成。", code="review_inputs")
        return short, review

    @staticmethod
    def approved_review(review, revision_id, state):
        return bool(
            isinstance(review, dict)
            and state.composition
            and review.get("approved") is True
            and review.get("revision_id") == revision_id
            and review.get("job_id") == state.composition.job_id
            and review.get("mock") == state.composition.mock
            and isinstance(review.get("id"), str)
            and re.fullmatch(r"[a-f0-9]{32}", review["id"])
            and all(review.get(field) is True for field in ("melody", "arrangement", "style"))
            and (not state.inputs.selected_lines or review.get("lyrics_sung") is True)
            and isinstance(review.get("assessment"), dict)
            and review["assessment"].get("valid") is True
        )

    def repair_alignment(self, project_id, body: LyricRepairRequest):
        with self.lock, self.store.lock:
            project = self.store.get(project_id)
            self.store.check_base(project, body.base_revision)
            self.validate_source(project_id, body.state)
            state = bind_notes(body.state, body.line_index, body.unit_index, body.note_ids)
            return {
                "state": state.model_dump(),
                "assessment": assess(state, self.resource),
                "saved": False,
            }

    def review_status(self, project_id):
        with self.lock, self.store.lock:
            project = self.store.get(project_id)
            state = SongState.model_validate(project["revision"]["state"])
            assessment = assess(state, self.resource)
            source_check = {
                "id": "source",
                "label": "候选来源与创作输入",
                "passed": True,
                "code": None,
                "message": "来源完成，创作输入与生成时一致",
            }
            try:
                self.validate_source(project_id, state)
            except (ModelError, StorageError) as error:
                source_check.update(
                    passed=False, code=getattr(error, "code", "review_source"), message=str(error)
                )
            assessment["checks"].append(source_check)
            assessment["valid"] = all(check["passed"] for check in assessment["checks"])
            valid = assessment["valid"]
            reason = next(
                (check["message"] for check in assessment["checks"] if not check["passed"]), None
            )
            path = (
                self.store.directory(project_id)
                / "reviews"
                / (project["current_revision"] + ".json")
            )
            review = self.store.read_json(path) if path.exists() else None
            full_reason = None
            full_spec = None
            mode = self.status()
            if state.composition and state.composition.scope == "lyrics":
                full_reason = (
                    "当前已是全文歌词作品，可直接试唱、审核、整体修改及导出，无需再生成60秒选段。"
                )
            elif not state.composition or state.composition.scope != "short":
                full_reason = "请先采用并审核一个60秒短曲；整曲版本可编辑、审核或整体修改。"
            elif not valid:
                full_reason = reason
            else:
                try:
                    review_id = review.get("id") if isinstance(review, dict) else None
                    if not isinstance(review_id, str) or not re.fullmatch(
                        r"[a-f0-9]{32}", review_id
                    ):
                        review_id = "0" * 32
                    _, context = full_context(
                        state,
                        project["current_revision"],
                        review_id,
                        flexible_lyrics=True,
                    )
                    full_spec = {
                        "lyric_scope": "all_nonempty_lines",
                        "lyric_lines": len(context["selected_lyrics"]),
                        "lyric_units": sum(
                            len(line["units"]) for line in context["selected_lyrics"]
                        ),
                        "duration_policy": context["duration_policy"],
                        "target_seconds": context["target_seconds"],
                        "max_seconds": 600,
                        "max_output_tokens": FULL_SONG_MAX_TOKENS,
                        "approach": "rearrange_from_reviewed_short",
                    }
                except ModelError as error:
                    full_reason = str(error)
                if full_reason is None:
                    if not self.approved_review(review, project["current_revision"], state):
                        full_reason = "请先对当前保存版本完成人工试听/试唱并记录审核通过。"
                    else:
                        try:
                            history = self.store.read_json(
                                self.store.directory(project_id)
                                / "reviews"
                                / "history"
                                / (review["id"] + ".json")
                            )
                            if history != review:
                                full_reason = "审核历史与当前记录不一致，请重新审核，尚未请求模型。"
                        except StorageError:
                            full_reason = "审核历史记录缺失或损坏，请重新审核，尚未请求模型。"
                    if full_reason is None and not mode["ready"]:
                        full_reason = mode["error"]
                    elif full_reason is None and mode["mode"] == "real" and state.composition.mock:
                        full_reason = "模拟短曲只能继续模拟整曲；请先生成并审核真实短曲。"
            return {
                "revision_id": project["current_revision"],
                "revision_number": project["revision"]["number"],
                "valid": valid,
                "reason": reason,
                "review": review,
                "assessment": assessment,
                "full_song_enabled": full_reason is None,
                "full_song_reason": full_reason,
                "full_song_spec": full_spec,
            }

    def review(self, project_id, body: ReviewRequest):
        with self.lock, self.store.lock:
            project = self.store.get(project_id)
            self.store.check_base(project, body.base_revision)
            state = SongState.model_validate(project["revision"]["state"])
            if not state.composition:
                raise ModelError("请先采用一个短曲候选，再审核这个保存版本。", code="review_source")
            status = self.review_status(project_id)
            if body.approved:
                if not status["valid"]:
                    raise ModelError(status["reason"], code="review_invalid")
                if not (
                    body.melody
                    and body.arrangement
                    and body.style
                    and (not state.inputs.selected_lines or body.lyrics_sung)
                ):
                    raise ModelError(
                        "请完成旋律、声部、曲风检查；有歌词时须亲自照词试唱。", code="review_checks"
                    )
            item = {
                "id": new_id(),
                "revision_id": body.base_revision,
                "revision_number": project["revision"]["number"],
                "job_id": state.composition.job_id,
                "mock": state.composition.mock,
                "created_at": now(),
                "assessment": status["assessment"],
                **body.model_dump(exclude={"base_revision"}),
            }
            directory = self.store.directory(project_id) / "reviews"
            self.store.write_json(directory / "history" / (item["id"] + ".json"), item)
            self.store.write_json(directory / (body.base_revision + ".json"), item)
            return self.review_status(project_id)
