"""An explicit multi-request contract, never silently extending legacy requests."""

import hashlib
from threading import Event

from pydantic import Field

from .lyric_phrases import (
    CONTRACT,
    assemble,
    budget,
    chunks,
    compile_phrases,
    fingerprint,
    messages,
    mock_complete,
    mock_global,
    mock_phrase,
    phrase_context,
    validate_block,
    validate_global,
)
from .midi import export_midi
from .model_settings import ModelError
from .models import Model, Note, Score, SongState, Track, new_id
from .resources import AudioError
from .storage import StorageError, now

ACTIVE = ("queued", "running", "validating", "cancelling")


class BudgetRequest(Model):
    base_revision: str = Field(pattern=r"^[a-f0-9]{32}$")
    operation: str = Field(default="generate", pattern=r"^(generate|modify)$")
    instruction: str = Field(default="", max_length=2000)
    state: SongState | None = None


class Stopped(Exception):
    pass


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PhraseTaskRunner:
    def __init__(self, manager):
        self.manager = manager
        self.events = {}

    def offer(self, context, settings):
        result = budget(context)
        result.update(requested_model="deterministic-demo" if settings.mock else settings.name,
                      provider="mock" if settings.mock else settings.provider, mock=settings.mock)
        result["hash"] = fingerprint({"budget": {k: v for k, v in result.items() if k != "hash"},
                                      "context": context})
        return result

    def prepare(self, project_id, body):
        m = self.manager
        project = m.store.get(project_id)
        m.store.check_base(project, body.base_revision)
        source = body.state if getattr(body, "state", None) else SongState.model_validate(project["revision"]["state"])
        if not source.inputs.selected_lines:
            raise ModelError("请明确使用歌词全文，再启动试验乐句谱曲。", code="phrase_input")
        if body.operation == "full":
            raise ModelError("v2直接覆盖歌词全文；无歌词整曲保留原入口。", code="phrase_operation")
        source, context = phrase_context(source, body.operation, body.instruction)
        context["scope"] = "lyrics"
        return project, source, context

    def preview(self, project_id, body):
        m = self.manager
        with m.lock, m.store.lock:
            _, _, context = self.prepare(project_id, body)
            return self.offer(context, m.configuration())

    def check(self, project_id, job_id, base_revision):
        m = self.manager
        with m.lock, m.store.lock:
            if m.closed or self.events[job_id].is_set():
                raise Stopped()
            if m.store.header(project_id)["current_revision"] != base_revision:
                raise ModelError("输入版本已改变，已停止后续请求；旧块不能覆盖新版本。", 409, "stale")

    def _resume(self, project_id, old_id, context):
        m = self.manager
        old = m.read(project_id, old_id)
        if old["contract"] != CONTRACT or old["status"] not in ("failed", "cancelled", "interrupted"):
            raise ModelError("只能显式重试失败、取消或中断的v2任务。", 409, "phrase_retry")
        old_root = m.directory(project_id, old_id)
        original = m.store.read_json(old_root / "input.json")
        if fingerprint(original) != old["input_hash"] or fingerprint(original) != fingerprint(context):
            raise ModelError("输入快照已改变或损坏，不能复用分块；请新建完整任务。", 409, "phrase_retry_stale")
        if not old.get("plan_hash"):
            return old, None, []
        plan_file = old_root / "global-plan.json"
        if file_hash(plan_file) != old.get("global_file_hash"):
            raise ModelError("全局规划文件校验失败，拒绝重试复用。", 409, "phrase_retry_hash")
        plan = validate_global(m.store.read_json(plan_file), context)
        if fingerprint(plan.model_dump()) != old["plan_hash"]:
            raise ModelError("全局规划哈希不符。", 409, "phrase_retry_hash")
        assignments, successful = chunks(plan), []
        for i, status in enumerate(old.get("blocks", [])):
            if status["status"] != "completed":
                break
            result_file = old_root / "blocks" / f"{i:03d}" / "result.json"
            if file_hash(result_file) != status["file_hash"]:
                raise ModelError("已成功分块文件校验失败，拒绝复用。", 409, "phrase_retry_hash")
            successful.append(validate_block(m.store.read_json(result_file), plan, assignments[i], i))
        if (len(successful) == len(assignments) and old["status"] == "failed"
                and old.get("error_code") not in ("storage", "processing")):
            raise ModelError("局部块已完成但全文校验失败，请重新规划完整任务；重复旧块不能修复这个错误。",
                             409, "phrase_retry_complete_failure")
        return old, plan, successful

    def retry_preview(self, project_id, old_id, body):
        m = self.manager
        with m.lock, m.store.lock:
            old = m.read(project_id, old_id)
            if m.store.header(project_id)["current_revision"] != body.base_revision or old["base_revision"] != body.base_revision:
                raise ModelError("原输入版本已改变，请以新输入重新生成，不复用旧块。", 409, "stale")
            context = m.store.read_json(m.directory(project_id, old_id) / "input.json")
            state = SongState.model_validate(m.store.get(project_id)["revision"]["state"])
            _, actual = phrase_context(state, old["operation"], old["instruction"])
            actual["scope"] = "lyrics"
            old, plan, blocks = self._resume(project_id, old_id, actual)
            settings = m.configuration()
            if old["mock"] != settings.mock or old["model"] != ("deterministic-demo" if settings.mock else settings.name):
                raise ModelError("请求配置改变，请新建完整任务，不复用旧块。", 409, "phrase_retry_mode")
            result = self.offer(context, settings)
            result["retry_of"] = old_id
            result["reused_blocks"] = len(blocks)
            if plan:
                result["request_limit"] = len(chunks(plan)) - len(blocks)
                result["total_output_token_limit"] = result["request_limit"] * 32768
            result["hash"] = fingerprint({"budget": {k: v for k, v in result.items() if k != "hash"},
                                          "input_hash": old["input_hash"], "plan_hash": old.get("plan_hash"),
                                          "blocks": [fingerprint(b.model_dump()) for b in blocks]})
            return result

    def start(self, project_id, body, settings, retry_of=None):
        m = self.manager
        project, source, context = self.prepare(project_id, body)
        if body.operation == "modify":
            m.validate_source(project_id, source)
        old, plan, reuse = None, None, []
        if retry_of:
            old = m.read(project_id, retry_of)
            # The original instruction/operation are immutable for this retry.
            body = body.model_copy(update={"operation": old["operation"], "instruction": old["instruction"]})
            project, source, context = self.prepare(project_id, body)
            offer = self.retry_preview(project_id, retry_of, body)
            old, plan, reuse = self._resume(project_id, retry_of, context)
            if old["mock"] != settings.mock or old["model"] != ("deterministic-demo" if settings.mock else settings.name):
                raise ModelError("请求配置改变，不能复用旧块；请新建完整任务。", 409, "phrase_retry_mode")
        else:
            offer = self.offer(context, settings)
        if not settings.mock and body.budget_hash != offer["hash"]:
            raise ModelError("请先查看并确认v2请求数及token预算；预算或输入已改变。", 403, "phrase_budget_consent")
        m.resource.info()
        from .composition import CHANNELS, FAMILIES, reference_events
        pinned = []
        for family in FAMILIES:
            recordings, notes = reference_events(source, family, 500000)
            if notes:
                pinned.append(Track(name=family, family=family, channel=CHANNELS[family], is_drum=family == "drums",
                                    program=recordings[0].program, bank_msb=recordings[0].bank_msb,
                                    bank_lsb=recordings[0].bank_lsb, notes=notes))
        if pinned:
            m.resource.validate_score(Score(tracks=pinned, end_tick=max(
                n.start_tick + n.duration_tick for t in pinned for n in t.notes)), "full")
        settings = settings.model_copy(update={"max_tokens": 32768})
        job_id, timestamp = new_id(), now()
        root = m.directory(project_id, job_id)
        item = {
            "id": job_id, "project_id": project_id, "base_revision": body.base_revision,
            "base_number": project["revision"]["number"], "operation": body.operation,
            "instruction": body.instruction.strip(), "scope": "lyrics", "contract": CONTRACT,
            "target_seconds": None, "duration_policy": "lyrics", "max_output_tokens": 32768,
            "full_source": None, "status": "queued", "phase": "等待试验乐句谱曲",
            "mock": settings.mock, "provider": "mock" if settings.mock else settings.provider,
            "model": "deterministic-demo" if settings.mock else settings.name,
            "created_at": timestamp, "updated_at": timestamp, "error": None, "error_code": None,
            "request_count": 0, "simulated_requests": 0, "metadata": None, "checks": [],
            "budget": offer, "reserved_attempts": offer["request_limit"], "input_hash": fingerprint(context),
            "retry_of": retry_of, "reused_blocks": len(reuse), "blocks": [], "plan_hash": None,
        }
        m.store.write_json(root / "input.json", context)
        m.store.write_json(root / "budget.json", offer)
        m.store.write_json(root / "job.json", item)
        self.events[job_id] = Event()
        m.job_projects[job_id] = project_id
        m.futures[job_id] = m.executor.submit(self.run, project_id, job_id, source, context, settings, plan, reuse)
        return item

    def _call(self, project_id, job_id, context, settings, stage, raw_mock, validate):
        m = self.manager
        item = m.read(project_id, job_id)
        self.check(project_id, job_id, item["base_revision"])
        number = item["request_count"] + item.get("simulated_requests", 0)
        if number >= item["reserved_attempts"]:
            raise ModelError("已达到预留请求上限，未分发额外请求。", 409, "phrase_budget")
        root = m.directory(project_id, job_id) / "requests" / f"{number:03d}"
        m.store.write_json(root / "input.json", context)
        m.store.write_json(root / "status.json", {"stage": stage, "status": "prepared", "input_hash": fingerprint(context)})
        metadata = None
        try:
            if settings.mock:
                m._update(project_id, job_id, simulated_requests=number + 1)
                raw = raw_mock()
                metadata = {"returned_model": "deterministic-demo", "finish_reason": "mock",
                            "elapsed_seconds": None, "usage": None}
            else:
                adapter = m.adapter_factory(settings)

                def dispatched():
                    with m.lock, m.store.lock:
                        self.check(project_id, job_id, item["base_revision"])
                        current = m.read(project_id, job_id)
                        m._update(project_id, job_id, request_count=current["request_count"] + 1)

                adapter.on_request = dispatched
                raw, metadata = adapter.generate(messages(context))
            self.check(project_id, job_id, item["base_revision"])
            result = validate(raw)
            m.store.write_json(root / "result.json", result.model_dump() if hasattr(result, "model_dump") else result)
            m.store.write_json(root / "status.json", {"stage": stage, "status": "completed",
                                                     "metadata": metadata, "result_hash": fingerprint(raw)})
            self._usage(project_id, job_id, metadata)
            return result
        except Exception as error:
            metadata = getattr(error, "metadata", metadata)
            m.store.write_json(root / "status.json", {"stage": stage, "status": "failed_or_discarded",
                                                     "metadata": metadata})
            if metadata:
                self._usage(project_id, job_id, metadata)
            raise

    def _usage(self, project_id, job_id, metadata):
        m = self.manager
        item = m.read(project_id, job_id)
        totals = dict((item.get("metadata") or {}).get("usage") or {})
        for name, value in (metadata.get("usage") or {}).items():
            totals[name] = totals.get(name, 0) + value
        m._update(project_id, job_id, metadata={"returned_model": metadata.get("returned_model"),
                                               "elapsed_seconds": None, "usage": totals or None})

    def run(self, project_id, job_id, source, context, settings, plan=None, reuse=()):
        m = self.manager
        root = m.directory(project_id, job_id)
        try:
            self.check(project_id, job_id, m.read(project_id, job_id)["base_revision"])
            m._update(project_id, job_id, status="running", phase="规划歌词与音乐乐句")
            item = m.read(project_id, job_id)
            if item["budget"]["mode"] == "single" and plan is None:
                from .lyric_phrases import PhrasePlan, parse
                raw_plan = self._call(project_id, job_id, {**context, "stage": "single"}, settings,
                                      "single", lambda: mock_complete(context), lambda raw: parse(PhrasePlan, raw))
                raw = raw_plan.model_dump()
            else:
                if plan is None:
                    def checked_global(raw):
                        value = validate_global(raw, context)
                        from .composition import CHANNELS
                        m.resource.validate_score(Score(end_tick=480, tracks=[
                            Track(name=i.family, family=i.family, channel=CHANNELS[i.family],
                                  is_drum=i.family == "drums", program=i.program, bank_msb=i.bank_msb,
                                  notes=[Note(pitch=36 if i.family == "drums" else 60,
                                              start_tick=0, duration_tick=480)])
                            for i in value.instruments]), "full")
                        return value
                    plan = self._call(project_id, job_id, {**context, "stage": "global"}, settings,
                                      "global", lambda: mock_global(context), checked_global)
                m.store.write_json(root / "global-plan.json", plan.model_dump())
                m._update(project_id, job_id, plan_hash=fingerprint(plan.model_dump()),
                          global_file_hash=file_hash(root / "global-plan.json"))
                assignments, accepted = chunks(plan), []
                statuses = [{"index": i, "status": "pending", "unit_count": sum(len(p.uids) for p in ps)}
                            for i, ps in enumerate(assignments)]
                m._update(project_id, job_id, blocks=statuses, planned_requests=1 + len(assignments))
                for i, assigned in enumerate(assignments):
                    self.check(project_id, job_id, item["base_revision"])
                    block_root = root / "blocks" / f"{i:03d}"
                    block_context = {
                        **context, "stage": "block", "global_plan": plan.model_dump(),
                        "plan_hash": fingerprint(plan.model_dump()), "block_index": i,
                        "assigned_phrases": [p.model_dump() for p in assigned],
                        "previous_tail": [p.model_dump() for p in accepted[-1].phrases[-1:]] if accepted else [],
                        "next_intent": assignments[i + 1][0].model_dump() if i + 1 < len(assignments) else None,
                    }
                    m.store.write_json(block_root / "input.json", block_context)
                    statuses[i]["status"] = "running"
                    m._update(project_id, job_id, blocks=statuses, phase=f"谱写第{i + 1}/{len(assignments)}块")
                    if i < len(reuse):
                        block = validate_block(reuse[i].model_dump(), plan, assigned, i)
                        statuses[i]["reused"] = True
                    else:
                        block = self._call(
                            project_id, job_id, block_context, settings, f"block-{i}",
                            lambda assigned=assigned, i=i: {"format": "lyric-phrases-block-v2",
                                     "plan_hash": fingerprint(plan.model_dump()), "block_index": i,
                                     "phrases": [mock_phrase(p.model_dump()) for p in assigned]},
                            lambda raw, assigned=assigned, i=i: validate_block(raw, plan, assigned, i),
                        )
                    accepted.append(block)
                    m.store.write_json(block_root / "result.json", block.model_dump())
                    statuses[i].update(status="completed", file_hash=file_hash(block_root / "result.json"),
                                       plan_hash=fingerprint(plan.model_dump()), result_hash=fingerprint(block.model_dump()))
                    m._update(project_id, job_id, blocks=statuses)
                raw = assemble(plan, accepted)
            self.check(project_id, job_id, item["base_revision"])
            m._update(project_id, job_id, status="validating", phase="全文统一校验与四声部编配")
            state, complete = compile_phrases(raw, source, job_id, settings.mock, context)
            m.resource.validate_score(state.score, "full")
            with m.lock, m.store.lock:
                self.check(project_id, job_id, item["base_revision"])
                m.store.write_json(root / "plan.json", complete.model_dump())
                m.store.write_json(root / "candidate.json", state.model_dump())
                m.store.write_bytes(root / "candidate.mid", export_midi(state.score))
                m._update(project_id, job_id, status="completed", phase="全文候选完成，等待明确采用",
                          checks=state.composition.checks, result_seconds=state.composition.target_seconds)
        except Stopped:
            self.terminal(project_id, job_id, status="cancelled", phase="已取消；没有发布半首候选",
                      error="当前请求已停止使用，不再分发后续请求。", error_code="cancelled")
        except (ModelError, StorageError, AudioError, OSError) as error:
            self.terminal(project_id, job_id, status="failed", phase="未发布全文候选，后续请求已停止",
                      error=str(error) if isinstance(error, (ModelError, StorageError, AudioError)) else "分块文件读写失败。",
                      error_code=getattr(error, "code", "storage"))
        except Exception:
            self.terminal(project_id, job_id, status="failed", phase="分块处理失败，后续请求已停止",
                      error="分块或编配处理失败，未发布不完整候选。", error_code="processing")

    def terminal(self, project_id, job_id, **changes):
        m = self.manager
        item = m.read(project_id, job_id)
        blocks = item.get("blocks", [])
        for block in blocks:
            if block["status"] == "running":
                block["status"] = changes["status"]
            elif block["status"] == "pending":
                block["status"] = "skipped"
        m._update(project_id, job_id, blocks=blocks, **changes)

    def cancel(self, project_id, job_id):
        m = self.manager
        with m.lock:
            item = m.read(project_id, job_id)
            if item["contract"] != CONTRACT or item["status"] not in ACTIVE or job_id not in self.events:
                raise ModelError("当前任务不可取消。", 409, "phrase_cancel")
            self.events[job_id].set()
            if m.futures[job_id].cancel():
                return m._update(project_id, job_id, status="cancelled", phase="排队任务已取消")
            return m._update(project_id, job_id, status="cancelling",
                             phase="取消中；当前请求结束/超时后停止，不再分发")
