import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';

test('Phase 8 CUDA 검색 이벤트 클립 저장, 인증 재생과 삭제', async ({page, playwright}) => {
  test.setTimeout(90000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  const prefix = `CLIP-E2E-${Date.now()}`;
  let personId: number | undefined, cameraId: number | undefined;
  let headers: Record<string, string> = {};
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태'})).toBeVisible();
  headers = {'X-CSRF-Token':(await (await page.request.get('/api/auth/me')).json()).csrf_token};
  try {
    personId = (await (await page.request.post('/api/persons', {headers, data:{name:prefix}})).json()).id;
    expect((await page.request.post(`/api/persons/${personId}/faces`, {headers:{...headers,'Content-Type':'image/jpeg'}, data:readFileSync('../data/calibration/person_b_reference.jpg')})).status()).toBe(201);
    cameraId = (await (await page.request.post('/api/cameras', {headers, data:{name:prefix, source_type:'mp4'}})).json()).camera_id;
    expect((await page.request.put(`/api/cameras/${cameraId}/video`, {headers:{...headers,'Content-Type':'video/mp4'}, data:readFileSync('../data/videos/face-smoke.mp4')})).status()).toBe(200);
    await page.goto(`/#/live/${cameraId}`);
    const started = await page.request.post(`/api/cameras/${cameraId}/start`, {headers, data:{source_type:'mp4',loop:false}});
    expect((await started.json()).actual_device).toMatch(/^cuda/);
    let event: any;
    await expect.poll(async () => {
      const rows = (await (await page.request.get('/api/events?latest=true&limit=100')).json()).items;
      event = rows.find((row:any) => row.camera_id === cameraId && row.person_id === personId);
      return event?.clip_state;
    }, {timeout:35000}).toBe('ready');
    expect(event.clip_details.partial).toBe(true);
    const card = page.locator(`.event-card[data-event-id="${event.event_id}"]`);
    await expect(card.locator('summary')).toContainText('일부 구간');
    await card.locator('summary').click();
    const video = card.locator('video');
    await video.evaluate((element:HTMLVideoElement) => element.load());
    await expect.poll(() => video.evaluate((element:HTMLVideoElement) => element.duration)).toBeGreaterThan(0);
    await video.evaluate((element:HTMLVideoElement) => element.play());
    await expect.poll(() => video.evaluate((element:HTMLVideoElement) => element.currentTime)).toBeGreaterThan(.1);
    const ranged = await page.request.get(event.video_clip_url, {headers:{Range:'bytes=0-31'}});
    expect(ranged.status()).toBe(206);
    const anonymous = await playwright.request.newContext({baseURL:'http://127.0.0.1:4200'});
    expect((await anonymous.get(event.video_clip_url)).status()).toBe(401);
    await anonymous.dispose();
    mkdirSync('../data/screenshots', {recursive:true});
    await card.screenshot({path:'../data/screenshots/phase8-clip.png'});
    expect((await page.request.delete(`/api/events/${event.event_id}`, {headers})).status()).toBe(200);
    expect((await page.request.get(event.video_clip_url)).status()).toBe(404);
    mkdirSync('../data/reports', {recursive:true});
    writeFileSync('../data/reports/phase8-browser.json', JSON.stringify({status:'passed', device:'cuda:0', checks:['native_candidate','partial_clip','browser_playback','range','anonymous_denied','delete'], details:event.clip_details}, null, 2));
  } finally {
    if (cameraId) {await page.request.post(`/api/cameras/${cameraId}/stop`, {headers}); await page.request.delete(`/api/cameras/${cameraId}`, {headers});}
    if (personId) await page.request.delete(`/api/persons/${personId}`, {headers});
    await page.request.post('/api/auth/logout', {headers});
  }
});
