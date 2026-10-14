import { expect, test, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';

test('OSMD maps individual chord notes and cross-system ties, clicks and redraws after wrapping', async ({page}, info) => {
  const token = (await (await page.request.get('/api/session')).json()).token;
  const response = await page.request.post('/api/practice/materials', {headers: {'X-Session-Token': token}, multipart: {file: {name: '固定映射.mid', mimeType: 'audio/midi', buffer: await readFile('../../.cache/practice-evaluation/implementation-20261013/fixed-score/source.mid')}}});
  expect(response.status()).toBe(201);
  const material = await response.json();
  await page.goto('/'); await page.getByRole('button', {name:'练习', exact:true}).click();
  await page.getByLabel('已有练习材料').selectOption(material.source.id);
  await page.getByLabel('练习轨道').selectOption(material.parsed.tracks[0].id);
  await page.getByLabel('结束小节').fill('8'); await page.getByLabel('量化网格').selectOption('0');
  await page.getByRole('button', {name:'整理并预览五线谱', exact:true}).click();
  const score = page.getByTestId('practice-score');
  await expect(page.getByText('五线谱音符映射已核对')).toBeVisible();
  await expect(score.locator('[data-notation-id]')).toHaveCount(16);
  const first = score.locator('[data-pitch="60"]').first(), other = score.locator('[data-pitch="64"]').first();
  await first.click(); await expect(first).toHaveAttribute('data-active','true');
  await other.click(); await expect(other).toHaveAttribute('data-active','true'); await expect(first).toHaveAttribute('data-active','false');
  await page.getByText('手动圆滑线（不合并起音）',{exact:true}).click();
  await page.getByLabel('圆滑线起点').selectOption(material.parsed.tracks[0].notes[0].id);
  await page.getByLabel('圆滑线终点').selectOption(material.parsed.tracks[0].notes[3].id);
  await page.getByRole('button',{name:'添加圆滑线并重新预览',exact:true}).click();
  await page.getByRole('button',{name:'整理并预览五线谱',exact:true}).click();
  await expect(page.getByText('五线谱音符映射已核对')).toBeVisible();
  await expect(score.locator('[data-notation-id]')).toHaveCount(16);
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByText('五线谱音符映射已核对')).toBeVisible();
  await expect(score.locator('[data-notation-id]')).toHaveCount(16);
  await score.locator('[data-notation-id]').last().click(); await expect(score.locator('[data-notation-id]').last()).toHaveAttribute('data-active','true');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('score-mobile.png'),fullPage:true});
});

async function simpleVersion(page:Page,name:string){
  const token=(await(await page.request.get('/api/session')).json()).token;
  const uploaded=await page.request.post('/api/practice/materials',{headers:{'X-Session-Token':token},multipart:{file:{name:name+'.mid',mimeType:'audio/midi',buffer:await readFile('../../.cache/practice-evaluation/implementation-20261013/simple-score/source.mid')}}});
  expect(uploaded.status()).toBe(201);const material=await uploaded.json();
  await page.goto('/');await page.getByRole('button',{name:'练习',exact:true}).click();await page.getByLabel('已有练习材料').selectOption(material.source.id);await page.getByLabel('练习轨道').selectOption(material.parsed.tracks[0].id);await page.getByLabel('结束小节').fill('2');await page.getByLabel('量化网格').selectOption('0');await page.getByRole('button',{name:'整理并预览五线谱',exact:true}).click();await page.getByRole('button',{name:'确认五线谱并保存练习版本',exact:true}).click();return{token,material};
}
async function readCorrectly(page:Page){
  await page.getByRole('button',{name:'开始新的认音轮',exact:true}).click();const capture=page.getByTestId('practice-capture');await expect(capture).toContainText('认音 1/4');
  for(const[key,next]of [['s',2],['d',3],['e',4]]as const){await page.keyboard.press(key);await expect(capture).toContainText(`认音 ${next}/4`);}
  for(const key of ['s','f','h'])await page.keyboard.down(key);await expect(capture).toContainText('认音全部完成');for(const key of ['s','f','h'])await page.keyboard.up(key);
  await expect(page.getByTestId('practice-save-state')).toContainText('已保存');
}

