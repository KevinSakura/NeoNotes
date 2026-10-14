import { secondsAt, percentile } from './judge.mjs';

export function audioAnchor(context, now) {
  const pair=context.getOutputTimestamp?.();
  if(pair&&pair.contextTime>0&&pair.performanceTime>0&&Math.abs(pair.performanceTime-now)<2000){return{audio:pair.contextTime,performance:pair.performanceTime,method:'output_timestamp',calibrated:false};}
  return{audio:context.currentTime,performance:now+(context.outputLatency??context.baseLatency??0)*1000,method:'estimated_latency',calibrated:false};
}
export const performanceAt=(anchor,audio)=>anchor.performance+(audio-anchor.audio)*1000;
export function tapCalibration(expected,actual){const offsets=actual.map((at,i)=>at-expected[i]);return{status:'tap_estimate',method:'human_taps',offset_ms:percentile(offsets,.5)??0,dispersion_ms:percentile(offsets.map(x=>Math.abs(x-(percentile(offsets,.5)??0))),.5),samples:offsets.length};}
export class PracticeTransport{
  constructor(context, now=()=>performance.now()){this.context=context;this.now=now;this.nodes=new Set();this.serial=0;}
  click(at,strong=false){const c=this.context,o=c.createOscillator(),g=c.createGain();o.frequency.value=strong?1200:850;g.gain.setValueAtTime(0,at);g.gain.linearRampToValueAtTime(.08,at+.003);g.gain.exponentialRampToValueAtTime(.001,at+.04);o.connect(g);g.connect(c.destination);o.start(at);o.stop(at+.045);this.nodes.add(o);o.onended=()=>{o.disconnect();g.disconnect();this.nodes.delete(o);};}
  start(targets,from,speed){
    this.stop();if(this.context.state!=='running')throw Error('音频时钟未运行，请重新解锁音频。');
    const anchor=audioAnchor(this.context,this.now()),quarter=[...targets.tempos].reverse().find(t=>t.tick<=from).tempo/1000000/speed;
    const measure=targets.measures.find(m=>m.start_tick<=from&&from<m.end_tick)??targets.measures[0],compound=measure.numerator===6&&measure.denominator===8;
    const beats=compound?2:measure.numerator,beatTicks=compound?720:1920/measure.denominator,beatSeconds=quarter*beatTicks/480,countAt=this.context.currentTime+.15;
    const fullTicks=measure.numerator*1920/measure.denominator;
    // A pickup occupies the end of the virtual bar, so its first sound is not a downbeat.
    const barOrigin=measure.pickup?measure.end_tick-fullTicks:measure.start_tick;
    const phaseTicks=from-barOrigin;
    const countSeconds=(measure.pickup?phaseTicks:fullTicks+phaseTicks)*quarter/480,startAt=countAt+countSeconds;
    for(let i=0;countAt+i*beatSeconds<startAt-1e-8;i++)this.click(countAt+i*beatSeconds,i%beats===0);
    const base=secondsAt(targets.tempos,from);
    for(const m of targets.measures){const step=m.numerator===6&&m.denominator===8?720:1920/m.denominator,origin=m.pickup?m.end_tick-m.numerator*1920/m.denominator:m.start_tick;const first=origin+Math.ceil((Math.max(m.start_tick,from)-origin)/step)*step;for(let tick=first;tick<m.end_tick;tick+=step){this.click(startAt+(secondsAt(targets.tempos,tick)-base)/speed,tick===origin);}}
    const endAt=startAt+(secondsAt(targets.tempos,targets.end_tick)-base)/speed;
    this.running={anchor,startAt,endAt,from,speed,base,targets,lastAudio:this.context.currentTime,lastPerformance:this.now()};
    return{expected_start:performanceAt(anchor,startAt),expected_end:performanceAt(anchor,endAt),clock:anchor,count_in_beats:Math.ceil(countSeconds/beatSeconds),pickup:!!measure.pickup,metrical_phase_ticks:phaseTicks};
  }
  position(){const r=this.running;if(!r)return null;const now=this.now(),audio=this.context.currentTime,lastValid=r.lastPerformance;const anomalous=this.context.state!=='running'||now-r.lastPerformance>500||audio<r.lastAudio||Math.abs((audio-r.lastAudio)*1000-(now-r.lastPerformance))>200;r.lastAudio=audio;r.lastPerformance=now;return{seconds:Math.max(0,(audio-r.startAt)*r.speed),counting:audio<r.startAt,ended:now>performanceAt(r.anchor,r.endAt)+800,anomalous,last_valid:lastValid};}
  stop(){this.serial++;this.running=null;for(const node of this.nodes){try{node.stop();}catch{}node.disconnect();}this.nodes.clear();}
}
