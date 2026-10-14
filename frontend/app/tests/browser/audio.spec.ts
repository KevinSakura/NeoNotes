import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';

async function create(page: import('@playwright/test').Page, name: string) {
  await page.getByRole('button', { name: '＋ 新建歌曲', exact: true }).click();
  await page.getByRole('textbox', { name: '歌曲名称' }).fill(name);
  await page.getByRole('button', { name: '创建歌曲', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '主题', exact: true })).toBeVisible();
}

test('actual SoundFont four instruments, recorded playback, WAV/MP3, refresh and old-version notice', async ({ page }, testInfo) => {
  test.setTimeout(60000);
  const errors: string[] = [], hosts = new Set<string>(); let fonts = 0;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { hosts.add(new URL(request.url()).hostname); if (request.url().includes('/audio/soundfont?')) fonts++; });
  await page.goto('/'); await create(page, '真实音色与导出');
  await page.getByRole('button', { name: '● 开始录制', exact: true }).click();
  await expect(page.getByRole('button', { name: '■ 停止录制', exact: true })).toBeVisible();
  await expect(page.getByTestId('audio-status')).toContainText('已就绪');
  for (const name of ['键盘', '鼓', '吉他', '贝斯']) {
    await page.getByRole('button', { name, exact: true }).click();
    await page.keyboard.down('a');
    await expect.poll(async () => Number(await page.getByTestId('audio-level').getAttribute('value'))).toBeGreaterThan(0.0001);
    // Record a sustained musical note; 20ms notes can end before playback meter polling starts.
    await page.waitForTimeout(350);
    await page.keyboard.up('a');
  }
  await page.getByRole('button', { name: '■ 停止录制', exact: true }).click();
  await expect(page.getByTestId('record-info')).toContainText('4 个音符');
  await page.getByRole('button', { name: '▷ 回放片段', exact: true }).click();
  await expect(page.locator('.floating-player')).toBeVisible();
  await expect.poll(async () => Number(await page.getByTestId('audio-level').getAttribute('value'))).toBeGreaterThan(0.0001);
  await page.locator('.floating-player').getByRole('button', { name: '■ 停止', exact: true }).click();
  await page.getByRole('button', { name: '把片段加入编曲 →', exact: true }).click();
  await page.getByRole('button', { name: /03\s*保存与导出/ }).click();
  await page.getByRole('button', { name: '生成 WAV 与 MP3', exact: true }).click();
  await expect(page.getByTestId('render-status').first()).toContainText('已完成', { timeout: 40000 });
  const player = page.getByLabel('渲染后的 WAV 试听');
  await expect.poll(() => player.evaluate(element => (element as HTMLAudioElement).readyState)).toBeGreaterThanOrEqual(1);
  expect(await player.evaluate(element => (element as HTMLAudioElement).duration)).toBeCloseTo(63, 2);
  await player.evaluate(element => (element as HTMLAudioElement).play());
  await expect.poll(() => player.evaluate(element => (element as HTMLAudioElement).currentTime)).toBeGreaterThan(0.05);
  await player.evaluate(element => (element as HTMLAudioElement).pause());
  for (const extension of ['MP3', 'WAV']) {
    const event = page.waitForEvent('download');
    await page.getByRole('link', { name: `下载 ${extension}`, exact: true }).first().click();
    const file = await event; const data = await readFile((await file.path())!);
    expect(file.suggestedFilename()).toMatch(new RegExp(`\\.${extension.toLowerCase()}$`));
    expect(data.length).toBeGreaterThan(10000);
    if (extension === 'WAV') { expect(data.subarray(0, 4).toString()).toBe('RIFF'); expect(data.subarray(8, 12).toString()).toBe('WAVE'); }
    else expect(data.subarray(0, 3).toString()).toBe('ID3');
  }
  await page.screenshot({ path: testInfo.outputPath('audio-export.png'), fullPage: true });
  await page.reload();
  await page.getByRole('button', { name: /真实音色与导出.*本地歌曲/ }).click();
  await page.getByRole('button', { name: /03\s*保存与导出/ }).click();
  await expect(page.getByTestId('render-status')).toHaveCount(1);
  await expect(page.locator('.render-player')).toContainText('对应当前保存版本');
  await page.getByRole('button', { name: /02\s*演奏与编辑/ }).click();
  await page.getByLabel('贝斯音色', { exact: true }).selectOption('34');
  await page.getByRole('button', { name: /03\s*保存与导出/ }).click();
  await expect(page.locator('.audio-exports .render-notice')).toContainText('未保存修改');
  await expect(page.locator('.render-player')).toContainText('历史音频');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('mobile-export.png'), fullPage: true });
  await page.getByRole('button', { name: '生成 WAV 与 MP3', exact: true }).click();
  await expect(page.getByTestId('render-status').first()).toContainText('已完成', { timeout: 40000 });
  await expect(page.getByTestId('render-status')).toHaveCount(2);
  await page.getByRole('button', { name: '试听 v2 当前混音', exact: true }).click();
  await expect(page.locator('.render-player')).toContainText('v2');
  await expect(page.locator('.render-player')).toContainText('历史音频');
  await page.getByRole('button', { name: '试听 v3 当前混音', exact: true }).click();
  await expect(page.locator('.render-player')).toContainText('对应当前保存版本');
  expect(fonts).toBe(1);
  expect([...hosts]).toEqual(['127.0.0.1']);
  expect(errors).toEqual([]);
});

test('missing SoundFont reports failure, keeps text, retries without synthesizer fallback', async ({ page }) => {
  await page.goto('/'); await create(page, '加载失败保护');
  await page.getByRole('textbox', { name: '主题', exact: true }).fill('保留这个主题');
  await page.route('**/api/audio/soundfont**', route => route.fulfill({ status: 503, body: 'unavailable' }));
  await page.getByRole('button', { name: '● 开始录制', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('音色文件读取失败');
  await expect(page.getByTestId('audio-status')).toContainText('加载失败');
  await expect(page.getByTestId('record-info')).toContainText('0 个音符');
  await expect(page.getByRole('textbox', { name: '主题', exact: true })).toHaveValue('保留这个主题');
  await page.unroute('**/api/audio/soundfont**');
  await page.getByRole('button', { name: '● 开始录制', exact: true }).click();
  await expect(page.getByRole('button', { name: '■ 停止录制', exact: true })).toBeVisible();
  await expect(page.getByTestId('audio-status')).toContainText('已就绪');
  await page.getByRole('button', { name: '■ 停止录制', exact: true }).click();
});
