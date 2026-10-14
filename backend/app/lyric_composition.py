"""Line melodies expanded into lyrics, timing and accompaniment by the local arranger.

The provider never returns note IDs or a second list of global note indices. A motif
explicitly repeats over a line's known units; its cadence belongs to the last unit.
This is a composition contract, not a repair of an invalid legacy response.
"""

import json
import math
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from .composition import (
    FAMILIES,
    MAX_FULL_AI_NOTES,
    PROGRAMS,
    input_context,
    reference_events,
)
from .model_settings import ModelError
from .models import Model

CONTRACT = "lyric-motifs-v1"
Tone = Annotated[int, Field(strict=True, ge=48, le=84)]
Duration = Annotated[float, Field(ge=0.5, le=4)]
SungNote = tuple[Tone, Duration]
Cell = Annotated[list[SungNote], Field(min_length=1, max_length=12)]


def lyric_context(state, operation="generate", instruction=""):
    """Explicit new requests use the whole enabled lyric box, with legacy jobs untouched."""
    source = state.model_copy(deep=True)
    source.inputs.selected_lines = [
        index for index, line in enumerate(source.inputs.lyrics_text.split("\n")) if line.strip()
    ]
    context = input_context(source, operation, instruction, flexible_lyrics=True)
    context.update(
        contract=CONTRACT,
        scope="full" if context["scope"] == "full" else "lyrics",
        target_seconds=None,
        duration_policy="lyrics",
        lyric_scope="all_nonempty_lines",
        arrangement_policy="repeat_line_motifs_and_cadence; bar_aligned_breaths; local_four_part_accompaniment",
        max_seconds=600,
        max_notes=MAX_FULL_AI_NOTES,
    )
    context.pop("lyric_index_rule", None)
    context.pop("lyric_prefix_eligible_indices", None)
    return source, context


class LyricChord(Model):
    label: str = Field(min_length=1, max_length=40)
    pitches: list[Tone] = Field(min_length=3, max_length=6)


class LyricPhrase(Model):
    line_index: Annotated[int, Field(strict=True, ge=0)]
    section: str = Field(min_length=1, max_length=60)
    motif: list[Cell] = Field(min_length=1, max_length=32)
    cadence: Cell
    chords: list[LyricChord] = Field(min_length=1, max_length=8)
    interlude_bars: Annotated[int, Field(strict=True, ge=0, le=4)] = 0


class LyricInstrument(Model):
    family: Literal["keyboard", "drums", "guitar", "bass"]
    program: Annotated[int, Field(strict=True, ge=0, le=127)]
    bank_msb: Annotated[int, Field(strict=True, ge=0, le=127)] = 0
    volume: Annotated[int, Field(strict=True, ge=1, le=100)] = 75


class LyricPlan(Model):
    format: Literal[CONTRACT]
    title: str = Field(min_length=1, max_length=100)
    bpm: Annotated[int, Field(strict=True, ge=60, le=160)]
    key: str = Field(min_length=1, max_length=20)
    style_summary: str = Field(min_length=1, max_length=1500)
    intro_bars: Annotated[int, Field(strict=True, ge=0, le=4)] = 1
    outro_bars: Annotated[int, Field(strict=True, ge=1, le=4)] = 2
    accompaniment: Literal["sparse", "steady", "driving"] = "steady"
    instruments: list[LyricInstrument] = Field(min_length=4, max_length=4)
    phrases: list[LyricPhrase] = Field(min_length=1, max_length=64)


