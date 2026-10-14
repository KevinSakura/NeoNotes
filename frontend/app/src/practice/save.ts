import { draftHash } from '../draft-hash.mjs';
import { request } from '../api';
import type { Event, Session } from './types';

function database(): Promise<IDBDatabase> {
  return new Promise((resolve,reject) => {const open=indexedDB.open('neonote-practice-buffer-v1',1);open.onupgradeneeded=()=>open.result.createObjectStore('sessions',{keyPath:'id'});open.onsuccess=()=>resolve(open.result);open.onerror=()=>reject(open.error);});
}
async function local(id: string, value?: unknown): Promise<any> {
  const db=await database();
  try{return await new Promise((resolve,reject)=>{const tx=db.transaction('sessions',value===undefined?'readonly':'readwrite'),store=tx.objectStore('sessions'),req=value===undefined?store.get(id):store.put(value);let result:any;req.onsuccess=()=>{result=req.result;};tx.oncomplete=()=>resolve(result);tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});}finally{db.close();}
}
/** Durable browser buffer is a queue, never the authoritative practice result. */
export class PracticeBuffer {
  session: Session; events: Event[]; confirmed: number; seq: number;
  private saving=false; private closed=false; private pendingBatch?: {seq:number;events:Event[];payload_hash:string};
  private localChain: Promise<any>=Promise.resolve();
  constructor(session: Session, private status: (text:string)=>void) {this.session=session;this.events=[...session.events];this.confirmed=this.events.length;this.seq=session.last_seq;}
  async restore() {
    const cached=await local(this.session.snapshot.id);
    if(cached){
      const server=this.session.events;
      if(cached.events.length<server.length){
        if(await draftHash(server.slice(0,cached.events.length))!==await draftHash(cached.events))throw Error('浏览器缓冲与后端记录冲突，未覆盖任何作答。');
      }else{
        if(await draftHash(cached.events.slice(0,server.length))!==await draftHash(server))throw Error('浏览器缓冲与后端记录冲突，未覆盖任何作答。');
        this.events=cached.events;
      }
    }
    await this.persist();
  }
  push(event:Event) {this.events.push(event);this.status('正在保存');void this.persist().catch(()=>this.status('保存失败：浏览器缓冲写入失败，请先保留页面'));}
  persist() {
    const value={id:this.session.snapshot.id,events:structuredClone(this.events),confirmed:this.confirmed,seq:this.seq};
    this.localChain=this.localChain.catch(()=>{}).then(()=>local(value.id,value));
    return this.localChain;
  }
  async flush() {
    if(this.saving)return;
    this.saving=true;
    try{
      await this.localChain;
      while(this.confirmed<this.events.length){
        this.pendingBatch??={seq:this.seq+1,events:structuredClone(this.events.slice(this.confirmed,this.confirmed+256)),payload_hash:''};
        this.pendingBatch.payload_hash=await draftHash(this.pendingBatch.events);
        this.status('正在保存');
        const result=await request<Session>(`/practice/sessions/${this.session.snapshot.id}/batches`,{method:'POST',body:JSON.stringify(this.pendingBatch)});
        const length=this.confirmed+this.pendingBatch.events.length;
        if(result.events.length!==length||await draftHash(result.events)!==await draftHash(this.events.slice(0,length)))throw Error('同一会话在另一页面发生变化，未合并或覆盖；请下载本地记录并重新打开。');
        this.confirmed+=this.pendingBatch.events.length;this.seq=result.last_seq;this.pendingBatch=undefined;
        await this.persist();
      }
      this.status('已保存');
    }catch(e){this.status('保存失败：'+(e instanceof Error?e.message:'请保留浏览器缓冲并重试'));}
    finally{this.saving=false;if(this.closed&&this.confirmed<this.events.length)await this.persist().catch(()=>{});}
  }
  pending(){return this.confirmed<this.events.length;}
  close(){this.closed=true;void this.persist();void this.flush();}
}
