"""Independent immutable practice materials and append-only, idempotent sessions."""

import hashlib
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .lyric_group_edit import draft_hash
from .practice_import import parse_practice
from .practice_notation import hash_json, prepare_notation
from .storage import StorageError, now


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{32}", value):
        raise StorageError("练习标识无效。", 404)
    return value


class PracticeStore:
    def __init__(self, root):
        # Listing/importing backend modules never creates a formal practice directory.
        self.root = Path(root).absolute()
        self.lock = RLock()
        self.epoch = uuid4().hex

    def safe(self, path):
        if (
            not path.resolve().is_relative_to(self.root)
            or path.is_symlink()
            or any(p.is_symlink() for p in path.parents)
        ):
            raise StorageError("练习路径无效或指向链接目录。", 400)
        return path

    def write(self, path, content):
        self.safe(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def json(self, path, value):
        self.write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode())

    def read(self, path):
        self.safe(path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StorageError("练习材料或记录不存在。", 404) from None
        except (ValueError, UnicodeError):
            raise StorageError("练习记录损坏，请保留文件并检查备份。", 409) from None

    def material_path(self, mid):
        return self.safe(self.root / "materials" / identifier(mid))

    def session_path(self, sid):
        return self.safe(self.root / "sessions" / identifier(sid))

    def materials(self):
        with self.lock:
            folder = self.safe(self.root / "materials")
            if not folder.exists():
                return []
            return sorted(
                [
                    self.read(p / "source.json")
                    for p in folder.iterdir()
                    if p.is_dir()
                    and re.fullmatch("[a-f0-9]{32}", p.name)
                    and (p / "source.json").exists()
                ],
                key=lambda x: x["created_at"],
                reverse=True,
            )

    def import_material(self, content, filename, association=None, builtin=None):
        parsed = parse_practice(content)
        with self.lock:
            mid = uuid4().hex
            path = self.material_path(mid)
            source = {
                "id": mid,
                "name": str(filename).replace("\\", "/").split("/")[-1][:120],
                "created_at": now(),
                "sha256": parsed["sha256"],
                "bytes": len(content),
                "association": association,
                "parsed_hash": hash_json(parsed),
                "format": "neonote-practice-source-v1",
            }
            if builtin is not None:
                source["builtin"] = deepcopy(builtin)
            self.write(path / "source.mid", content)
            self.json(path / "parsed.json", parsed)
            self.json(path / "source.json", source)  # Published last.
            return {"source": source, "parsed": parsed, "revisions": []}

    def unit_material(self, content, builtin):
        """Reuse only the same packaged artifact; never reclassify earlier materials."""
        with self.lock:
            for source in self.materials():
                if (source.get("builtin") or {}).get("artifact_hash") == builtin["artifact_hash"]:
                    return self.material(source["id"])
            return self.import_material(
                content, builtin["exercise_title"] + ".mid", builtin=builtin
            )

    def material(self, mid):
        with self.lock:
            path = self.material_path(mid)
            revisions = path / "revisions"
            source, parsed = self.read(path / "source.json"), self.read(path / "parsed.json")
            with self.safe(path / "source.mid").open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != source["sha256"] or hash_json(parsed) != source["parsed_hash"]:
                raise StorageError("原MIDI或练习解析材料哈希不符，拒绝改写目标。", 409)
            return {
                "source": source,
                "parsed": parsed,
                "revisions": [
                    self.read(p / "revision.json")
                    for p in revisions.iterdir()
                    if p.is_dir() and (p / "revision.json").exists()
                ]
                if revisions.exists()
                else [],
            }

    def preview(self, mid, settings):
        with self.lock:
            material = self.material(mid)
            result = prepare_notation(material["parsed"], settings)
            builtin = material["source"].get("builtin")
            if (
                builtin
                and result["ready"]
                and all(
                    actual == builtin[key]
                    for key, actual in {
                        "settings_hash": hash_json(settings.model_dump()),
                        "targets_hash": hash_json(result["targets"]),
                        "notation_hash": hash_json(result["notation"]),
                        "musicxml_hash": hashlib.sha256(result["musicxml"].encode()).hexdigest(),
                    }.items()
                )
            ):
                result["builtin"] = deepcopy(builtin)
            result["preview_hash"] = hash_json(
                {
                    "source": material["source"]["sha256"],
                    "settings": settings.model_dump(),
                    "result": result,
                }
            )
            return result

    def confirm(self, mid, settings, preview_hash):
        with self.lock:
            result = self.preview(mid, settings)
            if result["preview_hash"] != preview_hash:
                raise StorageError("整理预览或输入已变化，请重新查看五线谱再确认。", 409)
            if not result["ready"]:
                raise StorageError(result["reason"], 422)
            rid = uuid4().hex
            path = self.material_path(mid) / "revisions" / rid
            revision = {
                "id": rid,
                "material_id": mid,
                "created_at": now(),
                "settings": settings.model_dump(),
                "preview_hash": preview_hash,
                "targets_hash": hash_json(result["targets"]),
                "notation_hash": hash_json(result["notation"]),
                "report": result["report"],
                "settings_hash": hash_json(settings.model_dump()),
                "musicxml_hash": hashlib.sha256(result["musicxml"].encode()).hexdigest(),
            }
            builtin = self.material(mid)["source"].get("builtin")
            if builtin and all(
                revision[k] == builtin[k]
                for k in ("settings_hash", "targets_hash", "notation_hash", "musicxml_hash")
            ):
                revision["builtin"] = deepcopy(builtin)
            self.json(path / "settings.json", settings.model_dump())
            self.json(path / "notation.json", result["notation"])
            self.json(path / "targets.json", result["targets"])
            self.write(path / "score.musicxml", result["musicxml"].encode())
            self.json(path / "revision.json", revision)
            return self.revision(mid, rid)

    def revision(self, mid, rid):
        with self.lock:
            path = self.material_path(mid) / "revisions" / identifier(rid)
            metadata = self.read(path / "revision.json")
            notation, targets = self.read(path / "notation.json"), self.read(path / "targets.json")
            musicxml = self.safe(path / "score.musicxml").read_text(encoding="utf-8")
            if (
                metadata["targets_hash"] != hash_json(targets)
                or metadata["notation_hash"] != hash_json(notation)
                or metadata["settings_hash"] != hash_json(self.read(path / "settings.json"))
                or metadata["musicxml_hash"] != hashlib.sha256(musicxml.encode()).hexdigest()
            ):
                raise StorageError("练习整理版本哈希不符，拒绝改写目标。", 409)
            return {
                "revision": metadata,
                "notation": notation,
                "targets": targets,
                "musicxml": musicxml,
            }

    def create_session(self, mid, rid, speed, calibration, self_test):
        with self.lock:
            if len(json.dumps(self_test)) > 100000 or len(json.dumps(calibration)) > 2000:
                raise StorageError("设备自检或校准信息过大。", 422)
            offset = calibration.get("offset_ms", 0)
            if type(offset) not in (int, float) or not math.isfinite(offset) or abs(offset) > 500:
                raise StorageError("校准偏移应在±500ms内。", 422)
            revision = self.revision(mid, rid)
            sid = uuid4().hex
            rules = {
                "version": "neonote-practice-rules-1.0",
                "mapping": "tap-register-held-octaves-v2",
                "base_octave": 4,
                "base_octave_min": 3,
                "base_octave_max": 5,
                "chord_ms": 100,
                "match_ms": 250,
                "onset_quarter_ratio": 0.2,
                "onset_min_ms": 60,
                "onset_max_ms": 150,
                "release_min_ms": 120,
                "release_ratio": 0.2,
                "slur_gap_ms": 80,
                "initial_speed": speed,
                "calibration": calibration,
                "self_test": self_test,
                "suggested_pitch_goal": 0.9,
                "suggested_timing_goal": 0.8,
            }
            snapshot = {
                "id": sid,
                "material_id": mid,
                "revision_id": rid,
                "created_at": now(),
                "epoch": self.epoch,
                "rules": rules,
                "targets": revision["targets"],
                "targets_hash": revision["revision"]["targets_hash"],
                "rules_hash": hash_json(rules),
                "format": "neonote-practice-session-v1",
            }
            if revision["revision"].get("builtin"):
                snapshot["builtin"] = deepcopy(revision["revision"]["builtin"])
            self.json(self.session_path(sid) / "session.json", snapshot)
            return self.session(sid)

    def batches(self, sid):
        root = self.session_path(sid) / "events"
        paths = sorted(root.glob("*.json")) if root.exists() else []
        output = []
        for i, path in enumerate(paths, 1):
            if path.name != f"{i:06d}.json":
                raise StorageError("练习事件批次不连续，请保留缓存并修复记录。", 409)
            item = self.read(path)
            if item["seq"] != i or item["payload_hash"] != draft_hash(item["events"]):
                raise StorageError("练习事件批次校验失败。", 409)
            output.append(item)
        return output

    def append(self, sid, seq, events, payload_hash):
        with self.lock:
            snapshot = self.read(self.session_path(sid) / "session.json")
            if (
                hash_json(snapshot["targets"]) != snapshot["targets_hash"]
                or hash_json(snapshot["rules"]) != snapshot["rules_hash"]
            ):
                raise StorageError("练习目标或规则快照校验失败。", 409)
            if not events or len(events) > 512 or draft_hash(events) != payload_hash:
                raise StorageError("练习批次为空、过大或哈希不符。", 422)
            existing = self.batches(sid)
            if seq <= len(existing):
                if existing[seq - 1]["payload_hash"] != payload_hash:
                    raise StorageError(
                        "同一练习序号已有不同内容；请保留本地缓冲，不覆盖旧作答。", 409
                    )
                return self.session(sid)
            if seq != len(existing) + 1:
                raise StorageError("练习批次序号不连续，请先补存前面的批次。", 409)
            total = sum(len(b["events"]) for b in existing)
            if total + len(events) > 100000:
                raise StorageError("本次会话事件已达上限，请保存并新建练习。", 422)
            previous_at = (
                existing[-1]["events"][-1].get("recorded_at", existing[-1]["events"][-1]["at"])
                if existing
                else 0
            )
            allowed = {
                "raw_down",
                "raw_up",
                "note_down",
                "note_up",
                "register",
                "recognition_start",
                "recognition_commit",
                "recognition_resume",
                "target_ready",
                "pause",
                "segment_start",
                "segment_end",
                "checkpoint",
                "help",
                "self_test",
            }
            for event in events:
                at = event.get("at")
                recorded = event.get("recorded_at", at)
                if (
                    event.get("kind") not in allowed
                    or not isinstance(at, (int, float))
                    or not math.isfinite(at)
                    or not isinstance(recorded, (int, float))
                    or not math.isfinite(recorded)
                    or not 0 <= at <= 86400000
                    or not previous_at <= recorded <= 86400000
                ):
                    raise StorageError("练习事件类型/时间顺序无效。", 422)
                previous_at = recorded
                if event["kind"] in ("note_down", "note_up") and (
                    type(event.get("pitch")) is not int
                    or not 0 <= event["pitch"] <= 127
                    or not isinstance(event.get("instance"), str)
                    or len(event["instance"]) > 64
                ):
                    raise StorageError("练习起音/松键信息无效。", 422)
                if event["kind"] in ("raw_down", "raw_up") and event.get("code") not in (
                    "KeyS",
                    "KeyD",
                    "KeyF",
                    "KeyG",
                    "KeyH",
                    "KeyJ",
                    "KeyK",
                    "KeyL",
                    "KeyE",
                    "KeyR",
                    "KeyY",
                    "KeyU",
                    "KeyI",
                    "ShiftLeft",
                    "ShiftRight",
                    "Space",
                ):
                    raise StorageError("只保存练习键，不记录输入框正文。", 422)
                if event["kind"] == "segment_start" and (
                    event.get("speed") not in (0.5, 0.75, 1)
                    or not isinstance(event.get("expected_start"), (int, float))
                    or not math.isfinite(event["expected_start"])
                    or type(event.get("from_tick")) is not int
                    or not snapshot["targets"]["start_tick"]
                    <= event.get("from_tick", -1)
                    < snapshot["targets"]["end_tick"]
                ):
                    raise StorageError("节奏时间段配置无效。", 422)
                if event["kind"] == "segment_start" and any(
                    a["tick"] < event["from_tick"] < n["end_tick"]
                    for a in snapshot["targets"]["attacks"]
                    for n in a["notes"]
                ):
                    raise StorageError(
                        "恢复起点会切断持续音或延音线，请从整条延音的起音重新预备。", 422
                    )
            from .practice_judge import replay

            replay(
                snapshot, [e for batch in existing for e in batch["events"]] + events
            )  # Validate before publishing a batch.
            self.json(
                self.session_path(sid) / "events" / f"{seq:06d}.json",
                {"seq": seq, "payload_hash": payload_hash, "events": events},
            )
            result = self.session(sid)
            self.json(
                self.session_path(sid) / "attempts.json",
                result["result"]["recognition"]["attempts"],
            )
            self.json(self.session_path(sid) / "summary.json", result["result"])
            return result

    def session(self, sid):
        from .practice_judge import replay

        with self.lock:
            snapshot = self.read(self.session_path(sid) / "session.json")
            if (
                hash_json(snapshot["targets"]) != snapshot["targets_hash"]
                or hash_json(snapshot["rules"]) != snapshot["rules_hash"]
            ):
                raise StorageError("练习目标或规则快照校验失败。", 409)
            batches = self.batches(sid)
            events = [e for batch in batches for e in batch["events"]]
            result = replay(snapshot, events)
            status = result["status"]
            if status in ("recognition", "rhythm") and snapshot["epoch"] != self.epoch:
                status = "interrupted"
            return {
                "snapshot": snapshot,
                "events": events,
                "last_seq": len(batches),
                "status": status,
                "result": result,
            }

    def sessions(self):
        root = self.safe(self.root / "sessions")
        with self.lock:
            if not root.exists():
                return []
            results = [
                self.session(p.name)
                for p in root.iterdir()
                if re.fullmatch("[a-f0-9]{32}", p.name) and (p / "session.json").exists()
            ]
            return sorted(
                [
                    {
                        "id": s["snapshot"]["id"],
                        "material_id": s["snapshot"]["material_id"],
                        "revision_id": s["snapshot"]["revision_id"],
                        "created_at": s["snapshot"]["created_at"],
                        "status": s["status"],
                        "result": s["result"],
                        "builtin": s["snapshot"].get("builtin"),
                    }
                    for s in results
                ],
                key=lambda s: s["created_at"],
                reverse=True,
            )
