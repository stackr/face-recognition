import {expect, test, type WebSocketRoute} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('검색 이벤트 개별·필터 목록 삭제, 사진 제거, 다른 화면 갱신과 오프라인 삭제 복구', async ({page}) => {
  test.setTimeout(120000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  const prefix = `EVENT-DELETE-E2E-${Date.now()}`;
  let personId: number | undefined;
  let cameraId: number | undefined;
  let headers: Record<string, string> | undefined;
  let paused = false;
  let browserSocket: WebSocketRoute | undefined;
  let serverSocket: WebSocketRoute | undefined;
  const observer = await page.context().newPage();
  const errors: string[] = [];
  for (const current of [page, observer]) current.on('pageerror', error => errors.push(error.message));
  await page.routeWebSocket('**/ws/events', async socket => {
    if (paused) {await socket.close({code:1013}); return;}
    browserSocket = socket;
    serverSocket = socket.connectToServer();
  });
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태'})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  headers = {'X-CSRF-Token':auth.csrf_token};
  try {
    const person = await page.request.post('/api/persons', {headers, data:{name:prefix}});
    expect(person.status()).toBe(201);
    personId = (await person.json()).id;
    expect((await page.request.post(`/api/persons/${personId}/faces`, {
      headers:{...headers, 'Content-Type':'image/jpeg'},
      data:readFileSync('../data/calibration/person_b_reference.jpg')
    })).status()).toBe(201);
    const camera = await page.request.post('/api/cameras', {headers, data:{name:prefix, source_type:'mp4'}});
    expect(camera.status()).toBe(201);
    cameraId = (await camera.json()).camera_id;
    expect((await page.request.put(`/api/cameras/${cameraId}/video`, {
      headers:{...headers, 'Content-Type':'video/mp4'}, data:readFileSync('../data/videos/face-smoke.mp4')
    })).status()).toBe(200);
    const panel = page.locator('app-event-panel');
    await page.goto(`/#/live/${cameraId}`);
    await expect(panel.getByRole('status')).toHaveText('연결됨', {timeout:15000});
    await panel.getByLabel('인물 이름', {exact:true}).fill(prefix);
    const eventIds: number[] = [];
    for (let session = 0; session < 3; session++) {
      const started = await page.request.post(`/api/cameras/${cameraId}/start`, {
        headers, data:{source_type:'mp4', loop:false}
      });
      expect(started.status()).toBe(200);
      expect((await started.json()).actual_device).toMatch(/^cuda(?::\d+)?$/);
      let newId = 0;
      await expect.poll(async () => {
        const response = await page.request.get('/api/events?latest=true&limit=100');
        expect(response.status()).toBe(200);
        const event = (await response.json()).items.find((row: {event_id:number; camera_id:number; person_id:number}) =>
          row.camera_id === cameraId && row.person_id === personId && !eventIds.includes(row.event_id));
        newId = event?.event_id ?? 0;
        return newId;
      }, {timeout:30000}).toBeGreaterThan(0);
      eventIds.push(newId);
      expect((await page.request.post(`/api/cameras/${cameraId}/stop`, {headers})).status()).toBe(200);
    }
    const [singleId, bulkId, offlineId] = eventIds;
    const card = (id: number) => panel.locator(`.event-card[data-event-id="${id}"]`);
    await expect(panel.locator('.event-card')).toHaveCount(3);
    await observer.goto(`/#/live/${cameraId}`);
    const observerPanel = observer.locator('app-event-panel');
    await expect(observerPanel.getByRole('status')).toHaveText('연결됨', {timeout:15000});
    await observerPanel.getByLabel('인물 이름', {exact:true}).fill(prefix);
    await expect(observerPanel.locator('.event-card')).toHaveCount(3);

    // Cancelling a deletion must leave both the record and photographs intact.
    page.once('dialog', dialog => dialog.dismiss());
    await card(singleId).getByRole('button', {name:'삭제', exact:true}).click();
    await expect(card(singleId)).toHaveCount(1);
    expect((await page.request.get(`/api/events/${singleId}/face`)).status()).toBe(200);
    page.once('dialog', async dialog => {
      expect(dialog.message()).toContain('검출 사진'); await dialog.accept();
    });
    await card(singleId).getByRole('button', {name:'삭제', exact:true}).click();
    await expect(card(singleId)).toHaveCount(0);
    await expect(observerPanel.locator(`.event-card[data-event-id="${singleId}"]`)).toHaveCount(0);
    for (const path of [`/api/events/${singleId}`, `/api/events/${singleId}/face`, `/api/events/${singleId}/frame`]) {
      expect((await page.request.get(path)).status()).toBe(404);
    }

    // The bulk action deletes only the current filter, leaving another status intact.
    expect((await page.request.post(`/api/events/${offlineId}/confirm`, {headers})).status()).toBe(200);
    await expect(card(offlineId).locator('.event-status')).toHaveText('운영자 확인');
    await panel.getByLabel('이벤트 상태', {exact:true}).selectOption('candidate');
    await expect(panel.locator('.event-card')).toHaveCount(1);
    await page.setViewportSize({width:390, height:844});
    await panel.getByRole('button', {name:'목록 삭제', exact:true}).scrollIntoViewIfNeeded();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    mkdirSync('../data/screenshots', {recursive:true});
    await panel.screenshot({path:'../data/screenshots/event-deletion-mobile.png'});
    page.once('dialog', async dialog => {
      expect(dialog.message()).toContain('1건'); await dialog.accept();
    });
    await panel.getByRole('button', {name:'목록 삭제', exact:true}).click();
    await expect(card(bulkId)).toHaveCount(0);
    expect((await page.request.get(`/api/events/${bulkId}`)).status()).toBe(404);
    expect((await page.request.get(`/api/events/${offlineId}`)).status()).toBe(200);
    await panel.getByLabel('이벤트 상태', {exact:true}).selectOption('all');
    await expect(card(offlineId)).toHaveCount(1);

    paused = true;
    await browserSocket!.close({code:1013});
    await serverSocket!.close({code:1013});
    await expect(panel.getByRole('status')).toHaveText('재연결 대기');
    const deletion = await page.request.delete(`/api/events/${offlineId}`, {headers});
    expect(deletion.status()).toBe(200);
    const deletedChange = (await deletion.json()).change_id;
    await expect(card(offlineId)).toHaveCount(1);
    await expect(observerPanel.locator(`.event-card[data-event-id="${offlineId}"]`)).toHaveCount(0);
    const recovered = page.waitForResponse(async response => {
      const url = new URL(response.url());
      if (url.pathname !== '/api/events' || !url.searchParams.has('after_change_id') || response.status() !== 200) return false;
      return (await response.json()).items.some((row: {type:string; event_id:number; change_id:number}) =>
        row.type === 'event_deleted' && row.event_id === offlineId && row.change_id >= deletedChange);
    }, {timeout:20000});
    paused = false;
    await recovered;
    await expect(panel.getByRole('status')).toHaveText('연결됨');
    await expect(panel.locator('.event-card')).toHaveCount(0);
    await page.reload();
    await expect(panel.getByRole('status')).toHaveText('연결됨', {timeout:15000});
    await panel.getByLabel('인물 이름', {exact:true}).fill(prefix);
    await expect(panel.locator('.event-card')).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    paused = false;
    await observer.close();
    if (headers) {
      try {
        if (cameraId !== undefined) {
          await page.request.post(`/api/cameras/${cameraId}/stop`, {headers});
          expect((await page.request.delete(`/api/cameras/${cameraId}`, {headers})).status()).toBe(204);
        }
      } finally {
        try {
          if (personId !== undefined) expect([202,204]).toContain((await page.request.delete(`/api/persons/${personId}`, {headers})).status());
        } finally {await page.request.post('/api/auth/logout', {headers});}
      }
    }
  }
});