PROMPT = """你是中文歌词谱曲助手。为输入的每句歌词设计可试唱的旋律型、句尾收束和和声。
歌词决定内容，music_style决定呈现方式；悲伤歌词可以使用积极曲风。不要输出音频、MIDI或Base64。
采用lyric-motifs-v1契约：平台已经分好中文字符/英文词。不要逐字计数，不要输出lyrics_alignment、
note_indices或任何全局音符编号。平台按歌词原顺序自动建立字词与实际音符ID的对应。
每句motif是一组依次循环的“字词旋律单元”，每个单元是1至12个[pitch,duration_beats]。
除最后一个字词外，平台按顺序循环motif；cadence用于最后一个字词。此循环是明确编曲规则，
不是缺词补救。请给有变化且和本句语气相称的旋律型，同一单元可以一字多音。
谱曲时考虑中文语气、英文音节、长句的密度；器乐结构合法不代表歌词可唱。
每个音高48至84，时值0.5至4拍；速度60至160且为4的倍数。平台在句间留至少一拍换气，
按4/4小节安排前奏/间奏/尾奏，自动确定总时长，不把歌词强塞进60秒。上限10分钟/5000音符。
phrases必须恰好包含selected_lyrics每行一次、原顺序，line_index沿用给出的原始行号。
每句chords给1至8个和弦，平台按小节循环这些和弦；不要列出每个小节。
section给主歌/副歌等名称，相邻同名合并；共同段落最多24个（包含前奏、间奏与尾奏）。
四声部共用这些和弦和时间规划。平台按accompaniment安排鼓、吉他、贝斯，必要时减少伴奏密度以
保留字词、主旋律与5000音符上限。sparse适合稀疏伴奏，steady稳定节奏，driving增加鼓律动。
有reference_notes时，保留模式由平台原样固定演奏作为无词前缀，之后才开始歌词。
自由改编必须沿原旋律走向发展。存在approved_short_song时沿用其旋律动机和风格，按全词重排。
modify操作根据instruction和current_score对整首作品重新编配，歌词时长仍由本次旋律决定。
只返回一个JSON对象，无代码围栏。格式：
{"format":"lyric-motifs-v1","title":"作品名","bpm":96,"key":"C major",
 "style_summary":"旋律、曲风和伴奏安排","intro_bars":1,"outro_bars":2,
 "accompaniment":"steady","instruments":[
 {"family":"keyboard","program":0,"bank_msb":0,"volume":82},
 {"family":"drums","program":0,"bank_msb":0,"volume":60},
 {"family":"guitar","program":24,"bank_msb":0,"volume":65},
 {"family":"bass","program":33,"bank_msb":0,"volume":70}],
 "phrases":[{"line_index":0,"section":"主歌","motif":[[[60,0.5]],[[62,0.5]],[[64,1]]],
 "cadence":[[60,1.5]],"chords":[{"label":"C","pitches":[60,64,67]}],"interlude_bars":0}]}
"""


def lyric_messages(context):
    return [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]


