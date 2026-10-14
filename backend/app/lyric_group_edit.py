"""Atomic drafts: exact note groups, guarded arrangement ripple and auditable time."""

import hashlib
import math
import struct
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from .composition import validate_alignment, validate_preservation, validate_score_content
from .lyric_phrases import BAR, CONTRACT, accompaniment_notes, fingerprint, region_hash
from .model_settings import ModelError
from .models import Identifier, Model, Note, SongState
from .song_metadata import refresh_composition_metadata
from .storage import now


def normalized(value):
    # IEEE-754 numbers have identical representation in Python and the browser.
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return {"@number": struct.pack(">d", float(value) if value != 0 else 0.0).hex()}
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [normalized(v) for v in value]
    return value


def draft_hash(state):
    return fingerprint(normalized(state.model_dump() if hasattr(state, "model_dump") else state))


def timing_hash(state):
    c = state.composition
    v2 = c.v2.model_dump()
    v2.pop("edit_chain")
    return fingerprint({"end_tick": state.score.end_tick, "tempos": [t.model_dump() for t in state.score.tempo_map],
                        "meters": [t.model_dump() for t in state.score.time_signatures],
                        "target_seconds": c.target_seconds, "sections": [s.model_dump() for s in c.sections],
                        "harmony": [c.model_dump() for c in c.chords], "v2": v2})


class GroupEditRequest(Model):
    base_revision: str = Field(pattern=r"^[a-f0-9]{32}$")
    draft_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    transaction_id: Identifier
    state: SongState
    line_index: Annotated[int, Field(strict=True, ge=0)]
    unit_index: Annotated[int, Field(strict=True, ge=0)]
    action: Literal["add", "simplify", "set"]
    time_mode: Literal["fixed", "ripple"] = "fixed"
    note_index: Annotated[int, Field(strict=True, ge=0, le=11)] = 0
    interval: Annotated[int, Field(strict=True, ge=-12, le=12)] = 2
    keep: Literal["first", "last"] = "first"
    pitches: list[Annotated[int, Field(strict=True, ge=48, le=84)]] | None = Field(default=None, min_length=1, max_length=12)
    weights: list[Annotated[int, Field(strict=True, ge=1, le=100000)]] | None = Field(default=None, min_length=1, max_length=12)
    duration_ticks: Annotated[int, Field(strict=True, ge=1, le=100000)] | None = None
    preview: bool = True


def reject(message, code="lyric_group"):
    raise ModelError(message, 422, code)


def distribute(total, weights):
    denominator = sum(weights)
    parts, remainders = zip(*(divmod(total * w, denominator) for w in weights), strict=True)
    parts = list(parts)
    for i in sorted(range(len(parts)), key=lambda i: (-remainders[i], i))[:total - sum(parts)]:
        parts[i] += 1
    if any(d <= 0 for d in parts):
        reject("总时长太短，无法分配为这些音符。", "lyric_group_duration")
    return parts


def new_note_id(body, index):
    return hashlib.sha256(f"{body.transaction_id}:{body.base_revision}:{body.draft_hash}:{index}".encode()).hexdigest()[:32]


def extend_segments(segments, boundary, delta):
    for s in segments:
        if s.start_tick >= boundary:
            s.start_tick += delta
            s.end_tick += delta
        elif s.end_tick >= boundary:
            s.end_tick += delta


