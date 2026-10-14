"""Saved-note conversion only; never recompose, transpose, or guess lyric references."""

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .composition import validate_alignment
from .model_settings import ModelError
from .models import Model, RevisionAction, SongState
from .resources import AudioError

PROTOCOL = "neonote-vocal-v1"
PHRASE_PROTOCOL = "neonote-vocal-v2"
SINGER_ID = "qixuan-2.7.0"
SAMPLE_RATE = 44100
TAIL_SECONDS = 3
MANDARIN_CHARACTER = re.compile(r"[\u3400-\u9fff\U00020000-\U0002fa1f]")


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


class Pronunciation(Model):
    line_index: Annotated[int, Field(strict=True, ge=0, le=10000)]
    unit_index: Annotated[int, Field(strict=True, ge=0, le=1023)]
    pinyin: str = Field(min_length=1, max_length=12, pattern=r"^[a-z]+$")


class VocalSettings(Model):
    singer: Literal["qixuan-2.7.0"] = SINGER_ID
    runner: Literal["DirectML", "CPU"] = "DirectML"
    gpu: Annotated[int, Field(strict=True, ge=0, le=15)] = 0
    pronunciations: list[Pronunciation] = Field(default_factory=list, max_length=1024)
    vocal_gain: float = Field(default=0.8, ge=0, le=2)
    accompaniment_gain: float = Field(default=1.0, ge=0, le=2)

    @model_validator(mode="after")
    def unique_readings(self):
        keys = [(item.line_index, item.unit_index) for item in self.pronunciations]
        if len(keys) != len(set(keys)):
            raise ValueError("同一个字不能重复设置读音。")
        self.pronunciations.sort(key=lambda p: (p.line_index, p.unit_index))
        return self


class VocalSettingsRequest(RevisionAction):
    settings: VocalSettings = Field(default_factory=VocalSettings)
    expected_settings_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    lyrics_identity_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class VocalRequest(RevisionAction):
    settings_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def validate_pronunciation_targets(state: SongState, settings: VocalSettings):
    units = state.composition.lyrics_alignment if state.composition else []
    valid_keys = {(u.line_index, u.unit_index) for u in units}
    if any((p.line_index, p.unit_index) not in valid_keys for p in settings.pronunciations):
        raise AudioError("读音修正引用了当前版本不存在的字，请重新选择。")


def reading_identity(state):
    c = state.composition
    return fingerprint({"lyrics_hash": hashlib.sha256(state.inputs.lyrics_text.encode("utf-8")).hexdigest(),
                        "tokenizer_version": c.v2.lyrics_identity.tokenizer_version if c and c.v2 else "neonote-lyric-units-1",
                        "units": [[u.line_index, u.unit_index, u.text] for u in c.lyrics_alignment] if c else []})


def score_check(state: SongState, settings: VocalSettings):
    """Read-only readiness for the saved score; not a resource or listening pass."""
    unsupported = [
        {"line_index": u.line_index, "unit_index": u.unit_index, "text": u.text}
        for u in (state.composition.lyrics_alignment if state.composition else [])
        if not MANDARIN_CHARACTER.fullmatch(u.text)
    ]
    try:
        convert_score(state, settings)
    except AudioError as error:
        return {
            "ready": False,
            "detail": str(error),
            "unsupported_count": len(unsupported),
            "unsupported_units": unsupported[:12],
        }
    return {"ready": True, "detail": None, "unsupported_count": 0, "unsupported_units": []}