test('actual audio clock, raw event timestamps and full rhythm completion replay early late missed and rest inputs',async({page},info)=>{
  const{material}=await simpleVersion(page,'节奏计时隔离');await readCorrectly(page);
  const list=(await(await page.request.get('/api/practice/sessions')).json()).items;
  const sid=list.find((s:any)=>s.material_id===material.source.id).id;
  await page.getByLabel('节奏练习速度').selectOption('1');await page.getByRole('button',{name:'从头开始节奏轮',exact:true}).click();
  let start:any;
  await expect.poll(async()=>{const record=await(await page.request.get('/api/practice/sessions/'+sid)).json();start=record.events.find((e:any)=>e.kind==='segment_start');return !!start;},{intervals:[40,60,100]}).toBe(true);
  const absolute=start.clock.performance+(start.clock.start_audio-start.clock.audio)*1000;
  const scheduled=await page.evaluate(async(origin)=>{
    const events=[{at:-200,code:'KeyS',down:true},{at:50,code:'KeyS',down:false},{at:700,code:'KeyD',down:true},{at:950,code:'KeyD',down:false},{at:1400,code:'KeyL',down:true},{at:1440,code:'KeyL',down:false},...['KeyS','KeyF','KeyH'].map(code=>({at:1500,code,down:true})),...['KeyS','KeyF','KeyH'].map(code=>({at:1750,code,down:false}))];
    if(origin-200<performance.now())return false;
    await Promise.all(events.map(event=>new Promise<void>(resolve=>setTimeout(()=>{document.querySelector('[data-testid="practice-capture"]')!.dispatchEvent(new KeyboardEvent(event.down?'keydown':'keyup',{code:event.code,bubbles:true}));resolve();},Math.max(0,origin+event.at-performance.now())))));return true;
  },absolute);
  expect(scheduled).toBe(true);await expect(page.getByTestId('practice-capture')).toContainText('节奏轮已结束',{timeout:12000});await expect(page.getByTestId('practice-save-state')).toContainText('已保存');
  const record=await(await page.request.get('/api/practice/sessions/'+sid)).json(),timing=record.result.rhythm.at(-1);
  expect(record.status).toBe('completed');expect(timing.complete).toBe(true);expect(timing.matched.filter((m:any)=>m.timing==='early')).toHaveLength(1);expect(timing.matched.filter((m:any)=>m.timing==='late')).toHaveLength(1);expect(timing.missed).toHaveLength(1);expect(timing.extras).toHaveLength(1);expect(timing.rest_errors).toBe(1);expect(timing.on_time_rate).toBe(.5);expect(timing.pitch_accuracy).toBeCloseTo(5/7);
  expect(record.events.filter((e:any)=>e.kind==='note_down'&&e.phase==='rhythm').every((e:any)=>e.at===e.physical_at&&e.resolved_at>=e.at)).toBe(true);
  await page.screenshot({path:info.outputPath('rhythm-complete.png'),fullPage:true});
});

test('paused rhythm resumes the saved measure after refresh and speed change as a separate attempt',async({page})=>{
  const{material}=await simpleVersion(page,'顺序恢复隔离');await readCorrectly(page);const sid=(await(await page.request.get('/api/practice/sessions')).json()).items.find((s:any)=>s.material_id===material.source.id).id;
  await page.getByLabel('节奏练习速度').selectOption('1');await page.getByRole('button',{name:'从头开始节奏轮',exact:true}).click();await expect(page.getByTestId('practice-capture')).toContainText('原小节2',{timeout:10000});await page.getByRole('button',{name:'暂停 / 停止',exact:true}).click();await expect(page.getByTestId('practice-save-state')).toContainText('已保存');
  await page.reload();await page.getByRole('button',{name:'练习',exact:true}).click();await page.getByRole('button',{name:'查看记录 / 恢复练习',exact:true}).first().click();await expect(page.getByTestId('practice-capture')).toHaveAttribute('data-phase','paused');await page.getByLabel('节奏练习速度').selectOption('0.5');
  await page.getByRole('button',{name:'重新预备后续练',exact:true}).click();await expect(page.getByTestId('practice-capture')).toHaveAttribute('data-phase','rhythm');await page.getByRole('button',{name:'暂停 / 停止',exact:true}).click();await expect(page.getByTestId('practice-save-state')).toContainText('已保存');
  const record=await(await page.request.get('/api/practice/sessions/'+sid)).json();expect(record.result.rhythm).toHaveLength(2);expect(record.result.rhythm[0].from_tick).toBe(0);expect(record.result.rhythm[1].from_tick).toBe(1920);expect(record.result.rhythm[1].speed).toBe(.5);expect(record.result.rhythm.every((r:any)=>r.interrupted&&!r.complete)).toBe(true);
});

