import {expect, test, type WebSocketRoute} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

interface Candidate {
  type: string;
  event_id: number;
  camera_id: number;
  person_id: number;
  status: string;
}

test('Search events, review, WebSocket recovery and mobile display', async ({page, request}) => {
  test.setTimeout(90000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  let paused = false;
  let browserSocket: WebSocketRoute | undefined;
  let serverSocket: WebSocketRoute | undefined;
  let candidate: Candidate | undefined;
  let cameraId: number | undefined;
  let personId: number | undefined;
  let headers: Record<string, string> | undefined;

  // Forward the real server's messages; only interrupt this browser's event connection.
  await page.routeWebSocket('**/ws/events', async socket => {
    if (paused) {
      await socket.close({code: 1013});
      return;
    }
    const server = socket.connectToServer();
    browserSocket = socket;
    serverSocket = server;
    server.onMessage(message => {
      socket.send(message);
      try {
        const value = JSON.parse(message.toString()) as Candidate;
        if (!candidate && value.type === 'person_match' && value.camera_id === cameraId
          && value.person_id === personId) candidate = value;
      } catch { /* Ignore non-JSON protocol messages. */ }
    });
  });

  await page.goto('/');
  await page.getByLabel('Username', {exact: true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('Password', {exact: true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name: 'Log In', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'System Readiness'})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  headers = {'X-CSRF-Token': auth.csrf_token};
  const suffix = `${Date.now()}`;
  const personName = `EVENT-PERSON-E2E-${suffix}`;
  const cameraName = `EVENT-CAMERA-E2E-${suffix}`;

  try {
    const person = await page.request.post('/api/persons', {headers, data: {name: personName}});
    if ([201, 202].includes(person.status())) personId = (await person.json()).id;
    expect(person.status()).toBe(201);
    const reference = await page.request.post(`/api/persons/${personId}/faces`, {
      headers: {...headers, 'Content-Type': 'image/jpeg'},
      data: readFileSync('../data/calibration/person_b_reference.jpg')
    });
    expect(reference.status()).toBe(201);
    expect((await reference.json()).state).toBe('ready');
    const camera = await page.request.post('/api/cameras', {
      headers, data: {name: cameraName, source_type: 'mp4'}
    });
    if (camera.status() === 201) cameraId = (await camera.json()).camera_id;
    expect(camera.status()).toBe(201);
    const video = await page.request.put(`/api/cameras/${cameraId}/video`, {
      headers: {...headers, 'Content-Type': 'video/mp4'},
      data: readFileSync('../data/videos/face-smoke.mp4')
    });
    expect(video.status()).toBe(200);

    await page.getByRole('button', {name: 'Refresh', exact: true}).click();
    await page.getByRole('navigation').getByRole('button', {name:/Cameras/}).click();
    const row = page.getByRole('row').filter({hasText: cameraName});
    await expect(row).toBeVisible();
    await row.getByRole('button', {name: 'Analyze Video', exact: true}).click();
    const panel = page.locator('app-event-panel');
    await expect(panel.getByRole('heading', {name: 'Search Events', exact: true})).toBeVisible();
    await expect(panel.getByRole('status')).toHaveText('Connected', {timeout: 15000});
    const started = await page.request.post(`/api/cameras/${cameraId}/start`, {
      headers, data: {source_type: 'mp4', loop: false}
    });
    expect(started.status()).toBe(200);
    expect((await started.json()).actual_device).toMatch(/^cuda(?::\d+)?$/);
    await expect.poll(() => candidate?.event_id, {timeout: 30000}).toBeGreaterThan(0);
    const eventId = candidate!.event_id;
    const facePath = `/api/events/${eventId}/face`;
    const card = panel.locator('.event-card').filter({has: page.locator(`img[src^="${facePath}?"]`)});
    await expect(card).toHaveCount(1);
    await expect(card).toContainText(personName);
    await expect(card).toContainText(cameraName);
    await expect(card.locator('.event-status')).toHaveText('Unconfirmed candidate');
    await expect.poll(() => card.locator('.event-detected-image').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(112);
    await card.getByRole('button', {name: 'Detected Frame', exact:true}).click();
    const frameDialog = page.getByRole('dialog', {name:'Detected Frame', exact:true});
    await expect(frameDialog).toBeVisible();
    await expect(frameDialog.locator('img')).toHaveAttribute('src', `/api/events/${eventId}/frame`);
    await expect.poll(() => frameDialog.locator('img').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
    await frameDialog.getByRole('button', {name:'Close Detected Frame'}).click();
    await expect(frameDialog).toHaveCount(0);
    expect((await request.get(facePath)).status()).toBe(401);
    expect((await page.request.post(`/api/cameras/${cameraId}/stop`, {headers})).status()).toBe(200);

    await card.getByRole('button', {name: 'Reject', exact: true}).click();
    await expect(card.locator('.event-status')).toHaveText('Rejected by operator');
    await expect(card.getByRole('button', {name: 'Reject', exact: true})).toBeDisabled();
    await card.getByRole('button', {name: 'Confirm', exact: true}).click();
    await expect(card.locator('.event-status')).toHaveText('Confirmed by operator');
    await expect(card.getByRole('button', {name: 'Confirm', exact: true})).toBeDisabled();

    // Save a review while WS is disconnected, then require cursor recovery to contain it.
    paused = true;
    expect(browserSocket).toBeDefined();
    expect(serverSocket).toBeDefined();
    // Each route closes only its own side; explicitly deliver the close to the browser.
    await browserSocket!.close({code: 1013});
    await serverSocket!.close({code: 1013});
    await expect(panel.getByRole('status')).toHaveText('Waiting to reconnect');
    const rejected = await page.request.post(`/api/events/${eventId}/reject`, {headers});
    expect(rejected.status()).toBe(200);
    const rejectedChange = (await rejected.json()).change_id;
    await expect(card.locator('.event-status')).toHaveText('Confirmed by operator');
    const recovery = page.waitForResponse(async response => {
      const url = new URL(response.url());
      if (url.pathname !== '/api/events' || !url.searchParams.has('after_change_id')
        || response.status() !== 200) return false;
      const body = await response.json();
      return body.items.some((item: Candidate & {change_id: number}) => item.event_id === eventId
        && item.change_id >= rejectedChange && item.status === 'rejected');
    }, {timeout: 20000});
    paused = false;
    await recovery;
    await expect(panel.getByRole('status')).toHaveText('Connected');
    await expect(card.locator('.event-status')).toHaveText('Rejected by operator');
    await expect(card).toHaveCount(1);

    await page.reload();
    await expect(panel.getByRole('status')).toHaveText('Connected', {timeout: 15000});
    await expect(card.locator('.event-status')).toHaveText('Rejected by operator');
    await page.setViewportSize({width: 390, height: 844});
    await card.scrollIntoViewIfNeeded();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await expect(card.getByRole('button', {name: 'Confirm', exact: true})).toBeVisible();
    mkdirSync('../data/screenshots', {recursive: true});
    await card.screenshot({path: '../data/screenshots/phase6-event-mobile.png'});

    expect((await page.request.delete(`/api/cameras/${cameraId}`, {headers})).status()).toBe(204);
    cameraId = undefined;
    expect([202, 204]).toContain((await page.request.delete(`/api/persons/${personId}`, {headers})).status());
    personId = undefined;
    await page.getByRole('button', {name: 'Log Out', exact: true}).click();
    await expect(page.getByRole('heading', {name: 'Log In', exact: true})).toBeVisible();
    await expect(panel).toHaveCount(0);
  } finally {
    paused = false;
    if (headers) {
      try {
        if (cameraId !== undefined) {
          await page.request.post(`/api/cameras/${cameraId}/stop`, {headers});
          expect((await page.request.delete(`/api/cameras/${cameraId}`, {headers})).status()).toBe(204);
        }
      } finally {
        try {
          if (personId !== undefined) {
            expect([202, 204]).toContain((await page.request.delete(`/api/persons/${personId}`, {headers})).status());
          }
        } finally { await page.request.post('/api/auth/logout', {headers}); }
      }
    }
  }
});
