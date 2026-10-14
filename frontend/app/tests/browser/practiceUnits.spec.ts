import { expect, test } from '@playwright/test';

test('empty workstation selects a local lesson, keeps corrections and reopens without MIDI or creation',async({page},info)=>{
  test.setTimeout(60000);
  const errors:string[]=[],writes:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()!=='GET')writes.push(new URL(r.url()).pathname);});
  await page.goto('/');await page.getByRole('button',{name:'练习',exact:true}).click();
  const library=page.getByTestId('practice-units');await expect(library.getByRole('button',{name:'查看练习：认识 C–G',exact:true})).toBeVisible();
  await expect(page.getByTestId('practice-player')).toHaveCount(0);
  await library.getByRole('button',{name:'查看练习：认识 C–G',exact:true}).click();
  await expect(page.getByText('五线谱音符映射已核对')).toBeVisible();await expect(page.getByTestId('practice-score').locator('[data-notation-id]')).toHaveCount(8);
  await expect(page.getByTestId('active-unit')).toContainText('默认八度4');await expect(page.getByLabel('练习轨道')).toHaveCount(0);
  await page.getByRole('button',{name:'确认五线谱并保存练习版本',exact:true}).click();await page.getByRole('button',{name:'开始新的认音轮',exact:true}).click();const capture=page.getByTestId('practice-capture');await expect(capture).toContainText('认音 1/8');
  await page.keyboard.press('j');await expect(page.getByTestId('practice-result')).toContainText('尝试1次');
  const keys=['s','d','f','g','h','f','d','s'];
  for(let i=0;i<keys.length;i++){await capture.focus();await page.keyboard.press(keys[i]);await expect(capture).toContainText(i===keys.length-1?'认音全部完成':`认音 ${i+2}/8`);}
  await expect(page.getByTestId('practice-result')).toContainText('首次已答准确率：87.5%');await expect(library.getByTestId('unit-reading-five-notes')).toContainText('认音已完成');
  await page.getByRole('button',{name:'从头开始节奏轮',exact:true}).click();await expect(capture).toHaveAttribute('data-phase','rhythm');await page.getByRole('button',{name:'暂停 / 停止',exact:true}).click();await expect(page.getByTestId('practice-save-state')).toContainText('已保存');
  await page.getByLabel('节奏练习速度').selectOption('1');await page.getByRole('button',{name:'从头开始节奏轮',exact:true}).click();await expect(capture).toHaveAttribute('data-phase','completed',{timeout:16000});await expect(library.getByTestId('unit-reading-five-notes')).toContainText('完整节奏轮已练');await expect(library.getByTestId('unit-reading-five-notes')).not.toContainText('达到建议标准');
  await page.reload();await page.getByRole('button',{name:'练习',exact:true}).click();await expect(page.getByTestId('unit-reading-five-notes')).toContainText('认音已完成');await page.getByRole('button',{name:'查看记录 / 恢复练习',exact:true}).first().click();await expect(page.getByTestId('active-unit')).toContainText('认识 C–G');await expect(page.getByTestId('practice-result')).toContainText('首次已答准确率：87.5%');await expect(page.getByTestId('practice-capture')).toHaveAttribute('data-phase','paused');
  const records=(await(await page.request.get('/api/practice/sessions')).json()).items,lesson=records.find((r:any)=>r.builtin?.exercise_id==='reading-five-notes');
  expect(lesson.result.recognition.attempts).toHaveLength(9);expect(lesson.result.rhythm[0].interrupted).toBe(true);expect(lesson.result.rhythm.at(-1).complete).toBe(true);expect(lesson.result.rhythm.at(-1).suggested_goal_met).toBe(false);
  const token=(await(await page.request.get('/api/session')).json()).token;const cached=await(await page.request.post('/api/practice/units/reading-five-notes/prepare',{headers:{'X-Session-Token':token},data:{}})).json();expect(cached.material.source.id).toBe(lesson.material_id);expect(cached.revision.revision.id).toBe(lesson.revision_id);
  expect(writes.filter(p=>p==='/api/practice/materials'||p==='/api/practice/from-song'||p==='/api/projects'||p.includes('/generations')||p.includes('/vocals'))).toEqual([]);
  expect(errors).toEqual([]);await page.screenshot({path:info.outputPath('lesson-record.png'),fullPage:true});
});

test('every original lesson renders the expected independent noteheads including flats, chords and ties',async({page},info)=>{
  test.setTimeout(60000);
  await page.goto('/');await page.getByRole('button',{name:'练习',exact:true}).click();
  const catalogue=await(await page.request.get('/api/practice/units')).json();
  for(const unit of catalogue.units){await page.getByRole('button',{name:unit.title,exact:true}).click();for(const exercise of unit.exercises){
    await page.getByRole('button',{name:'查看练习：'+exercise.exercise_title,exact:true}).click();await expect(page.getByText('五线谱音符映射已核对')).toBeVisible();await expect(page.getByTestId('active-unit')).toContainText(exercise.exercise_title);
    const materials=(await(await page.request.get('/api/practice/materials')).json()).items;const source=materials.find((m:any)=>m.builtin?.artifact_hash===exercise.artifact_hash);expect(source).toBeTruthy();
    const token=(await(await page.request.get('/api/session')).json()).token;const bundle=await(await page.request.post('/api/practice/units/'+exercise.exercise_id+'/prepare',{headers:{'X-Session-Token':token},data:{}})).json();
    await expect(page.getByTestId('practice-score').locator('[data-notation-id]')).toHaveCount(bundle.preview.notation.segments.length);
    if(exercise.exercise_id==='ties-across-bar'){expect(bundle.preview.notation.segments.length).toBe(bundle.preview.notation.notes.length+1);expect(bundle.preview.targets.attacks.filter((a:any)=>a.notes.some((n:any)=>n.tie_chain_id))).toHaveLength(1);}
  }}
  await page.setViewportSize({width:390,height:844});await expect(page.getByText('五线谱音符映射已核对')).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:info.outputPath('units-mobile.png'),fullPage:true});
});

test('failed local preparation is explicit and retains the current score and attempts',async({page})=>{
  await page.goto('/');await page.getByRole('button',{name:'练习',exact:true}).click();await page.getByRole('button',{name:'查看练习：认识 C–G',exact:true}).click();await expect(page.getByTestId('active-unit')).toContainText('认识 C–G');
  await expect(page.getByText('五线谱音符映射已核对')).toBeVisible();const confirm=page.getByRole('button',{name:'确认五线谱并保存练习版本',exact:true});if(await confirm.count())await confirm.click();await page.getByRole('button',{name:'开始新的认音轮',exact:true}).click();await expect(page.getByTestId('practice-capture')).toContainText('认音 1/8');await page.keyboard.press('j');await expect(page.getByTestId('practice-result')).toContainText('尝试1次');
  await page.route('**/api/practice/units/reading-white-keys/prepare',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'隔离测试：本地教材不可用'})}));
  await page.getByRole('button',{name:'查看练习：走完一个八度',exact:true}).click();await expect(page.getByRole('alert')).toContainText('本地教材不可用');await expect(page.getByTestId('active-unit')).toContainText('认识 C–G');await expect(page.getByTestId('practice-result')).toContainText('尝试1次');await expect(page.getByTestId('practice-capture')).toHaveAttribute('data-phase','paused');
});
