import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('Face quality, aligned thumbnails, embeddings and stale-session access', async ({page, request}) => {
  test.setTimeout(60000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  await page.goto('/');
  await page.getByLabel('Username', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('Password', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'Log In', exact:true}).click();
  await expect(page.getByRole('heading', {name:'System Readiness'})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  const headers = {'X-CSRF-Token':auth.csrf_token};
  const name = `FACE-E2E-${Date.now()}`;
  let cameraId: number | undefined;
  try {
    const created = await page.request.post('/api/cameras', {headers, data:{name, source_type:'mp4'}});
    expect(created.status()).toBe(201);
    cameraId = (await created.json()).camera_id;
    await page.getByRole('button', {name:'Refresh', exact:true}).click();
    await page.getByRole('navigation').getByRole('button', {name:/Cameras/}).click();
    const row = page.getByRole('row').filter({hasText:name});
    await expect(row).toBeVisible();
    await row.getByRole('button', {name:'Analyze Video', exact:true}).click();
    await page.getByLabel('Test MP4 Upload').setInputFiles('../data/videos/face-smoke.mp4');
    await expect(page.getByText('Test video uploaded. Click Start Analysis.')).toBeVisible();
    await page.getByRole('button', {name:'Start Analysis', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('Analyzing', {timeout:15000});
    const thumb = page.locator('.face-ready img').first();
    await expect(thumb).toBeVisible({timeout:15000});
    await expect.poll(() => thumb.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(112);
    await expect(page.getByText('Face features ready', {exact:true}).first()).toBeVisible();
    const first = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    expect(first.face_counts.embeddings_created).toBeGreaterThan(0);
    const track = first.result.tracks.find((item: {face?: {embedding_ready?: boolean}}) => item.face?.embedding_ready);
    const oldPath = `/api/cameras/${cameraId}/faces/${track.track_id}?stream_session_id=${first.stream_session_id}`;
    expect((await request.get(oldPath)).status()).toBe(401);
    expect((await page.request.get(oldPath)).status()).toBe(200);
    mkdirSync('../data/screenshots', {recursive:true});
    await page.screenshot({path:'../data/screenshots/phase3-live.png', fullPage:true});
    await page.getByRole('button', {name:'Stop Analysis', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('Stopped');
    await expect(page.locator('.face-card')).toHaveCount(0);
    expect((await page.request.get(oldPath)).status()).toBe(404);
    await page.getByRole('button', {name:'Start Analysis', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('Analyzing', {timeout:15000});
    await expect(page.locator('.face-ready img').first()).toBeVisible({timeout:15000});
    const second = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    expect(second.stream_session_id).not.toBe(first.stream_session_id);
    expect((await page.request.get(oldPath)).status()).toBe(404);
  } finally {
    if (cameraId !== undefined) {
      await page.request.post(`/api/cameras/${cameraId}/stop`, {headers});
      expect((await page.request.delete(`/api/cameras/${cameraId}`, {headers})).status()).toBe(204);
    }
    await page.request.post('/api/auth/logout', {headers});
  }
});
