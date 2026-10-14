export const KEYS = Object.freeze({KeyS:60, KeyD:62, KeyF:64, KeyG:65, KeyH:67, KeyJ:69, KeyK:71, KeyL:72, KeyE:61, KeyR:63, KeyY:66, KeyU:68, KeyI:70});
const modifier = code => code === 'Space' ? 'space' : ['ShiftLeft','ShiftRight'].includes(code) ? 'shift' : null;
/** New confirmed mapping: tap changes register; held modifiers add an octave. l is always an ordinary note. */
export class PracticeKeyboard {
  constructor(emit, sound, update = (state) => {}) {
    this.emit = emit; this.sound = sound; this.update = update; this.octave = 4; this.serial = 0;
    this.keys = new Map(); this.owners = new Map(); this.pitches = new Map();
    this.mods = {shift:{codes:new Set(), used:false}, space:{codes:new Set(), used:false}};
  }
  add(owner, pitch, at, physical, code, role) {
    if (this.owners.has(owner)) return;
    this.owners.set(owner, pitch);
    let voice = this.pitches.get(pitch);
    if (!voice) {
      const instance = `p${++this.serial}-${pitch}`;
      const stop = this.sound(pitch, instance);
      voice = {owners:new Set(), stop, instance}; this.pitches.set(pitch, voice);
      this.emit({kind:'note_down', at, physical_at:physical, semantic_at:at, resolved_at:at,
        pitch, instance, code, role, octave:this.octave});
    }
    voice.owners.add(owner);
  }
  remove(owner, at, reason = 'keyup') {
    const pitch = this.owners.get(owner); if (pitch === undefined) return;
    this.owners.delete(owner); const voice = this.pitches.get(pitch);
    voice.owners.delete(owner);
    if (!voice.owners.size) {
      voice.stop(); this.pitches.delete(pitch);
      this.emit({kind:'note_up', at, physical_at:at, semantic_at:at, pitch, instance:voice.instance, reason});
    }
  }
  down(code, at, options = {}) {
    const type = modifier(code);
    if ((!type && KEYS[code] === undefined) || options.repeat || options.ctrlKey || options.altKey || options.metaKey || options.isComposing || this.keys.has(code) || (type && this.mods[type].codes.has(code))) return false;
    this.emit({kind:'raw_down', at, code, octave:this.octave, shift:this.mods.shift.codes.size > 0, space:this.mods.space.codes.size > 0});
    if (type) {
      const group = this.mods[type], first = !group.codes.size;
      group.codes.add(code);
      if (first) group.used = this.keys.size > 0;
      if (this.mods.shift.codes.size && this.mods.space.codes.size) {
        this.mods.shift.used = this.mods.space.used = true;
        this.emit({kind:'help', at, reason:'modifier_conflict', message:'请只保留一个跨八度键；已发声音保持。'});
      } else if (first && this.keys.size) {
        for (const [key, press] of this.keys) if (!press.invalid) this.add(`${press.id}:${type}`, press.pitch + (type === 'shift' ? -12 : 12), at, at, key, type);
      }
    } else {
      const pitch = KEYS[code] + (this.octave - 4) * 12, id = `key${++this.serial}`;
      const shift = this.mods.shift.codes.size > 0, space = this.mods.space.codes.size > 0;
      this.keys.set(code, {id, pitch, at, invalid:shift && space});
      if (shift) this.mods.shift.used = true;
      if (space) this.mods.space.used = true;
      if (shift && space) this.emit({kind:'help', at, reason:'modifier_conflict', message:'双修饰冲突，本次不产生猜测音高。'});
      else {
        this.add(id + ':base', pitch, at, at, code, 'base');
        if (shift || space) this.add(`${id}:${shift ? 'shift' : 'space'}`, pitch + (shift ? -12 : 12), at, at, code, shift ? 'shift' : 'space');
      }
    }
    this.update(this.state()); return true;
  }
  up(code, at) {
    const type = modifier(code);
    if (type) {
      const group = this.mods[type]; if (!group.codes.has(code)) return false;
      this.emit({kind:'raw_up',at,code}); group.codes.delete(code);
      if (!group.codes.size) {
        for (const press of this.keys.values()) this.remove(press.id + ':' + type, at);
        if (!group.used) this.register(this.octave + (type === 'shift' ? -1 : 1), at, 'modifier_tap');
        group.used = false;
      }
    } else {
      const press = this.keys.get(code); if (!press) return false;
      this.emit({kind:'raw_up',at,code});
      for (const role of ['base','shift','space']) this.remove(press.id + ':' + role, at);
      this.keys.delete(code);
    }
    this.update(this.state()); return true;
  }
  register(octave, at, source = 'button') {
    this.octave = Math.max(3, Math.min(5, octave));
    this.emit({kind:'register',at,octave:this.octave,source}); this.update(this.state());
  }
  clear(at, reason = 'cleanup') {
    for (const owner of [...this.owners.keys()]) this.remove(owner, at, reason);
    this.keys.clear();
    for (const group of Object.values(this.mods)) { group.codes.clear(); group.used = false; }
    this.update(this.state());
  }
  state() { return {octave:this.octave, codes:[...this.keys.keys(), ...this.mods.shift.codes, ...this.mods.space.codes], pitches:[...this.pitches.keys()]}; }
}

export function inputTime(timestamp, now, origin) {
  const normalized = timestamp > 1e12 ? timestamp - origin : timestamp;
  return Number.isFinite(normalized) && Math.abs(normalized - now) < 2000
    ? {at:normalized, precision:'event'} : {at:now, precision:'fallback'};
}
