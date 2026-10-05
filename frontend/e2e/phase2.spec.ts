import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('MP4 upload, GPU analysis, authenticated preview and start/stop', async ({page, request}) => {
  test.setTimeout(60000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  await page.goto('/');
  await page.getByLabel('Username', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('Password', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'Log In', exact:true}).click();
  await expect(page.getByRole('heading', {name:'System Readiness'})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  const headers = {'X-CSRF-Token':auth.csrf_token};
  const controls = await (await page.request.get('/api/function-settings')).json();
  const name = `MP4-E2E-${Date.now()}`;
  let cameraId: number | undefined;
  try {
    await page.getByRole('navigation').getByRole('button', {name:/Cameras/}).click();
    await page.getByLabel('RTSP Address', {exact:true}).fill('not-an-rtsp-url');
    await page.getByLabel('Input Type').selectOption('mp4');
    await page.getByRole('button', {name:'Save Camera'}).click();
    await expect(page.getByRole('alert')).toHaveText('Enter a camera name.');
    await page.getByLabel('Camera Name', {exact:true}).fill(name);
    const saved = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/cameras');
    await page.getByRole('button', {name:'Save Camera'}).click();
    const created = await saved;
    if (created.status() === 201) cameraId = (await created.json()).camera_id;
    expect(created.status()).toBe(201);
    expect((await created.json()).rtsp_url).toBe('');
    expect(created.request().postDataJSON().rtsp_url).toBe('');
    const row = page.getByRole('row').filter({hasText:name});
    await expect(row).toBeVisible();
    await row.getByRole('button', {name:'Analyze Video', exact:true}).click();
    await page.getByLabel('Test MP4 Upload').setInputFiles('../data/videos/people.mp4');
    await expect(page.getByText('Test video uploaded. Click Start Analysis.')).toBeVisible();
    await expect(page.getByLabel('Camera', {exact:true}).locator('option:checked')).toHaveText(name);
    await page.getByRole('button', {name:'Start Analysis', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('Analyzing', {timeout:15000});
    const image = page.getByAltText('Camera video showing detection results and track IDs');
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
    await expect.poll(async () => {
      const state = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
      return controls.values.person_detection_enabled === false
        ? state.result?.detection_mode === 'face' && state.processed_frames > 0
        : state.max_people > 0;
    }).toBe(true);
    expect((await request.get(`/api/cameras/${cameraId}/preview`)).status()).toBe(401);
    const first = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    mkdirSync('../data/screenshots', {recursive:true});
    await page.screenshot({path:'../data/screenshots/phase2-live.png', fullPage:true});
    await page.getByRole('button', {name:'Stop Analysis', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('Stopped');
    await expect(image).toHaveCount(0);
    await page.getByRole('button', {name:'Start Analysis', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('Analyzing', {timeout:15000});
    const restarted = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    expect(restarted.stream_session_id).not.toBe(first.stream_session_id);
    await page.getByRole('button', {name:'Stop Analysis', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('Stopped');
  } finally {
    if (cameraId !== undefined) {
      await page.request.post(`/api/cameras/${cameraId}/stop`, {headers});
      expect((await page.request.delete(`/api/cameras/${cameraId}`, {headers})).status()).toBe(204);
    }
    await page.request.post('/api/auth/logout', {headers});
  }
});