def ripple(state, target_index, delta, transaction_id):
    c, score = state.composition, state.score
    if c.arrangement != CONTRACT:
        reject("顺延只开放给v2自动编配乐句，旧作品可保持总时长编辑。", "lyric_group_ripple")
    v2 = c.v2
    p = next(p for p in v2.phrase_layout if target_index in p.uids)
    start, boundary = p.start_tick, p.end_tick
    if (len(score.tempo_map) != 1 or len(score.time_signatures) != 1
            or score.time_signatures[0].numerator != 4 or score.time_signatures[0].denominator != 4
            or p.pickup_tick or start % BAR or boundary % BAR or start < v2.prefix_end_tick):
        reject("顺延需要固定速度、4/4、无弱起且不与保留前缀相交的v2小节区域。", "lyric_group_ripple")
    region = next((r for r in v2.auto_regions if r.phrase_id == p.phrase_id), None)
    if not region or region_hash(score, start, boundary) != region.accompaniment_hash:
        reject("本句伴奏已有手动修改或来源不符，顺延会覆盖编辑；请保持总时长。", "lyric_group_manual")
    for track in score.tracks:
        if any(n.start_tick < b < n.start_tick + n.duration_tick for n in track.notes for b in (start, boundary)):
            reject("有跨乐句边界持续音，不能使用受限顺延。", "lyric_group_boundary")
    if any(q is not p and q.start_tick >= boundary and q.start_tick - q.pickup_tick < boundary
           for q in v2.phrase_layout):
        reject("后句弱起跨过顺延边界，不能移动这一时间区域。", "lyric_group_boundary")
    lead = next(t for t in score.tracks if t.id == c.melody_track_id)
    bound = {n for u in c.lyrics_alignment for n in u.note_ids}
    if any(start <= n.start_tick < boundary and n.id not in bound for n in lead.notes):
        reject("本句含无词手动键盘音符，不能重新编配覆盖。", "lyric_group_manual")
    by_id = {n.id: n for n in lead.notes}
    for uid in p.uids:
        if uid > target_index:
            for nid in c.lyrics_alignment[uid].note_ids:
                by_id[nid].start_tick += delta
    # Shrinking increases phrase-tail rest; no unexpected shortening of the whole song.
    sung_end = p.sung_end_tick + delta
    end = boundary if boundary - sung_end >= 480 else math.ceil((sung_end + 480) / BAR) * BAR
    shift = end - boundary
    for track in score.tracks:
        for note in track.notes:
            if note.start_tick >= boundary:
                note.start_tick += shift
    extend_segments(c.chords, boundary, shift)
    extend_segments(c.sections, boundary, shift)
    for q in v2.phrase_layout:
        if q is p:
            q.end_tick, q.sung_end_tick = end, sung_end
        elif q.start_tick >= boundary:
            q.start_tick += shift
            q.end_tick += shift
            q.sung_end_tick += shift
    score.end_tick += shift
    for meta in score.midi_meta:
        if meta.tick >= boundary:
            meta.tick += shift
    arranged = accompaniment_notes(c.chords, start, end, v2.accompaniment)
    for track in score.tracks:
        if track.family not in arranged:
            continue
        old = [n for n in track.notes if start <= n.start_tick < boundary]
        # Future notes have already moved. Use the original boundary for the old region.
        keep = [n for n in track.notes if n not in old]
        existing = {(n.pitch, n.start_tick, n.duration_tick, n.velocity): n.id for n in old}
        for note in arranged[track.family]:
            key = (note.pitch, note.start_tick, note.duration_tick, note.velocity)
            note.id = existing.get(key, hashlib.sha256(
                f"{transaction_id}:accompaniment:{track.family}:{key}".encode()).hexdigest()[:32])
        track.notes = keep + arranged[track.family]
    return p, delta, shift


