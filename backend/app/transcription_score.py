"""Seconds-preserving MIDI and independent, full-range score cleanup."""

from collections import defaultdict
from io import BytesIO
from typing import Literal

import mido
from pydantic import Field, model_validator

from .models import Model, new_id
from .practice_notation import FLAT, SHARP, assign_voices, measures_for, musicxml, parts
from .storage import StorageError

MAX_MODEL_PADDING_SECONDS = 16
MAX_VOICES_PER_HAND = 32


class TranscriptionNote(Model):
    id: str = Field(default_factory=new_id, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    pitch: int = Field(ge=0, le=127, strict=True)
    onset: float = Field(ge=0, le=60)
    offset: float = Field(gt=0, le=60.1)
    velocity: int = Field(default=80, ge=1, le=127, strict=True)
    hand: Literal["auto", "right", "left"] = "auto"

    @model_validator(mode="after")
    def interval(self):
        if self.offset <= self.onset:
            raise ValueError("音符结束时间必须晚于起始时间。")
        return self


class ScoreSettings(Model):
    bpm: float = Field(default=120, ge=30, le=240)
    numerator: Literal[2, 3, 4, 6] = 4
    denominator: Literal[4, 8] = 4
    first_downbeat: float = Field(default=0, ge=-8, le=8)
    grid: Literal[0, 60, 120, 160, 240, 480] = 120
    key_fifths: int = Field(default=0, ge=-7, le=7)


def validate_notes(notes, duration):
    if not notes or len(notes) > 5000:
        raise StorageError("需要1–5000个音符；原始文件仍保留。", 422)
    if len({n.id for n in notes}) != len(notes):
        raise StorageError("音符ID重复。", 422)
    if any(n.offset > duration + 0.001 for n in notes):
        raise StorageError("音符超出所选片段，请调整时间。", 422)


def read_midi(path, duration, diagnostics=None):
    if path.stat().st_size > 8 * 1024 * 1024:
        raise StorageError("模型MIDI输出过大，拒绝发布。", 422)
    midi = mido.MidiFile(path)
    if midi.type == 2 or midi.ticks_per_beat <= 0:
        raise StorageError("转录MIDI时间格式无效。", 422)
    report = {
        "policy": "clip-intersection-v1",
        "duration": duration,
        "clipped_notes": [],
        "outside_notes": [],
        "outside_controls": 0,
    }
    active = defaultdict(list)
    notes, controls, seconds = [], [], 0.0
    for msg in midi:
        seconds += msg.time
        if seconds < 0 or seconds > duration + MAX_MODEL_PADDING_SECONDS:
            raise StorageError("模型MIDI时间超出允许的16秒补帧窗口，拒绝发布。", 422)
        if msg.type == "note_on" and msg.velocity:
            active[(msg.channel, msg.note)].append((seconds, msg.velocity))
        elif msg.type in ("note_on", "note_off"):
            entries = active[(msg.channel, msg.note)]
            if not entries:
                raise StorageError("转录MIDI包含没有起音的松键。", 422)
            start, velocity = entries.pop(0)
            if seconds <= start:
                raise StorageError("转录音符结束时间不晚于起音，拒绝发布。", 422)
            if start >= duration:
                report["outside_notes"].append(
                    {"pitch": msg.note, "channel": msg.channel, "onset": start, "offset": seconds}
                )
                continue
            if seconds > duration + 1e-9:
                report["clipped_notes"].append(
                    {
                        "pitch": msg.note,
                        "channel": msg.channel,
                        "onset": start,
                        "original_offset": seconds,
                        "offset": duration,
                    }
                )
            notes.append(
                TranscriptionNote(
                    pitch=msg.note, onset=start, offset=min(seconds, duration), velocity=velocity
                )
            )
        elif msg.type == "control_change":
            if seconds > duration:
                report["outside_controls"] += 1
            controls.append(
                {
                    "seconds": seconds,
                    "channel": msg.channel,
                    "control": msg.control,
                    "value": msg.value,
                }
            )
    if any(active.values()):
        raise StorageError("转录MIDI存在未结束音符。", 422)
    validate_notes(notes, duration)
    if diagnostics is not None:
        diagnostics.update(report)
    return sorted(notes, key=lambda n: (n.onset, n.pitch)), controls


def clip_midi_bytes(path, duration):
    """Preserve in-clip channels, instruments, tempo and controls; close at boundary.

    Original file is never rewritten. Crossing notes receive a note-off at the
    clip edge; note-ons wholly in model padding are excluded from this derivative.
    """
    source = mido.MidiFile(path)
    result = mido.MidiFile(type=0, ticks_per_beat=source.ticks_per_beat)
    track = mido.MidiTrack()
    result.tracks.append(track)
    tempo = 500000
    seconds = 0.0
    tick = 0
    written_tick = 0
    active = defaultdict(int)
    pedals = {}
    for msg in mido.merge_tracks(source.tracks):
        next_seconds = seconds + mido.tick2second(msg.time, source.ticks_per_beat, tempo)
        if next_seconds > duration + 1e-9:
            break
        seconds = next_seconds
        tick += msg.time
        if msg.type == "end_of_track":
            continue
        if msg.type == "note_on" and msg.velocity:
            if seconds >= duration:
                continue
            active[(msg.channel, msg.note)] += 1
        elif msg.type in ("note_on", "note_off"):
            active[(msg.channel, msg.note)] -= 1
        elif msg.type == "control_change" and msg.control in (64, 66, 67):
            pedals[(msg.channel, msg.control)] = msg.value
        track.append(msg.copy(time=tick - written_tick))
        written_tick = tick
        if msg.type == "set_tempo":
            tempo = msg.tempo
    end_tick = max(
        tick,
        tick + round(mido.second2tick(max(0, duration - seconds), source.ticks_per_beat, tempo)),
    )
    for (channel, pitch), count in sorted(active.items()):
        for _ in range(max(0, count)):
            track.append(
                mido.Message(
                    "note_off",
                    channel=channel,
                    note=pitch,
                    velocity=0,
                    time=end_tick - written_tick,
                )
            )
            written_tick = end_tick
    for (channel, control), value in sorted(pedals.items()):
        if value >= 64:
            track.append(
                mido.Message(
                    "control_change",
                    channel=channel,
                    control=control,
                    value=0,
                    time=end_tick - written_tick,
                )
            )
            written_tick = end_tick
    track.append(mido.MetaMessage("end_of_track", time=end_tick - written_tick))
    output = BytesIO()
    result.save(file=output)
    return output.getvalue()


def midi_bytes(notes, bpm=120, ticks=None, settings=None):
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    meta = mido.MidiTrack()
    midi.tracks.append(meta)
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)))
    if settings:
        meta.append(
            mido.MetaMessage(
                "time_signature", numerator=settings.numerator, denominator=settings.denominator
            )
        )
        from .practice_notation import FIFTHS

        key = next(k for k, v in FIFTHS.items() if v == settings.key_fifths and not k.endswith("m"))
        meta.append(mido.MetaMessage("key_signature", key=key))
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="NeoNote piano"))
    track.append(mido.Message("program_change", program=0))
    events = []
    for n in notes:
        start, end = ticks[n.id] if ticks else (round(n.onset * 960), round(n.offset * 960))
        if end <= start:
            raise StorageError("MIDI舍入产生零时长音符。", 422)
        events.extend([(start, 1, n.pitch, n.velocity), (end, 0, n.pitch, 0)])
    previous = 0
    for tick, on, pitch, velocity in sorted(events):
        track.append(
            mido.Message(
                "note_on" if on else "note_off", note=pitch, velocity=velocity, time=tick - previous
            )
        )
        previous = tick
    buffer = BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()


