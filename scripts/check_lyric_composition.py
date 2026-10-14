"""Bounded lyric-workflow acceptance; isolated songs and explicit real-call permission.

Mock mode never loads .env. Real mode uses the backend configuration loader without
printing secrets. A failed first candidate/audio chain always skips modification.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.composition import model_messages  # noqa: E402
from backend.app.generation import GenerationManager, GenerationRequest  # noqa: E402
from backend.app.midi import export_midi, import_midi  # noqa: E402
from backend.app.model_settings import ModelError, ModelSettings, load_model_settings  # noqa: E402
from backend.app.models import SongState  # noqa: E402
from backend.app.render import RenderManager, RenderRequest  # noqa: E402
from backend.app.resources import AudioError, AudioPaths, SoundFontResource  # noqa: E402
from backend.app.storage import ProjectStore, StorageError, now  # noqa: E402
from scripts.evaluate_model import human_review, wait_for  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true")
    mode.add_argument(
        "--allow-real", action="store_true", help="Only after explicit user authorization."
    )
    parser.add_argument("--max-requests", type=int, choices=(1, 2))
    parser.add_argument("--lyrics-file", type=Path, required=True)
    args = parser.parse_args()
    if args.allow_real and args.max_requests is None:
        parser.error("Real acceptance requires an explicitly authorized --max-requests of 1 or 2.")
    if args.mock and args.max_requests is not None:
        parser.error("Mock acceptance uses zero real requests; do not supply --max-requests.")
    path = args.lyrics_file.resolve()
    if path.suffix.lower() != ".txt" or ".env" in path.name.lower() or not path.is_file():
        parser.error("Provide a UTF-8 lyric .txt file; configuration files are never lyric input.")
    if path.stat().st_size > 512000:
        parser.error("Lyric file exceeds the 500 KB upload limit.")
    content = path.read_bytes()
    try:
        text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        if "\x00" in text:
            raise ValueError
    except (UnicodeError, ValueError):
        parser.error("Provide a UTF-8 plain text lyric file.")
    try:
        settings = ModelSettings() if args.mock else load_model_settings()
        if args.allow_real and settings.mock:
            print("MODEL_MOCK仍为true，真实分发0次。请用户自行调整后再执行。", flush=True)
            return 2
        settings.ensure_available()
        resource = SoundFontResource(AudioPaths())
        resource.info()
        if not all(
            p.is_file()
            for p in (resource.paths.ffmpeg, resource.paths.ffprobe, resource.paths.fluidsynth)
        ):
            raise AudioError("音频工具不完整，尚未请求模型。")
    except (ModelError, AudioError) as error:
        print(str(error), flush=True)
        return 2
    root = ROOT / ".cache" / "lyric-composition-checks" / uuid4().hex
    if not root.resolve().is_relative_to(ROOT / ".cache") or any(
        p.is_symlink() for p in root.parents
    ):
        print("隔离验收目录无效，未请求模型。", flush=True)
        return 2
    artifacts, store = ProjectStore(root), ProjectStore(root / "data")
    generator = GenerationManager(store, resource, lambda: settings)
    renderer = RenderManager(store, resource)
    summary = {
        "created_at": now(),
        "mode": "mock" if settings.mock else "real",
        "root": str(root),
        "contract": "lyric-motifs-v1",
        "max_requests": 0 if args.mock else args.max_requests,
        "reserved_attempts": 0,
        "actual_requests": 0,
        "max_output_tokens": 32768,
        "request_count_meaning": "HTTP dispatch attempts, including timeout; not proof of billing or server receipt.",
        "cost": "unknown",
        "music_quality": "not_assessed",
        "singability": "not_assessed",
        "cases": [],
        "input_sha256": hashlib.sha256(content).hexdigest(),
    }
    print(f"Artifacts: {root}", flush=True)
    adopted = None
    count = 2 if args.mock else args.max_requests
    try:
        for case in ("whole_lyrics", "modify")[:count]:
            result = {"case": case, "mock": settings.mock, "request_count": 0, "status": "starting"}
            summary["cases"].append(result)
            if case == "modify" and adopted is None:
                result.update(
                    status="skipped",
                    reason="全文候选或音频链路未成功，跳过依赖修改；未替换或补发。",
                )
                artifacts.write_json(root / "summary.json", summary)
                break
            if case == "whole_lyrics":
                project = store.create("歌词谱曲重写隔离验收")

                def transform(state):
                    state.inputs.lyrics_text = text
                    state.inputs.selected_lines = [
                        i for i, line in enumerate(text.split("\n")) if line.strip()
                    ]
                    state.inputs.theme = "追逐梦想、相互理解、温柔地迎接明天"
                    state.inputs.emotion = (
                        "积极明亮的流行乐队曲风，中速，清晰自然且便于试唱的主旋律"
                    )

                project = store.upload(
                    project["id"],
                    project["current_revision"],
                    content,
                    path.name,
                    "lyrics",
                    transform,
                )
            else:
                project = adopted
            result.update(
                project_id=project["id"],
                input_revision=project["current_revision"],
                operation="modify" if case == "modify" else "generate",
            )
            if not settings.mock:
                summary["reserved_attempts"] += 1
            artifacts.write_json(root / "summary.json", summary)
            print(f"Case: {case}", flush=True)
            job = None
            try:
                job = generator.start(
                    project["id"],
                    GenerationRequest(
                        base_revision=project["current_revision"],
                        operation=result["operation"],
                        confirm_real_call=args.allow_real,
                        instruction="整体更轻快一些，吉他更柔和，保留歌词全文和积极明亮的风格。"
                        if case == "modify"
                        else "",
                    ),
                )
                result["job_id"] = job["id"]
                context = store.read_json(
                    generator.directory(project["id"], job["id"]) / "input.json"
                )
                result["messages_sha256"] = hashlib.sha256(
                    json.dumps(model_messages(context), ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
                artifacts.write_json(root / "summary.json", summary)
                item = wait_for(generator, project["id"], job["id"])
                result.update(
                    status=item["status"],
                    request_count=item["request_count"],
                    metadata=item["metadata"],
                    error=item["error"],
                    error_code=item.get("error_code"),
                )
                if item["status"] == "completed":
                    project = generator.apply(project["id"], job["id"], project["current_revision"])
                    state = SongState.model_validate(project["revision"]["state"])
                    status = generator.review_status(project["id"])
                    if not status["valid"]:
                        raise ModelError("保存版本的结构或输入来源核对失败。", code="assessment")
                    imported = import_midi(export_midi(state.score))
                    actual = state.score.seconds_at(state.score.end_tick)
                    if abs(imported.seconds_at(imported.end_tick) - actual) > 0.001:
                        raise ModelError("MIDI往返时长不一致。", code="midi_roundtrip")
                    if sum(m.type == "lyrics" for m in imported.midi_meta) != len(
                        state.composition.lyrics_alignment
                    ):
                        raise ModelError("MIDI没有完整保存歌词标记。", code="midi_lyrics")
                    result.update(
                        output_revision=project["current_revision"],
                        assessment=status["assessment"],
                        timeline_seconds=actual,
                        notes=sum(len(t.notes) for t in state.score.tracks),
                        lyric_units=len(state.composition.lyrics_alignment),
                        midi=str(generator.directory(project["id"], job["id"]) / "candidate.mid"),
                    )
                    render = renderer.start(
                        project["id"],
                        RenderRequest(base_revision=project["current_revision"], mode="full"),
                    )
                    render = wait_for(renderer, project["id"], render["id"])
                    result.update(
                        render_status=render["status"],
                        render_error=render["error"],
                        audio_duration_seconds=render["duration_seconds"],
                    )
                    if render["status"] == "completed":
                        audio_dir = renderer.directory(project["id"], render["id"])
                        decoded = subprocess.run(
                            [
                                str(resource.paths.ffmpeg),
                                "-hide_banner",
                                "-v",
                                "error",
                                "-i",
                                str(audio_dir / "mix.mp3"),
                                "-f",
                                "null",
                                "-",
                            ],
                            capture_output=True,
                            timeout=120,
                        )
                        if decoded.returncode:
                            raise AudioError("MP3完整解码失败。")
                        result.update(
                            wav=str(audio_dir / "mix.wav"),
                            mp3=str(audio_dir / "mix.mp3"),
                            wav_metrics=render["wav"],
                            mp3_decode="passed",
                            files=render["files"],
                        )
                        if case == "whole_lyrics":
                            adopted = (
                                project  # Technical evaluation only; no human approval record.
                            )
                    human_review(artifacts, root, result, project, status)
            except (ModelError, AudioError, StorageError) as error:
                result.update(
                    status="failed", error=str(error), error_code=getattr(error, "code", "local")
                )
            finally:
                if job is not None:
                    item = generator.read(project["id"], job["id"])
                    result.update(request_count=item["request_count"], metadata=item["metadata"])
                summary["actual_requests"] = sum(c["request_count"] for c in summary["cases"])
                artifacts.write_json(root / "summary.json", summary)
            print(
                f"Result: {case}: {result['status']}; dispatched={result['request_count']}",
                flush=True,
            )
    finally:
        generator.close()
        renderer.close()
        summary["finished_at"] = now()
        artifacts.write_json(root / "summary.json", summary)
    print(f"Summary: {root / 'summary.json'}", flush=True)
    return (
        0
        if all(
            c["status"] == "completed" and c.get("render_status") == "completed"
            for c in summary["cases"]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
