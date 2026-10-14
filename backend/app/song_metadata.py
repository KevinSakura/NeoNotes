"""Refresh derived AI MIDI markers for a new save; never change lyric references."""

from .models import MidiMeta, SongState


def refresh_composition_metadata(state: SongState):
    if not state.composition:
        return state
    state = state.model_copy(deep=True)
    composition, score = state.composition, state.score
    lead = next((track for track in score.tracks if track.id == composition.melody_track_id), None)
    by_id = {note.id: note for note in lead.notes} if lead else {}
    metadata = [meta for meta in score.midi_meta if meta.type not in ("marker", "lyrics")]
    metadata.extend(
        MidiMeta(tick=section.start_tick, type="marker", text=section.name)
        for section in composition.sections
        if section.start_tick <= score.end_tick
    )
    for unit in composition.lyrics_alignment:
        note = by_id.get(unit.note_ids[0])
        if note:
            metadata.append(
                MidiMeta(tick=note.start_tick, type="lyrics", text=unit.text, track_id=lead.id)
            )
        # A deleted first note keeps the invalid unit in composition for diagnostics.
        # Do not export a stale text event or invent a replacement musical reference.
    score.midi_meta = sorted(metadata, key=lambda meta: meta.tick)
    return SongState.model_validate(state.model_dump())
