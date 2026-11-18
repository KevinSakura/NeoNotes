import { test, expect } from '@playwright/test';

function wav(duration=2){const samples=44100*duration;const b=Buffer.alloc(44+samples*4);b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVEfmt ',8);b.writeUInt32LE(16,16);b.writeUInt16LE(1,20);b.writeUInt16LE(2,22);b.writeUInt32LE(44100,24);b.writeUInt32LE(176400,28);b.writeUInt16LE(4,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(samples*4,40);return b;}

test.beforeEach(async({page})=>{
  await page.route('**/api/transcriptions/resources',route=>route.fulfill({json:{ready:true,error:null,manifest:{mock:true}}}));
  await page.goto('/');
  await page.getByRole('button',{name:'扒谱',exact:true}).click();
  await expect(page.getByRole('heading',{name:'乐曲键盘扒谱'})).toBeVisible();
});

test('independent solo transcription edit undo score downloads and reopen',async({page})=>{
  await page.getByLabel('上传 MP3 / WAV').setInputFiles({name:'original-demo.wav',mimeType:'audio/wav',buffer:wav()});
  await expect(page.getByRole('heading',{name:'1. 选择片段'})).toBeVisible();
  await page.getByRole('button',{name:'开始转录',exact:true}).click();
  await expect(page.getByText('处理完成',{exact:true})).toBeVisible({timeout:15000});
  await expect(page.getByRole('heading',{name:'3. 校对音符'})).toBeVisible();
  await page.getByRole('button',{name:'添加音符',exact:true}).click();
  await expect(page.getByText('4个音符',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'撤销',exact:true}).click();
  await expect(page.getByText('3个音符',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'确认参数，保存并生成五线谱'}).click();
  await expect(page.getByRole('link',{name:'下载 MusicXML'})).toBeVisible();
  await expect(page.locator('[data-notation-id]')).toHaveCount(3);
  const download=page.waitForEvent('download');await page.getByRole('link',{name:'下载整理 MIDI',exact:true}).click();
  expect((await download).suggestedFilename()).toMatch(/neonote-.*score.mid/);
  await page.screenshot({path:test.info().outputPath('transcription-desktop.png'),fullPage:true});
  await page.getByRole('button',{name:'练习',exact:true}).click();await expect(page.getByRole('heading',{name:'从一小段旋律练起。'})).toBeVisible();
  await page.getByRole('button',{name:'创作',exact:true}).click();
  await page.getByRole('button',{name:'扒谱',exact:true}).click();await expect(page.getByRole('link',{name:'下载 MusicXML'})).toBeVisible();
  await page.reload();await page.getByRole('button',{name:'扒谱',exact:true}).click();await page.getByRole('button',{name:'original-demo.wav',exact:true}).click();
  await expect(page.getByRole('link',{name:'下载 MusicXML'})).toBeVisible();
});

test('mix waits for review, cancellation, then explicit continue',async({page})=>{
  await page.getByLabel('上传 MP3 / WAV').setInputFiles({name:'mixed-demo.wav',mimeType:'audio/wav',buffer:wav()});
  await expect(page.getByRole('heading',{name:'1. 选择片段'})).toBeVisible();
  await page.getByLabel('输入类型').selectOption('mix');
  await page.getByRole('button',{name:'开始分离',exact:true}).click();
  await expect(page.getByText('等待试听确认',{exact:true})).toBeVisible({timeout:15000});
  await expect(page.getByRole('link',{name:'下载原始转录 MIDI'})).toHaveCount(0);
  await page.getByRole('button',{name:'载入这次分离试听'}).click();
  await expect(page.getByLabel('对照声音')).toHaveValue('piano');
  await page.getByRole('button',{name:'取消',exact:true}).click();await expect(page.getByText('已取消',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'开始分离',exact:true}).click();
  await expect(page.getByText('等待试听确认',{exact:true})).toBeVisible({timeout:15000});
  await page.getByRole('button',{name:'我已试听，继续转录'}).click();
  await expect(page.getByText('处理完成',{exact:true})).toBeVisible({timeout:15000});
  await expect(page.getByRole('link',{name:'下载原始转录 MIDI'})).toBeVisible();
  await page.setViewportSize({width:400,height:860});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBeTruthy();
});

test('model clip boundary and three voices in both hands retain every notehead',async({page})=>{
  await page.getByLabel('上传 MP3 / WAV').setInputFiles({name:'polyphonic.wav',mimeType:'audio/wav',buffer:wav(3.5)});
  await expect(page.getByRole('heading',{name:'1. 选择片段'})).toBeVisible();
  await page.getByRole('button',{name:'开始转录',exact:true}).click();
  await expect(page.getByText('处理完成',{exact:true})).toBeVisible({timeout:15000});
  await expect(page.getByTestId('transcription-boundary-report')).toContainText('1个持续音');
  await expect(page.getByTestId('transcription-boundary-report')).toContainText('1个完全位于片段外');
  await expect(page.getByRole('link',{name:'下载选段 MIDI',exact:true})).toBeVisible();
  const projects=await (await page.request.get('/api/transcriptions')).json();
  const pid=projects.find((p:any)=>p.name==='polyphonic.wav').id;
  const detail=await (await page.request.get(`/api/transcriptions/${pid}`)).json();
  const notes=['right','left'].flatMap((hand,side)=>Array.from({length:3},(_,i)=>({id:`${hand}-${i}`,pitch:(side?48:72)+i*3,onset:i*.25,offset:3.5-i*.25,velocity:80,hand})));
  const token=(await (await page.request.get('/api/session')).json()).token;
  const response=await page.request.post(`/api/transcriptions/${pid}/revisions`,{headers:{'X-Session-Token':token},data:{base:null,result_id:detail.results[0].id,notes,settings:{bpm:120,numerator:4,denominator:4,first_downbeat:0,grid:120,key_fifths:0}}});
  expect(response.ok()).toBeTruthy();const revision=await response.json();
  expect(revision.notation_error).toBeNull();expect(revision.score.report.voices_per_hand).toEqual({right:3,left:3});
  await page.getByRole('button',{name:'polyphonic.wav',exact:true}).click();
  await expect(page.getByText('右手3个声部，左手3个声部。',{exact:false})).toBeVisible();
  await expect(page.locator('[data-notation-id]')).toHaveCount(revision.score.notation.segments.length);
  await expect(page.getByText('五线谱音符映射已核对',{exact:true})).toBeVisible();
  // Independent voices are clickable even when sharing staff/time/pitch classes.
  const head=page.locator('[data-notation-id]').last();await head.click();
  await expect(head).toHaveAttribute('data-active','true');
  await page.setViewportSize({width:420,height:900});
  await expect(page.locator('[data-notation-id]')).toHaveCount(revision.score.notation.segments.length);
  await expect(page.getByText('五线谱音符映射已核对',{exact:true})).toBeVisible();
  await page.screenshot({path:test.info().outputPath('polyphonic-score.png'),fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBeTruthy();
});