def mock_lyric_plan(context):
    """An explicit offline fixture, never evidence of model musical quality."""
    return {
        "format": CONTRACT,
        "title": "模拟 · 歌词旋律型编配",
        "bpm": (112 if context["operation"] == "modify" else 96)
        if not context.get("feasible_bpms")
        else context["feasible_bpms"][0],
        "key": "C major",
        "style_summary": "模拟旋律型与程序伴奏，仅验证逐词展开和完整音频流程。",
        "intro_bars": 1,
        "outro_bars": 2,
        "accompaniment": "steady",
        "instruments": [{"family": f, "program": PROGRAMS[f]} for f in FAMILIES],
        "phrases": [
            {
                "line_index": line["line_index"],
                "section": "主歌"
                if position < max(1, len(context["selected_lyrics"]) // 2)
                else "副歌",
                "motif": [[[60, 0.5]], [[62, 0.5]], [[64, 0.5]], [[67, 0.5]]],
                "cadence": [[64, 1], [60, 1]],
                "chords": [{"label": "C", "pitches": [60, 64, 67]}],
                "interlude_bars": 0,
            }
            for position, line in enumerate(context["selected_lyrics"])
        ],
    }


def arrange_lyrics(raw, source, context):
    """Return a compiled legacy-shaped score plan plus the original motif contract.

    Global indices below are calculated simultaneously with actual notes by Python;
    they are an internal bridge to the existing MIDI/score validator, never LLM data.
    """
    try:
        plan = LyricPlan.model_validate(raw)
    except ValidationError as error:
        places = [
            ".".join(str(part) for part in item["loc"])
            for item in error.errors(include_input=False, include_context=False)[:3]
        ]
        raise ModelError(
            "歌词旋律型结构不符合约定：" + "、".join(places) + "。未采用候选。", code="lyric_schema"
        ) from None
    lines = context["selected_lyrics"]
    if [p.line_index for p in plan.phrases] != [line["line_index"] for line in lines]:
        raise ModelError("模型漏掉、重复或改变了歌词行顺序，未采用候选。", code="lyric_lines")
    if sorted(i.family for i in plan.instruments) != sorted(FAMILIES):
        raise ModelError("歌词编配须包含四个不同声部。", code="lyric_instruments")
    if plan.bpm % 4 or plan.bpm not in context.get("feasible_bpms", [plan.bpm]):
        raise ModelError("歌词速度不符合可用速度或原演奏保留条件。", code="lyric_tempo")
    tempo = round(60_000_000 / plan.bpm)
    prefix = {}
    for family in FAMILIES:
        _, anchors = reference_events(source, family, tempo)
        prefix[family] = anchors
    cutoffs = {
        f: max((n.start_tick + n.duration_tick for n in ns), default=0) for f, ns in prefix.items()
    }
    notes = {family: [] for family in FAMILIES}
    sections, chords, alignment = [], [], []
    beat_tick, bar = 480, 1920
    prefix_end = math.ceil(max(cutoffs.values()) / bar) * bar
    motif = context.get("full_source", {}).get("motif_pitches", [])
    intro_end = prefix_end + max(plan.intro_bars * bar, len(motif) * beat_tick)
    # Instrumental units are unbound. They never compete with word-note numbering.
    intro_cell = motif or [cell[0][0] for cell in plan.phrases[0].motif]

    def add(family, start, pitch, duration, velocity=82):
        if start >= cutoffs[family] and duration > 0:
            notes[family].append([start / beat_tick, pitch, duration / beat_tick, velocity])

    def segment(name, start, end, progression):
        if end <= start:
            return
        if sections and sections[-1]["name"] == name and sections[-1]["end"] == start / beat_tick:
            sections[-1]["end"] = end / beat_tick
        else:
            sections.append({"name": name, "start": start / beat_tick, "end": end / beat_tick})
        for position, tick in enumerate(range(start, end, bar)):
            chord = progression[position % len(progression)]
            last = min(end, tick + bar)
            item = {
                "label": chord.label,
                "pitches": chord.pitches,
                "start": tick / beat_tick,
                "end": last / beat_tick,
            }
            if chords and all(chords[-1][k] == item[k] for k in ("label", "pitches")):
                chords[-1]["end"] = item["end"]
            else:
                chords.append(item)

    if intro_end:
        segment("前奏", 0, intro_end, plan.phrases[0].chords)
        cursor = math.ceil(cutoffs["keyboard"] / beat_tick) * beat_tick
        for position, tick in enumerate(range(cursor, intro_end, beat_tick)):
            add(
                "keyboard",
                tick,
                intro_cell[position % len(intro_cell)],
                min(beat_tick, intro_end - tick),
            )
    cursor = intro_end
    for phrase, line in zip(plan.phrases, lines, strict=True):
        start = cursor
        units = line["units"]
        if not units:
            raise ModelError(
                "歌词行只有标点，无法谱曲；请去除该行或选择其他歌词。", code="lyric_empty_line"
            )
        for unit_index, _ in enumerate(units):
            cell = (
                phrase.cadence
                if unit_index == len(units) - 1
                else phrase.motif[unit_index % len(phrase.motif)]
            )
            indices = []
            for pitch, duration in cell:
                ticks = round(duration * beat_tick)
                indices.append(len(prefix["keyboard"]) + len(notes["keyboard"]))
                add("keyboard", cursor, pitch, ticks)
                cursor += ticks
            alignment.append(
                {"line_index": phrase.line_index, "unit_index": unit_index, "note_indices": indices}
            )
        # A whole beat plus bar alignment gives >=0.375s at the fastest allowed BPM.
        end = math.ceil((cursor + beat_tick) / bar) * bar
        segment(phrase.section, start, end, phrase.chords)
        cursor = end
        if phrase.interlude_bars:
            end = cursor + phrase.interlude_bars * bar
            segment("间奏", cursor, end, phrase.chords)
            for tick in range(cursor, end, beat_tick):
                add("keyboard", tick, phrase.chords[0].pitches[(tick // beat_tick) % 3], beat_tick)
            cursor = end
    end = cursor + plan.outro_bars * bar
    segment("尾奏", cursor, end, plan.phrases[-1].chords)
    for tick in range(cursor, end, beat_tick):
        add("keyboard", tick, plan.phrases[-1].chords[-1].pitches[0], beat_tick)
    duration_seconds = end * tempo / beat_tick / 1_000_000
    if duration_seconds > 600:
        raise ModelError(
            f"这份歌词旋律展开后为{duration_seconds:.1f}秒，超过10分钟；请选更短歌词或更紧凑旋律。原输入保留。",
            code="lyric_duration",
        )
    if len(sections) > 24 or len(chords) > 256:
        raise ModelError(
            "歌词编配超过24段/256和弦边界，请简化段落和和声变化。", code="lyric_planning"
        )
    sung_count = sum(len(ns) for ns in notes.values()) + sum(len(ns) for ns in prefix.values())
    bars = end // bar
    density = plan.accompaniment
    # Sparse accompaniment is the documented deterministic note-budget policy.
    upper = {"sparse": 8, "steady": 14, "driving": 18}[density]
    if sung_count + bars * upper > MAX_FULL_AI_NOTES:
        density = "sparse"
    if sung_count + bars * 8 > MAX_FULL_AI_NOTES:
        raise ModelError(
            "歌词主旋律及最少伴奏超过5000音符，请减少花腔或歌词长度。", code="lyric_notes"
        )
    chord_index = 0
    for tick in range(0, end, bar):
        while tick / beat_tick >= chords[chord_index]["end"]:
            chord_index += 1
        pitches = chords[chord_index]["pitches"]
        root = pitches[0]
        bass = 36 + (root % 12)
        if density == "sparse":
            for pitch in dict.fromkeys(pitches[:3]):
                add("guitar", tick, pitch, bar - 120, 60)
            add("bass", tick, bass, bar, 75)
        else:
            for position in range(4):
                add(
                    "guitar",
                    tick + position * beat_tick,
                    pitches[position % len(pitches)],
                    beat_tick - 60,
                    65,
                )
            for position in (0, 2):
                add("bass", tick + position * beat_tick, bass, beat_tick * 2, 78)
        for position in (0, 2):
            add("drums", tick + position * beat_tick, 36, 120, 88)
        for position in (1, 3):
            add("drums", tick + position * beat_tick, 38, 120, 75)
        if density != "sparse":
            for position in range(8 if density == "driving" else 4):
                add(
                    "drums",
                    tick + position * (beat_tick // 2 if density == "driving" else beat_tick),
                    42,
                    90,
                    55,
                )
    # All accompaniment is after each family's exact prefix window; nothing is filtered.
    tracks = [
        {
            "family": family,
            "program": next(i.program for i in plan.instruments if i.family == family),
            "bank_msb": next(i.bank_msb for i in plan.instruments if i.family == family),
            "volume": next(i.volume for i in plan.instruments if i.family == family),
            "notes": notes[family],
        }
        for family in FAMILIES
    ]
    rendered = {
        "title": plan.title,
        "bpm": plan.bpm,
        "key": plan.key,
        "style_summary": plan.style_summary,
        "duration_seconds": duration_seconds,
        "sections": sections,
        "chords": chords,
        "tracks": tracks,
        "lyrics_alignment": alignment,
    }
    return rendered, plan, density
