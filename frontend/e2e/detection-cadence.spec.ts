import {expect, test} from '@playwright/test';
import {mkdirSync, mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

test.use({actionTimeout:10000, navigationTimeout:20000});

test('사람·얼굴 독립 FPS, 모든 프레임 검사, 저장·반영·모바일 표시', async ({page}) => {
  test.setTimeout(120000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  let original: Record<string, number | boolean> | undefined;
  let ownedRevision: number | undefined;
  let cameraId: number | undefined;
  let headers: Record<string, string> = {};
  const fixtureDir = mkdtempSync(join(tmpdir(), 'face-cadence-'));
  const fixture = join(fixtureDir, 'two-seconds.mp4');
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({width:1600, height:1000});
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태', exact:true})).toBeVisible();
  headers = {'X-CSRF-Token': (await (await page.request.get('/api/auth/me')).json()).csrf_token};
  const save = async () => {
    const response = page.waitForResponse(r => r.url().endsWith('/api/function-settings') && r.request().method() === 'PUT');
    await page.getByRole('button', {name:'설정 저장', exact:true}).click();
    ownedRevision = (await (await response).json()).revision;
    await expect.poll(async () => (await (await page.request.get('/api/function-settings')).json()).applied).toBe(true);
    await expect(page.locator('.settings-state')).toContainText('반영 완료');
  };
  try {
    execFileSync('ffmpeg', ['-v', 'error', '-i', '../data/videos/face-smoke.mp4', '-t', '2', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', fixture]);
    original = (await (await page.request.get('/api/function-settings')).json()).values;
    await page.getByRole('navigation', {name:'주 메뉴'}).getByRole('button', {name:'기능 설정'}).click();
    for (const name of ['사람 검출', '얼굴 검출', '등록 인물 비교']) {
      await expect(page.getByRole('heading', {name, exact:true})).toBeVisible();
    }
    const personAll = page.getByLabel('사람 검출 · 모든 프레임 검사', {exact:true});
    const faceAll = page.getByLabel('얼굴 검출 · 모든 프레임 검사', {exact:true});
    const personFps = page.getByLabel('사람 검출 빈도 (FPS)', {exact:true});
    const faceFps = page.getByLabel('얼굴 검출 빈도 (FPS)', {exact:true});
    await personAll.uncheck(); await faceAll.uncheck();
    await expect(page.getByLabel('사람 검출 사용', {exact:true})).toHaveCount(0);
    for (const field of [personFps, faceFps]) {
      await field.fill('0');
      await expect(page.getByRole('button', {name:'설정 저장', exact:true})).toBeDisabled();
      await field.fill('241');
      await expect(page.getByRole('button', {name:'설정 저장', exact:true})).toBeDisabled();
      await field.fill('5');
    }
    await faceAll.check();
    await expect(faceFps).toBeDisabled(); await expect(personFps).toBeEnabled();
    await save();
    await page.reload();
    await expect(faceAll).toBeChecked(); await expect(faceFps).toBeDisabled();
    await expect(personFps).toHaveValue('5');
    mkdirSync('../data/screenshots', {recursive:true});
    await page.locator('app-function-settings').screenshot({path:'../data/screenshots/detection-cadence-desktop.png'});
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.locator('app-function-settings').screenshot({path:'../data/screenshots/detection-cadence-mobile.png'});
    await page.setViewportSize({width:1600, height:1000});
    const camera = await page.request.post('/api/cameras', {headers, data:{name:`CADENCE-E2E-${Date.now()}`, source_type:'mp4'}});
    expect(camera.status()).toBe(201); cameraId = (await camera.json()).camera_id;
    expect((await page.request.put(`/api/cameras/${cameraId}/video`, {
      headers:{...headers, 'Content-Type':'video/mp4'}, data:readFileSync(fixture)
    })).status()).toBe(200);
    const analyze = async (personFrames: number, faceFrames: number) => {
      expect((await page.request.post(`/api/cameras/${cameraId}/start`, {headers, data:{source_type:'mp4', loop:false, person_detection_enabled:true, face_detection_enabled:true}})).status()).toBe(200);
      await expect.poll(async () => (await (await page.request.get(`/api/cameras/${cameraId}/status`)).json()).state, {timeout:45000}).toBe('ended');
      const result = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
      expect(result.actual_device).toMatch(/^cuda(?::\d+)?$/);
      expect(result.captured_frames).toBe(20); expect(result.processed_frames).toBe(20);
      expect(result.dropped_frames).toBe(0);
      expect(result.person_detection_frames).toBe(personFrames);
      expect(result.face_detection_frames).toBe(faceFrames);
      expect((await page.request.post(`/api/cameras/${cameraId}/stop`, {headers})).status()).toBe(200);
    };
    await analyze(10, 20);
    await personAll.check(); await faceAll.uncheck(); await faceFps.fill('2');
    await expect(personFps).toBeDisabled(); await expect(faceFps).toBeEnabled();
    await save();
    await analyze(20, 4);
    await page.goto(`/#/live/${cameraId}`);
    const personEnabled = page.getByLabel('사람 검출 사용', {exact:true});
    const faceEnabled = page.getByLabel('얼굴 검출 사용', {exact:true});
    const start = page.getByRole('button', {name:'분석 시작', exact:true});
    await expect(personEnabled).toBeEnabled();
    await personEnabled.uncheck(); await faceEnabled.uncheck();
    await expect(start).toBeDisabled();
    await expect(page.locator('.analysis-detection-options [role="status"]')).toContainText('하나 이상 선택');
    await personEnabled.check();
    await page.getByLabel('반복 재생', {exact:true}).uncheck();
    const personResponse = page.waitForResponse(r => r.url().endsWith(`/api/cameras/${cameraId}/start`) && r.request().method() === 'POST');
    await start.click();
    const personRun = await (await personResponse).json();
    expect(personRun.person_detection_enabled).toBe(true);
    expect(personRun.face_detection_enabled).toBe(false);
    await expect(personEnabled).toBeDisabled(); await expect(faceEnabled).toBeDisabled();
    await expect.poll(async () => (await (await page.request.get(`/api/cameras/${cameraId}/status`)).json()).state).toBe('ended');
    const peopleOnly = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    expect(peopleOnly.face_detection_frames).toBe(0);
    expect(peopleOnly.face_counts.embeddings_created ?? 0).toBe(0);
    await expect(personEnabled).toBeEnabled();
    await personEnabled.uncheck(); await faceEnabled.check();
    await page.getByLabel('반복 재생', {exact:true}).check();
    const faceResponse = page.waitForResponse(r => r.url().endsWith(`/api/cameras/${cameraId}/start`) && r.request().method() === 'POST');
    await start.click();
    const faceRun = await (await faceResponse).json();
    expect(faceRun.person_detection_enabled).toBe(false);
    expect(faceRun.face_detection_enabled).toBe(true);
    await page.reload();
    await expect(personEnabled).not.toBeChecked(); await expect(faceEnabled).toBeChecked();
    await expect(personEnabled).toBeDisabled(); await expect(faceEnabled).toBeDisabled();
    mkdirSync('../data/screenshots', {recursive:true});
    await page.locator('.analysis-controls').screenshot({path:'../data/screenshots/live-detection-selection-desktop.png'});
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.locator('.analysis-controls').screenshot({path:'../data/screenshots/live-detection-selection-mobile.png'});
    await page.getByRole('button', {name:'분석 중지', exact:true}).click();
    await expect(faceEnabled).toBeEnabled();
    expect(errors).toEqual([]);
  } finally {
    rmSync(fixtureDir, {recursive:true, force:true});
    if (cameraId) {
      await page.request.post(`/api/cameras/${cameraId}/stop`, {headers});
      await page.request.delete(`/api/cameras/${cameraId}`, {headers});
    }
    if (original && ownedRevision !== undefined) {
      const current = await (await page.request.get('/api/function-settings')).json();
      if (current.revision === ownedRevision) {
        expect([200,202]).toContain((await page.request.put('/api/function-settings', {headers, data:{...original, revision:current.revision}})).status());
        await expect.poll(async () => (await (await page.request.get('/api/function-settings')).json()).applied).toBe(true);
      }
    }
    await page.request.post('/api/auth/logout', {headers});
  }
});
