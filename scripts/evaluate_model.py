"""Explicit, bounded model evaluation; isolated artifacts, never raw credentials."""

import argparse
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.generation import GenerationManager, GenerationRequest  # noqa: E402
from backend.app.model_settings import ModelError, ModelSettings, load_model_settings  # noqa: E402
from backend.app.models import Inputs, RecordedNote, SongState  # noqa: E402
from backend.app.render import RenderManager, RenderRequest  # noqa: E402
from backend.app.resources import AudioError, AudioPaths, SoundFontResource  # noqa: E402
from backend.app.storage import ProjectStore, StorageError, now  # noqa: E402


def wait_for(manager, project_id, job_id, seconds=420):
    deadline, last = time.monotonic() + seconds, None
    while time.monotonic() < deadline:
        item = manager.read(project_id, job_id)
        if item["phase"] != last:
            print(item["phase"], flush=True)
            last = item["phase"]
        if item["status"] in ("completed", "failed"):
            return item
        time.sleep(0.2)
    raise ModelError("验收等待超时；未重发请求，请保留目录查看原任务。", code="evaluation_timeout")


def source_for(case, initial):
    state = SongState.model_validate(initial)
    inputs = Inputs(
        theme="走过低谷后的重新出发",
        emotion="积极、温暖的流行曲风，清楚的旋律，轻快但不过度吵闹。",
    )
    if case in ("lyrics", "preserve_lyrics", "extend_lyrics"):
        inputs.lyrics_text = "把昨日轻轻放下\n让脚步迎向朝阳"
        inputs.selected_lines = [0, 1]
    if case in ("preserve", "preserve_lyrics", "extend_lyrics"):
        inputs.melody_mode = "extend" if case == "extend_lyrics" else "preserve"
        inputs.recording = [
            RecordedNote(
                pitch=pitch,
                start_seconds=index * 0.5,
                duration_seconds=0.35,
                track_id=state.score.tracks[0].id,
                family="keyboard",
            )
            for index, pitch in enumerate((60, 62, 64))
        ]
    return state.model_copy(update={"inputs": inputs})


def capture_requests(summary, result, job):
    """Reserved case attempts are never reported as dispatched HTTP attempts or costs."""
    result.update(
        request_count=job.get("request_count", 0),
        metadata=job.get("metadata"),
    )
    if job.get("error_code"):
        result["error_code"] = job["error_code"]
    summary["actual_requests"] = sum(item.get("request_count", 0) for item in summary["cases"])
    summary["live_attempts"] = summary["actual_requests"]  # Legacy field, with corrected semantics.