test('failed autosave retains answers in IndexedDB, refresh restores pending events and retries without duplicates',async({page})=>{
  const{material}=await simpleVersion(page,'保存故障隔离');await page.getByRole('button',{name:'开始新的认音轮',exact:true}).click();await expect(page.getByTestId('practice-capture')).toContainText('认音 1/4');await expect(page.getByTestId('practice-save-state')).toContainText('已保存');
  const sid=(await(await page.request.get('/api/practice/sessions')).json()).items.find((s:any)=>s.material_id===material.source.id).id;
  await page.route('**/api/practice/sessions/*/batches',route=>route.abort());
  await page.keyboard.press('j');await expect(page.getByTestId('practice-result')).toContainText('尝试1次');await expect(page.getByTestId('practice-save-state')).toContainText('保存失败');
  await page.keyboard.press('s');await expect(page.getByTestId('practice-capture')).toContainText('认音 2/4');
  await expect.poll(async()=>page.evaluate(sid=>new Promise<number>((resolve,reject)=>{const open=indexedDB.open('neonote-practice-buffer-v1',1);open.onerror=()=>reject(open.error);open.onsuccess=()=>{const db=open.result,req=db.transaction('sessions').objectStore('sessions').get(sid);req.onsuccess=()=>{resolve(req.result.events.filter((e:any)=>e.kind==='note_down').length);db.close();};};}),sid)).toBe(2);
  page.on('dialog',dialog=>dialog.accept());await page.reload();await page.getByRole('button',{name:'练习',exact:true}).click();await page.getByRole('button',{name:'查看记录 / 恢复练习',exact:true}).first().click();await expect(page.getByTestId('practice-result')).toContainText('尝试2次');await expect(page.getByTestId('practice-save-state')).toContainText('保存失败');
  await page.unroute('**/api/practice/sessions/*/batches');await page.getByRole('button',{name:'重试保存',exact:true}).click();await expect(page.getByTestId('practice-save-state')).toContainText('已保存');
  const record=await(await page.request.get('/api/practice/sessions/'+sid)).json();expect(record.result.recognition.attempts).toHaveLength(2);expect(record.events.filter((e:any)=>e.kind==='note_down')).toHaveLength(2);expect(record.status).toBe('paused');expect(record.result.recognition.first_accuracy).toBe(0);
});

test('confirmed octave controls, form focus and entry changes release all practice sounds and preserve creation drafts',async({page})=>{
  await simpleVersion(page,'输入生命周期隔离');await page.getByText('多键自检与延迟校准',{exact:true}).click();await page.getByRole('button',{name:'开始多键自检',exact:true}).click();const state=page.getByTestId('practice-key-state'),capture=page.getByTestId('practice-capture');
  await expect(capture).toHaveAttribute('data-phase','self_test');
  await page.keyboard.press('Shift');await expect(state).toContainText('当前基础八度3');await page.keyboard.down('l');await expect(state).toContainText('发声：C4');await page.keyboard.up('l');await page.keyboard.press('Space');await expect(state).toContainText('当前基础八度4');
  await page.keyboard.down('Shift');await page.keyboard.down('d');await expect(state).toContainText('发声：D4、D3');await page.keyboard.up('Shift');await expect(state).toContainText('发声：D4');await page.keyboard.up('d');await expect(state).toContainText('发声：无');
  await page.keyboard.down('d');await page.keyboard.down('Space');await expect(state).toContainText('发声：D4、D5');await page.keyboard.down('Shift');await page.keyboard.down('f');await expect(state).toContainText('发声：D4、D5');await page.keyboard.up('f');await page.keyboard.up('Shift');await page.keyboard.up('Space');await page.keyboard.up('d');
  await page.keyboard.down('s');await page.getByLabel('节奏练习速度').focus();await expect(state).toContainText('发声：无');await page.keyboard.up('s');await page.keyboard.press('Space');await expect(state).toContainText('当前基础八度4');
  await page.getByRole('button',{name:'开始多键自检',exact:true}).click();await expect(capture).toHaveAttribute('data-phase','self_test');await capture.focus();await page.keyboard.down('h');await expect(state).toContainText('发声：G4');await page.getByRole('button',{name:'创作',exact:true}).click();await page.keyboard.up('h');await page.getByRole('button',{name:'＋ 新建歌曲',exact:true}).click();await page.getByRole('textbox',{name:'歌曲名称'}).fill('入口草稿隔离');await page.getByRole('button',{name:'创建歌曲',exact:true}).click();await page.getByRole('textbox',{name:'主题',exact:true}).fill('此草稿切入口应保留');
  await page.getByRole('button',{name:'练习',exact:true}).click();await expect(state).toContainText('发声：无');await page.getByRole('button',{name:'创作',exact:true}).click();await expect(page.getByRole('textbox',{name:'主题',exact:true})).toHaveValue('此草稿切入口应保留');
});

test('entry change during asynchronous SoundFont load cannot start a stale practice session',async({page})=>{
  const{material}=await simpleVersion(page,'加载中切页隔离');let release!:()=>void,seen!:()=>void;const gate=new Promise<void>(resolve=>release=resolve),requested=new Promise<void>(resolve=>seen=resolve);
  await page.route('**/api/audio/soundfont?*',async route=>{seen();await gate;await route.continue();});
  await page.getByRole('button',{name:'开始新的认音轮',exact:true}).click();await requested;await page.getByRole('button',{name:'创作',exact:true}).click();release();await page.getByRole('button',{name:'练习',exact:true}).click();
  await page.getByText('多键自检与延迟校准',{exact:true}).click();await page.getByRole('button',{name:'开始多键自检',exact:true}).click();await expect(page.getByTestId('practice-capture')).toHaveAttribute('data-phase','self_test');await expect(page.getByTestId('practice-key-state')).toContainText('发声：无');
  expect((await(await page.request.get('/api/practice/sessions')).json()).items.filter((s:any)=>s.material_id===material.source.id)).toHaveLength(0);
});

