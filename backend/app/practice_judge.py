"""Replayable rules. All times are saved performance-domain milliseconds, not arrival time."""

import math
from collections import defaultdict

from .practice_import import seconds_at
from .storage import StorageError


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = math.floor(index)
    return ordered[low] + (ordered[math.ceil(index)] - ordered[low]) * (index - low)


def recognition(snapshot, events):
    targets, rules = snapshot["targets"]["attacks"], snapshot["rules"]
    index, ready_at, paused_ms, mode = 0, 0, 0, "ready"
    held, attempts, current, wait_release, pause_at = {}, [], None, False, None
    pending, ready_marked = False, False

    def finish(at, interrupted=False):
        nonlocal current, index, ready_at, paused_ms, wait_release, ready_marked
        if current is None:
            return
        expected = set(n["pitch"] for n in targets[index]["notes"])
        actual = set(current["pitches"])
        correct = actual == expected and current["simultaneous"] and not interrupted
        record = {
            **current,
            "actual": sorted(actual),
            "expected": sorted(expected),
            "missing": sorted(expected - actual),
            "extra": sorted(actual - expected),
            "correct": correct,
            "interrupted": interrupted,
            "finished_at": at,
            "final_correction_ms": max(
                0, (current["simultaneous_at"] if correct else at) - ready_at - paused_ms
            ),
        }
        record.pop("pitches")
        attempts.append(record)
        current = None
        wait_release = not correct and bool(held)
        if correct:
            index += 1
            ready_at, paused_ms = at, 0
            ready_marked = False

    for event in events:
        kind, at = event["kind"], event["at"]
        if kind == "recognition_start":
            if attempts or index:
                raise StorageError("认音轮已开始；重新练习须创建新会话，不能清空首次答案。", 409)
            mode, ready_at = "recognition", at
        elif kind == "target_ready" and event.get("target_index") == index and not ready_marked:
            ready_at, ready_marked = at, True
        elif kind == "recognition_resume":
            if pause_at is not None:
                paused_ms += at - pause_at
            pause_at, mode = None, "recognition"
            held.clear()
            wait_release = False
        elif kind == "pause":
            if mode == "recognition":
                finish(at, True)
                pause_at = at
            mode = "paused"
            held.clear()
            wait_release = False
        elif kind == "segment_start":
            if index < len(targets):
                raise StorageError("全部认音目标最终正确后才能启动节奏轮。", 409)
            mode = "rhythm"
        elif kind == "note_up":
            held.pop(event["instance"], None)
            if not held:
                wait_release = False
        elif kind == "note_down":
            held[event["instance"]] = event["pitch"]
            if mode != "recognition" or index >= len(targets) or wait_release:
                continue
            if current is not None and at > current["started_at"] + rules["chord_ms"]:
                finish(current["started_at"] + rules["chord_ms"])
                if wait_release or index >= len(targets):
                    continue
            if current is None:
                current = {
                    "target_index": index,
                    "target_id": targets[index]["id"],
                    "started_at": at,
                    "first_response_ms": max(0, at - ready_at - paused_ms),
                    "pitches": [],
                    "simultaneous": False,
                    "simultaneous_at": None,
                }
            current["pitches"].append(event["pitch"])
            expected = set(n["pitch"] for n in targets[index]["notes"])
            if expected <= set(held.values()):
                current["simultaneous"] = True
                if current["simultaneous_at"] is None:
                    current["simultaneous_at"] = at
        elif kind == "recognition_commit" and mode == "recognition" and current is not None:
            if at + 1e-7 >= current["started_at"] + rules["chord_ms"]:
                finish(at)
        pending = current is not None
    grouped = [[a for a in attempts if a["target_index"] == i] for i in range(len(targets))]
    firsts = [a[0] for a in grouped if a]

    def pitch_accuracy(rows):
        tp = sum(len(set(a["actual"]) & set(a["expected"])) for a in rows)
        denominator = tp + sum(len(a["missing"]) + len(a["extra"]) for a in rows)
        return tp / denominator if denominator else None

    return {
        "complete": index == len(targets),
        "next_index": index,
        "pending": pending,
        "attempts": attempts,
        "target_count": len(targets),
        "answered": len(firsts),
        "first_correct": sum(a["correct"] for a in firsts),
        "first_accuracy": sum(a["correct"] for a in firsts) / len(firsts) if firsts else None,
        "first_pitch_accuracy": pitch_accuracy(firsts),
        "cumulative_pitch_accuracy": pitch_accuracy(attempts),
        "first_response_median_ms": percentile([a["first_response_ms"] for a in firsts], 0.5),
        "first_response_p90_ms": percentile([a["first_response_ms"] for a in firsts], 0.9),
        "final_correction_ms": [
            next((a["final_correction_ms"] for a in row if a["correct"]), None) for row in grouped
        ],
    }