def score(notes, settings):
    if (settings.numerator, settings.denominator) not in ((2, 4), (3, 4), (4, 4), (6, 8)):
        raise StorageError("首版支持2/4、3/4、4/4、6/8。", 422)
    scale = settings.bpm * 8  # seconds -> 480PPQ
    bar = 1920 * settings.numerator // settings.denominator
    # First downbeat is in clip-local seconds. Negative means a preceding bar start.
    origin = settings.first_downbeat
    lead = 0
    pickup = round(origin * scale) if origin > 0 else 0
    if pickup >= bar:
        raise StorageError("第一强拍须位于首个完整小节长度以内；更长前奏请调整选段。", 422)
    rows, shifts, ticks = [], [], {}
    for n in notes:
        raw_start = (n.onset - origin) * scale + (pickup if origin >= 0 else 0) + lead
        raw_end = (n.offset - origin) * scale + (pickup if origin >= 0 else 0) + lead
        grid = settings.grid
        start = round(raw_start / grid) * grid if grid else round(raw_start)
        end = round(raw_end / grid) * grid if grid else round(raw_end)
        if end <= start or start < 0:
            raise StorageError(
                f"音符{n.id}量化后为零时长或负起点；请改细网格或校对音符，原MIDI可继续下载。", 422
            )
        ticks[n.id] = (start, end)
        shifts.append(max(abs(start - raw_start), abs(end - raw_end)) / scale * 1000)
        rows.append(
            {
                "id": n.id,
                "pitch": n.pitch,
                "velocity": n.velocity,
                "start_tick": start,
                "end_tick": end,
                "hand": n.hand,
            }
        )
    # Automatic lanes use nonoverlap and pitch continuity, then ranked register.
    voices = assign_voices(rows)
    ranked = sorted(voices, key=lambda v: -v["mean_pitch"])
    split = max(1, (len(ranked) + 1) // 2)
    hand_for = {v["voice"]: 1 if i < split else 2 for i, v in enumerate(ranked)}
    for n in rows:
        n["staff"] = {"left": 2, "right": 1}.get(n["hand"], hand_for[n["voice"]])
    # Reallocate per hand so explicit hand changes never create overlapping lanes.
    voice_counts = {}
    for staff in (1, 2):
        subset = [n for n in rows if n["staff"] == staff]
        lanes = assign_voices(subset)
        voice_counts[staff] = len(lanes)
        if len(lanes) > MAX_VOICES_PER_HAND:
            raise StorageError(
                f"{'右' if staff == 1 else '左'}手需要{len(lanes)}个独立声部，超过安全排版上限{MAX_VOICES_PER_HAND}；请校对密集重叠音或缩短片段。原MIDI不受影响。",
                422,
            )
    parsed = {
        "meters": [
            {"tick": 0, "numerator": settings.numerator, "denominator": settings.denominator}
        ],
        "end_tick": max(n["end_tick"] for n in rows),
    }
    measures = measures_for(parsed, pickup)
    segments = []
    for m in measures:
        m.update(display_number=m["number"], source_number=m["number"])
        for n in rows:
            low, high = max(n["start_tick"], m["start_tick"]), min(n["end_tick"], m["end_tick"])
            if low >= high:
                continue
            step, alter = (FLAT if settings.key_fifths < 0 else SHARP)[n["pitch"] % 12]
            position = low
            for duration, kind, dots, tuplet in parts(high - low):
                segments.append(
                    {
                        "id": f"{n['id']}-s{len(segments)}",
                        "source_id": n["id"],
                        "measure": m["number"],
                        "staff": n["staff"],
                        "voice": n["voice"],
                        "offset": position - m["start_tick"],
                        "tick": position,
                        "duration": duration,
                        "pitch": n["pitch"],
                        "step": step,
                        "alter": alter,
                        "octave": n["pitch"] // 12 - 1,
                        "type": kind,
                        "dots": dots,
                        "tuplet": tuplet,
                        "tie_start": position + duration < n["end_tick"],
                        "tie_stop": position > n["start_tick"],
                    }
                )
                position += duration
    notation = {
        "format": "neonote-transcription-notation-v2",
        "voice_stride": max(2, *voice_counts.values()),
        "start_tick": 0,
        "end_tick": measures[-1]["end_tick"],
        "clef": "grand",
        "measures": measures,
        "segments": segments,
        "notes": rows,
        "keys": [],
        "key_fifths": settings.key_fifths,
        "tempos": [{"tick": 0, "tempo": mido.bpm2tempo(settings.bpm)}],
        "slurs": [],
    }
    xml = musicxml(notation).replace("NeoNote练习钢琴", "NeoNote扒谱钢琴")
    return {
        "notation": notation,
        "musicxml": xml,
        "report": {
            "max_shift_ms": max(shifts),
            "quantized_notes": sum(v > 0.01 for v in shifts),
            "hand_assignment": "inferred continuity/register; per-note user overrides",
            "voices_per_hand": {"right": voice_counts[1], "left": voice_counts[2]},
            "first_downbeat": origin,
            "clip_zero_tick": round(-origin * scale + lead) if origin < 0 else 0,
        },
    }, midi_bytes(notes, settings.bpm, ticks, settings)
