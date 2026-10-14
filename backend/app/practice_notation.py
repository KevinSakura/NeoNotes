"""Own notation/attack identities; MusicXML is only the rendering exchange format."""

import hashlib
import json
import math
from collections import defaultdict
from copy import deepcopy
from xml.etree.ElementTree import Element, SubElement, tostring

from pydantic import Field

from .models import Model
from .practice_import import seconds_at
from .storage import StorageError

PPQ = 480
SHARP = [
    ("C", 0),
    ("C", 1),
    ("D", 0),
    ("D", 1),
    ("E", 0),
    ("F", 0),
    ("F", 1),
    ("G", 0),
    ("G", 1),
    ("A", 0),
    ("A", 1),
    ("B", 0),
]
FLAT = [
    ("C", 0),
    ("D", -1),
    ("D", 0),
    ("E", -1),
    ("E", 0),
    ("F", 0),
    ("G", -1),
    ("G", 0),
    ("A", -1),
    ("A", 0),
    ("B", -1),
    ("B", 0),
]
FIFTHS = {
    "Cb": -7,
    "Gb": -6,
    "Db": -5,
    "Ab": -4,
    "Eb": -3,
    "Bb": -2,
    "F": -1,
    "C": 0,
    "G": 1,
    "D": 2,
    "A": 3,
    "E": 4,
    "B": 5,
    "F#": 6,
    "C#": 7,
    "Abm": -7,
    "Ebm": -6,
    "Bbm": -5,
    "Fm": -4,
    "Cm": -3,
    "Gm": -2,
    "Dm": -1,
    "Am": 0,
    "Em": 1,
    "Bm": 2,
    "F#m": 3,
    "C#m": 4,
    "G#m": 5,
    "D#m": 6,
    "A#m": 7,
}
VALUES = [
    (1920, "whole", 0, False),
    (1440, "half", 1, False),
    (960, "half", 0, False),
    (720, "quarter", 1, False),
    (640, "half", 0, True),
    (480, "quarter", 0, False),
    (360, "eighth", 1, False),
    (320, "quarter", 0, True),
    (240, "eighth", 0, False),
    (180, "16th", 1, False),
    (160, "eighth", 0, True),
    (120, "16th", 0, False),
    (90, "32nd", 1, False),
    (80, "16th", 0, True),
    (60, "32nd", 0, False),
    (40, "32nd", 0, True),
]


class NotationSettings(Model):
    track_id: str = Field(max_length=48)
    start_measure: int = Field(default=1, ge=1, le=1500)
    end_measure: int = Field(default=4, ge=1, le=1500)
    grid: int = Field(default=120, ge=0, le=240)
    clef: str = Field(default="treble", pattern="^(treble|bass|grand)$")
    transpose: int = Field(default=0, ge=-24, le=24)
    pickup_ticks: int = Field(default=0, ge=0, le=3840)
    key_fifths: int | None = Field(default=None, ge=-7, le=7)
    voices: list[int] = Field(default_factory=list, max_length=16)
    acknowledge_controls: bool = False
    acknowledge_large_quantization: bool = False
    expand_boundaries: bool = False
    slurs: list[tuple[str, str]] = Field(default_factory=list, max_length=16)


def fail(message):
    raise StorageError(message, 422)


def measures_for(parsed, pickup=0):
    meters = parsed["meters"]
    measures, tick, mi = [], 0, 0
    while tick < parsed["end_tick"]:
        while mi + 1 < len(meters) and meters[mi + 1]["tick"] <= tick:
            mi += 1
        meter = meters[mi]
        n, d = meter["numerator"], meter["denominator"]
        if (n, d) not in ((2, 4), (3, 4), (4, 4), (6, 8)):
            fail(f"tick{tick}的{n}/{d}需要另定节拍分组；首版支持2/4、3/4、4/4、6/8。")
        length = 1920 * n // d
        if not measures and pickup:
            if pickup >= length:
                fail("弱起须短于第一完整小节。")
            length = pickup
        end = tick + length
        if mi + 1 < len(meters) and tick < meters[mi + 1]["tick"] < end:
            fail(
                f"tick{meters[mi + 1]['tick']}在小节中途变拍；请先重新划分小节或导出边界一致的片段。"
            )
        measures.append(
            {
                "number": len(measures) + 1,
                "start_tick": tick,
                "end_tick": end,
                "numerator": n,
                "denominator": d,
                "pickup": bool(not measures and pickup),
            }
        )
        tick = end
        if len(measures) > 1500:
            fail("小节数量超过首版限制。")
    return measures