def human_review(artifacts, root, result, project, status):
    case = result["case"]
    lines = [
        f"# {case} 人工试听记录（未评估）",
        "",
        f"歌曲：{project['id']}；保存版本：{project['current_revision']}。",
        f"输入版本：{result['input_revision']}；模式：{'模拟' if result['mock'] else '真实'}。",
        "",
        "请播放summary.json中的WAV/MP3，照词亲自试唱；每项填写符合/需改/未评估，注明秒位置或歌词行与原因。",
        "结构检查、真实发声和模拟审核均不能替代这份音乐效果记录；此文件不会自动改变软件审核或汇总为质量通过。",
        "",
        "| 项目 | 结果 | 位置与具体说明 |",
        "| --- | --- | --- |",
    ]
    for dimension in (
        "旋律与段落发展",
        "四声部配合与音色",
        "主题及要求的曲风",
        "原旋律保留/走向利用",
        "整曲修改要求体现（如适用）",
    ):
        lines.append(f"| {dimension} | 未评估 | |")
    for line in status["assessment"]["lyric_lines"]:
        lines.append(
            f"| 第{line['line_index'] + 1}行试唱：{line['text']} | 未评估 | {line['start_seconds']:.2f}–{line['end_seconds']:.2f}秒；请检查音域、字词时长、跳进、重音及换气 |"
        )
    path = root / f"human-review-{case}.md"
    artifacts.write_bytes(path, ("\n".join(lines) + "\n").encode("utf-8"))
    result["human_review"] = str(path)
    result["music_quality"] = "not_assessed"
    result["singability"] = (
        "not_assessed"
        if project["revision"]["state"]["inputs"]["selected_lines"]
        else "not_applicable"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="No .env load or network requests.")
    mode.add_argument(
        "--allow-real", action="store_true", help="Requires explicit user authorization."
    )
    parser.add_argument("--max-requests", type=int, choices=range(1, 4))
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        choices=(12000, 32768),
        default=32768,
        help="Explicit new budget; old authorized rounds are not resumed.",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=("text", "lyrics", "preserve", "preserve_lyrics", "extend_lyrics", "modify"),
        default=["text", "lyrics", "preserve"],
    )
    parser.add_argument(
        "--accept-structural-source",
        action="store_true",
        help="Explicitly adopt the valid preserve_lyrics evaluation candidate for dependent modification, without human quality approval.",
    )
    args = parser.parse_args()
    if len(set(args.cases)) != len(args.cases):
        parser.error("Each case may run only once; retries are not supported.")
    if args.allow_real and (args.max_requests is None or len(args.cases) > args.max_requests):
        parser.error("Real evaluation requires a sufficient explicit --max-requests limit.")
    if "modify" in args.cases:
        if "preserve_lyrics" not in args.cases or args.cases.index("modify") < args.cases.index(
            "preserve_lyrics"
        ):
            parser.error(
                "modify requires an earlier preserve_lyrics case; failed sources are not replaced."
            )
        if args.allow_real and not args.accept_structural_source:
            parser.error(
                "Real dependent modification also requires explicit --accept-structural-source."
            )
    try:
        settings = ModelSettings() if args.mock else load_model_settings()
        if args.allow_real and settings.mock:
            print("MODEL_MOCK 仍为 true：未发起真实请求。请用户自行改为 false 后再开始。")
            return 2
        settings = settings.model_copy(
            update={"max_tokens": min(settings.max_tokens, args.max_output_tokens)}
        )
        settings.ensure_available()
    except ModelError as error:
        print(str(error))
        return 2
    root = ROOT / ".cache" / "model-evaluations" / uuid4().hex
    if not root.resolve().is_relative_to(ROOT.resolve() / ".cache") or any(
        parent.is_symlink() for parent in (root, *root.parents)
    ):
        print("验收目录无效。")
        return 2
    artifacts, store = ProjectStore(root), ProjectStore(root / "data")
    resource = SoundFontResource(AudioPaths())
    generator = GenerationManager(store, resource, lambda: settings)
    renderer = RenderManager(store, resource)
    summary = {
        "stage": 5,
        "created_at": now(),
        "mock": settings.mock,
        "max_requests": 0 if args.mock else args.max_requests,
        "live_attempts": 0,
        "reserved_attempts": 0,
        "actual_requests": 0,
        "request_count_meaning": "HTTP dispatch attempts, including connection failures; never confirms server receipt or billing.",
        "cost": "unknown",
        "max_output_tokens": settings.max_tokens,
        "music_quality": "not_assessed",
        "lyrics_singability": "not_assessed",
        "cases": [],
    }
    print(f"Artifacts: {root}", flush=True)
    print(
        "Mode: mock (0 requests)" if args.mock else "Mode: real; one attempt per case; no retries",
        flush=True,
    )
    adopted_source = None
    try:
        for case in args.cases:
            if case == "modify" and adopted_source is None:
                summary["cases"].append(
                    {
                        "case": case,
                        "status": "skipped",
                        "reason": "preserve_lyrics没有可采用且已完成音频链路的候选；未补发或替换来源。",
                        "request_count": 0,
                    }
                )
                artifacts.write_json(root / "summary.json", summary)
                continue
            if case == "modify":
                project = adopted_source
            else:
                project = store.create(f"短曲隔离验收-{case}")
                state = source_for(case, project["revision"]["state"])
                project = store.save(project["id"], project["current_revision"], state, "验收输入")
            result = {
                "case": case,
                "project_id": project["id"],
                "input_revision": project["current_revision"],
                "operation": "modify" if case == "modify" else "generate",
                "mock": settings.mock,
                "status": "starting",
                "preflight": "pending",
                "request_count": 0,
            }
            summary["cases"].append(result)
            # Reserve before submission: failures and timeouts consume the bounded attempt budget.
            if not settings.mock:
                if summary["reserved_attempts"] >= args.max_requests:
                    result.update(status="skipped", reason="预算已预留完毕")
                    break
                summary["reserved_attempts"] += 1
            artifacts.write_json(root / "summary.json", summary)
            print(f"Case: {case}", flush=True)
            job = None
            try:
                job = generator.start(
                    project["id"],
                    GenerationRequest(
                        base_revision=project["current_revision"],
                        confirm_real_call=args.allow_real,
                        operation=result["operation"],
                        instruction="整体更轻快，仍保持歌词原文和原演奏约束"
                        if case == "modify"
                        else "",
                    ),
                )
                result["job_id"] = job["id"]
                result["preflight"] = "passed"
                artifacts.write_json(root / "summary.json", summary)
                job = wait_for(generator, project["id"], job["id"])
                result.update(
                    status=job["status"],
                    error=job["error"],
                    request_count=job["request_count"],
                    metadata=job["metadata"],
                    checks=job["checks"],
                )
                capture_requests(summary, result, job)
                if job["status"] == "completed":
                    project = generator.apply(
                        project["id"],
                        job["id"],
                        project["current_revision"],
                    )
                    candidate = SongState.model_validate(project["revision"]["state"])
                    status = generator.review_status(project["id"])
                    result["assessment"] = status["assessment"]
                    if not status["valid"]:
                        raise ModelError(status["reason"], code="assessment")
                    result["timeline_seconds"] = candidate.score.seconds_at(
                        candidate.score.end_tick
                    )
                    result["notes"] = sum(len(track.notes) for track in candidate.score.tracks)
                    result["output_revision"] = project["current_revision"]
                    result["filtered_model_notes"] = (
                        candidate.composition.reference_lock.filtered_model_notes
                        if candidate.composition.reference_lock
                        else 0
                    )
                    result["midi"] = str(
                        generator.directory(project["id"], job["id"]) / "candidate.mid"
                    )
                    render = renderer.start(
                        project["id"],
                        RenderRequest(base_revision=project["current_revision"], mode="full"),
                    )
                    render = wait_for(renderer, project["id"], render["id"])
                    result["render_status"] = render["status"]
                    result["render_error"] = render["error"]
                    result["audio_duration_seconds"] = render["duration_seconds"]
                    if render["status"] == "completed":
                        result["mp3"] = str(
                            renderer.directory(project["id"], render["id"]) / "mix.mp3"
                        )
                        result["wav"] = str(
                            renderer.directory(project["id"], render["id"]) / "mix.wav"
                        )
                        if case == "preserve_lyrics":
                            adopted_source = project
                    human_review(artifacts, root, result, project, status)
                print(f"Result: {case}: {result['status']}", flush=True)
            except (ModelError, StorageError, AudioError) as error:
                result.update(
                    status="failed", error=str(error), error_code=getattr(error, "code", "storage")
                )
                if job is None:
                    result["preflight"] = "failed"
                else:
                    current_job = generator.read(project["id"], job["id"])
                    capture_requests(summary, result, current_job)
                print(str(error), flush=True)
            artifacts.write_json(root / "summary.json", summary)
    finally:
        generator.close()
        renderer.close()
    summary["finished_at"] = now()
    artifacts.write_json(root / "summary.json", summary)
    print(f"Summary: {root / 'summary.json'}", flush=True)
    return (
        0
        if all(
            item["status"] == "completed" and item.get("render_status") == "completed"
            for item in summary["cases"]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
