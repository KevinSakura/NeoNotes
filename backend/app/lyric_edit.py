"""Explicit edits of existing lyric references; no inference or file mutation."""

from .composition import lyric_units, monophonic, validate_preservation
from .model_settings import ModelError
from .models import SongState
from .song_metadata import refresh_composition_metadata


def bind_notes(state: SongState, line_index: int, unit_index: int, note_ids: list[str]):
    composition = state.composition
    if not composition:
        raise ModelError("请先采用歌词候选，再修复对应。", code="alignment")
    expected = [
        (line, index, word)
        for line in state.inputs.selected_lines
        for index, word in enumerate(lyric_units(state.inputs.lyrics_text.split("\n")[line]))
    ]
    units = composition.lyrics_alignment
    if expected != [(unit.line_index, unit.unit_index, unit.text) for unit in units]:
        raise ModelError("歌词选段或字词已改变，请重新生成，不能用绑定替换原词。", code="alignment")
    position = next(
        (
            i
            for i, unit in enumerate(units)
            if (unit.line_index, unit.unit_index) == (line_index, unit_index)
        ),
        None,
    )
    if position is None:
        raise ModelError("所选字词不存在，请重新选择。", code="alignment_unit")
    track = next(
        (track for track in state.score.tracks if track.id == composition.melody_track_id), None
    )
    if not track or track.family != "keyboard" or not monophonic(track.notes):
        raise ModelError(
            "请先恢复键盘主旋律的单旋律；不能绑定鼓或其他声部。", code="vocal_polyphony"
        )
    by_id = {note.id: note for note in track.notes}
    if any(note_id not in by_id for note_id in note_ids):
        raise ModelError(
            "所选音符缺失或属于其他声部，请选择当前主旋律音符。", code="alignment_missing"
        )
    assigned = {
        note_id for i, unit in enumerate(units) if i != position for note_id in unit.note_ids
    }
    if len(set(note_ids)) != len(note_ids) or assigned.intersection(note_ids):
        raise ModelError(
            "所选音符已用于其他字词或重复选择；每个音符只能对应一个字词。",
            code="alignment_duplicate",
        )
    # Ignore other missing references so several broken words can be repaired one by one.
    # Check the target against every still-existing neighbour, then report remaining errors.
    preceding = [
        (unit, by_id[nid]) for unit in units[:position] for nid in unit.note_ids if nid in by_id
    ]
    following = [
        (unit, by_id[nid])
        for unit in units[position + 1 :]
        for nid in unit.note_ids
        if nid in by_id
    ]
    target = units[position]
    chain = preceding + [(target, by_id[nid]) for nid in note_ids] + following
    target_notes = [by_id[nid] for nid in note_ids]
    for note in target_notes:
        duration = state.score.seconds_at(
            note.start_tick + note.duration_tick
        ) - state.score.seconds_at(note.start_tick)
        if not 48 <= note.pitch <= 84 or not 0.12 <= duration <= 5.001:
            raise ModelError(
                "试唱音符须在C3–C6之间，时长0.12–5秒；请先调整所选音符。", code="vocal_range"
            )
    for (first_unit, first), (second_unit, second) in zip(chain, chain[1:], strict=False):
        if first_unit is not target and second_unit is not target:
            continue
        if first.start_tick + first.duration_tick > second.start_tick:
            raise ModelError(
                "所选音符与前后字词错序或重叠，请按歌曲时间选择。", code="alignment_order"
            )
        if (
            first_unit.line_index != second_unit.line_index
            and state.score.seconds_at(second.start_tick)
            - state.score.seconds_at(first.start_tick + first.duration_tick)
            < 0.149
        ):
            raise ModelError(
                "这次绑定没有保留行间0.15秒换气位置，请调整音符或选择其他音符。", code="breath"
            )
    validate_preservation(state)
    result = state.model_copy(deep=True)
    result.composition.lyrics_alignment[position].note_ids = note_ids.copy()
    return refresh_composition_metadata(result)
