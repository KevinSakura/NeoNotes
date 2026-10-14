"""Independent practice MIDI inventory. Original events and missing metadata stay explicit."""

import hashlib
from collections import defaultdict, deque
from io import BytesIO

import mido

from .storage import StorageError


def seconds_at(tempos, tick):
    total, previous, tempo = 0.0, 0, 500000
    for item in tempos:
        if item["tick"] > tick:
            break
        total += (item["tick"] - previous) * tempo / 480000000
        previous, tempo = item["tick"], item["tempo"]
    return total + (tick - previous) * tempo / 480000000


def parse_practice(content):
    if not content or len(content) > 2 * 1024 * 1024:
        raise StorageError("练习MIDI须为1字节至2MiB。", 422)
    try:
        try:
            midi = mido.MidiFile(file=BytesIO(content), charset="utf-8", clip=False)
        except UnicodeDecodeError:
            midi = mido.MidiFile(file=BytesIO(content), charset="latin1", clip=False)
    except (ValueError, EOFError, OSError, KeyError, IndexError, TypeError):
        raise StorageError("无法解析这个MIDI；请使用有效的.mid文件。", 422) from None
    if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
        raise StorageError("练习支持Type0/1正PPQ；Type2与SMPTE时间文件请先另存为PPQ MIDI。", 422)
    digest = hashlib.sha256(content).hexdigest()
    groups, events, tempos, meters, keys = {}, [], {}, {}, {}
    count, end_tick, rounding = 0, 0, 0.0

    def tick(raw):
        nonlocal rounding
        value = (raw * 960 + midi.ticks_per_beat) // (2 * midi.ticks_per_beat)
        rounding = max(rounding, abs(value - raw * 480 / midi.ticks_per_beat))
        return value

    for ti, track in enumerate(midi.tracks):
        absolute, active = 0, defaultdict(deque)
        name = next((m.name for m in track if m.type == "track_name"), f"原轨道{ti + 1}")
        settings = defaultdict(lambda: {"program": None, "bank_msb": None, "bank_lsb": None})
        for mi, message in enumerate(track):
            absolute += message.time
            position = tick(absolute)
            end_tick = max(end_tick, position)
            event = {
                "track": ti,
                "index": mi,
                "raw_tick": absolute,
                "tick": position,
                **message.dict(),
            }
            events.append(event)
            if message.is_meta:
                mapping, value = (
                    (tempos, message.tempo)
                    if message.type == "set_tempo"
                    else (
                        (meters, (message.numerator, message.denominator))
                        if message.type == "time_signature"
                        else (
                            (keys, message.key) if message.type == "key_signature" else (None, None)
                        )
                    )
                )
                if mapping is not None:
                    if position in mapping and mapping[position] != value:
                        raise StorageError(
                            f"tick{position}存在冲突的速度、拍号或调号，请整理原MIDI。", 422
                        )
                    mapping[position] = value
                continue
            ch = getattr(message, "channel", None)
            if ch is None:
                continue
            gid = f"t{ti}-c{ch}"
            if gid not in groups:
                groups[gid] = {
                    "id": gid,
                    "source_track": ti,
                    "channel": ch,
                    "name": name,
                    "instrument": dict(settings[ch]),
                    "notes": [],
                    "controls": [],
                    "attack_count": 0,
                    "ambiguous_same_pitch_pairs": 0,
                }
            group = groups[gid]
            if message.type == "program_change":
                settings[ch]["program"] = message.program
                if not group["attack_count"]:
                    group["instrument"] = dict(settings[ch])
                else:
                    group["controls"].append(event)
            elif (
                message.type == "control_change"
                and message.control in (0, 32)
                and not group["attack_count"]
            ):
                settings[ch]["bank_msb" if message.control == 0 else "bank_lsb"] = message.value
                group["instrument"] = dict(settings[ch])
            elif message.type == "note_on" and message.velocity > 0:
                group["attack_count"] += 1
                if active[(ch, message.note)]:
                    group["ambiguous_same_pitch_pairs"] += 1
                count += 1
                if count > 5000:
                    raise StorageError("原MIDI超过5000个note-on；请先导出较短材料。", 422)
                active[(ch, message.note)].append(
                    {
                        "id": f"{digest[:12]}-t{ti}-e{mi}",
                        "pitch": message.note,
                        "velocity": message.velocity,
                        "start_tick": position,
                        "raw_start": absolute,
                        "instrument": dict(settings[ch]),
                    }
                )
            elif message.type in ("note_off", "note_on"):
                pending = active[(ch, message.note)]
                if not pending:
                    raise StorageError(
                        f"原轨{ti + 1}音高{message.note}有未配对的松键事件，请整理原MIDI。", 422
                    )
                note = pending.popleft()
                if position <= note["start_tick"]:
                    raise StorageError(f"原轨{ti + 1}音符时长小于480PPQ可表示精度。", 422)
                note.update(end_tick=position, raw_end=absolute)
                group["notes"].append(note)
            else:
                group["controls"].append(event)
        if any(active.values()):
            raise StorageError(f"原轨{ti + 1}含未结束音符，请补全note-off。", 422)
    declared = {"tempo": 0 in tempos, "meter": 0 in meters, "key": 0 in keys}
    tempos.setdefault(0, 500000)
    meters.setdefault(0, (4, 4))
    tempo_map = [{"tick": t, "tempo": v} for t, v in sorted(tempos.items())]
    if not count or seconds_at(tempo_map, end_tick) > 600.001:
        raise StorageError("练习材料须包含音符，原时间轴不能超过10分钟。", 422)
    tracks = []
    for group in groups.values():
        if not group["notes"]:
            continue
        group["notes"].sort(key=lambda n: (n["start_tick"], n["pitch"], n["id"]))
        group["note_count"] = len(group["notes"])
        group["pitch_range"] = [
            min(n["pitch"] for n in group["notes"]),
            max(n["pitch"] for n in group["notes"]),
        ]
        group["keyboard_candidate"] = group["channel"] != 9 and (
            group["instrument"]["program"] is None or group["instrument"]["program"] in range(24)
        )
        tracks.append(group)
    # System events have no channel and may affect every track; retain and report them.
    system_controls = [e for e in events if e["type"] in ("sysex", "unknown_meta")]
    return {
        "format": "neonote-practice-midi-v1",
        "sha256": digest,
        "original_ppq": midi.ticks_per_beat,
        "ppq": 480,
        "max_rounding_ticks": rounding,
        "midi_type": midi.type,
        "end_tick": end_tick,
        "seconds": seconds_at(tempo_map, end_tick),
        "declared": declared,
        "tracks": tracks,
        "tempos": tempo_map,
        "meters": [
            {"tick": t, "numerator": v[0], "denominator": v[1]} for t, v in sorted(meters.items())
        ],
        "keys": [{"tick": t, "key": v} for t, v in sorted(keys.items())],
        "system_controls": system_controls,
        "events": events,
    }
