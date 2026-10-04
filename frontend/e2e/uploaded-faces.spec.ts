import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test.use({actionTimeout:10000, navigationTimeout:20000});

test('업로드 MP4 얼굴 직접 검출, 등록 인물 이벤트, 기준 변경 및 사람 검출 복원', async ({page, request}) => {
  test.setTimeout(120000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  const prefix = `VIDEO-FACES-E2E-${Date.now()}`;
  let cameraId: number | undefined;
  let personId: number | undefined;
  let originalValues: Record<string, number | boolean> | undefined;
  let ownedRevision: number | undefined;
  let headers: Record<string, string> = {};
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' && message.text().startsWith('ERROR')) errors.push(message.text().split('\n')[0]);
  });
  await page.setViewportSize({width:1600, height:1000});
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태', exact:true})).toBeVisible();
  headers = {'X-CSRF-Token': (await (await page.request.get('/api/auth/me')).json()).csrf_token};
  const settingsPage = async () => {
    await page.getByRole('navigation', {name:'주 메뉴'}).getByRole('button', {name:'기능 설정'}).click();
    await expect(page.locator('app-function-settings')).toBeVisible();
  };
  const save = async () => {
    const response = page.waitForResponse(response => response.url().endsWith('/api/function-settings') && response.request().method() === 'PUT');
    await page.getByRole('button', {name:'설정 저장', exact:true}).click();
    const result = await (await response).json();
    ownedRevision = result.revision;
    await expect.poll(async () => (await (await page.request.get('/api/function-settings')).json()).applied, {timeout:15000}).toBe(true);
  };
  const state = async () => (await (await page.request.get(`/api/cameras/${cameraId}/status`)).json());
  try {
    originalValues = (await (await page.request.get('/api/function-settings')).json()).values;
    const person = await page.request.post('/api/persons', {headers, data:{name:prefix}});
    expect(person.status()).toBe(201); personId = (await person.json()).id;
    expect((await page.request.post(`/api/persons/${personId}/faces`, {
      headers:{...headers, 'Content-Type':'image/jpeg'}, data:readFileSync('../data/calibration/person_b_reference.jpg')
    })).status()).toBe(201);
    const camera = await page.request.post('/api/cameras', {headers, data:{name:prefix, source_type:'mp4'}});
    expect(camera.status()).toBe(201); cameraId = (await camera.json()).camera_id;
    expect((await page.request.put(`/api/cameras/${cameraId}/video`, {
      headers:{...headers, 'Content-Type':'video/mp4'}, data:readFileSync('../data/videos/face-smoke.mp4')
    })).status()).toBe(200);
    await settingsPage();
    await page.getByLabel('사람 검출 사용', {exact:true}).uncheck();
    const cutoff = page.getByLabel('얼굴 검출 기준', {exact:true});
    const size = page.getByLabel('최소 얼굴 크기 (px)', {exact:true});
    await cutoff.fill('1');
    await expect(page.getByRole('button', {name:'설정 저장', exact:true})).toBeDisabled();
    await cutoff.fill('0.5');
    await size.fill('7');
    await expect(page.getByRole('button', {name:'설정 저장', exact:true})).toBeDisabled();
    await size.fill('32.5');
    await expect(page.getByRole('button', {name:'설정 저장', exact:true})).toBeDisabled();
    await size.fill('32');
    await page.getByLabel('얼굴 검사 간격 (초)', {exact:true}).fill('0.2');
    await page.getByLabel('사람 검출 빈도 (FPS)', {exact:true}).fill('5');
    await page.getByLabel('비교점수 기준', {exact:true}).fill('0.7');
    await save();
    await page.reload();
    await expect(page.getByLabel('사람 검출 사용', {exact:true})).not.toBeChecked();
    await expect(cutoff).toHaveValue('0.5');
    await expect(size).toHaveValue('32');
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.setViewportSize({width:1600, height:1000});
    await page.goto(`/#/live/${cameraId}`);
    await page.getByRole('button', {name:'분석 시작', exact:true}).click();
    await expect.poll(async () => (await state()).result?.detection_mode).toBe('face');
    await expect.poll(async () => (await state()).result?.tracks.length).toBe(2);
    await expect.poll(async () => (await state()).result?.tracks.some((track: {face?: {matches?: {person_id:number}[]}}) => track.face?.matches?.some(match => match.person_id === personId)), {timeout:30000}).toBe(true);
    const session = (await state()).stream_session_id;
    expect((await state()).actual_device).toMatch(/^cuda(?::\d+)?$/);
    await expect(page.locator('.live-legend')).toContainText('얼굴 박스');
    const panel = page.locator('app-event-panel');
    await panel.getByLabel('인물 이름', {exact:true}).fill(prefix);
    const event = panel.locator('.event-card').filter({hasText:prefix});
    await expect(event).toHaveCount(1, {timeout:30000});
    await event.scrollIntoViewIfNeeded();
    for (const selector of ['.event-reference-image', '.event-detected-image']) {
      await expect.poll(() => event.locator(selector).evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(112);
    }
    const eventId = await event.getAttribute('data-event-id');
    expect((await request.get(`/api/events/${eventId}`)).status()).toBe(401);
    const processed = (await state()).processed_frames;
    await expect.poll(async () => (await state()).processed_frames).toBeGreaterThan(processed + 3);
    await expect(event).toHaveCount(1);
    mkdirSync('../data/screenshots', {recursive:true});
    await page.locator('.live-search-layout').screenshot({path:'../data/screenshots/uploaded-faces-live.png'});
    await settingsPage();
    await size.fill('512');
    await save();
    await expect.poll(async () => {
      const value = await state();
      return value.result?.min_face_size === 512 && value.result.tracks.length === 0;
    }).toBe(true);
    expect((await page.request.get(`/api/events/${eventId}`)).status()).toBe(200);
    await size.fill('32');
    await cutoff.fill('0.99');
    await save();
    await expect.poll(async () => {
      const value = await state();
      return value.result?.face_detection_threshold === 0.99 && value.result.tracks.length === 0;
    }).toBe(true);
    await cutoff.fill('0.5');
    await save();
    await expect.poll(async () => (await state()).result?.tracks.length).toBe(2);
    await page.getByLabel('사람 검출 사용', {exact:true}).check();
    await save();
    await expect.poll(async () => (await state()).result?.detection_mode).toBe('person');
    expect((await state()).stream_session_id).not.toBe(session);
    expect(errors).toEqual([]);
  } finally {
    test.setTimeout(180000);
    try {
      if (cameraId !== undefined) await page.request.post(`/api/cameras/${cameraId}/stop`, {headers});
      if (originalValues && ownedRevision !== undefined) {
        const current = await (await page.request.get('/api/function-settings')).json();
        if (current.revision === ownedRevision) {
          expect([200,202]).toContain((await page.request.put('/api/function-settings', {headers, data:{...originalValues, revision:current.revision}})).status());
        }
      }
    } finally {
      try {
        if (cameraId !== undefined) expect((await page.request.delete(`/api/cameras/${cameraId}`, {headers})).status()).toBe(204);
        if (personId !== undefined) expect([202,204]).toContain((await page.request.delete(`/api/persons/${personId}`, {headers})).status());
      } finally {await page.request.post('/api/auth/logout', {headers});}
    }
  }
});
