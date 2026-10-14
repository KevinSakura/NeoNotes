"""Full-song inputs derived from a pinned, reviewed short-song revision."""

from .composition import input_context
from .lyric_composition import lyric_context
from .model_settings import ModelError
from .models import FullSongSource, Inputs, SongState


def motif_pitches(state: SongState):
    lead = next(
        (track for track in state.score.tracks if track.id == state.composition.melody_track_id),
        None,
    )
    if lead is None or lead.family != "keyboard" or not lead.notes:
        raise ModelError("审核短曲缺少有效的键盘主旋律参考，请恢复或重新生成。", code="full_source")
    by_id = {note.id: note for note in lead.notes}
    sung = [by_id[nid] for unit in state.composition.lyrics_alignment for nid in unit.note_ids]
    notes = sung or sorted(lead.notes, key=lambda note: note.start_tick)
    return [note.pitch for note in notes[:8]]


def reviewed_reference(state: SongState):
    composition = state.composition
    if not composition or composition.scope != "short":
        raise ModelError("整曲必须从已审核的60秒短曲开始。", code="full_source")
    return {
        "title": composition.title,
        "key": composition.key,
        "style_summary": composition.style_summary,
        "motif_pitches": motif_pitches(state),
        "sections": [section.model_dump() for section in composition.sections],
        "chords": [chord.model_dump() for chord in composition.chords],
        "timing_unit": "ticks_at_480_ppq",
    }


def full_inputs(state: SongState):
    data = state.inputs.model_dump()
    data["selected_lines"] = [
        index for index, line in enumerate(state.inputs.lyrics_text.split("\n")) if line.strip()
    ]
    if len(data["selected_lines"]) > 64:
        raise ModelError("整曲歌词暂限64个非空行，请整理歌词后重新审核短曲。", code="lyrics_size")
    return state.model_copy(update={"inputs": Inputs.model_validate(data)}, deep=True)


def full_context(short: SongState, revision_id: str, review_id: str, *, flexible_lyrics=False):
    source = full_inputs(short)
    if source.inputs.selected_lines and flexible_lyrics:
        source, context = lyric_context(source, "full")
    else:
        context = input_context(source, "full")
    reference = reviewed_reference(short)
    context.update(
        full_source=FullSongSource(
            revision_id=revision_id, review_id=review_id, motif_pitches=reference["motif_pitches"]
        ).model_dump(),
        approved_short_song=reference,
        lyric_scope="all_nonempty_lines",
        approach="rearrange_from_reviewed_short",
    )
    return source, context