test('grand staff has independent bass and treble semantic mappings',async({page})=>{
  const token=(await(await page.request.get('/api/session')).json()).token;
  const uploaded=await page.request.post('/api/practice/materials',{headers:{'X-Session-Token':token},multipart:{file:{name:'大谱表隔离.mid',mimeType:'audio/midi',buffer:await readFile('../../.cache/practice-evaluation/implementation-20261013/grand-score/source.mid')}}});
  const material=await uploaded.json();await page.goto('/');await page.getByRole('button',{name:'练习',exact:true}).click();await page.getByLabel('已有练习材料').selectOption(material.source.id);await page.getByLabel('练习轨道').selectOption(material.parsed.tracks[0].id);await page.getByLabel('结束小节').fill('8');await page.getByLabel('量化网格').selectOption('0');await page.getByLabel('练习谱表').selectOption('grand');await page.getByRole('button',{name:'整理并预览五线谱',exact:true}).click();
  const score=page.getByTestId('practice-score');await expect(page.getByText('五线谱音符映射已核对')).toBeVisible();await expect(score.locator('[data-notation-id]')).toHaveCount(17);await score.locator('[data-pitch="48"]').click();await expect(score.locator('[data-pitch="48"]')).toHaveAttribute('data-active','true');
});

test('recognition corrections keep first score, rhythm restarts, automatic saving and refresh resume', async ({page},info)=>{
  const failures:string[]=[];page.on('pageerror',e=>failures.push(e.message));
  const token=(await(await page.request.get('/api/session')).json()).token;
  const response=await page.request.post('/api/practice/materials',{headers:{'X-Session-Token':token},multipart:{file:{name:'认音节奏隔离.mid',mimeType:'audio/midi',buffer:await readFile('../../.cache/practice-evaluation/implementation-20261013/simple-score/source.mid')}}});
  const material=await response.json();
  await page.goto('/');await page.getByRole('button',{name:'练习',exact:true}).click();await page.getByLabel('已有练习材料').selectOption(material.source.id);await page.getByLabel('练习轨道').selectOption(material.parsed.tracks[0].id);await page.getByLabel('结束小节').fill('2');await page.getByLabel('量化网格').selectOption('0');await page.getByRole('button',{name:'整理并预览五线谱',exact:true}).click();
  await page.getByRole('button',{name:'确认五线谱并保存练习版本',exact:true}).click();
  await page.getByRole('button',{name:'开始新的认音轮',exact:true}).click();
  const capture=page.getByTestId('practice-capture'),player=page.getByTestId('practice-player');await expect(capture).toContainText('认音 1/4');
  await page.keyboard.down('j');await page.keyboard.up('j');await expect(player.getByTestId('practice-result')).toContainText('尝试1次');
  await capture.focus();await page.keyboard.down('s');await page.keyboard.up('s');await expect(capture).toContainText('认音 2/4');
  await page.keyboard.down('d');await page.keyboard.up('d');await expect(capture).toContainText('认音 3/4');
  await page.keyboard.down('e');await page.keyboard.up('e');await expect(capture).toContainText('认音 4/4');
  await page.keyboard.down('s');await page.keyboard.down('f');await page.keyboard.down('h');
  await expect(capture).toContainText('认音全部完成');await page.keyboard.up('s');await page.keyboard.up('f');await page.keyboard.up('h');
  await expect(player.getByTestId('practice-result')).toContainText('首次已答准确率：75.0%');
  await expect(player.getByTestId('practice-save-state')).toContainText('已保存');
  await page.getByLabel('节奏练习速度').selectOption('1');await page.getByRole('button',{name:'从头开始节奏轮',exact:true}).click();await expect(capture).toContainText('预备拍');
  await page.getByRole('button',{name:'暂停 / 停止',exact:true}).click();await expect(capture).toContainText('已暂停');await expect(player.getByTestId('practice-save-state')).toContainText('已保存');
  await page.reload();await page.getByRole('button',{name:'练习',exact:true}).click();await page.getByRole('button',{name:'查看记录 / 恢复练习',exact:true}).first().click();
  await expect(page.getByTestId('practice-result')).toContainText('首次已答准确率：75.0%');await expect(page.getByTestId('practice-capture')).toContainText('已暂停');
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('records-mobile.png'),fullPage:true});expect(failures).toEqual([]);
});
