import type { Sequencer, WorkletSynthesizer } from 'spessasynth_lib';
import workletUrl from 'spessasynth_lib/dist/spessasynth_processor.min.js?url';
import type { Track } from './types';
import { request } from './api';

type Instrument = Pick<Track, 'family' | 'program' | 'volume'> & Partial<Pick<Track, 'channel' | 'bank_msb' | 'bank_lsb' | 'is_drum'>>;
export type PlayEvent = { pitch: number; velocity: number; start: number; duration: number; track: Instrument };
export type AudioResources = { ready: boolean; render_ready: boolean; error: string | null; soundfont: null | { name: string; sha256: string; bytes: number; presets: { bank: number; program: number; name: string }[] } };

/** A single worklet reads the local SF2; its sequencer schedules notes on the audio thread. */
export class AudioEngine {
  private context?: AudioContext;
  private synth?: WorkletSynthesizer;
  private seq?: Sequencer;
  private analyser?: AnalyserNode;
  private samples = new Float32Array(512);
  private loading?: Promise<void>;
  private resource?: AudioResources;
  private timer?: ReturnType<typeof setInterval>;
  private ended?: () => void;
  private serial = 0;
  private ready = false;
  private held = new Map<string, number>();
  onStatus?: (status: string) => void;

  async enable() {
    if (this.ready && this.synth && this.context?.state === 'running') return;
    if (this.loading) return this.loading;
    this.context ??= new AudioContext({ latencyHint: 'interactive', sampleRate: 44100 });
    const context = this.context;
    const resume = context.resume();
    this.loading = (async () => {
      this.onStatus?.('正在加载本地音色，首次使用请稍候…');
      try {
        await resume;
        if (!this.synth) {
          this.resource = await request<AudioResources>('/audio/resources');
          if (!this.resource.ready || !this.resource.soundfont) throw Error(this.resource.error ?? '本地音色未准备。');
          const font = this.resource.soundfont;
          const response = await fetch(`/api/audio/soundfont?sha256=${font.sha256}`, { signal: AbortSignal.timeout(20000) });
          if (!response.ok) throw Error('音色文件读取失败，请检查本地服务并刷新页面。');
          const data = await response.arrayBuffer();
          const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))].map(byte => byte.toString(16).padStart(2, '0')).join('');
          if (digest !== font.sha256) throw Error('音色文件校验失败，请检查 tools 中的文件。');
          await context.audioWorklet.addModule(workletUrl);
          const { WorkletSynthesizer, Sequencer } = await import('spessasynth_lib');
          const synth = new WorkletSynthesizer(context); this.synth = synth;
          synth.setLogLevel(false, false, false);
          await Promise.race([synth.isReady, new Promise((_, reject) => setTimeout(() => reject(Error('音色引擎启动超时，请重试。')), 20000))]);
          await synth.soundBankManager.addSoundBank(data, 'general-user');
          synth.setSystemParameter('effectsEnabled', false);
          synth.setSystemParameter('gain', 0.2);
          synth.setSystemParameter('voiceCap', 256);
          this.analyser = context.createAnalyser(); this.analyser.fftSize = 512;
          synth.connect(this.analyser); this.analyser.connect(context.destination);
          this.seq = new Sequencer(synth, { skipToFirstNoteOn: false }); this.seq.loopCount = 0;
          this.seq.eventHandler.addEvent('songEnded', 'workstation', () => { if (this.ended) this.stop(); });
        }
        this.ready = true; this.onStatus?.('本地 SoundFont 已就绪');
      } catch (error) {
        this.ready = false; this.synth?.destroy(); this.synth = undefined; this.seq = undefined;
        this.analyser?.disconnect(); this.analyser = undefined;
        this.onStatus?.('音色加载失败，可再次点击重试'); throw error;
      } finally { this.loading = undefined; }
    })();
    return this.loading;
  }

  level() {
    if (!this.analyser) return 0;
    this.analyser.getFloatTimeDomainData(this.samples);
    let peak = 0; for (const value of this.samples) peak = Math.max(peak, Math.abs(value));
    return Math.min(1, peak);
  }

  /** Practice scores raw event times against this audio clock; sequencer polling is display-only. */
  async practiceContext(): Promise<AudioContext> {
    await this.enable();
    return this.context!;
  }

  practiceVoice(pitch: number, velocity = 90): () => void {
    return this.voice(pitch, {family:'keyboard', program:0, volume:80, channel:15, bank_msb:0, bank_lsb:0}, velocity);
  }

  private instrument(track: Instrument) {
    const drums = track.is_drum ?? track.family === 'drums';
    const channel = track.channel ?? ({ keyboard: 0, guitar: 1, bass: 2, drums: 9, other: 3 }[track.family]);
    const msb = track.bank_msb ?? 0, lsb = track.bank_lsb ?? 0, bank = drums ? 128 : msb;
    if (lsb !== 0 || (drums && msb !== 0) || !this.resource?.soundfont?.presets.some(item => item.bank === bank && item.program === track.program)) {
      throw Error('当前音色库没有这个 Bank / Program；请更换音色。MIDI 原信息保留。');
    }
    return { channel, msb, drums };
  }

  voice(pitch: number, track: Instrument, velocity = 90): () => void {
    if (!this.synth) throw Error('音色尚未加载。');
    const { channel, msb, drums } = this.instrument(track);
    this.synth.midiChannels[channel].setDrums(drums);
    this.synth.controllerChange(channel, 0, msb); this.synth.controllerChange(channel, 32, 0);
    this.synth.controllerChange(channel, 7, Math.round(track.volume / 100 * 127));
    this.synth.programChange(channel, track.program);
    const key = `${channel}:${pitch}`; this.held.set(key, (this.held.get(key) ?? 0) + 1);
    this.synth.noteOn(channel, pitch, velocity);
    let released = false;
    return () => {
      if (released) return; released = true;
      const count = (this.held.get(key) ?? 1) - 1;
      if (count > 0) this.held.set(key, count);
      else { this.held.delete(key); this.synth?.noteOff(channel, pitch); }
    };
  }

  async play(events: PlayEvent[], duration: number, progress: (seconds: number) => void, ended: () => void, options: { loop?: boolean } = {}) {
    this.stop(); const serial = this.serial;
    await this.enable(); if (serial !== this.serial) { ended(); return; }
    const { MIDIBuilder } = await import('spessasynth_core');
    if (serial !== this.serial) { ended(); return; }
    const midi = new MIDIBuilder({ timeDivision: 10000, initialTempo: 60, format: 1 });
    const tracks = new Map<number, number>();
    for (const event of events) {
      const { channel, msb } = this.instrument(event.track);
      let index = tracks.get(channel);
      if (index === undefined) {
        midi.addTrack(`Channel ${channel + 1}`); index = tracks.size + 1; tracks.set(channel, index);
        midi.controllerChange(0, index, channel, 0, msb); midi.controllerChange(0, index, channel, 32, 0);
        midi.controllerChange(0, index, channel, 7, Math.round(event.track.volume / 100 * 127));
      }
      const start = Math.round(event.start * 10000), end = Math.max(start + 1, Math.round((event.start + event.duration) * 10000));
      midi.programChange(start, index, channel, event.track.program);
      midi.noteOn(start, index, channel, event.pitch, event.velocity); midi.noteOff(end, index, channel, event.pitch);
    }
    // Explicit loop boundaries retain the preparation gap and release tail. The
    // installed core otherwise infers its loop from first note-on to last note-off.
    midi.addEvent(0, 0, 0x06, Array.from('loopStart', char => char.charCodeAt(0)));
    midi.addEvent(Math.round(duration * 10000), 0, 0x06, Array.from('loopEnd', char => char.charCodeAt(0)));
    midi.addEvent(Math.round(duration * 10000), 0, 0x2f, []); midi.flush();
    const seq = this.seq!, listener = `preview-${serial}`;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    try {
      await new Promise<void>((resolve, reject) => {
        seq.eventHandler.addEvent('songChange', listener, () => resolve());
        seq.eventHandler.addEvent('midiError', listener, error => reject(error));
        timeout = setTimeout(() => reject(Error('回放加载超时，请重新试听。')), 15000);
        // MIDIBuilder holds a TextEncoder that cannot cross a MessagePort.
        seq.loadNewSongList([{ binary: midi.writeMIDI(), fileName: 'preview.mid' }]);
      });
    } finally {
      clearTimeout(timeout); seq.eventHandler.removeEvent('songChange', listener); seq.eventHandler.removeEvent('midiError', listener);
    }
    if (serial !== this.serial) { ended(); return; }
    // This installed audio processor uses Infinity for indefinite looping;
    // its facade's older -1 documentation does not match the dispatched core.
    this.ended = ended; seq.loopCount = options.loop ? Infinity : 0; seq.currentTime = 0; seq.play();
    this.timer = setInterval(() => { const time = Math.max(0, this.seq!.currentTime); progress(Math.min(duration, time)); }, 50);
  }

  stop() {
    this.serial++; clearInterval(this.timer); this.timer = undefined;
    this.seq?.pause(); this.synth?.stopAll(true); this.held.clear();
    const ended = this.ended; this.ended = undefined; ended?.();
  }

  dispose() { this.stop(); this.synth?.destroy(); this.synth = undefined; void this.context?.close(); this.context = undefined; }
}