def convert_score(state: SongState, settings: VocalSettings):
    try:
        validate_alignment(state)
    except ModelError as error:
        raise AudioError(f"无法演唱：{error}") from None
    composition = state.composition
    if not composition or not composition.lyrics_alignment:
        raise AudioError("保存版本缺少有效歌词与音符对应，请先谱曲或修复对应后保存。")
    full_lines = [i for i, line in enumerate(state.inputs.lyrics_text.split("\n")) if line.strip()]
    if state.inputs.selected_lines != full_lines:
        raise AudioError("本次演唱需要全文对应；旧选段作品不能补唱尚未对应的歌词。")
    lead = next(t for t in state.score.tracks if t.id == composition.melody_track_id)
    by_id = {note.id: note for note in lead.notes}
    readings = {(p.line_index, p.unit_index): p.pinyin for p in settings.pronunciations}
    validate_pronunciation_targets(state, settings)
    lines = []
    used = set()
    for unit in composition.lyrics_alignment:
        location = f"歌词第{unit.line_index + 1}行第{unit.unit_index + 1}字词"
        if not MANDARIN_CHARACTER.fullmatch(unit.text):
            raise AudioError(
                f"{location}“{unit.text}”：当前演唱接入仅支持普通话汉字，尚未接入英文或数字读法。"
                "请使用已完成歌词对应的中文谱曲版本；拼音修正用于汉字读音，不能替代英文演唱。"
            )
        notes = [by_id[note_id] for note_id in unit.note_ids]
        if any(
            a.start_tick + a.duration_tick != b.start_tick
            for a, b in zip(notes, notes[1:], strict=False)
        ):
            raise AudioError(f"{location}：一字多音需要相邻连续音符；休止或器乐音符不能跨过续唱。")
        if not lines or lines[-1]["line_index"] != unit.line_index:
            lines.append({"line_index": unit.line_index, "units": []})
        lines[-1]["units"].append(
            {
                "unit_index": unit.unit_index,
                "text": unit.text,
                "lyric": readings.get((unit.line_index, unit.unit_index), unit.text),
                "notes": [
                    {
                        "note_id": note.id,
                        "position": note.start_tick,
                        "duration": note.duration_tick,
                        "tone": note.pitch,
                        "start_seconds": state.score.seconds_at(note.start_tick),
                        "end_seconds": state.score.seconds_at(note.start_tick + note.duration_tick),
                    }
                    for note in notes
                ],
            }
        )
        used.update(unit.note_ids)
    # USTX represents meter changes by bar position, so reject mid-bar changes explicitly.
    signatures, bar, previous = [], 0, None
    for signature in state.score.time_signatures:
        if previous:
            bar_ticks = state.score.ppq * 4 * previous.numerator // previous.denominator
            distance = signature.tick - previous.tick
            if distance % bar_ticks:
                raise AudioError("拍号在小节中途改变，当前演唱适配暂不支持；原谱保留。")
            bar += distance // bar_ticks
        signatures.append(
            {
                "bar_position": bar,
                "numerator": signature.numerator,
                "denominator": signature.denominator,
            }
        )
        previous = signature
    duration = state.score.seconds_at(state.score.end_tick) + TAIL_SECONDS
    request = {
        "protocol": PROTOCOL,
        "end_tick": state.score.end_tick,
        "frames": round(duration * SAMPLE_RATE),
        "tempos": [
            {"position": t.tick, "bpm": 60_000_000 / t.microseconds_per_beat}
            for t in state.score.tempo_map
        ],
        "time_signatures": signatures,
        "lines": lines,
    }
    if composition.arrangement == "lyric-phrases-v2":
        from .lyric_phrases import validate_phrases
        validate_phrases(state)
        indexed = {(line["line_index"], u["unit_index"]): u for line in lines for u in line["units"]}
        metadata = composition.v2
        phrases = []
        for p in metadata.phrase_layout:
            units = []
            for uid in p.uids:
                source_unit = metadata.lyrics_identity.units[uid]
                units.append({**indexed[(source_unit.line_index, source_unit.unit_index)],
                              "uid": uid, "line_index": source_unit.line_index})
            phrases.append({"phrase_id": p.phrase_id, "line_index": p.line_index,
                            "start_tick": p.start_tick, "end_tick": p.end_tick,
                            "pickup_tick": p.pickup_tick, "units": units})
        request.pop("lines")
        request.update(protocol=PHRASE_PROTOCOL, phrases=phrases,
                       lyrics_identity=metadata.lyrics_identity.model_dump(),
                       phrase_layout_hash=fingerprint([p.model_dump() for p in metadata.phrase_layout]))
    request["singing_policy"] = {"pitch": "score", "vibrato": 0, "merge_nearby_phrases": False,
                                 "parts": "musical_phrases" if request["protocol"] == PHRASE_PROTOCOL else "original_lines"}
    accompaniment = state.score.model_copy(deep=True)
    target = next(t for t in accompaniment.tracks if t.id == lead.id)
    target.notes = [note for note in target.notes if note.id not in used]
    # Lyrics metadata are not played, and must not describe the removed guide notes.
    accompaniment.midi_meta = [m for m in accompaniment.midi_meta if m.type != "lyrics"]
    return request, accompaniment, sorted(used)