def assign_voices(notes):
    groups = defaultdict(list)
    for n in notes:
        groups[(n["start_tick"], n["end_tick"])].append(n)
    available = []
    for (start, end), chord in sorted(groups.items()):
        # Unisons are distinct attacks, not silently collapsed into one chord.
        split = []
        for n in sorted(chord, key=lambda n: (n["pitch"], n["id"])):
            part = next((p for p in split if n["pitch"] not in [q["pitch"] for q in p]), None)
            if part is None:
                part = []
                split.append(part)
            part.append(n)
        for part in split:
            pitch = sum(n["pitch"] for n in part) / len(part)
            candidates = [i for i, v in enumerate(available) if v["end"] <= start]
            index = (
                min(candidates, key=lambda i: (abs(available[i]["pitch"] - pitch), i))
                if candidates
                else len(available)
            )
            if index == len(available):
                available.append({"end": end, "pitch": pitch})
            else:
                available[index] = {"end": end, "pitch": pitch}
            for n in part:
                n["voice"] = index + 1
    return [
        {
            "voice": i + 1,
            "note_count": sum(n["voice"] == i + 1 for n in notes),
            "mean_pitch": sum(n["pitch"] for n in notes if n["voice"] == i + 1)
            / sum(n["voice"] == i + 1 for n in notes),
        }
        for i in range(len(available))
    ]


def parts(duration):
    # Find an exact representable decomposition; never round or drop the remainder.
    cache = {0: []}

    def solve(value):
        if value in cache:
            return cache[value]
        for item in VALUES:
            if item[0] <= value:
                tail = solve(value - item[0])
                if tail is not None:
                    cache[value] = [item] + tail
                    return cache[value]
        cache[value] = None
        return None

    result = solve(duration)
    if result is None:
        fail(f"{duration}tick无法用首版二分/附点/三连音表示；请选合适量化网格。")
    return result


def reachability(pitches):
    target = set(pitches)
    if any(p < 36 or p > 96 for p in target):
        return {
            "level": "blocked",
            "reason": "超出当前跨八度组合发声边界C2–C7；请选片段/声部或明确移调。",
        }
    if any(p < 48 or p > 84 for p in target):
        for p in target:
            if p < 48 and p + 12 not in target or p > 84 and p - 12 not in target:
                return {
                    "level": "blocked",
                    "reason": f"音高{p}只能与邻八度一起生成，当前目标缺少对应双音；请选声部或移调。",
                }
    bases = [{(o + 1) * 12 + pc for pc in range(12)} | {(o + 2) * 12} for o in (3, 4, 5)]
    simple = False
    for base in bases:
        for direction in (0, -12, 12):
            options = [{p} if direction == 0 else {p, p + direction} for p in base]
            union = set().union(*(output for output in options if output <= target))
            if union == target:
                simple = True
    return {
        "level": "simple" if simple else "blocked",
        "reason": ""
        if simple
        else "这个音高集合不能由单档普通键或一种跨八度双音直接同时完成；首版不放行依赖复杂修饰历史的组合，请选较简单的声部/片段或明确移调。",
    }


