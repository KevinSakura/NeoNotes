"""Versioned, explicitly numbered phrase contract. No lyric or index repair."""

import hashlib
import json
import math
import re
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from .composition import (
    CHANNELS,
    FAMILIES,
    NAMES,
    PROGRAMS,
    reference_events,
    validate_alignment,
    validate_preservation,
    validate_score_content,
)
from .lyric_composition import LyricInstrument, lyric_context
from .model_settings import ModelError
from .models import (
    AutoRegion,
    Composition,
    LyricsIdentity,
    Model,
    Note,
    NumberedLyric,
    PhraseLayout,
    PhraseMetadata,
    ReferenceLock,
    Score,
    SongChord,
    SongSection,
    SongState,
    Tempo,
    Track,
)
from .vocal_score import fingerprint

CONTRACT = "lyric-phrases-v2"
SLOT = 120
BAR = 1920
TOKENIZER = "neonote-lyric-units-1"
TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[^\W\d_]+(?:['’][^\W\d_]+)*|\d+", re.UNICODE)
Integer = Annotated[int, Field(strict=True, ge=0, le=100000)]
Pitch = Annotated[int, Field(strict=True, ge=48, le=84)]
SlotDuration = Annotated[int, Field(strict=True, ge=1, le=16)]
Uid = Annotated[int, Field(strict=True, ge=0, le=1023)]


def identity(text):
    if sum(bool(line.strip()) for line in text.split("\n")) > 64:
        raise ModelError("全文最多64个非空歌词行。", code="phrase_lyrics")
    units, offset = [], 0
    for line_index, line in enumerate(text.split("\n")):
        for unit_index, match in enumerate(TOKEN_PATTERN.finditer(line)):
            if len(units) >= 1024 or len(match.group()) > 100:
                raise ModelError("全文最多1024字词，单个字词最多100字符。", code="phrase_lyrics")
            units.append(NumberedLyric(
                uid=len(units), line_index=line_index, unit_index=unit_index,
                text=match.group(), source_span=(offset + match.start(), offset + match.end()),
            ))
        offset += len(line) + 1
    if not units or len(units) > 1024:
        raise ModelError("乐句谱曲需要1–1024个字词。", code="phrase_lyrics")
    return LyricsIdentity(lyrics_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(), units=units)


def phrase_context(state, operation="generate", instruction=""):
    source, context = lyric_context(state, operation, instruction)
    numbered = identity(source.inputs.lyrics_text)
    context.update(contract=CONTRACT, lyrics_identity=numbered.model_dump(mode="json"),
                   slot_ticks=SLOT, compiler_version="neonote-phrases-2.0",
                   arrangement_policy="explicit_uid_groups_and_phrase_containers")
    # The actual saved melody, not its initial plan, is the modification reference.
    if operation == "modify" and source.composition:
        by_id = {n.id: n for t in source.score.tracks for n in t.notes}
        context["current_score"] = {
            "tempo_map": [t.model_dump() for t in source.score.tempo_map],
            "end_tick": source.score.end_tick,
            "groups": [{"line_index": u.line_index, "unit_index": u.unit_index,
                        "notes": [[by_id[n].pitch, by_id[n].start_tick, by_id[n].duration_tick]
                                  for n in u.note_ids if n in by_id]}
                       for u in source.composition.lyrics_alignment],
            "harmony": [c.model_dump() for c in source.composition.chords],
            "phrase_layout": [p.model_dump() for p in source.composition.v2.phrase_layout]
            if source.composition.v2 else None,
            "actual_tracks": [t.model_dump() for t in source.score.tracks]
            if source.composition.v2 and source.composition.v2.edit_chain else None,
        }
    return source, context


class Section(Model):
    name: str = Field(min_length=1, max_length=60)
    start_slot: Integer
    length_slots: Annotated[int, Field(strict=True, ge=1, le=6400)]


class Harmony(Model):
    start_slot: Integer
    length_slots: Annotated[int, Field(strict=True, ge=1, le=6400)]
    pitches: list[Pitch] = Field(min_length=3, max_length=6)
    label: str = Field(default="和弦", min_length=1, max_length=40)


class Group(Model):
    uid: Uid
    notes: list[tuple[Pitch, SlotDuration]] = Field(min_length=1, max_length=12)
    rest_after_slots: Annotated[int, Field(strict=True, ge=0, le=6400)] = 0
    stress: Literal["none", "secondary", "primary"] = "none"


class Container(Model):
    phrase_id: str = Field(min_length=1, max_length=64, pattern=r"^[\w-]+$")
    line_index: Integer
    start_slot: Integer
    pickup_slots: Annotated[int, Field(strict=True, ge=0, le=15)] = 0
    length_slots: Annotated[int, Field(strict=True, ge=16, le=6400)]


class MusicPhrase(Container):
    word_groups: list[list[Uid]] = Field(min_length=1, max_length=64)
    groups: list[Group] = Field(min_length=1, max_length=64)


class Header(Model):
    bpm: Annotated[int, Field(strict=True, ge=60, le=160)]
    key: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=100)
    style_summary: str = Field(min_length=1, max_length=1500)
    slot_ticks: Literal[120] = SLOT
    total_slots: Annotated[int, Field(strict=True, ge=32, le=6400)]
    accompaniment: Literal["sparse", "steady", "driving"] = "steady"
    instruments: list[LyricInstrument] = Field(min_length=4, max_length=4)
    sections: list[Section] = Field(min_length=2, max_length=24)
    harmony: list[Harmony] = Field(min_length=1, max_length=256)


class PhrasePlan(Header):
    format: Literal["lyric-phrases-v2"]
    phrases: list[MusicPhrase] = Field(min_length=1, max_length=256)


class PlannedPhrase(Container):
    uids: list[Uid] = Field(min_length=1, max_length=64)
    first_pitch: Pitch
    last_pitch: Pitch
    motif_id: str = Field(min_length=1, max_length=64, pattern=r"^[\w-]+$")


class Motif(Model):
    motif_id: str = Field(min_length=1, max_length=64, pattern=r"^[\w-]+$")
    intervals: list[Annotated[int, Field(strict=True, ge=-24, le=24)]] = Field(min_length=1, max_length=16)
    rhythm_slots: list[SlotDuration] = Field(min_length=1, max_length=16)


class GlobalPlan(Header):
    format: Literal["lyric-phrases-plan-v2"]
    motifs: list[Motif] = Field(min_length=1, max_length=16)
    phrases: list[PlannedPhrase] = Field(min_length=1, max_length=256)


class Block(Model):
    format: Literal["lyric-phrases-block-v2"]
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    block_index: Annotated[int, Field(strict=True, ge=0, le=15)]
    phrases: list[MusicPhrase] = Field(min_length=1, max_length=16)


def parse(model, raw):
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        places = [".".join(map(str, e["loc"])) for e in error.errors(include_input=False)[:3]]
        raise ModelError("乐句合同结构不合法：" + "、".join(places), code="phrase_schema") from None


def fail(message, code="phrase_plan"):
    raise ModelError(message, code=code)


def containers(plan, numbered, context):
    if plan.bpm % 4 or plan.bpm not in context.get("feasible_bpms", [plan.bpm]):
        fail("乐句速度不满足4倍数或原演奏约束。", "phrase_tempo")
    if sorted(i.family for i in plan.instruments) != sorted(FAMILIES):
        fail("乐句合同需要四个不同声部。")
    if plan.total_slots % 16:
        fail("曲末须处于完整4/4小节边界。")
    for segments in (plan.sections, plan.harmony):
        end = 0
        for s in segments:
            if s.start_slot != end or s.start_slot % 16 or s.length_slots % 16:
                fail("段落与和声须按整小节连续覆盖，不能有空洞。")
            end += s.length_slots
        if end != plan.total_slots:
            fail("段落与和声没有覆盖曲末。")
    expected, actual, ids = list(range(len(numbered.units))), [], set()
    previous_container_end, previous_sung_end = 0, 0
    for p in plan.phrases:
        uids = p.uids if isinstance(p, PlannedPhrase) else [g.uid for g in p.groups]
        if p.phrase_id in ids or p.start_slot % 16 or p.length_slots % 16:
            fail("乐句编号重复或容器未对齐小节。")
        ids.add(p.phrase_id)
        if not uids or len(uids) > 64 or any(u >= len(numbered.units) for u in uids):
            fail("乐句含有不存在的uid或超出64字词。", "phrase_uid")
        if uids != list(range(uids[0], uids[-1] + 1)):
            fail("同一乐句的uid必须连续且保持原顺序。", "phrase_uid")
        if any(numbered.units[u].line_index != p.line_index for u in uids):
            fail(f"乐句{p.phrase_id}跨越或改变了原歌词行。", "phrase_line")
        start = p.start_slot - p.pickup_slots
        if start < 0 or p.start_slot < previous_container_end or p.start_slot + p.length_slots > plan.total_slots:
            fail("乐句时间容器重叠、倒序或越过曲末。", "phrase_time")
        if actual and start < previous_sung_end + 4:
            fail("乐句之间必须保留至少一拍换气。", "phrase_breath")
        if isinstance(p, MusicPhrase):
            if [u for w in p.word_groups for u in w] != uids or any(not w for w in p.word_groups):
                fail(f"乐句{p.phrase_id}的词组没有完整、顺序覆盖全部uid。", "phrase_words")
            consumed = sum(sum(d for _, d in g.notes) + g.rest_after_slots for g in p.groups)
            if consumed != p.length_slots + p.pickup_slots:
                fail(f"乐句{p.phrase_id}音符与休止总长不等于时间容器。", "phrase_time")
            if p.groups[-1].rest_after_slots < 4:
                fail(f"乐句{p.phrase_id}末尾不足一拍换气。", "phrase_breath")
            previous_sung_end = p.start_slot + p.length_slots - p.groups[-1].rest_after_slots
        else:
            previous_sung_end = p.start_slot + p.length_slots - 4
        actual.extend(uids)
        previous_container_end = p.start_slot + p.length_slots
    if actual != expected:
        fail("uid必须覆盖全文各一次，不能缺失、重复、补词或改变顺序。", "phrase_coverage")


def accompaniment_notes(chords, start, end, density):
    """Stable published arrangement rules. Shared with the restricted ripple editor."""
    notes = {f: [] for f in ("guitar", "bass", "drums")}
    for tick in range(start, end, BAR):
        chord = next(c for c in chords if c.start_tick <= tick < c.end_tick)
        pitches, bass = chord.pitches, 36 + chord.pitches[0] % 12
        pattern = [(0, p, BAR - 120, 60) for p in dict.fromkeys(pitches[:3])] if density == "sparse" else [
            (i * 480, pitches[i % len(pitches)], 420, 65) for i in range(4)]
        for offset, pitch, duration, velocity in pattern:
            notes["guitar"].append(Note(pitch=pitch, start_tick=tick + offset, duration_tick=duration, velocity=velocity))
        for i in ((0,) if density == "sparse" else (0, 2)):
            notes["bass"].append(Note(pitch=bass, start_tick=tick + i * 480,
                                      duration_tick=BAR if density == "sparse" else 960, velocity=78))
        for i, pitch in ((0, 36), (1, 38), (2, 36), (3, 38)):
            notes["drums"].append(Note(pitch=pitch, start_tick=tick + i * 480, duration_tick=120, velocity=78))
        if density != "sparse":
            for i in range(8 if density == "driving" else 4):
                notes["drums"].append(Note(pitch=42, start_tick=tick + i * (240 if density == "driving" else 480),
                                           duration_tick=90, velocity=55))
    return notes


def region_hash(score, start, end):
    return fingerprint([
        {"family": t.family, "instrument": [t.program, t.bank_msb, t.bank_lsb],
         "notes": [{**n.model_dump(), "start_tick": n.start_tick - start}
                   for n in sorted(t.notes, key=lambda n: (n.start_tick, n.id))
                   if start <= n.start_tick < end]}
        for t in score.tracks if t.family in ("guitar", "bass", "drums")
    ])


def compile_phrases(raw, source, job_id, mock, context):
    plan = parse(PhrasePlan, raw)
    numbered = identity(source.inputs.lyrics_text)
    if context["lyrics_identity"] != numbered.model_dump(mode="json"):
        fail("歌词快照或分词版本已改变。", "phrase_identity")
    containers(plan, numbered, context)
    tempo = round(60_000_000 / plan.bpm)
    prefixes = {f: reference_events(source, f, tempo) for f in FAMILIES}
    prefix_end = math.ceil(max((n.start_tick + n.duration_tick for _, ns in prefixes.values()
                                for n in ns), default=0) / BAR) * BAR
    if (plan.phrases[0].start_slot - plan.phrases[0].pickup_slots) * SLOT < prefix_end:
        fail("歌词侵入原演奏保留前缀。", "phrase_prefix")
    tracks = []
    for f in FAMILIES:
        recorded, notes = prefixes[f]
        i = next(i for i in plan.instruments if i.family == f)
        original = next((t for t in source.score.tracks if t.family == f), None)
        t = Track(name=NAMES[f], family=f, channel=CHANNELS[f], is_drum=f == "drums",
                  program=recorded[0].program if recorded else i.program,
                  bank_msb=recorded[0].bank_msb if recorded else i.bank_msb,
                  bank_lsb=recorded[0].bank_lsb if recorded else 0, volume=i.volume, notes=list(notes))
        if context["operation"] == "modify" and original:
            t.id, t.channel, t.mute, t.solo = original.id, original.channel, original.mute, original.solo
        tracks.append(t)
    lead, alignment, layouts = tracks[0], [], []
    for p in plan.phrases:
        cursor, uids = (p.start_slot - p.pickup_slots) * SLOT, []
        last_end = cursor
        for group in p.groups:
            unit = numbered.units[group.uid]
            location = f"歌词第{unit.line_index + 1}行第{unit.unit_index + 1}字词“{unit.text}”"
            ids = []
            for pitch, duration in group.notes:
                seconds = duration * SLOT * tempo / 480_000_000
                if not 0.12 <= seconds <= 5.001:
                    fail(location + "的音符时长超出0.12–5秒。", "phrase_note_duration")
                note = Note(pitch=pitch, start_tick=cursor, duration_tick=duration * SLOT,
                            velocity={"none": 82, "secondary": 90, "primary": 100}[group.stress])
                lead.notes.append(note)
                ids.append(note.id)
                cursor += note.duration_tick
            last_end = cursor
            cursor += group.rest_after_slots * SLOT
            from .models import LyricUnit
            alignment.append(LyricUnit(line_index=unit.line_index, unit_index=unit.unit_index,
                                       text=unit.text, note_ids=ids))
            uids.append(group.uid)
        layouts.append(PhraseLayout(phrase_id=p.phrase_id, line_index=p.line_index, uids=uids,
                                    word_groups=p.word_groups, stresses=[g.stress for g in p.groups],
                                    start_tick=p.start_slot * SLOT, end_tick=cursor,
                                    pickup_tick=p.pickup_slots * SLOT, sung_end_tick=last_end))
    end_tick = plan.total_slots * SLOT
    chords = [SongChord(label=c.label, pitches=c.pitches, start_tick=c.start_slot * SLOT,
                        end_tick=(c.start_slot + c.length_slots) * SLOT) for c in plan.harmony]
    # Only explicitly unworded containers get instrumental keyboard notes.
    cursor = prefix_end
    for left, right in [(p.start_tick - p.pickup_tick, p.end_tick) for p in layouts] + [(end_tick, end_tick)]:
        if left - cursor >= BAR:
            for tick in range(cursor, left, 480):
                chord = next(c for c in chords if c.start_tick <= tick < c.end_tick)
                lead.notes.append(Note(pitch=chord.pitches[0], start_tick=tick,
                                       duration_tick=min(480, left - tick), velocity=72))
        cursor = max(cursor, right)
    total_melody = sum(len(t.notes) for t in tracks)
    density = plan.accompaniment
    for choice in [density, "steady", "sparse"]:
        arranged = accompaniment_notes(chords, 0, end_tick, choice)
        arranged = {f: [n for n in ns if n.start_tick >= max(
            (a.start_tick + a.duration_tick for a in prefixes[f][1]), default=0)]
                    for f, ns in arranged.items()}
        if total_melody + sum(map(len, arranged.values())) <= 5000:
            density = choice
            break
    else:
        fail("主旋律与最低密度伴奏仍超过5000音符，拒绝全文候选。", "phrase_note_budget")
    for t in tracks:
        if t.family in arranged:
            t.notes.extend(arranged[t.family])
        t.notes.sort(key=lambda n: (n.start_tick, n.id))
    try:
        score = Score(end_tick=end_tick, tempo_map=[Tempo(microseconds_per_beat=tempo)], tracks=tracks)
    except ValidationError:
        fail("乐句编配超出音符/时间/声部边界。", "phrase_score")
    locked = [f for f, (_, ns) in prefixes.items() if ns]
    metadata = PhraseMetadata(
        lyrics_identity=numbered, phrase_layout=layouts, accompaniment=density, prefix_end_tick=prefix_end,
        auto_regions=[AutoRegion(phrase_id=p.phrase_id, start_tick=p.start_tick, end_tick=p.end_tick,
                                 accompaniment_hash=region_hash(score, p.start_tick, p.end_tick)) for p in layouts],
    )
    composition = Composition(job_id=job_id, mock=mock, title=plan.title, key=plan.key,
                              style_summary=plan.style_summary, scope="lyrics", arrangement=CONTRACT,
                              target_seconds=score.seconds_at(end_tick), melody_track_id=lead.id,
                              sections=[SongSection(name=s.name, start_tick=s.start_slot * SLOT,
                                                    end_tick=(s.start_slot + s.length_slots) * SLOT)
                                        for s in plan.sections],
                              chords=chords, lyrics_alignment=alignment, v2=metadata,
                              reference_lock=ReferenceLock(note_count=len(source.inputs.recording), families=locked)
                              if locked else None,
                              checks=["试验乐句合同v2；程序固定uid并同时创建音符与绑定",
                                      "全文、乐句换气、四声部与无词器乐段",
                                      f"实际伴奏密度：{density}；重音标签不代表人声重音验收"])
    state = SongState(schema_version=2, inputs=source.inputs.model_copy(deep=True),
                      score=score, composition=composition)
    validate_score_content(score, composition)
    validate_alignment(state)
    validate_phrases(state)
    validate_preservation(state)
    from .song_metadata import refresh_composition_metadata
    return refresh_composition_metadata(state), plan


def validate_phrases(state):
    c = state.composition
    if not c or c.arrangement != CONTRACT:
        return
    v2 = c.v2
    if not v2 or v2.lyrics_identity != identity(state.inputs.lyrics_text):
        fail("歌词身份/原文偏移或分词版本与保存谱不符。", "phrase_identity")
    if state.inputs.selected_lines != [i for i, line in enumerate(state.inputs.lyrics_text.split("\n")) if line.strip()]:
        fail("v2歌词必须覆盖全文。", "phrase_coverage")
    phrase_ids = [p.phrase_id for p in v2.phrase_layout]
    if len(set(phrase_ids)) != len(phrase_ids) or [r.phrase_id for r in v2.auto_regions] != phrase_ids:
        fail("乐句或编配来源区域编号缺失、重复或错序。", "phrase_layout")
    lead = next(t for t in state.score.tracks if t.id == c.melody_track_id)
    by_id = {n.id: n for n in lead.notes}
    flat = []
    previous_end = None
    for p in v2.phrase_layout:
        if len(p.uids) != len(p.stresses) or any(not w for w in p.word_groups) or [u for w in p.word_groups for u in w] != p.uids:
            fail("保存的词组、重音和uid不一致。", "phrase_words")
        units = [c.lyrics_alignment[u] for u in p.uids if u < len(c.lyrics_alignment)]
        if len(units) != len(p.uids) or any(u.line_index != p.line_index for u in units):
            fail("保存的乐句uid缺失或跨原行。", "phrase_uid")
        notes = [by_id[n] for u in units for n in u.note_ids if n in by_id]
        if len(notes) != sum(len(u.note_ids) for u in units):
            fail("保存的乐句有不存在的音符。", "alignment_missing")
        for u in units:
            group = [by_id[n] for n in u.note_ids]
            if any(a.start_tick + a.duration_tick != b.start_tick for a, b in zip(group, group[1:], strict=False)):
                fail(f"歌词第{u.line_index + 1}行第{u.unit_index + 1}字词同字音符不连续。", "phrase_continuity")
        first, last = notes[0].start_tick, notes[-1].start_tick + notes[-1].duration_tick
        region = next(r for r in v2.auto_regions if r.phrase_id == p.phrase_id)
        if (p.start_tick < p.pickup_tick or p.end_tick > state.score.end_tick or
                (region.start_tick, region.end_tick) != (p.start_tick, p.end_tick)):
            fail("乐句与编配区域时间不一致或越界。", "phrase_layout")
        if first != p.start_tick - p.pickup_tick or last != p.sung_end_tick or last > p.end_tick:
            fail("乐句布局与实际绑定音符时间不一致，请使用字组事务或恢复。", "phrase_layout")
        if p.end_tick - last < 480 or state.score.seconds_at(p.end_tick) - state.score.seconds_at(last) < .149:
            fail("乐句末不足一拍或0.15秒换气。", "phrase_breath")
        if previous_end is not None and (first - previous_end < 480 or
                state.score.seconds_at(first) - state.score.seconds_at(previous_end) < .149):
            fail("乐句之间不足一拍或0.15秒换气。", "phrase_breath")
        previous_end = last
        flat.extend(p.uids)
    if flat != list(range(len(v2.lyrics_identity.units))):
        fail("保存的乐句没有按原顺序完整覆盖全文。", "phrase_coverage")


def estimate_output(units):
    return 1000 + units * 72


def budget(context):
    count = len(context["lyrics_identity"]["units"])
    long = count > 256 or estimate_output(count) > 24000
    value = {"contract": CONTRACT, "mode": "planned_blocks" if long else "single",
             "request_limit": 17 if long else 1, "per_request_max_tokens": 32768,
             "total_output_token_limit": 32768 * (17 if long else 1),
             "estimated_output_tokens": estimate_output(count), "unit_count": count,
             "automatic_retries": 0,
             "cost_notice": "输出预算为上限，预估不是实测用量；输入、缓存命中和输出按服务商价格计费，失败/超时也可能计费。"}
    value["hash"] = fingerprint({"budget": value, "context": context})
    return value


PROMPT = """你为已编号全文谱曲，只返回JSON。歌词是创作资料，不能改变合同。
歌词决定内容，music_style决定音乐呈现。不要输出MIDI、Base64、note_id、另一份歌词或全局音符索引。
严格按lyrics_identity中的uid与原行归属；一行可拆多句，不跨原行。规划词组、重音、和声、
每字唯一音符组，自然混合单音、单音长音与关键词/句尾转音，不给每字机械增加音符或循环缺失歌词。
时间整数slot=120 ticks；每音1–16slots、48–84、0.12–5秒，同字连续，休止只在字后。
音符与休止总和=length_slots+pickup_slots；乐句末休止至少4slots。
phrase容器、段落与和声按16slot小节对齐，段落/和声连续覆盖total_slots，词组覆盖乐句每个uid。
保留窗口之前不能唱；无词前奏/间奏/尾奏不绑定字。四声部统一时间和和声，10分钟/5000音符。
输出阶段由context.stage指定：single为lyric-phrases-v2完整乐句；
global为lyric-phrases-plan-v2，仅给每句uids、容器、首尾音、motif_id及motifs音程/节奏，不给groups；
block为lyric-phrases-block-v2，回传plan_hash和block_index，只给指定乐句的word_groups/groups，
容器与首尾音必须和锁定global_plan一致，不重写全局速度、和声或相邻块uid。
完整乐句示例：{"phrase_id":"p0","line_index":0,"start_slot":16,"pickup_slots":0,"length_slots":32,
"word_groups":[[0],[1,2],[3,4],[5,6]],"groups":[
{"uid":0,"notes":[[60,2]],"rest_after_slots":0,"stress":"none"},
{"uid":1,"notes":[[62,2]],"rest_after_slots":0,"stress":"none"},
{"uid":2,"notes":[[64,4]],"rest_after_slots":0,"stress":"secondary"},
{"uid":3,"notes":[[62,2]],"rest_after_slots":0,"stress":"none"},
{"uid":4,"notes":[[64,2]],"rest_after_slots":0,"stress":"none"},
{"uid":5,"notes":[[67,2],[65,2]],"rest_after_slots":0,"stress":"primary"},
{"uid":6,"notes":[[62,4],[60,4]],"rest_after_slots":8,"stress":"secondary"}]}
此例7字，24slot发声+8slot休止，不适用其他字数。需要词组停顿时可给非末group显式rest，
再精确计算容器；长原行可拆p0/p1，uids接续且每句有4slot以上换气，禁止按固定字数循环旋律。
顶层还需format,bpm,key,title,style_summary,slot_ticks,total_slots,accompaniment,
instruments四类family/program/bank_msb/volume,sections{name,start_slot,length_slots},
harmony{start_slot,length_slots,pitches,label}和phrases。速度60–160且4倍数。"""


def messages(context):
    return [{"role": "system", "content": PROMPT + "\n设计示例的uid仅属于各自示例快照，不得照搬到实际歌词：\n"
             + json.dumps(design_examples(), ensure_ascii=False)},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]


def design_examples():
    """Complete small format examples, not generated music or missing-output repair."""
    from .models import empty_song
    example_source = empty_song()
    example_source.inputs.lyrics_text = "让歌声陪我远行"
    example_source.inputs.selected_lines = [0]
    _, example_context = phrase_context(example_source)
    complete = mock_complete(example_context)
    p = complete["phrases"][0]
    p["word_groups"] = [[0], [1, 2], [3, 4], [5, 6]]
    p["groups"] = [
        {"uid": 0, "notes": [[60, 2]], "rest_after_slots": 0, "stress": "none"},
        {"uid": 1, "notes": [[62, 2]], "rest_after_slots": 0, "stress": "none"},
        {"uid": 2, "notes": [[64, 4]], "rest_after_slots": 0, "stress": "secondary"},
        {"uid": 3, "notes": [[62, 2]], "rest_after_slots": 0, "stress": "none"},
        {"uid": 4, "notes": [[64, 2]], "rest_after_slots": 0, "stress": "none"},
        {"uid": 5, "notes": [[67, 2], [65, 2]], "rest_after_slots": 0, "stress": "primary"},
        {"uid": 6, "notes": [[62, 4], [60, 4]], "rest_after_slots": 8, "stress": "secondary"},
    ]
    pause = json.loads(json.dumps(p))
    pause["groups"][2]["rest_after_slots"] = 2
    pause["groups"][-1]["rest_after_slots"] = 6
    long_text = "当晚风穿过安静的街巷我仍记得你微笑的模样"
    long_source = empty_song()
    long_source.inputs.lyrics_text, long_source.inputs.selected_lines = long_text, [0]
    _, long_context = phrase_context(long_source)
    long_plan = mock_global(long_context)
    split = long_text.index("我")
    uid_sets = [list(range(split)), list(range(split, len(long_text)))]
    cursor = 16
    fragments = []
    for i, uids in enumerate(uid_sets):
        length = math.ceil((len(uids) * 2 + 8) / 16) * 16
        fragments.append(mock_phrase({"uids": uids, "first_pitch": 60, "last_pitch": 60,
                                      "phrase_id": "p" + str(i), "line_index": 0,
                                      "start_slot": cursor, "pickup_slots": 0, "length_slots": length}))
        cursor += length
    long_complete = {**{k: v for k, v in long_plan.items() if k not in ("format", "motifs", "phrases")},
                     "format": CONTRACT, "total_slots": cursor + 16, "phrases": fragments,
                     "sections": [{"name": "前奏", "start_slot": 0, "length_slots": 16},
                                  {"name": "主歌", "start_slot": 16, "length_slots": cursor - 16},
                                  {"name": "尾奏", "start_slot": cursor, "length_slots": 16}],
                     "harmony": [{"start_slot": 0, "length_slots": cursor + 16, "pitches": [60, 64, 67]}]}
    return {"complete": {"lyrics_identity": example_context["lyrics_identity"], "output": complete},
            "word_group_pause": {"phrase": pause, "meaning": "歌声之后停半拍，容器总长仍32slot"},
            "split_long_line": {"lyrics_identity": long_context["lyrics_identity"], "output": long_complete}}


def mock_global(context):
    """Fixed offline scheduling only. No claim about language or music expression."""
    units = context["lyrics_identity"]["units"]
    bpm = (context.get("feasible_bpms") or [96])[0]
    tempo = round(60_000_000 / bpm)
    prefix = max((round((n["start_seconds"] + n["duration_seconds"]) * 1_000_000 / tempo * 480)
                  for n in context.get("reference_notes", []) if context.get("melody_mode") == "preserve"), default=0)
    cursor = math.ceil(prefix / BAR) * 16 + 16
    phrases = []
    for line in context["selected_lyrics"]:
        row = [u["uid"] for u in units if u["line_index"] == line["line_index"]]
        for pos in range(0, len(row), 16):
            uids = row[pos:pos + 16]
            length = math.ceil((len(uids) * 2 + 8) / 16) * 16
            phrases.append({"phrase_id": "p" + str(len(phrases)), "line_index": line["line_index"],
                            "start_slot": cursor, "length_slots": length, "pickup_slots": 0,
                            "uids": uids, "first_pitch": 60, "last_pitch": 60, "motif_id": "offline"})
            cursor += length
    return {"format": "lyric-phrases-plan-v2", "bpm": bpm, "key": "C major", "title": "模拟 · 乐句谱曲",
            "style_summary": "模拟固定材料，仅用于合同、事务和任务测试。",
            "slot_ticks": SLOT, "total_slots": cursor + 16, "accompaniment": "steady",
            "instruments": [{"family": f, "program": PROGRAMS[f]} for f in FAMILIES],
            "sections": [{"name": "前奏", "start_slot": 0, "length_slots": phrases[0]["start_slot"]},
                         {"name": "乐句", "start_slot": phrases[0]["start_slot"],
                          "length_slots": cursor - phrases[0]["start_slot"]},
                         {"name": "尾奏", "start_slot": cursor, "length_slots": 16}],
            "harmony": [{"start_slot": t, "length_slots": 16, "pitches": [60, 64, 67]} for t in range(0, cursor + 16, 16)],
            "motifs": [{"motif_id": "offline", "intervals": [0, 2, 2, -4], "rhythm_slots": [2, 2, 4]}],
            "phrases": phrases}


def mock_phrase(p):
    uids = p["uids"]
    groups = []
    for i, uid in enumerate(uids):
        notes = [[60 + (i % 3) * 2, 2]]
        if i == len(uids) - 1:
            notes = [[64, 2], [60, 2]] if len(uids) > 1 else [[60, 4]]
        groups.append({"uid": uid, "notes": notes, "rest_after_slots": 0, "stress": "secondary" if i == 2 else "none"})
    groups[0]["notes"][0][0] = p["first_pitch"]
    groups[-1]["notes"][-1][0] = p["last_pitch"]
    groups[-1]["rest_after_slots"] = p["length_slots"] + p["pickup_slots"] - sum(d for g in groups for _, d in g["notes"])
    return {**{k: p[k] for k in ("phrase_id", "line_index", "start_slot", "pickup_slots", "length_slots")},
            "word_groups": [[u] for u in uids], "groups": groups}


def mock_complete(context):
    plan = mock_global(context)
    return {**{k: v for k, v in plan.items() if k not in ("format", "motifs", "phrases")},
            "format": CONTRACT, "phrases": [mock_phrase(p) for p in plan["phrases"]]}


def chunks(plan):
    blocks, current, count = [], [], 0
    for p in plan.phrases:
        if current and (len(current) == 16 or count + len(p.uids) > 256 or estimate_output(count + len(p.uids)) > 24000):
            blocks.append(current)
            current, count = [], 0
        current.append(p)
        count += len(p.uids)
    if current:
        blocks.append(current)
    if len(blocks) > 16:
        fail("全局规划需要超过16块，请重新规划；没有分发分块请求。", "phrase_block_budget")
    return blocks


def validate_global(raw, context):
    plan = parse(GlobalPlan, raw)
    containers(plan, LyricsIdentity.model_validate(context["lyrics_identity"]), context)
    tempo = round(60_000_000 / plan.bpm)
    if plan.total_slots * SLOT * tempo / 480_000_000 > 600:
        fail("全局规划超过10分钟，未分发分块请求。", "phrase_duration")
    prefix = max((round((n["start_seconds"] + n["duration_seconds"]) * 1_000_000 / tempo * 480)
                  for n in context.get("reference_notes", []) if context.get("melody_mode") == "preserve"), default=0)
    if (plan.phrases[0].start_slot - plan.phrases[0].pickup_slots) * SLOT < math.ceil(prefix / BAR) * BAR:
        fail("全局乐句侵入保留前缀，未分发分块请求。", "phrase_prefix")
    motifs = [m.motif_id for m in plan.motifs]
    if len(set(motifs)) != len(motifs) or any(p.motif_id not in motifs for p in plan.phrases):
        fail("全局主题动机缺失或编号重复。", "phrase_motif")
    chunks(plan)
    return plan


def validate_block(raw, plan, expected, index):
    block = parse(Block, raw)
    if block.plan_hash != fingerprint(plan.model_dump()) or block.block_index != index or len(block.phrases) != len(expected):
        fail("分块的规划哈希、编号或乐句数量不符。", "phrase_block_identity")
    for p, assigned in zip(block.phrases, expected, strict=True):
        if any(not .12 <= d * SLOT * round(60_000_000 / plan.bpm) / 480_000_000 <= 5.001
               for g in p.groups for _, d in g.notes):
            fail("分块音符时长超出0.12–5秒，停止后续分发。", "phrase_note_duration")
        if any(getattr(p, k) != getattr(assigned, k) for k in ("phrase_id", "line_index", "start_slot", "pickup_slots", "length_slots")):
            fail("分块修改了锁定的乐句容器。", "phrase_block_container")
        if [g.uid for g in p.groups] != assigned.uids:
            fail("分块缺字、重复或跨块输出。", "phrase_block_coverage")
        if p.groups[0].notes[0][0] != assigned.first_pitch or p.groups[-1].notes[-1][0] != assigned.last_pitch:
            fail("分块首尾音违反全局边界意图。", "phrase_block_boundary")
        if sum(sum(d for _, d in g.notes) + g.rest_after_slots for g in p.groups) != p.length_slots + p.pickup_slots:
            fail("分块乐句时长不符合时间容器。", "phrase_time")
        if p.groups[-1].rest_after_slots < 4 or [u for w in p.word_groups for u in w] != assigned.uids:
            fail("分块的词组覆盖或乐句换气不合法。", "phrase_breath")
    return block


def assemble(plan, blocks):
    return {**{k: v for k, v in plan.model_dump().items() if k not in ("format", "motifs", "phrases")},
            "format": CONTRACT, "phrases": [p.model_dump() for b in blocks for p in b.phrases]}
