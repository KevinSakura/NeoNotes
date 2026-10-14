"""Backward-compatible song format; credentials never enter song data."""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

MAX_NOTES = 5000
MAX_SECONDS = 600
MAX_SHORT_LYRIC_UNITS = 256
MAX_FULL_LYRIC_UNITS = 1024
Tick = Annotated[int, Field(strict=True, ge=0, le=10_000_000)]
Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[\w-]+$")]


def new_id() -> str:
    return uuid4().hex


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Note(Model):
    id: Identifier = Field(default_factory=new_id)
    pitch: Annotated[int, Field(strict=True, ge=0, le=127)]
    velocity: Annotated[int, Field(strict=True, ge=1, le=127)] = 90
    start_tick: Tick
    duration_tick: Annotated[int, Field(strict=True, ge=1, le=10_000_000)]


class Track(Model):
    id: Identifier = Field(default_factory=new_id)
    name: str = Field(min_length=1, max_length=80)
    family: Literal["keyboard", "drums", "guitar", "bass", "other"]
    program: Annotated[int, Field(strict=True, ge=0, le=127)] = 0
    bank_msb: Annotated[int, Field(strict=True, ge=0, le=127)] = 0
    bank_lsb: Annotated[int, Field(strict=True, ge=0, le=127)] = 0
    channel: Annotated[int, Field(strict=True, ge=0, le=15)]
    is_drum: bool = False
    volume: Annotated[int, Field(strict=True, ge=0, le=100)] = 75
    mute: bool = False
    solo: bool = False
    notes: list[Note] = Field(default_factory=list, max_length=MAX_NOTES)

    @model_validator(mode="after")
    def check_instrument(self):
        if self.is_drum != (self.channel == 9) or self.is_drum != (self.family == "drums"):
            raise ValueError("鼓声部必须使用 GM 第 10 通道，旋律声部不能占用该通道。")
        if not self.name.strip():
            raise ValueError("声部名称不能为空。")
        return self


class Tempo(Model):
    tick: Tick = 0
    microseconds_per_beat: Annotated[int, Field(strict=True, ge=1, le=16_777_215)] = 500000


class TimeSignature(Model):
    tick: Tick = 0
    numerator: Annotated[int, Field(strict=True, ge=1, le=32)] = 4
    denominator: Literal[1, 2, 4, 8, 16, 32] = 4
    clocks_per_click: Annotated[int, Field(strict=True, ge=0, le=255)] = 24
    notated_32nd_notes_per_beat: Annotated[int, Field(strict=True, ge=0, le=255)] = 8


class MidiMeta(Model):
    tick: Tick
    type: Literal[
        "text",
        "lyrics",
        "marker",
        "cue_marker",
        "copyright",
        "key_signature",
        "instrument_name",
    ]
    text: str = Field(default="", max_length=10000)
    key: Literal[
        "Cb",
        "Gb",
        "Db",
        "Ab",
        "Eb",
        "Bb",
        "F",
        "C",
        "G",
        "D",
        "A",
        "E",
        "B",
        "F#",
        "C#",
        "Abm",
        "Ebm",
        "Bbm",
        "Fm",
        "Cm",
        "Gm",
        "Dm",
        "Am",
        "Em",
        "Bm",
        "F#m",
        "C#m",
        "G#m",
        "D#m",
        "A#m",
    ] = "C"
    track_id: Identifier | None = None


class Score(Model):
    ppq: Literal[480] = 480
    end_tick: Annotated[int, Field(strict=True, ge=1, le=10_000_000)] = 57600
    tempo_map: list[Tempo] = Field(default_factory=lambda: [Tempo()], min_length=1, max_length=1000)
    time_signatures: list[TimeSignature] = Field(
        default_factory=lambda: [TimeSignature()], min_length=1, max_length=1000
    )
    tracks: list[Track] = Field(min_length=1, max_length=16)
    midi_meta: list[MidiMeta] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def check_timeline(self):
        for values in (self.tempo_map, self.time_signatures):
            ticks = [value.tick for value in values]
            if ticks[0] != 0 or ticks != sorted(set(ticks)) or ticks[-1] > self.end_tick:
                raise ValueError("速度和拍号必须从第 0 tick 开始，按时间排序且不重复。")
        if len({track.id for track in self.tracks}) != len(self.tracks):
            raise ValueError("声部 ID 重复。")
        if len({track.channel for track in self.tracks}) != len(self.tracks):
            raise ValueError("第一阶段要求各声部通道独立，避免乐器设置互相覆盖。")
        notes = [note for track in self.tracks for note in track.notes]
        if len(notes) > MAX_NOTES or len({note.id for note in notes}) != len(notes):
            raise ValueError("音符 ID 重复，或超过第一阶段 5000 个音符的上限。")
        if any(note.start_tick + note.duration_tick > self.end_tick for note in notes):
            raise ValueError("音符超过工程时间轴。")
        track_ids = {track.id for track in self.tracks}
        if any(
            meta.tick > self.end_tick or (meta.track_id and meta.track_id not in track_ids)
            for meta in self.midi_meta
        ):
            raise ValueError("MIDI 元信息的时间或声部引用无效。")
        if self.seconds_at(self.end_tick) > MAX_SECONDS + 0.001:
            raise ValueError("工程最长支持 10 分钟。")
        return self

    def seconds_at(self, tick: int) -> float:
        seconds, cursor, tempo = 0.0, 0, self.tempo_map[0].microseconds_per_beat
        for item in self.tempo_map[1:]:
            if item.tick > tick:
                break
            seconds += (item.tick - cursor) * tempo / self.ppq / 1_000_000
            cursor, tempo = item.tick, item.microseconds_per_beat
        return seconds + (tick - cursor) * tempo / self.ppq / 1_000_000


