"""Current-score checks and factual lyric practice hints, never a quality score."""

from .composition import validate_alignment, validate_preservation, validate_score_content
from .model_settings import ModelError
from .models import SongState
from .resources import AudioError


def validate_planning(state: SongState):
    composition = state.composition
    if not composition:
        raise ModelError("请先采用一个候选。", code="review_source")
    if len(composition.sections) < 2:
        raise ModelError("共同段落不足两段，请重新生成候选。", code="planning")
    for segments in (composition.sections, composition.chords):
        end = 0
        for segment in segments:
            if segment.start_tick != end or segment.end_tick <= segment.start_tick:
                raise ModelError(
                    "段落或和声资料不连续，需重新生成或恢复完整版本。", code="planning"
                )
            end = segment.end_tick
        if end != state.score.end_tick:
            raise ModelError("段落或和声资料不覆盖当前时间轴，请恢复或重新生成。", code="planning")


def lyric_metrics(state: SongState):
    composition = state.composition
    if not composition:
        return []
    track = next(
        (track for track in state.score.tracks if track.id == composition.melody_track_id), None
    )
    if not track:
        return []
    by_id = {note.id: note for note in track.notes}
    rows, previous_end = [], None
    for line_index in state.inputs.selected_lines:
        units = [unit for unit in composition.lyrics_alignment if unit.line_index == line_index]
        notes = [by_id[note_id] for unit in units for note_id in unit.note_ids if note_id in by_id]
        if not notes:
            continue
        notes.sort(key=lambda note: note.start_tick)
        start = state.score.seconds_at(notes[0].start_tick)
        end = max(state.score.seconds_at(note.start_tick + note.duration_tick) for note in notes)
        pitches = [note.pitch for note in notes]
        jump = max((abs(a - b) for a, b in zip(pitches, pitches[1:], strict=False)), default=0)
        warnings = []
        if max(pitches) - min(pitches) > 12:
            warnings.append("这一句跨越超过一个八度，请亲自试唱确认音域。")
        if jump > 7:
            warnings.append("这一句有超过7个半音的跳进，请留意是否容易唱准。")
        rows.append(
            {
                "line_index": line_index,
                "text": state.inputs.lyrics_text.split("\n")[line_index],
                "unit_count": len(units),
                "note_count": len(notes),
                "start_seconds": round(start, 4),
                "end_seconds": round(end, 4),
                "pitch_low": min(pitches),
                "pitch_high": max(pitches),
                "max_jump_semitones": jump,
                "breath_before_seconds": round(start - previous_end, 4)
                if previous_end is not None
                else None,
                "warnings": warnings,
            }
        )
        previous_end = end
    return rows


def phrase_metrics(state):
    c = state.composition
    if not c or not c.v2:
        return []
    lead = next((t for t in state.score.tracks if t.id == c.melody_track_id), None)
    by_id = {n.id: n for n in lead.notes} if lead else {}
    reports = []
    for p in c.v2.phrase_layout:
        groups, warnings = [], []
        for uid, stress in zip(p.uids, p.stresses, strict=False):
            if uid >= len(c.lyrics_alignment):
                warnings.append("有字词编号缺失，请修复乐句资料。")
                continue
            unit = c.lyrics_alignment[uid]
            notes = [by_id[n] for n in unit.note_ids if n in by_id]
            if not notes:
                continue
            duration = sum(state.score.seconds_at(n.start_tick + n.duration_tick)
                           - state.score.seconds_at(n.start_tick) for n in notes)
            if duration > 5:
                warnings.append(f"“{unit.text}”总发声超过5秒，请确认延长是否自然。")
            groups.append({"uid": uid, "text": unit.text, "stress": stress, "note_count": len(notes),
                           "onset_tick": notes[0].start_tick, "bar_beat": (notes[0].start_tick / 480) % 4,
                           "duration_seconds": round(duration, 4),
                           "pitches": [n.pitch for n in notes]})
        duration = state.score.seconds_at(p.sung_end_tick) - state.score.seconds_at(p.start_tick - p.pickup_tick)
        if duration > 12:
            warnings.append("本乐句发声超过12秒，请亲自检查词组停顿和呼吸。")
        reports.append({"phrase_id": p.phrase_id, "line_index": p.line_index, "groups": groups,
                        "start_seconds": state.score.seconds_at(p.start_tick - p.pickup_tick),
                        "sung_end_seconds": state.score.seconds_at(p.sung_end_tick),
                        "tail_rest_seconds": state.score.seconds_at(p.end_tick) - state.score.seconds_at(p.sung_end_tick),
                        "warnings": warnings, "expression_review": "requires_human_listening"})
    return reports


def assess(state: SongState, resource):
    checks = []
    validators = [
        (
            "music",
            "目标时间、四声部与发声内容",
            lambda: validate_score_content(state.score, state.composition),
        ),
        ("planning", "共同段落与和声覆盖", lambda: validate_planning(state)),
        ("lyrics", "所选字词、主旋律与换气对应", lambda: validate_alignment(state)),
        ("preserve", "原演奏保留约束", lambda: validate_preservation(state)),
        ("soundfont", "当前音色库的实际预设", lambda: resource.validate_score(state.score, "full")),
    ]
    for check_id, label, validator in validators:
        try:
            validator()
            message = "通过"
            if check_id == "lyrics" and not state.inputs.selected_lines:
                message = "无所选歌词，无需字词对应"
            if check_id == "preserve" and (
                state.inputs.melody_mode != "preserve" or not state.inputs.recording
            ):
                message = "未要求保留原演奏；自由改编走向由人工对照"
            checks.append(
                {"id": check_id, "label": label, "passed": True, "code": None, "message": message}
            )
        except (ModelError, AudioError) as error:
            checks.append(
                {
                    "id": check_id,
                    "label": label,
                    "passed": False,
                    "code": getattr(error, "code", "audio"),
                    "message": str(error),
                }
            )
    return {
        "schema_version": 1,
        "valid": all(check["passed"] for check in checks),
        "checks": checks,
        "duration_seconds": round(state.score.seconds_at(state.score.end_tick), 4),
        "note_count": sum(len(track.notes) for track in state.score.tracks),
        "lyric_lines": lyric_metrics(state),
        "musical_phrases": phrase_metrics(state),
        "reference_lock": state.composition.reference_lock.model_dump()
        if state.composition and state.composition.reference_lock
        else None,
        "music_quality": "requires_human_review",
        "singability": "requires_human_trial" if state.inputs.selected_lines else "not_applicable",
    }