def prepare_notation(parsed, settings):
    if settings.grid not in (0, 80, 120, 160, 240) or settings.transpose not in (
        -24,
        -12,
        0,
        12,
        24,
    ):
        fail("请选择不量化、八分/十六分/三连音网格，以及整段八度移调。")
    track = next((t for t in parsed["tracks"] if t["id"] == settings.track_id), None)
    if not track or track["channel"] == 9:
        fail("请选择一个非鼓轨道；原轨名不代表它一定是键盘。")
    all_measures = measures_for(parsed, settings.pickup_ticks)
    if (
        settings.end_measure < settings.start_measure
        or settings.end_measure - settings.start_measure >= 16
    ):
        fail("首版每次练习选择1–16小节。")
    if settings.start_measure > len(all_measures):
        fail("起始小节超出原材料。")
    start = all_measures[settings.start_measure - 1]["start_tick"]
    end = all_measures[min(settings.end_measure, len(all_measures)) - 1]["end_tick"]
    crossing = [
        n
        for n in track["notes"]
        if n["start_tick"] < start < n["end_tick"] or n["start_tick"] < end < n["end_tick"]
    ]
    if crossing and not settings.expand_boundaries:
        fail(
            "片段边界切断持续音："
            + ", ".join(n["id"] for n in crossing[:3])
            + "；请勾选扩展到完整延音或重新选段。"
        )
    if settings.expand_boundaries:
        while True:
            crossing = [
                n
                for n in track["notes"]
                if n["start_tick"] < start < n["end_tick"] or n["start_tick"] < end < n["end_tick"]
            ]
            if not crossing:
                break
            low, high = (
                min(start, *(n["start_tick"] for n in crossing)),
                max(end, *(n["end_tick"] for n in crossing)),
            )
            start = next(
                m["start_tick"] for m in all_measures if m["start_tick"] <= low < m["end_tick"]
            )
            end = next(
                m["end_tick"] for m in all_measures if m["start_tick"] < high <= m["end_tick"]
            )
    selected_measures = [m for m in all_measures if start <= m["start_tick"] < end]
    if len(selected_measures) > 16:
        fail("完整持续链超过16小节；请选更短材料。")
    controls = [e for e in track["controls"] if start <= e["tick"] < end]
    prior_bends = [e for e in track["controls"] if e["type"] == "pitchwheel" and e["tick"] < start]
    if (
        any(e["type"] == "pitchwheel" for e in controls)
        or prior_bends
        and prior_bends[-1]["pitch"] != 0
    ):
        fail("所选片段含弯音或继承了未复位的弯音；首版不把连续音高伪装成整数琴键，请另选片段。")
    if parsed["system_controls"]:
        fail("原文件含SysEx/未知系统信息，首版不能确定音高含义；请先另存标准GM片段。")
    # Earlier controller values (notably pedal) can remain active into the fragment.
    inherited = [e for e in track["controls"] if e["tick"] < start]
    if (controls or inherited) and not settings.acknowledge_controls:
        fail("存在踏板/表情/音色控制；需明确确认仅练琴键起止、试听统一钢琴且不复现这些控制。")
    notes = deepcopy([n for n in track["notes"] if start <= n["start_tick"] < end])
    if not notes:
        fail("所选片段没有起音目标。")
    shifts, maximum = [], 0.0
    for n in notes:
        n["source_start_tick"], n["source_end_tick"] = n["start_tick"], n["end_tick"]
        n["pitch"] += settings.transpose
        if not 0 <= n["pitch"] <= 127:
            fail("移调后超出MIDI音高范围。")
        if settings.grid:
            grid = settings.grid
            n["start_tick"] = ((n["start_tick"] + grid // 2) // grid) * grid
            n["end_tick"] = ((n["end_tick"] + grid // 2) // grid) * grid
            if n["end_tick"] <= n["start_tick"] or n["start_tick"] < start or n["end_tick"] > end:
                fail(f"{n['id']}量化后时长/边界不合法；请用更细网格或更换片段。")
        delta = max(
            abs(seconds_at(parsed["tempos"], n[k]) - seconds_at(parsed["tempos"], n["source_" + k]))
            for k in ("start_tick", "end_tick")
        )
        maximum = max(maximum, delta)
        if n["start_tick"] != n["source_start_tick"] or n["end_tick"] != n["source_end_tick"]:
            shifts.append(
                {
                    "note_id": n["id"],
                    "before": [n["source_start_tick"], n["source_end_tick"]],
                    "after": [n["start_tick"], n["end_tick"]],
                    "max_seconds": delta,
                }
            )
        local = (
            next(
                t["tempo"]
                for t in reversed(parsed["tempos"])
                if t["tick"] <= n["source_start_tick"]
            )
            / 1000000
        )
        n["large_shift"] = delta > min(0.1, local / 8) + 1e-9
    notes.sort(key=lambda n: (n["start_tick"], n["pitch"], n["id"]))
    voices = assign_voices(notes)
    if any(n["large_shift"] for n in notes) and not settings.acknowledge_large_quantization:
        fail(f"量化最大偏移{maximum * 1000:.1f}ms，可能改变琶音/节奏；请明确核对大偏移或更换网格。")
    selected = settings.voices or [v["voice"] for v in voices]
    if any(v not in [x["voice"] for x in voices] for v in selected) or len(set(selected)) != len(
        selected
    ):
        fail("所选推断声部不存在或重复。")
    picked = [n for n in notes if n["voice"] in selected]
    if not picked:
        fail("所选声部没有音符。")
    if len(selected) > (4 if settings.clef == "grand" else 2):
        return {
            "ready": False,
            "reason": "超过每谱表2个记谱声部；请明确选择较少推断声部或大谱表。",
            "voices": voices,
        }
    for i, a in enumerate(picked):
        if any(
            b["pitch"] == a["pitch"]
            and a["start_tick"] < b["end_tick"]
            and b["start_tick"] < a["end_tick"]
            for b in picked[i + 1 :]
        ):
            return {
                "ready": False,
                "reason": f"音高{a['pitch']}有独立重叠起音，电脑琴键不能分别保持；请明确选择独立推断声部。",
                "voices": voices,
            }
    order = sorted(
        selected, key=lambda v: (-next(x["mean_pitch"] for x in voices if x["voice"] == v), v)
    )
    for n in picked:
        rank = order.index(n["voice"])
        split = math.ceil(len(order) / 2)
        n["staff"] = 1 if settings.clef != "grand" or rank < split else 2
        n["display_voice"] = rank + 1 if n["staff"] == 1 else rank - split + 1
    layouts, segments = [], []
    for display_index, measure in enumerate(selected_measures, 1):
        layout = {**measure, "display_number": display_index, "source_number": measure["number"]}
        layouts.append(layout)
        for n in picked:
            low, high = (
                max(n["start_tick"], measure["start_tick"]),
                min(n["end_tick"], measure["end_tick"]),
            )
            if low >= high:
                continue
            key_item = next((k for k in reversed(parsed["keys"]) if k["tick"] <= low), {"key": "C"})
            fifths = (
                settings.key_fifths
                if settings.key_fifths is not None
                else FIFTHS.get(key_item["key"], 0)
            )
            step, alter = (FLAT if fifths < 0 else SHARP)[n["pitch"] % 12]
            offset = low
            for duration, kind, dots, tuplet in parts(high - low):
                sid = f"{n['id']}-seg{len([s for s in segments if s['source_id'] == n['id']])}"
                segments.append(
                    {
                        "id": sid,
                        "source_id": n["id"],
                        "measure": display_index,
                        "voice": n["display_voice"],
                        "staff": n["staff"],
                        "tick": offset,
                        "duration": duration,
                        "offset": offset - measure["start_tick"],
                        "pitch": n["pitch"],
                        "step": step,
                        "alter": alter,
                        "octave": n["pitch"] // 12 - 1,
                        "type": kind,
                        "dots": dots,
                        "tuplet": tuplet,
                        "tie_start": offset + duration < n["end_tick"],
                        "tie_stop": offset > n["start_tick"],
                    }
                )
                offset += duration
    available_ids = {n["id"]: n for n in picked}
    for a, b in settings.slurs:
        if (
            a not in available_ids
            or b not in available_ids
            or available_ids[a]["voice"] != available_ids[b]["voice"]
            or available_ids[a]["start_tick"] >= available_ids[b]["start_tick"]
        ):
            fail("圆滑线须选择同一记谱声部中按时间排列的两个实际起音。")
    attacks = []
    for tick in sorted({n["start_tick"] for n in picked}):
        chord = [n for n in picked if n["start_tick"] == tick]
        attacks.append(
            {
                "id": f"attack-{tick}",
                "tick": tick,
                "notes": [
                    {
                        "id": n["id"],
                        "pitch": n["pitch"],
                        "end_tick": n["end_tick"],
                        "voice": n["voice"],
                        "source_note_ids": [n["id"]],
                        "notation_ids": [s["id"] for s in segments if s["source_id"] == n["id"]],
                        "tie_chain_id": n["id"]
                        if len([s for s in segments if s["source_id"] == n["id"]]) > 1
                        else None,
                    }
                    for n in chord
                ],
                "reachability": reachability(
                    [n["pitch"] for n in picked if n["start_tick"] <= tick < n["end_tick"]]
                ),
            }
        )
    cursor, silences = start, []
    for n in sorted(picked, key=lambda n: n["start_tick"]):
        if n["start_tick"] > cursor:
            silences.append({"start_tick": cursor, "end_tick": n["start_tick"]})
        cursor = max(cursor, n["end_tick"])
    if cursor < end:
        silences.append({"start_tick": cursor, "end_tick": end})
    key_change = [
        k
        for k in parsed["keys"]
        if start < k["tick"] < end and k["tick"] not in [m["start_tick"] for m in layouts]
    ]
    if key_change and settings.key_fifths is None:
        fail("片段内有小节中途调号变化；请明确选择统一调号拼写或另选片段。")
    notation = {
        "format": "neonote-practice-notation-v1",
        "ppq": 480,
        "start_tick": start,
        "end_tick": end,
        "measures": layouts,
        "segments": segments,
        "notes": picked,
        "clef": settings.clef,
        "tempos": parsed["tempos"],
        "keys": parsed["keys"],
        "key_fifths": settings.key_fifths,
        "slurs": [list(s) for s in settings.slurs],
        "inferred": ["clef", "voices", "spelling", "rests", "ties"],
        "pickup_source": "user" if settings.pickup_ticks else "not_inferred",
        "key_source": "user"
        if settings.key_fifths is not None
        else "declared"
        if parsed["declared"]["key"]
        else "default_C",
    }
    targets = {
        "format": "neonote-practice-targets-v1",
        "ppq": 480,
        "start_tick": start,
        "end_tick": end,
        "tempos": parsed["tempos"],
        "measures": layouts,
        "attacks": attacks,
        "silences": silences,
        "slurs": notation["slurs"],
    }
    return {
        "ready": True,
        "notation": notation,
        "targets": targets,
        "musicxml": musicxml(notation),
        "voices": voices,
        "report": {
            "quantized_notes": len(shifts),
            "max_shift_ms": maximum * 1000,
            "shifts": shifts,
            "selected_notes": len(picked),
            "excluded_notes": len(notes) - len(picked),
            "controls_ignored": len(controls) + len(inherited),
            "preview_instrument": "统一原声钢琴；非原控制/音色复现",
            "blocked_targets": [
                a["id"] for a in attacks if a["reachability"]["level"] == "blocked"
            ],
            "difficult_targets": [
                a["id"] for a in attacks if a["reachability"]["level"] == "difficult"
            ],
        },
    }


def xml(parent, name, value=None, **attributes):
    node = SubElement(parent, name, attributes)
    if value is not None:
        node.text = str(value)
    return node


def musicxml(notation):
    root = Element("score-partwise", {"version": "4.0"})
    plist = xml(root, "part-list")
    xml(xml(plist, "score-part", id="P1"), "part-name", "NeoNote练习钢琴")
    part = xml(root, "part", id="P1")
    staff_count = 2 if notation["clef"] == "grand" else 1
    previous_key, previous_meter = None, None
    for m in notation["measures"]:
        bar = xml(
            part,
            "measure",
            number=str(m["display_number"]),
            implicit="yes" if m["pickup"] else "no",
        )
        key = next(
            (k for k in reversed(notation["keys"]) if k["tick"] <= m["start_tick"]), {"key": "C"}
        )
        fifths = (
            notation["key_fifths"]
            if notation["key_fifths"] is not None
            else FIFTHS.get(key["key"], 0)
        )
        meter = (m["numerator"], m["denominator"])
        first_bar = m["display_number"] == 1
        if first_bar or previous_key != fifths or previous_meter != meter:
            attrs = xml(bar, "attributes")
            if first_bar:
                xml(attrs, "divisions", 480)
            if previous_key != fifths:
                xml(xml(attrs, "key"), "fifths", fifths)
            if previous_meter != meter:
                time = xml(attrs, "time")
                xml(time, "beats", m["numerator"])
                xml(time, "beat-type", m["denominator"])
            if first_bar:
                if staff_count == 2:
                    xml(attrs, "staves", 2)
                for staff in range(1, staff_count + 1):
                    clef = xml(attrs, "clef", number=str(staff))
                    low = notation["clef"] == "bass" or staff == 2
                    xml(clef, "sign", "F" if low else "G")
                    xml(clef, "line", 4 if low else 2)
        previous_key, previous_meter = fifths, meter
        active_tempo = next(t for t in reversed(notation["tempos"]) if t["tick"] <= m["start_tick"])
        tempos = (
            [{**active_tempo, "tick": m["start_tick"]}] if m["display_number"] == 1 else []
        ) + [t for t in notation["tempos"] if m["start_tick"] <= t["tick"] < m["end_tick"]]
        for tempo in {t["tick"]: t for t in tempos}.values():
            direction = xml(bar, "direction")
            metronome = xml(xml(direction, "direction-type"), "metronome")
            xml(metronome, "beat-unit", "quarter")
            xml(metronome, "per-minute", round(60000000 / tempo["tempo"], 3))
            xml(direction, "offset", tempo["tick"] - m["start_tick"])
            xml(direction, "sound", tempo=str(60000000 / tempo["tempo"]))
        lanes = sorted(
            {(s["staff"], s["voice"]) for s in notation["segments"]}
            | {(staff, 1) for staff in range(1, staff_count + 1)}
        )
        for lane_index, (staff, voice) in enumerate(lanes):
            if lane_index:
                xml(xml(bar, "backup"), "duration", m["end_tick"] - m["start_tick"])
            notes = [
                s
                for s in notation["segments"]
                if s["measure"] == m["display_number"]
                and (s["staff"], s["voice"]) == (staff, voice)
            ]
            cursor = 0
            written = []

            def rest_until(stop, bar=bar, voice=voice, staff=staff, written=written):
                nonlocal cursor
                for duration, kind, dots, tuplet in parts(stop - cursor):
                    node = xml(bar, "note")
                    written.append((node, cursor, duration, kind, tuplet))
                    xml(node, "rest")
                    xml(node, "duration", duration)
                    xml(node, "voice", voice + (staff - 1) * 2)
                    xml(node, "type", kind)
                    for _ in range(dots):
                        xml(node, "dot")
                    if tuplet:
                        mod = xml(node, "time-modification")
                        xml(mod, "actual-notes", 3)
                        xml(mod, "normal-notes", 2)
                    xml(node, "staff", staff)
                    cursor += duration

            for s in sorted(notes, key=lambda s: (s["offset"], s["pitch"], s["id"])):
                chord = s["offset"] < cursor
                if not chord:
                    rest_until(s["offset"])
                node = xml(bar, "note", id=s["id"])
                if not chord:
                    written.append((node, s["offset"], s["duration"], s["type"], s["tuplet"]))
                if chord:
                    xml(node, "chord")
                pitch = xml(node, "pitch")
                xml(pitch, "step", s["step"])
                xml(pitch, "alter", s["alter"])
                xml(pitch, "octave", s["octave"])
                xml(node, "duration", s["duration"])
                for side in ("stop", "start"):
                    if s["tie_" + side]:
                        xml(node, "tie", type=side)
                xml(node, "voice", voice + (staff - 1) * 2)
                xml(node, "type", s["type"])
                for _ in range(s["dots"]):
                    xml(node, "dot")
                if s["tuplet"]:
                    mod = xml(node, "time-modification")
                    xml(mod, "actual-notes", 3)
                    xml(mod, "normal-notes", 2)
                xml(node, "staff", staff)
                marks = xml(node, "notations")
                for side in ("stop", "start"):
                    if s["tie_" + side]:
                        xml(marks, "tied", type=side)
                for number, (a, b) in enumerate(notation["slurs"], 1):
                    if s["source_id"] == a and not s["tie_stop"]:
                        xml(marks, "slur", type="start", number=str(number))
                    if s["source_id"] == b and not s["tie_start"]:
                        xml(marks, "slur", type="stop", number=str(number))
                if not chord:
                    cursor += s["duration"]
            rest_until(m["end_tick"] - m["start_tick"])
            # Equal contiguous triplets share one bracket; isolated ratios remain explicit.
            position = 0
            while position < len(written):
                node, onset, duration, kind, tuplet = written[position]
                if not tuplet:
                    position += 1
                    continue
                group = written[position : position + 3]
                complete = len(group) == 3 and all(
                    n[4] and n[2] == duration and n[3] == kind and n[1] == onset + i * duration
                    for i, n in enumerate(group)
                )
                group = group if complete else [written[position]]
                for side, member in (("start", group[0]), ("stop", group[-1])):
                    marks = member[0].find("notations")
                    if marks is None:
                        marks = xml(member[0], "notations")
                    xml(
                        marks,
                        "tuplet",
                        type=side,
                        number="1",
                        bracket="yes",
                        **{"show-number": "actual" if complete else "both"},
                    )
                position += len(group)
    return '<?xml version="1.0" encoding="UTF-8"?>' + tostring(root, encoding="unicode")


def hash_json(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