class RecordedNote(Model):
    pitch: Annotated[int, Field(strict=True, ge=0, le=127)]
    velocity: Annotated[int, Field(strict=True, ge=1, le=127)] = 90
    start_seconds: float = Field(ge=0, le=MAX_SECONDS)
    duration_seconds: float = Field(gt=0, le=MAX_SECONDS)
    track_id: Identifier
    family: Literal["keyboard", "drums", "guitar", "bass", "other"]
    program: Annotated[int, Field(strict=True, ge=0, le=127)] = 0
    channel: Annotated[int | None, Field(strict=True, ge=0, le=15)] = None
    bank_msb: Annotated[int, Field(strict=True, ge=0, le=127)] = 0
    bank_lsb: Annotated[int, Field(strict=True, ge=0, le=127)] = 0

    @model_validator(mode="after")
    def check_end(self):
        if self.channel is not None and (self.channel == 9) != (self.family == "drums"):
            raise ValueError("录制的鼓必须使用第10通道，其他乐器不能占用该通道。")
        if self.start_seconds + self.duration_seconds > MAX_SECONDS + 0.001:
            raise ValueError("录制片段超过 10 分钟。")
        return self


class SourceFile(Model):
    id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["lyrics", "midi"]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Inputs(Model):
    theme: str = Field(default="", max_length=2000)
    emotion: str = Field(default="", max_length=4000)
    lyrics_text: str = Field(default="", max_length=512000)
    selected_lines: list[Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=list, max_length=10000
    )
    melody_mode: Literal["extend", "preserve"] = "extend"
    recording: list[RecordedNote] = Field(default_factory=list, max_length=MAX_NOTES)
    files: list[SourceFile] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_lines(self):
        lines = self.lyrics_text.split("\n")
        if self.selected_lines != sorted(set(self.selected_lines)):
            raise ValueError("歌词选段行号必须排序且不能重复。")
        if any(index >= len(lines) or not lines[index].strip() for index in self.selected_lines):
            raise ValueError("所选歌词行不存在或为空。")
        if self.selected_lines:
            expected = [
                i
                for i in range(self.selected_lines[0], self.selected_lines[-1] + 1)
                if lines[i].strip()
            ]
            if self.selected_lines != expected:
                raise ValueError("请选择连续的歌词段落。")
        if "\x00" in self.lyrics_text:
            raise ValueError("歌词包含无效的空字符。")
        if len({file.id for file in self.files}) != len(self.files):
            raise ValueError("输入文件 ID 重复。")
        return self


class SongSection(Model):
    name: str = Field(min_length=1, max_length=60)
    start_tick: Tick
    end_tick: Tick


class SongChord(Model):
    label: str = Field(min_length=1, max_length=40)
    start_tick: Tick
    end_tick: Tick
    pitches: list[Annotated[int, Field(strict=True, ge=0, le=127)]] = Field(
        min_length=1, max_length=8
    )


class LyricUnit(Model):
    line_index: Annotated[int, Field(strict=True, ge=0)]
    unit_index: Annotated[int, Field(strict=True, ge=0)]
    text: str = Field(min_length=1, max_length=100)
    note_ids: list[Identifier] = Field(min_length=1, max_length=12)


class ReferenceLock(Model):
    note_count: Annotated[int, Field(strict=True, ge=1, le=160)]
    families: list[Literal["keyboard", "drums", "guitar", "bass"]] = Field(
        min_length=1, max_length=4
    )
    filtered_model_notes: Annotated[int, Field(strict=True, ge=0, le=5000)] = 0