def rhythm(snapshot, events):
    targets, rules = snapshot["targets"], snapshot["rules"]
    segments, current = [], None
    for e in events:
        if e["kind"] == "segment_start":
            if current is not None:
                raise StorageError("旧节奏段尚未暂停或结束。", 409)
            current = {"start": e, "events": [], "end": None}
            segments.append(current)
        elif current is not None:
            current["events"].append(e)
            if e["kind"] in ("pause", "segment_end"):
                current["end"] = e
                current = None
    output = []
    for index, segment in enumerate(segments):
        start, end = segment["start"], segment["end"]
        speed, from_tick = start["speed"], start["from_tick"]
        origin = start["expected_start"]
        offset = rules.get("calibration", {}).get("offset_ms", 0)
        if not isinstance(offset, (int, float)) or not math.isfinite(offset) or abs(offset) > 500:
            raise StorageError("校准偏移无效。", 422)
        complete = bool(end and end["kind"] == "segment_end" and end.get("completed") is True)
        last_at = (end or (segment["events"][-1] if segment["events"] else start))["at"]
        if end and not complete and end.get("last_valid_at") is not None:
            last_at = min(last_at, end["last_valid_at"])
        base_seconds = seconds_at(targets["tempos"], from_tick)
        expected = []
        for attack in targets["attacks"]:
            if attack["tick"] < from_tick:
                continue
            when = (
                origin
                + (seconds_at(targets["tempos"], attack["tick"]) - base_seconds) * 1000 / speed
            )
            local = (
                next(t["tempo"] for t in reversed(targets["tempos"]) if t["tick"] <= attack["tick"])
                / 1000
                / speed
            )
            tolerance = max(
                rules["onset_min_ms"],
                min(rules["onset_max_ms"], local * rules["onset_quarter_ratio"]),
            )
            for note in attack["notes"]:
                expected.append(
                    {
                        "target_id": note["id"],
                        "attack_id": attack["id"],
                        "pitch": note["pitch"],
                        "when": when,
                        "tolerance": tolerance,
                        "release": origin
                        + (seconds_at(targets["tempos"], note["end_tick"]) - base_seconds)
                        * 1000
                        / speed,
                        "tie_chain_id": note["tie_chain_id"],
                    }
                )
        by_pitch = defaultdict(list)
        for target in expected:
            by_pitch[target["pitch"]].append(target)
        expected_end = (
            origin
            + (seconds_at(targets["tempos"], targets["end_tick"]) - base_seconds) * 1000 / speed
        )
        if complete and end["at"] + 0.001 < expected_end:
            raise StorageError("节奏时间轴尚未结束，不能标记完成。", 409)
        inputs = [e for e in segment["events"] if e["kind"] == "note_down" and e["at"] <= last_at]
        releases = {
            e["instance"]: e["at"] - offset
            for e in segment["events"]
            if e["kind"] == "note_up" and e.get("reason", "keyup") == "keyup" and e["at"] <= last_at
        }
        used, matched, missing = set(), [], []
        for pitch, row in by_pitch.items():
            for i, target in enumerate(row):
                low = max(
                    target["when"] - rules["match_ms"],
                    (row[i - 1]["when"] + target["when"]) / 2 if i else -math.inf,
                )
                high = min(
                    target["when"] + rules["match_ms"],
                    (row[i + 1]["when"] + target["when"]) / 2 if i + 1 < len(row) else math.inf,
                )
                candidates = [
                    (abs(e["at"] - offset - target["when"]), j, e)
                    for j, e in enumerate(inputs)
                    if j not in used and e["pitch"] == pitch and low <= e["at"] - offset < high
                ]
                if candidates:
                    _, j, event = min(candidates, key=lambda item: (item[0], item[1]))
                    used.add(j)
                    delta = event["at"] - offset - target["when"]
                    limit = min(target["tolerance"], target["when"] - low, high - target["when"])
                    release = releases.get(event["instance"])
                    duration = target["release"] - target["when"]
                    release_delta = release - target["release"] if release is not None else None
                    matched.append(
                        {
                            **target,
                            "input_index": j,
                            "actual": event["at"] - offset,
                            "delta_ms": delta,
                            "timing": "on_time"
                            if abs(delta) <= limit
                            else "early"
                            if delta < 0
                            else "late",
                            "release_delta_ms": release_delta,
                            "release_feedback": "held_or_interrupted"
                            if release_delta is None
                            else "early"
                            if release_delta
                            < -max(rules["release_min_ms"], duration * rules["release_ratio"])
                            else "late"
                            if release_delta
                            > max(rules["release_min_ms"], duration * rules["release_ratio"])
                            else "within_tolerance",
                            "release_warning": release_delta is not None
                            and abs(release_delta)
                            > max(rules["release_min_ms"], duration * rules["release_ratio"]),
                        }
                    )
                elif complete or last_at > high + 20:
                    missing.append(target)
        extras = []
        for j, event in enumerate(inputs):
            if j in used:
                continue
            actual = event["at"] - offset
            silent = any(
                origin
                + (seconds_at(targets["tempos"], s["start_tick"]) - base_seconds) * 1000 / speed
                <= actual
                < origin
                + (seconds_at(targets["tempos"], s["end_tick"]) - base_seconds) * 1000 / speed
                for s in targets["silences"]
            )
            nearest = min(expected, key=lambda t: abs(t["when"] - actual)) if expected else None
            extras.append(
                {
                    "pitch": event["pitch"],
                    "at": actual,
                    "kind": "rest_error" if silent else "wrong_or_extra",
                    "nearest_target": nearest["target_id"]
                    if nearest and abs(nearest["when"] - actual) <= rules["match_ms"]
                    else None,
                }
            )
        count = len(expected) if complete else len(matched) + len(missing)
        denominator = len(matched) + len(extras) + len(missing)
        onset_hits = sum(m["timing"] == "on_time" for m in matched)
        slurs = []
        by_id = {m["target_id"]: m for m in matched}
        chords = []
        for attack in targets["attacks"]:
            if attack["tick"] < from_tick or len(attack["notes"]) < 2:
                continue
            members = [by_id[n["id"]] for n in attack["notes"] if n["id"] in by_id]
            spread = (
                max(m["actual"] for m in members) - min(m["actual"] for m in members)
                if len(members) > 1
                else None
            )
            chords.append(
                {
                    "attack_id": attack["id"],
                    "matched": len(members),
                    "expected": len(attack["notes"]),
                    "spread_ms": spread,
                    "complete": len(members) == len(attack["notes"]),
                    "together": spread is not None and spread <= rules["chord_ms"],
                }
            )
        for a, b in targets["slurs"]:
            first, second = by_id.get(a), by_id.get(b)
            if first and second and first["release_delta_ms"] is not None:
                gap = second["actual"] - (first["release"] + first["release_delta_ms"])
                slurs.append(
                    {"start": a, "end": b, "gap_ms": gap, "warning": gap > rules["slur_gap_ms"]}
                )
        pitch_accuracy = len(matched) / denominator if denominator else None
        on_time_rate = onset_hits / count if count else None
        output.append(
            {
                "index": index,
                "from_tick": from_tick,
                "speed": speed,
                "complete": complete,
                "interrupted": bool(end and not complete),
                "interruption": end.get("reason") if end else None,
                "matched": matched,
                "missed": missing,
                "extras": extras,
                "slur_feedback": slurs,
                "chord_feedback": chords,
                "pitch_accuracy": pitch_accuracy,
                "on_time_rate": on_time_rate,
                "onset_median_abs_ms": percentile([abs(m["delta_ms"]) for m in matched], 0.5),
                "onset_p90_abs_ms": percentile([abs(m["delta_ms"]) for m in matched], 0.9),
                "rest_errors": sum(e["kind"] == "rest_error" for e in extras),
                "suggested_goal_met": complete
                and pitch_accuracy is not None
                and pitch_accuracy >= rules["suggested_pitch_goal"]
                and on_time_rate >= rules["suggested_timing_goal"]
                and not any(e["kind"] == "rest_error" for e in extras),
            }
        )
    return output


def replay(snapshot, events):
    if snapshot["rules"]["version"] != "neonote-practice-rules-1.0":
        raise StorageError("未知评分版本，保留原事件并等待对应规则。", 409)
    reading = recognition(snapshot, events)
    timing = rhythm(snapshot, events)
    controls = [
        e
        for e in events
        if e["kind"]
        in ("recognition_start", "recognition_resume", "pause", "segment_start", "segment_end")
    ]
    last = controls[-1]["kind"] if controls else None
    status = (
        "completed"
        if timing and timing[-1]["complete"]
        else "paused"
        if last == "pause"
        else "rhythm"
        if last == "segment_start"
        else "recognition_complete"
        if reading["complete"]
        else "recognition"
        if last in ("recognition_start", "recognition_resume")
        else "ready"
    )
    return {
        "status": status,
        "recognition": reading,
        "rhythm": timing,
        "pauses": [e for e in events if e["kind"] == "pause"],
        "event_count": len(events),
    }
