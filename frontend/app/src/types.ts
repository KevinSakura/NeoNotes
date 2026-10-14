export type Family = 'keyboard' | 'drums' | 'guitar' | 'bass' | 'other';
export interface Note { id: string; pitch: number; velocity: number; start_tick: number; duration_tick: number }
export interface Track {
  id: string; name: string; family: Family; program: number; bank_msb: number; bank_lsb: number;
  channel: number; is_drum: boolean; volume: number; mute: boolean; solo: boolean; notes: Note[];
}
export interface Tempo { tick: number; microseconds_per_beat: number }
export interface Score {
  ppq: number; end_tick: number; tempo_map: Tempo[];
  time_signatures: { tick: number; numerator: number; denominator: number; clocks_per_click: number; notated_32nd_notes_per_beat: number }[];
  tracks: Track[];
  midi_meta: { tick: number; type: string; text: string; key: string; track_id: string | null }[];
}
export interface RecordedNote {
  pitch: number; velocity: number; start_seconds: number; duration_seconds: number;
  track_id: string; family: Family; program: number;
  channel?: number | null; bank_msb?: number; bank_lsb?: number;
}
export interface Inputs {
  theme: string; emotion: string; lyrics_text: string; selected_lines: number[];
  melody_mode: 'extend' | 'preserve'; recording: RecordedNote[];
  files: { id: string; name: string; kind: 'lyrics' | 'midi'; sha256: string }[];
}
export interface Composition {
  job_id: string; mock: boolean; title: string; key: string; style_summary: string;
  sections: { name: string; start_tick: number; end_tick: number }[];
  chords: { label: string; start_tick: number; end_tick: number; pitches: number[] }[];
  lyrics_alignment: { line_index: number; unit_index: number; text: string; note_ids: string[] }[];
  melody_track_id: string; checks: string[];
  reference_lock?: { note_count: number; families: string[]; filtered_model_notes: number } | null;
  scope?: 'short' | 'full' | 'lyrics'; target_seconds?: number; arrangement?: 'events' | 'lyric-motifs-v1' | 'lyric-phrases-v2';
  v2?: {
    compiler_version: string; lyrics_identity: { lyrics_hash: string; tokenizer_version: string;
      units: { uid: number; line_index: number; unit_index: number; text: string; source_span: number[] }[] };
    phrase_layout: { phrase_id: string; line_index: number; uids: number[]; word_groups: number[][]; stresses: string[];
      start_tick: number; end_tick: number; pickup_tick: number; sung_end_tick: number }[];
    auto_regions: { phrase_id: string; start_tick: number; end_tick: number; accompaniment_hash: string }[];
    accompaniment: string; prefix_end_tick: number; edit_chain: string[];
  } | null;
  full_source?: { revision_id: string; review_id: string; motif_pitches: number[] } | null;
}
export interface SongState { schema_version: 1 | 2; inputs: Inputs; score: Score; composition?: Composition | null }
export interface Revision {
  id: string; number: number; label: string; created_at: string; parent_revision: string | null; state: SongState;
}
export interface Project {
  id: string; name: string; created_at: string; updated_at: string; current_revision: string;
  can_undo: boolean; can_redo: boolean; directory: string; revision: Revision;
}
export type ProjectItem = Pick<Project, 'id' | 'name' | 'updated_at' | 'current_revision'>;
export type HistoryItem = Omit<Revision, 'state'>;
export interface GenerationJob {
  id: string; base_revision: string; base_number: number; operation: 'generate' | 'modify' | 'full'; instruction: string;
  scope?: 'short' | 'full' | 'lyrics'; contract?: string; target_seconds?: number | null; result_seconds?: number; max_output_tokens?: number;
  status: 'queued' | 'running' | 'validating' | 'cancelling' | 'cancelled' | 'interrupted' | 'completed' | 'failed'; phase: string; mock: boolean;
  budget?: PhraseBudget; simulated_requests?: number; reserved_attempts?: number; reused_blocks?: number; retry_of?: string | null;
  blocks?: { index: number; status: string; unit_count: number; reused?: boolean }[];
  created_at: string; error: string | null; checks: string[]; request_count: number;
  metadata: { returned_model: string; elapsed_seconds: number | null; usage: Record<string, number> | null } | null;
  diagnostic?: {
    kind: 'line_index' | 'unit_index' | 'note_index'; alignment_index: number; line_index: number; unit_index: number;
    selected_line_indices?: number[]; unit_count?: number; note_index?: number; lead_note_count?: number;
    locked_keyboard_note_count?: number; filtered_keyboard_note_count?: number;
  };
}
export interface Assessment {
  valid: boolean; duration_seconds: number; note_count: number;
  checks: { id: string; label: string; passed: boolean; code: string | null; message: string }[];
  lyric_lines: { line_index: number; text: string; unit_count: number; note_count: number; start_seconds: number; end_seconds: number; pitch_low: number; pitch_high: number; max_jump_semitones: number; breath_before_seconds: number | null; warnings: string[] }[];
  reference_lock: Composition['reference_lock'];
  musical_phrases?: { phrase_id: string; line_index: number; start_seconds: number; sung_end_seconds: number;
    tail_rest_seconds: number; warnings: string[]; groups: { uid: number; text: string; stress: string;
      note_count: number; onset_tick: number; bar_beat: number; duration_seconds: number; pitches: number[] }[] }[];
}
export interface ReviewStatus {
  revision_id: string; valid: boolean; reason: string | null; assessment: Assessment;
  review: { id: string; approved: boolean; mock: boolean; comment: string; revision_number: number } | null;
  full_song_enabled: boolean; full_song_reason: string | null;
  full_song_spec: { lyric_lines: number; lyric_units: number; duration_policy: 'lyrics' | 'fixed'; target_seconds: number | null; max_seconds: number; max_output_tokens: number } | null;
}
export interface PracticeOptions { speed: 0.75 | 1; loop: boolean; unitIndex?: number; context?: 'word' | 'word_group' | 'phrase' }
export type PracticePlayer = (state: SongState, lineIndex: number | null, progress: (scoreSeconds: number | null) => void, options: PracticeOptions) => () => void;
export interface PhraseBudget { hash: string; mode: 'single' | 'planned_blocks'; request_limit: number;
  per_request_max_tokens: number; total_output_token_limit: number; unit_count: number; cost_notice: string; reused_blocks?: number }
export interface GroupParameters {
  line_index: number; unit_index: number; action: 'add' | 'simplify' | 'set'; time_mode: 'fixed' | 'ripple';
  note_index?: number; interval?: number; keep?: 'first' | 'last'; pitches?: number[]; weights?: number[]; duration_ticks?: number;
}
export interface GroupImpact { word: string; note_count: number; old_duration_ticks: number; duration_ticks: number;
  following_lyrics_delta_ticks: number; later_music_delta_ticks: number; later_music_bars: number; total_seconds: number }
export type GroupEditor = (params: GroupParameters, preview: boolean, transactionId: string) => Promise<GroupImpact>;
