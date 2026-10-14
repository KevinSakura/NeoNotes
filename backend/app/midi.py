"""Conservative SMF import: unsupported musical events fail before committing."""

from collections import defaultdict, deque
from io import BytesIO

import mido

from .models import MidiMeta, Note, Score, Tempo, TimeSignature, Track


class MidiError(ValueError):
    pass


def import_midi(content: bytes) -> Score:
    try:
        return _import_midi(content)
    except MidiError:
        raise
    except (ValueError, UnicodeError, KeyError, TypeError, IndexError):
        raise MidiError("MIDI 时间、事件或字符编码不受第一阶段支持；当前作品未改变。") from None


def _import_midi(content: bytes) -> Score:
    try:
        try:
            mid = mido.MidiFile(file=BytesIO(content), clip=False, charset="utf-8")
        except UnicodeDecodeError:
            mid = mido.MidiFile(file=BytesIO(content), clip=False, charset="latin1")
    except (ValueError, EOFError, OSError, KeyError, IndexError, TypeError):
        raise MidiError("无法解析 MIDI。请选择有效的 .mid 或 .midi 文件。") from None
    if mid.type not in (0, 1) or mid.ticks_per_beat <= 0:
        raise MidiError("第一阶段仅支持采用 PPQ 时间基准的 Type 0/1 MIDI，不支持 Type 2 或 SMPTE。")

    def tick(value):
        # Nearest tick, with a deterministic half-up rule; no musical quantization.
        return (value * 480 * 2 + mid.ticks_per_beat) // (2 * mid.ticks_per_beat)

    groups = {}
    settings = defaultdict(lambda: {"program": 0, "bank_msb": 0, "bank_lsb": 0, "volume": 100})
    declared = {}
    tempos, signatures, metadata, end_tick = {}, {}, [], 1
    allowed_meta = {
        "text",
        "lyrics",
        "marker",
        "cue_marker",
        "copyright",
        "key_signature",
        "instrument_name",
    }
    for track_index, source in enumerate(mid.tracks):
        absolute, active = 0, defaultdict(deque)
        source_meta = []
        for message in source:
            absolute += message.time
            position = tick(absolute)
            end_tick = max(end_tick, position)
            if message.is_meta:
                if message.type == "set_tempo":
                    if position in tempos and tempos[position] != message.tempo:
                        raise MidiError("同一时间点包含冲突的速度设置。")
                    tempos[position] = message.tempo
                elif message.type == "time_signature":
                    value = TimeSignature(
                        tick=position,
                        numerator=message.numerator,
                        denominator=message.denominator,
                        clocks_per_click=message.clocks_per_click,
                        notated_32nd_notes_per_beat=message.notated_32nd_notes_per_beat,
                    )
                    if position in signatures and signatures[position] != value:
                        raise MidiError("同一时间点包含冲突的拍号。")
                    signatures[position] = value
                elif message.type in allowed_meta:
                    source_meta.append(
                        MidiMeta(
                            tick=position,
                            type=message.type,
                            key=message.key if message.type == "key_signature" else "C",
                            text=(
                                ""
                                if message.type == "key_signature"
                                else message.name
                                if message.type == "instrument_name"
                                else message.text
                            ),
                        )
                    )
                elif message.type not in ("track_name", "end_of_track"):
                    raise MidiError(f"第一阶段不支持 MIDI 元事件 {message.type}；当前作品未改变。")
                continue
            if message.type not in (
                "program_change",
                "control_change",
                "note_on",
                "note_off",
            ):
                raise MidiError(
                    f"文件包含暂不支持的 {message.type}（如弯音或 SysEx）；当前作品未改变。"
                )
            channel = message.channel
            group_key = (track_index, channel)
            if message.type in ("program_change", "control_change"):
                if position != 0:
                    raise MidiError(
                        "文件含途中音色或控制变化，第一阶段不做有损转换；当前作品未改变。"
                    )
                if message.type == "program_change":
                    key, value = "program", message.program
                else:
                    mapping = {0: "bank_msb", 32: "bank_lsb", 7: "volume"}
                    if message.control not in mapping:
                        raise MidiError(
                            f"第一阶段不支持 CC{message.control}（包括踏板等控制）；当前作品未改变。"
                        )
                    key, value = mapping[message.control], message.value
                if (channel, key) in declared and declared[(channel, key)] != value:
                    raise MidiError("同一通道存在互相覆盖的乐器或音量设置。")
                declared[(channel, key)] = value
                settings[channel][key] = value
                groups.setdefault(group_key, [])
                continue
            groups.setdefault(group_key, [])
            pair = (channel, message.note)
            if message.type == "note_on" and message.velocity:
                active[pair].append((position, message.velocity))
            else:
                if not active[pair]:
                    raise MidiError("文件有未配对的音符结束事件，无法可靠导入。")
                start, velocity = active[pair].popleft()
                groups[group_key].append(
                    Note(
                        pitch=message.note,
                        velocity=velocity,
                        start_tick=start,
                        duration_tick=max(1, position - start),
                    )
                )
        if any(active.values()):
            raise MidiError("文件有未结束的音符，无法可靠导入。")
        keys = [key for key in groups if key[0] == track_index]
        metadata.extend((keys[0] if keys else None, meta) for meta in source_meta)
    if not groups or not any(groups.values()):
        raise MidiError("MIDI 中没有可编辑的音符。")
    channels = [key[1] for key in groups]
    if len(set(channels)) != len(channels):
        raise MidiError("多个轨道共用同一 MIDI 通道；第一阶段不自动合并或改配器，请先整理通道。")
    tracks, ids = [], {}
    for (index, channel), notes in groups.items():
        config = settings[channel]
        program = config["program"]
        family = (
            "drums"
            if channel == 9
            else "keyboard"
            if program < 24
            else "guitar"
            if program < 32
            else "bass"
            if program < 40
            else "other"
        )
        name = (
            mid.tracks[index].name
            or {
                "drums": "鼓",
                "keyboard": "键盘",
                "guitar": "吉他",
                "bass": "贝斯",
                "other": "乐器",
            }[family]
        )
        track = Track(
            name=name[:80],
            family=family,
            channel=channel,
            is_drum=channel == 9,
            program=program,
            bank_msb=config["bank_msb"],
            bank_lsb=config["bank_lsb"],
            volume=round(config["volume"] / 127 * 100),
            notes=sorted(notes, key=lambda note: note.start_tick),
        )
        tracks.append(track)
        ids[(index, channel)] = track.id
        end_tick = max(
            end_tick,
            max((note.start_tick + note.duration_tick for note in notes), default=1),
        )
    for group, meta in metadata:
        meta.track_id = ids.get(group)
    tempos.setdefault(0, 500000)
    signatures.setdefault(0, TimeSignature())
    try:
        return Score(
            end_tick=end_tick,
            tracks=tracks,
            tempo_map=[
                Tempo(tick=position, microseconds_per_beat=value)
                for position, value in sorted(tempos.items())
            ],
            time_signatures=[value for _, value in sorted(signatures.items())],
            midi_meta=[meta for _, meta in metadata],
        )
    except ValueError:
        raise MidiError(
            "MIDI 超过 16 个独立声部、5000 个音符或 10 分钟上限，或其时间信息不受支持。"
        ) from None


