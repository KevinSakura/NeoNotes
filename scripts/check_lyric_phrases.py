"""Offline evidence preparation. Never loads .env, calls an API, or renders singing."""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from backend.app.composition import compile_plan, mock_plan  # noqa: E402
from backend.app.lyric_composition import lyric_context  # noqa: E402
from backend.app.lyric_phrases import (  # noqa: E402
    compile_phrases,
    identity,
    mock_complete,
    phrase_context,
)
from backend.app.main import create_app  # noqa: E402
from backend.app.midi import export_midi, import_midi  # noqa: E402
from backend.app.model_settings import ModelSettings  # noqa: E402
from backend.app.models import empty_song, new_id  # noqa: E402
from backend.app.vocal_score import VocalSettings, convert_score  # noqa: E402

MATERIALS = {
    "short": "晨风轻轻吹过山岗",
    "long_sentence": "当晚风穿过安静的街巷我仍记得你微笑的模样",
    "ending": "让歌声陪我到远方",
}


def long_boundary():
    cursor, rows = 0, []
    for i in range(64):
        length = (20, 12, 18, 14)[i % 4]
        rows.append("".join(chr(0x4E00 + cursor + j) for j in range(length)))
        cursor += length
    return "\n".join(rows)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_worker(request_path, *, rejected=False):
    process = subprocess.run([
        str(ROOT / "tools/vocals/dotnet/dotnet.exe"),
        str(ROOT / "tools/vocals/worker/NeoNote.VocalWorker.dll"), "--validate-request",
        str(request_path),
    ], capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if rejected:
        assert process.returncode != 0, "Invalid protocol must be rejected before inference"
        report = {"rejected": True, "exit_code": process.returncode,
                  "validation_only": True, "inference_count": 0, "detail": process.stderr}
    else:
        if process.returncode:
            raise RuntimeError("Pure protocol validation failed: " + process.stderr[-1000:])
        report = json.loads(process.stdout)
        assert report["validation_only"] and report["inference_count"] == 0
    write(request_path.with_name("protocol-report.json"), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-validation", action="store_true",
                        help="Only --validate-request, never --request or singing inference.")
    args = parser.parse_args()
    output = ROOT / ".cache/lyric-phrases-evaluation" / uuid4().hex
    output.mkdir(parents=True)
    result = {"run_id": output.name, "paid_api_requests": 0, "real_singing_inferences": 0,
              "env_loaded": False, "materials": [], "music_quality": "unassessed",
              "worker_protocol_checks": {"enabled": args.worker_validation, "accepted": 0, "rejected": 0}}
    for name, text in MATERIALS.items():
        source = empty_song()
        source.inputs.lyrics_text = text
        source.inputs.selected_lines = [0]
        for contract in ("v1", "v2"):
            s, ctx = lyric_context(source) if contract == "v1" else phrase_context(source)
            raw = mock_plan(s, ctx) if contract == "v1" else mock_complete(ctx)
            state, plan = (compile_plan(raw, s, new_id(), True, context=ctx) if contract == "v1"
                           else compile_phrases(raw, s, new_id(), True, ctx))
            directory = output / (name + "-" + contract)
            write(directory / "input.json", ctx)
            write(directory / "plan.json", plan.model_dump())
            write(directory / "state.json", state.model_dump())
            midi = export_midi(state.score)
            (directory / "score.mid").write_bytes(midi)
            imported = import_midi(midi)
            assert sum(len(t.notes) for t in imported.tracks) == sum(len(t.notes) for t in state.score.tracks)
            request, _, removed = convert_score(state, VocalSettings())
            write(directory / "vocal-request.json", request)
            if args.worker_validation:
                validate_worker(directory / "vocal-request.json")
                result["worker_protocol_checks"]["accepted"] += 1
            result["materials"].append({"name": name, "contract": contract, "mock": True,
                                       "units": len(identity(text).units), "seconds": state.composition.target_seconds,
                                       "guide_notes": len(removed), "midi_sha256": hashlib.sha256(midi).hexdigest()})
    # Equal-duration hand fixtures are the future vowel-continuation comparison.
    s, ctx = phrase_context(source)
    for name, notes in [("ending-single-long", [[60, 4]]), ("ending-three-notes", [[64, 1], [62, 1], [60, 2]])]:
        raw = mock_complete(ctx)
        raw["phrases"][-1]["groups"][-1]["notes"] = notes
        state, _ = compile_phrases(raw, s, new_id(), True, ctx)
        request, _, _ = convert_score(state, VocalSettings())
        write(output / name / "state.json", state.model_dump())
        write(output / name / "vocal-request.json", request)
        if args.worker_validation:
            validate_worker(output / name / "vocal-request.json")
            result["worker_protocol_checks"]["accepted"] += 1
    if args.worker_validation:
        for failure in ("repeated-uid", "fragmented-word", "unsupported-policy"):
            malformed = json.loads(json.dumps(request))
            units = malformed["phrases"][-1]["units"]
            if failure == "repeated-uid":
                units[-1]["uid"] = units[-2]["uid"]
            elif failure == "fragmented-word":
                units[-1]["notes"][0]["duration"] -= 1
            else:
                malformed["singing_policy"]["vibrato"] = 1
            path = output / ("rejected-" + failure) / "vocal-request.json"
            write(path, malformed)
            validate_worker(path, rejected=True)
            result["worker_protocol_checks"]["rejected"] += 1
    with TestClient(create_app(output / "data", model_settings=ModelSettings(mock=True))) as client:
        client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
        p = client.post("/api/projects", json={"name": "隔离1024字分块"}).json()
        text = long_boundary()
        s = empty_song()
        s.inputs.lyrics_text, s.inputs.selected_lines = text, list(range(64))
        p = client.put("/api/projects/" + p["id"], json={"base_revision": p["current_revision"],
                                                        "state": s.model_dump()}).json()
        response = client.post("/api/projects/" + p["id"] + "/generations",
                               json={"base_revision": p["current_revision"], "contract": "lyric-phrases-v2"})
        assert response.status_code == 202, response.text
        job = response.json()
        for _ in range(2000):
            job = client.get("/api/projects/" + p["id"] + "/generations/" + job["id"]).json()
            if job["status"] not in ("queued", "running", "validating"):
                break
            time.sleep(.01)
        assert job["status"] == "completed" and job["request_count"] == 0, job
        candidate = client.get("/api/projects/" + p["id"] + "/generations/" + job["id"] + "/candidate").json()
        assert len(candidate["composition"]["lyrics_alignment"]) == 1024
        result["long_mock_task"] = {"project_id": p["id"], "job_id": job["id"], "blocks": len(job["blocks"]),
                                   "simulated_requests": job["simulated_requests"], "reserved_attempts": job["reserved_attempts"],
                                   "seconds": job["result_seconds"], "request_count": 0}
    result["future_validation_proposal"] = {
        "status": "awaiting_explicit_authorization", "model_requests": 6,
        "proposed_evaluation_output_cap": 8192, "platform_output_cap": 32768,
        "materials": MATERIALS, "real_singing_inference_limit": 8,
        "note": "Six v1/v2 small-sample calls and up to eight successful dependent singing samples; no retries or substituted cases. An explicitly capped isolated evaluator is required for 8192; normal platform requests remain 32768.",
    }
    write(output / "summary.json", result)
    print(json.dumps({"output": str(output), "mock_samples": 8, "paid_api_requests": 0,
                      "real_singing_inferences": 0, "worker_protocol_checks": result["worker_protocol_checks"],
                      "long_task": result["long_mock_task"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
