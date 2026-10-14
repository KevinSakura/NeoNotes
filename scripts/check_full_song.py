"""Offline full-song acceptance with isolated data and actual local audio tools.

No .env loading, network requests, paid calls or formal-song mutations.
"""

import argparse
import hashlib
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.assessment import assess  # noqa: E402
from backend.app.generation import GenerationManager, GenerationRequest, ReviewRequest  # noqa: E402
from backend.app.midi import export_midi, import_midi  # noqa: E402
from backend.app.model_settings import ModelSettings  # noqa: E402
from backend.app.models import RecordedNote, SongState  # noqa: E402
from backend.app.render import RenderManager, RenderRequest  # noqa: E402
from backend.app.resources import AudioPaths, SoundFontResource  # noqa: E402
from backend.app.storage import ProjectStore, now  # noqa: E402


def wait_for(manager, project_id, job_id):
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        item = manager.read(project_id, job_id)
        if item["status"] in ("completed", "failed"):
            if item["status"] != "completed":
                raise RuntimeError(item.get("error") or "隔离检查未完成。")
            return item
        time.sleep(0.1)
    raise RuntimeError("等待隔离任务超时；没有重发。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-audio", action="store_true", help="Only symbolic checks; no WAV/MP3 claim."
    )
    args = parser.parse_args()
    root = ROOT / ".cache" / "full-song-checks" / uuid4().hex
    artifacts, store = ProjectStore(root), ProjectStore(root / "data")
    resource = SoundFontResource(AudioPaths())
    generator = GenerationManager(store, resource, ModelSettings)
    renderer = RenderManager(store, resource)
    summary = {
        "stage": 6,
        "created_at": now(),
        "mode": "mock",
        "actual_network_requests": 0,
        "max_full_output_tokens": 32768,
        "music_quality": "not_assessed",
        "singability": "not_assessed",
        "root": str(root),
        "cases": [],
    }
    print(f"Artifacts: {root}", flush=True)
    try:
        for case in ("instrumental_full", "whole_lyrics_full", "preserved_full"):
            result = {"case": case, "status": "running", "mock": True}
            summary["cases"].append(result)
            project = store.create("整曲离线检查 · " + case)
            state = SongState.model_validate(project["revision"]["state"])
            state.inputs.theme = "经过低谷，开始新的旅程"
            state.inputs.emotion = "温暖积极的流行风格，沿用审核短曲旋律"
            if case != "instrumental_full":
                state.inputs.lyrics_text = (
                    "迎着微风出发\n昨日轻轻放下\n脚步迎向朝阳\n让每一段旅程都有回答"
                )
                state.inputs.selected_lines = [0, 1, 2, 3]
            if case == "preserved_full":
                state.inputs.melody_mode = "preserve"
                state.inputs.recording = [
                    RecordedNote(
                        pitch=pitch,
                        velocity=81 + i,
                        start_seconds=0.173 + i * 0.61,
                        duration_seconds=0.307,
                        track_id=state.score.tracks[0].id,
                        family="keyboard",
                        program=4,
                    )
                    for i, pitch in enumerate((60, 62, 64))
                ]
            project = store.save(project["id"], project["current_revision"], state, "隔离创作输入")
            input_revision = project["current_revision"]
            job = generator.start(
                project["id"], GenerationRequest(base_revision=project["current_revision"])
            )
            item = wait_for(generator, project["id"], job["id"])
            project = generator.apply(project["id"], job["id"], project["current_revision"])
            short_revision = None
            if case == "instrumental_full":
                short_revision = project["current_revision"]
                generator.review(
                    project["id"],
                    ReviewRequest(
                        base_revision=short_revision,
                        approved=True,
                        melody=True,
                        arrangement=True,
                        style=True,
                        lyrics_sung=True,
                        comment="模拟门槛检查，不代表人工音乐或试唱验收。",
                    ),
                )
                job = generator.start(
                    project["id"], GenerationRequest(base_revision=short_revision, operation="full")
                )
                item = wait_for(generator, project["id"], job["id"])
                project = generator.apply(project["id"], job["id"], short_revision)
            result.update(
                project_id=project["id"],
                source_revision=short_revision,
                input_revision=input_revision,
                lyric_workflow="direct_full_text"
                if case != "instrumental_full"
                else "reviewed_short_to_full",
                full_job_id=job["id"],
                max_output_tokens=item["max_output_tokens"],
                request_count=item["request_count"],
            )
            variants = [(case, project)]
            outputs = []
            for variant, saved in variants:
                state = SongState.model_validate(saved["revision"]["state"])
                score = state.score
                report = generator.review_status(saved["id"])
                # For the older variant, assess its own pinned state as well as its MIDI.
                assessment = assess(state, resource)
                generator.validate_source(saved["id"], state)
                if not assessment["valid"]:
                    raise RuntimeError("整曲结构检查失败。")
                midi = export_midi(score)
                imported = import_midi(midi)
                if (
                    abs(imported.seconds_at(imported.end_tick) - state.composition.target_seconds)
                    > 0.001
                ):
                    raise RuntimeError("整曲MIDI往返时长不一致。")
                output = {
                    "case": variant,
                    "revision_id": saved["current_revision"],
                    "job_id": state.composition.job_id,
                    "score_seconds": score.seconds_at(score.end_tick),
                    "notes": sum(len(track.notes) for track in score.tracks),
                    "lyric_lines": len(state.inputs.selected_lines),
                    "lyric_units": len(state.composition.lyrics_alignment),
                    "assessment": assessment,
                    "midi_sha256": hashlib.sha256(midi).hexdigest(),
                    "current_version_valid": report["valid"],
                    "human_quality": "not_assessed",
                    "audio": "not_checked",
                }
                if not args.no_audio:
                    render = renderer.start(
                        saved["id"],
                        RenderRequest(base_revision=saved["current_revision"], mode="full"),
                    )
                    render = wait_for(renderer, saved["id"], render["id"])
                    directory = renderer.directory(saved["id"], render["id"])
                    output.update(
                        audio="completed",
                        render=render,
                        wav=str(directory / "mix.wav"),
                        mp3=str(directory / "mix.mp3"),
                        midi=str(directory / "mix.mid"),
                    )
                outputs.append(output)
                print(
                    f"{variant}: {output['score_seconds']:.3f}s, {output['notes']} notes, audio={output['audio']}",
                    flush=True,
                )
                if variant == "whole_lyrics_full":
                    modified = generator.start(
                        saved["id"],
                        GenerationRequest(
                            base_revision=saved["current_revision"],
                            operation="modify",
                            instruction="整首更轻快，保留全部歌词与旋律参考",
                        ),
                    )
                    wait_for(generator, saved["id"], modified["id"])
                    current = generator.apply(
                        saved["id"], modified["id"], saved["current_revision"]
                    )
                    variants.append(("whole_full_modify", current))
            result.update(status="completed", outputs=outputs)
            artifacts.write_json(root / "summary.json", summary)
    except Exception as error:
        summary["error"] = str(error)
        if summary["cases"]:
            summary["cases"][-1]["status"] = "failed"
        artifacts.write_json(root / "summary.json", summary)
        raise
    finally:
        generator.close()
        renderer.close()
    artifacts.write_json(root / "summary.json", summary)


if __name__ == "__main__":
    main()