def export_midi(score: Score, audible_only: bool = False) -> bytes:
    score = Score.model_validate(score.model_dump())
    mid = mido.MidiFile(type=1, ticks_per_beat=score.ppq, charset="utf-8")
    events = [(0, 0, mido.MetaMessage("track_name", name="Tempo & Meter"))]
    for item in score.tempo_map:
        events.append(
            (
                item.tick,
                1,
                mido.MetaMessage("set_tempo", tempo=item.microseconds_per_beat),
            )
        )
    for item in score.time_signatures:
        events.append(
            (
                item.tick,
                2,
                mido.MetaMessage("time_signature", **item.model_dump(exclude={"tick"})),
            )
        )
    for meta in score.midi_meta:
        if meta.track_id is None:
            events.append((meta.tick, 3, _meta_message(meta)))
    mid.tracks.append(_event_track(events, score.end_tick))
    solo = any(track.solo for track in score.tracks)
    for track in score.tracks:
        events = [
            (0, 0, mido.MetaMessage("track_name", name=track.name)),
            (
                0,
                1,
                mido.Message(
                    "control_change",
                    channel=track.channel,
                    control=0,
                    value=track.bank_msb,
                ),
            ),
            (
                0,
                2,
                mido.Message(
                    "control_change",
                    channel=track.channel,
                    control=32,
                    value=track.bank_lsb,
                ),
            ),
            (
                0,
                3,
                mido.Message("program_change", channel=track.channel, program=track.program),
            ),
            (
                0,
                4,
                mido.Message(
                    "control_change",
                    channel=track.channel,
                    control=7,
                    value=round(track.volume / 100 * 127),
                ),
            ),
        ]
        for meta in score.midi_meta:
            if meta.track_id == track.id:
                events.append((meta.tick, 5, _meta_message(meta)))
        if not audible_only or (not track.mute and (not solo or track.solo)):
            for note in track.notes:
                events.append(
                    (
                        note.start_tick,
                        7,
                        mido.Message(
                            "note_on",
                            channel=track.channel,
                            note=note.pitch,
                            velocity=note.velocity,
                        ),
                    )
                )
                events.append(
                    (
                        note.start_tick + note.duration_tick,
                        6,
                        mido.Message(
                            "note_off",
                            channel=track.channel,
                            note=note.pitch,
                            velocity=0,
                        ),
                    )
                )
        mid.tracks.append(_event_track(events, score.end_tick))
    output = BytesIO()
    mid.save(file=output)
    return output.getvalue()


def _meta_message(meta: MidiMeta):
    return mido.MetaMessage(
        meta.type,
        **(
            {"key": meta.key}
            if meta.type == "key_signature"
            else {"name": meta.text}
            if meta.type == "instrument_name"
            else {"text": meta.text}
        ),
    )


def _event_track(events, end_tick):
    track, previous = mido.MidiTrack(), 0
    for absolute, _, message in sorted(events, key=lambda event: (event[0], event[1])):
        track.append(message.copy(time=absolute - previous))
        previous = absolute
    track.append(mido.MetaMessage("end_of_track", time=max(0, end_tick - previous)))
    return track
