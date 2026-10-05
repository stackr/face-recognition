import {expect, test} from '@playwright/test';
import {mkdirSync, mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

test.use({actionTimeout:10000, navigationTimeout:20000});

test('Independent detector FPS, every-frame processing and mobile display', async ({page}) => {
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
  await page.getByLabel('Username', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('Password', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'Log In', exact:true}).click();
  await expect(page.getByRole('heading', {name:'System Readiness', exact:true})).toBeVisible();
  headers = {'X-CSRF-Token': (await (await page.request.get('/api/auth/me')).json()).csrf_token};
  const save = async () => {
    const response = page.waitForResponse(r => r.url().endsWith('/api/function-settings') && r.request().method() === 'PUT');
    await page.getByRole('button', {name:'Save Settings', exact:true}).click();
    ownedRevision = (await (await response).json()).revision;
    await expect.poll(async () => (await (await page.request.get('/api/function-settings')).json()).applied).toBe(true);
  };
  try {
    execFileSync('ffmpeg', ['-v', 'error', '-i', '../data/videos/face-smoke.mp4', '-t', '2', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', fixture]);
    original = (await (await page.request.get('/api/function-settings')).json()).values;
    await page.getByRole('navigation', {name:'Main navigation'}).getByRole('button', {name:'Feature Settings'}).click();
    for (const name of ['Person Detection', 'Face Detection', 'Registered Person Comparison']) {
      await expect(page.getByRole('heading', {name, exact:true})).toBeVisible();
    }
    const personAll = page.getByLabel('Person Detection · Process Every Frame', {exact:true});
    const faceAll = page.getByLabel('Face Detection · Process Every Frame', {exact:true});
    const personFps = page.getByLabel('Person Detection Rate (FPS)', {exact:true});
    const faceFps = page.getByLabel('Face Detection Rate (FPS)', {exact:true});
    await personAll.uncheck(); await faceAll.uncheck();
    await expect(page.getByLabel('Enable Person Detection', {exact:true})).toHaveCount(0);
    for (const field of [personFps, faceFps]) {
      await field.fill('0');
      await expect(page.getByRole('button', {name:'Save Settings', exact:true})).toBeDisabled();
      await field.fill('241');
      await expect(page.getByRole('button', {name:'Save Settings', exact:true})).toBeDisabled();
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
    const personEnabled = page.getByLabel('Enable Person Detection', {exact:true});
    const faceEnabled = page.getByLabel('Enable Face Detection', {exact:true});
    const start = page.getByRole('button', {name:'Start Analysis', exact:true});
    await expect(personEnabled).toBeEnabled();
    await personEnabled.uncheck(); await faceEnabled.uncheck();
    await expect(start).toBeDisabled();
    await expect(page.locator('.analysis-detection-options [role="status"]')).toContainText('Select at least one detector');
    await personEnabled.check();
    await page.getByLabel('Loop Playback', {exact:true}).check();
    const personResponse = page.waitForResponse(r => r.url().endsWith(`/api/cameras/${cameraId}/start`) && r.request().method() === 'POST');
    await start.click();
    const personRun = await (await personResponse).json();
    expect(personRun.person_detection_enabled).toBe(true);
    expect(personRun.face_detection_enabled).toBe(false);
    await expect(personEnabled).toBeDisabled(); await expect(faceEnabled).toBeDisabled();
    const personPhotos = page.locator('.person-grid .person-track-image');
    await expect(personPhotos.first()).toBeVisible();
    await expect.poll(() => personPhotos.first().evaluate((img: HTMLImageElement) => img.naturalHeight)).toBeGreaterThan(0);
    await expect(page.locator('.faces-panel').getByRole('heading', {name:'Face Detection', exact:true})).toHaveCount(0);
    await page.locator('.faces-panel').screenshot({path:'../data/screenshots/person-tracks-only-desktop.png'});
    const peopleOnly = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    expect(peopleOnly.face_detection_frames).toBe(0);
    expect(peopleOnly.face_counts.embeddings_created ?? 0).toBe(0);
    await page.getByRole('button', {name:'Stop Analysis', exact:true}).click();
    await expect(personEnabled).toBeEnabled();
    await personEnabled.uncheck(); await faceEnabled.check();
    await page.getByLabel('Loop Playback', {exact:true}).check();
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
    await page.getByRole('button', {name:'Stop Analysis', exact:true}).click();
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
