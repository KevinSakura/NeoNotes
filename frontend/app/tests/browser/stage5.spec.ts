import { expect, test, type Page } from '@playwright/test';

async function fixture(page: Page, name: string) {
  await page.addInitScript(() => {
    const create = AudioContext.prototype.createAnalyser;
    AudioContext.prototype.createAnalyser = function() {
      const analyser = create.call(this);
      (window as unknown as {practiceAnalyser: AnalyserNode}).practiceAnalyser = analyser;
      return analyser;
    };
  });
  const token = (await (await page.request.get('/api/session')).json()).token;
  const headers = { 'X-Session-Token': token };
  let project = await (await page.request.post('/api/projects', {headers, data: {name}})).json();
  let state = project.revision.state;
  state.inputs = {...state.inputs, theme: '重新出发', emotion: '明亮轻快', lyrics_text: '向前\n远行', selected_lines: [0,1]};
  project = await (await page.request.put(`/api/projects/${project.id}`, {headers, data: {base_revision: project.current_revision, state}})).json();
  const response = await page.request.post(`/api/projects/${project.id}/generations`, {headers, data: {base_revision: project.current_revision}});
  expect(response.status()).toBe(202);
  const job = await response.json();
  expect(job.mock).toBe(true);
  await expect.poll(async () => (await (await page.request.get(`/api/projects/${project.id}/generations/${job.id}`)).json()).status).toBe('completed');
  project = await (await page.request.post(`/api/projects/${project.id}/generations/${job.id}/apply`, {headers, data: {base_revision: project.current_revision}})).json();
  state = project.revision.state;
  const lead = state.score.tracks[0];
  const tail = lead.notes.at(-1);
  lead.notes = [0,384,1536,1920].map((start_tick, i) => ({id: `practice-${i}`, pitch: 60+i*2, velocity: 90, start_tick, duration_tick: 240}));
  lead.notes.push(tail);
  state.composition.lyrics_alignment.forEach((unit: {note_ids: string[]}, i: number) => {unit.note_ids = [`practice-${i}`];});
  project = await (await page.request.put(`/api/projects/${project.id}`, {headers, data: {base_revision: project.current_revision, state}})).json();
  expect((await (await page.request.get(`/api/projects/${project.id}/review`)).json()).valid).toBe(true);
  await page.goto('/');
  await page.locator('.project-item').filter({ hasText: name }).click();
  return {project, headers};
}

test('manual lyric repair is explicit, rejects duplicate use, and participates in draft and disk undo', async ({page}, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const {project} = await fixture(page, '第五阶段人工修复');
  await page.getByRole('button', {name: '02 演奏与编辑', exact: true}).click();
  const panel = page.getByTestId('lyric-repair');
  await panel.getByTestId('repair-word').first().click();
  await page.getByTestId('roll-note').first().click();
  await page.getByRole('button', {name: '删除音符', exact: true}).click();
  await expect(panel.getByTestId('repair-word').first()).toContainText('对应缺失');
  await panel.getByTestId('repair-word').first().click();
  await expect(panel.getByTestId('repair-original')).toContainText('已删除的音符');
  await panel.getByRole('checkbox').first().check();
  await panel.getByRole('button', {name: '确认绑定到这个字词', exact: true}).click();
  await expect(page.getByRole('alert').filter({ hasText: '已用于其他字词' })).toBeVisible();
  const roll = page.locator('svg.roll');
  // Add C4 at beat zero, then shorten it so the next word remains chronological.
  const rect = await roll.boundingBox();
  expect(rect).not.toBeNull();
  await roll.click({position: {x: 66, y: 25+(85-60)*18+7}});
  await page.getByRole('spinbutton', {name: '时长（拍）', exact: true}).fill('0.5');
  await page.getByRole('button', {name: '应用修改', exact: true}).click();
  const originalSelection = panel.getByRole('checkbox').filter({visible: true});
  for (const box of await originalSelection.all()) if (await box.isChecked()) await box.uncheck();
  await panel.getByRole('checkbox').first().check();
  await panel.getByRole('button', {name: '确认绑定到这个字词', exact: true}).click();
  await expect(page.getByRole('alert').filter({ hasText: '歌词对应已更新到草稿' })).toBeVisible();
  await expect(panel.getByTestId('repair-word').first()).not.toContainText('对应缺失');
  await page.getByRole('button', {name: '↶ 撤销', exact: true}).click();
  await expect(panel.getByTestId('repair-word').first()).toContainText('对应缺失');
  await page.getByRole('button', {name: '↷ 重做', exact: true}).click();
  await expect(panel.getByTestId('repair-word').first()).not.toContainText('对应缺失');
  await page.getByRole('button', {name: '保存歌曲', exact: true}).click();
  await expect(page.getByTestId('save-state')).toContainText('已写入歌曲目录');
  await page.getByRole('button', {name: '去保存与导出 →', exact: true}).click();
  await expect(page.getByTestId('assessment-report')).toContainText('来源完成');
  const saved = await (await page.request.get(`/api/projects/${project.id}`)).json();
  expect(saved.revision.state.composition.lyrics_alignment[0].note_ids[0]).not.toBe('practice-0');
  await page.reload();
  await page.locator('.project-item').filter({ hasText: '第五阶段人工修复' }).click();
  await page.getByRole('button', {name: '02 演奏与编辑', exact: true}).click();
  await expect(page.getByTestId('lyric-repair').getByTestId('repair-word').first()).not.toContainText('对应缺失');
  await page.getByRole('button', {name: '↶ 撤销', exact: true}).click();
  const restored = await (await page.request.get(`/api/projects/${project.id}`)).json();
  expect(restored.current_revision).toBe(project.current_revision);
  await page.getByRole('button', {name: '↷ 重做', exact: true}).click();
  expect((await (await page.request.get(`/api/projects/${project.id}`)).json()).current_revision).toBe(saved.current_revision);
  await page.setViewportSize({width: 390, height: 844});
  await page.getByTestId('lyric-repair').getByTestId('repair-word').first().click();
  await expect(page.getByTestId('repair-selection')).toContainText('C4');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: testInfo.outputPath('repair-mobile.png'), fullPage: true});
  expect(errors).toEqual([]);
});