class FullSongSource(Model):
    revision_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    review_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    motif_pitches: list[Annotated[int, Field(strict=True, ge=0, le=127)]] = Field(
        min_length=1, max_length=8
    )


class NumberedLyric(Model):
    uid: Annotated[int, Field(strict=True, ge=0, le=1023)]
    line_index: Annotated[int, Field(strict=True, ge=0)]
    unit_index: Annotated[int, Field(strict=True, ge=0, le=1023)]
    text: str = Field(min_length=1, max_length=100)
    source_span: tuple[Tick, Tick]


class LyricsIdentity(Model):
    lyrics_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    tokenizer_version: Literal["neonote-lyric-units-1"] = "neonote-lyric-units-1"
    units: list[NumberedLyric] = Field(min_length=1, max_length=1024)


class PhraseLayout(Model):
    phrase_id: Identifier
    line_index: Annotated[int, Field(strict=True, ge=0)]
    uids: list[Annotated[int, Field(strict=True, ge=0, le=1023)]] = Field(min_length=1, max_length=64)
    word_groups: list[list[Annotated[int, Field(strict=True, ge=0, le=1023)]]] = Field(min_length=1, max_length=64)
    stresses: list[Literal["none", "secondary", "primary"]] = Field(min_length=1, max_length=64)
    start_tick: Tick
    end_tick: Tick
    pickup_tick: Tick = 0
    sung_end_tick: Tick


class AutoRegion(Model):
    phrase_id: Identifier
    start_tick: Tick
    end_tick: Tick
    accompaniment_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class PhraseMetadata(Model):
    compiler_version: Literal["neonote-phrases-2.0"] = "neonote-phrases-2.0"
    lyrics_identity: LyricsIdentity
    phrase_layout: list[PhraseLayout] = Field(min_length=1, max_length=256)
    auto_regions: list[AutoRegion] = Field(min_length=1, max_length=256)
    accompaniment: Literal["sparse", "steady", "driving"]
    prefix_end_tick: Tick = 0
    edit_chain: list[Identifier] = Field(default_factory=list, max_length=500)


class Composition(Model):
    job_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    mock: bool
    title: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=20)
    style_summary: str = Field(min_length=1, max_length=1500)
    scope: Literal["short", "full", "lyrics"] = "short"
    target_seconds: Annotated[float, Field(strict=True, ge=1, le=600)] = 60
    arrangement: Literal["events", "lyric-motifs-v1", "lyric-phrases-v2"] = "events"
    v2: PhraseMetadata | None = None
    full_source: FullSongSource | None = None
    sections: list[SongSection] = Field(min_length=1, max_length=24)
    chords: list[SongChord] = Field(min_length=1, max_length=256)
    lyrics_alignment: list[LyricUnit] = Field(default_factory=list, max_length=MAX_FULL_LYRIC_UNITS)
    melody_track_id: Identifier
    checks: list[str] = Field(default_factory=list, max_length=30)
    reference_lock: ReferenceLock | None = None

    @model_serializer(mode="wrap")
    def serialize_contract(self, handler):
        data = handler(self)
        if self.arrangement != "lyric-phrases-v2":
            data.pop("v2", None)
        return data


class SongState(Model):
    schema_version: Literal[1, 2] = 1
    inputs: Inputs = Field(default_factory=Inputs)
    score: Score
    composition: Composition | None = None

    @model_validator(mode="after")
    def check_contract_version(self):
        if self.composition and self.composition.arrangement == "lyric-phrases-v2":
            if self.schema_version != 2 or self.composition.v2 is None:
                raise ValueError("乐句级作品需要schema 2及完整乐句元数据。")
        elif self.composition and self.composition.v2 is not None:
            raise ValueError("旧合同不能附加v2乐句数据。")
        return self


def empty_song() -> SongState:
    tracks = [
        Track(name="键盘", family="keyboard", channel=0, program=0),
        Track(name="鼓", family="drums", channel=9, is_drum=True),
        Track(name="吉他", family="guitar", channel=1, program=24),
        Track(name="贝斯", family="bass", channel=2, program=33),
    ]
    return SongState(score=Score(tracks=tracks))


class CreateProject(Model):
    name: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def trim_name(self):
        self.name = self.name.strip()
        if not self.name or any(ord(char) < 32 for char in self.name):
            raise ValueError("请输入有效的歌曲名称。")
        return self


class SaveProject(Model):
    base_revision: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    label: str = Field(default="保存修改", min_length=1, max_length=120)
    state: SongState


class RevisionAction(Model):
    base_revision: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class RestoreRevision(RevisionAction):
    revision_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