def calculate(body):
    before = body.state
    if draft_hash(before) != body.draft_hash:
        raise ModelError("草稿已变化，请按当前草稿重新预览。", 409, "draft_conflict")
    validate_alignment(before)
    validate_preservation(before)
    c = before.composition
    index = next((i for i, u in enumerate(c.lyrics_alignment)
                  if (u.line_index, u.unit_index) == (body.line_index, body.unit_index)), None)
    if index is None:
        reject("当前草稿没有这个字词。")
    result = before.model_copy(deep=True)
    c, score = result.composition, result.score
    unit = c.lyrics_alignment[index]
    lead = next(t for t in score.tracks if t.id == c.melody_track_id)
    by_id = {n.id: n for n in lead.notes}
    original = [by_id[nid] for nid in unit.note_ids]
    if any(a.start_tick + a.duration_tick != b.start_tick for a, b in zip(original, original[1:], strict=False)):
        reject("这个字的音符不连续，请先明确修复对应或音符位置。", "lyric_group_continuity")
    old_duration = sum(n.duration_tick for n in original)
    total = body.duration_ticks if body.duration_ticks is not None else old_duration
    if body.time_mode == "fixed" and total != old_duration:
        reject("保持总时长只能调整内部音符与比例，不能改变这个字的总长。", "lyric_group_fixed")
    pitches, weights, ids = [n.pitch for n in original], [n.duration_tick for n in original], [n.id for n in original]
    if body.action == "add":
        i = body.note_index
        if i >= len(original) or len(original) >= 12:
            reject("选中音符不存在或这个字已达到12音。")
        pitches.insert(i + 1, pitches[i] + body.interval)
        duration = weights[i]
        weights[i:i + 1] = [duration // 2, duration - duration // 2]
        ids.insert(i + 1, new_note_id(body, i))
    elif body.action == "simplify":
        kept = original[0] if body.keep == "first" else original[-1]
        pitches, weights, ids = [kept.pitch], [1], [kept.id]
    else:
        pitches = body.pitches or pitches
        weights = body.weights or ([n.duration_tick for n in original] if len(pitches) == len(original) else [1] * len(pitches))
        ids = [original[i].id if i < len(original) else new_note_id(body, i) for i in range(len(pitches))]
    if len(pitches) != len(weights) or any(not 48 <= p <= 84 for p in pitches):
        reject("音高或时长比例不合法，旋律须在C3–C6范围。", "lyric_group_pitch")
    if any(w <= 0 for w in weights):
        reject("选中音符太短，无法添加转音。", "lyric_group_duration")
    durations = distribute(total, weights)
    affected_phrase, following_delta, song_shift = None, 0, 0
    # Validate the original arrangement before changing target notes or positions.
    if body.time_mode == "ripple":
        affected_phrase, following_delta, song_shift = ripple(result, index, total - old_duration, body.transaction_id)
    cursor, replacement = original[0].start_tick, []
    for i, (pitch, duration, nid) in enumerate(zip(pitches, durations, ids, strict=True)):
        seconds = score.seconds_at(cursor + duration) - score.seconds_at(cursor)
        if not .12 <= seconds <= 5.001:
            reject(f"这个字第{i + 1}音时长{seconds:.3f}秒，须在0.12–5秒。", "lyric_group_duration")
        replacement.append(Note(id=nid, pitch=pitch, start_tick=cursor, duration_tick=duration,
                                velocity=original[min(i, len(original) - 1)].velocity))
        cursor += duration
    lead.notes = [n for n in lead.notes if n.id not in unit.note_ids] + replacement
    lead.notes.sort(key=lambda n: (n.start_tick, n.id))
    unit.note_ids = ids
    if c.v2:
        c.target_seconds = score.seconds_at(score.end_tick)
        if affected_phrase:
            for region in c.v2.auto_regions:
                p = next(p for p in c.v2.phrase_layout if p.phrase_id == region.phrase_id)
                region.start_tick, region.end_tick = p.start_tick, p.end_tick
                # Only this recompiled region gets a new provenance baseline.
                # Later manual accompaniment stays manual when translated.
                if region.phrase_id == affected_phrase.phrase_id:
                    region.accompaniment_hash = region_hash(score, p.start_tick, p.end_tick)
        c.v2.edit_chain.append(body.transaction_id)
    try:
        result = SongState.model_validate(result.model_dump())
    except ValidationError:
        reject("修改超出10分钟/5000音符或乐谱时间边界，草稿未改变。", "lyric_group_limits")
    result = refresh_composition_metadata(result)
    validate_alignment(result)
    validate_preservation(result)
    validate_score_content(result.score, result.composition)
    impact = {"word": unit.text, "note_count": len(ids), "old_duration_ticks": old_duration,
              "duration_ticks": total, "following_lyrics_delta_ticks": following_delta,
              "later_music_delta_ticks": song_shift, "later_music_bars": song_shift // BAR,
              "total_seconds": result.score.seconds_at(result.score.end_tick),
              "phrase_id": affected_phrase.phrase_id if affected_phrase else None,
              "saved": False}
    return result, impact


def transaction(manager, project_id, body):
    with manager.lock, manager.store.lock:
        project = manager.store.get(project_id)
        manager.store.check_base(project, body.base_revision)
        manager.validate_source(project_id, body.state)
        result, impact = calculate(body)
        if not body.preview:
            root = manager.directory(project_id, body.state.composition.job_id) / "edits"
            path = root / f"{body.transaction_id}.json"
            receipt = {
                "id": body.transaction_id, "job_id": body.state.composition.job_id,
                "base_revision": body.base_revision, "created_at": now(),
                "before_hash": body.draft_hash, "after_hash": draft_hash(result),
                "before_timing": timing_hash(body.state) if body.state.composition.v2 else None,
                "after_timing": timing_hash(result) if result.composition.v2 else None,
                "previous_chain": body.state.composition.v2.edit_chain if body.state.composition.v2 else [],
                "action": body.action, "time_mode": body.time_mode, "impact": impact,
            }
            if path.exists():
                previous = manager.store.read_json(path)
                if any(previous.get(k) != receipt[k] for k in ("before_hash", "after_hash", "action", "time_mode")):
                    raise ModelError("事务编号已用于另一份草稿，请重新预览。", 409, "transaction_conflict")
            else:
                manager.store.write_json(path, receipt)
        return {"state": result.model_dump(), "impact": impact, "saved": False, "preview": body.preview,
                "base_revision": body.base_revision, "before_hash": body.draft_hash}


def validate_edit_history(manager, project_id, state, candidate):
    original, current = timing_hash(candidate), timing_hash(state)
    chain = state.composition.v2.edit_chain
    if len(set(chain)) != len(chain):
        reject("字组编辑来源链重复。", "review_group_source")
    for i, receipt_id in enumerate(chain):
        path = manager.directory(project_id, state.composition.job_id) / "edits" / f"{receipt_id}.json"
        receipt = manager.store.read_json(path)
        if (receipt.get("id") != receipt_id or receipt.get("job_id") != state.composition.job_id
                or receipt.get("previous_chain") != chain[:i] or receipt.get("before_timing") != original):
            reject("字组编辑来源链不连续或与候选不符。", "review_group_source")
        original = receipt["after_timing"]
    if original != current:
        reject("乐句时长/规划未经可验证字组事务修改，请恢复或使用字组编辑。", "review_group_source")