test('slow audible line practice loops twice, leaves song and MIDI unchanged, and cancels on stop and page switch', async ({page}, testInfo) => {
  test.setTimeout(45000);
  const errors: string[] = [], modelRequests: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const {project, headers} = await fixture(page, '第五阶段慢速循环');
  const midiUrl = `/api/projects/${project.id}/midi-export`;
  const data = {base_revision: project.current_revision};
  const beforeMidi = await (await page.request.post(midiUrl, {headers, data})).body();
  page.on('request', request => {if (request.method() === 'POST' && request.url().includes('/generations')) modelRequests.push(request.url());});
  await page.getByRole('button', {name: '03 保存与导出', exact: true}).click();
  await page.getByRole('combobox', {name: '练唱速度', exact: true}).selectOption('0.75');
  await page.getByRole('checkbox', {name: '循环这一句', exact: true}).check();
  await page.evaluate(() => {
    const element = document.querySelector('[data-testid="lyrics-alignment"] [aria-live]')!;
    const samples: number[] = [];
    (window as unknown as {practiceSamples: number[]}).practiceSamples = samples;
    new MutationObserver(() => {const found = element.textContent?.match(/原曲 ([\d.]+) 秒/); if (found) samples.push(Number(found[1]));}).observe(element, {subtree: true, characterData: true, childList: true});
  });
  await page.getByRole('button', {name: '听第 1 行歌词', exact: true}).click();
  await expect.poll(async () => Number(await page.getByTestId('playback-audio-level').getAttribute('value'))).toBeGreaterThan(.0001);
  await expect.poll(async () => page.evaluate(() => {
    const values = (window as unknown as {practiceSamples: number[]}).practiceSamples;
    return values.filter((value, i) => i && values[i-1]-value > .5).length;
  }), {timeout: 10000}).toBeGreaterThanOrEqual(2);
  await expect(page.getByRole('checkbox', {name: '已亲自照所选歌词试唱，音域与停顿合适', exact: true})).not.toBeChecked();
  await page.getByRole('button', {name: '停止试唱试听', exact: true}).click();
  await expect(page.locator('.floating-player')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => {
    const analyser = (window as unknown as {practiceAnalyser: AnalyserNode}).practiceAnalyser;
    const samples = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(samples);
    return Math.max(...samples.map(Math.abs));
  })).toBeLessThan(.0001);
  await page.waitForTimeout(2100);
  await expect(page.locator('.floating-player')).toHaveCount(0);
  await page.getByRole('button', {name: '听第 1 行歌词', exact: true}).click();
  await expect(page.locator('.floating-player')).toBeVisible();
  await page.getByRole('button', {name: '听第 2 行歌词', exact: true}).click();
  await expect(page.getByText(/跟随位置：原曲/)).toContainText(/原曲 [23]\./);
  await page.getByRole('button', {name: '02 演奏与编辑', exact: true}).click();
  await expect(page.locator('.floating-player')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => {
    const analyser = (window as unknown as {practiceAnalyser: AnalyserNode}).practiceAnalyser;
    const samples = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(samples);
    return Math.max(...samples.map(Math.abs));
  })).toBeLessThan(.0001);
  const after = await (await page.request.get(`/api/projects/${project.id}`)).json();
  expect(after).toEqual(project);
  const afterMidi = await (await page.request.post(midiUrl, {headers, data})).body();
  expect(afterMidi.equals(beforeMidi)).toBe(true);
  await page.getByRole('button', {name: '去保存与导出 →', exact: true}).click();
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('combobox', {name: '练唱速度', exact: true}).selectOption('0.75');
  await page.getByRole('checkbox', {name: '循环这一句', exact: true}).check();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: testInfo.outputPath('practice-mobile.png'), fullPage: true});
  expect(modelRequests).toEqual([]);
  expect(errors).toEqual([]);
});
