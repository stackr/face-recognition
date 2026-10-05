import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('Dashboard camera layout, status updates, permissions and responsive display', async ({page}) => {
  const cameras = Array.from({length:7}, (_, i) => ({camera_id:i+1, name:`Test Camera ${i+1}`, location:'Test Location', source_type:'mp4', enabled:i !== 4, can_view:i !== 5, can_operate:false, has_test_video:true, video_filename:`Test Video ${i+1}.mp4`}));
  let processed = 120;
  const requests: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    requests.push(path);
    let body: unknown = {};
    if (path === '/api/auth/me') body = {user:{id:1, username:'dashboard-test', role:'viewer'}, csrf_token:'test'};
    else if (path === '/api/cameras') body = cameras;
    else if (path === '/api/persons') body = [];
    else if (path === '/api/system/status') body = {checked_at:new Date().toISOString(), services:{mariadb:{status:'ok'}, qdrant:{status:'ok'}}, gpu:{status:'passed', actual_device:'cuda'}, worker:{status:'ok'}};
    else if (/\/status$/.test(path)) {
      const id = Number(path.split('/')[3]);
      if (id === 7) {await route.fulfill({status:503, json:{detail:'unavailable'}}); return;}
      body = id === 5 ? {camera_id:id, state:'stopped'} : {camera_id:id, state:['running','stopped','error','ended'][id-1], processed_frames:processed, detection_fps:12, person_detection_fps:5, face_detection_fps:10, face_counts:{faces_detected:24}, result:{tracks:[], person_tracks:[{track_id:1, confidence:0.9}]}, resolution:[1920,1080], actual_device:'cuda', last_frame_at:'2026-10-05T01:00:00Z'};
    }
    await route.fulfill({json:body});
  });
  await page.setViewportSize({width:1600, height:1000});
  await page.goto('/#/dashboard');
  const cards = page.locator('.dashboard-camera-card');
  await expect(cards).toHaveCount(7);
  await expect(cards.nth(0)).toContainText('Analyzing');
  await expect(cards.nth(0)).toContainText('120');
  await expect(cards.nth(0)).toContainText('1920 × 1080');
  await expect(cards.nth(1)).toContainText('Stopped');
  await expect(cards.nth(2)).toContainText('Connection or analysis failed');
  await expect(cards.nth(3)).toContainText('Playback complete');
  await expect(cards.nth(4)).toContainText('Disabled');
  await expect(cards.nth(4)).toContainText('No records');
  await expect(cards.nth(5)).toContainText('No view permission');
  await expect(cards.nth(5).getByRole('button')).toBeDisabled();
  await expect(cards.nth(5).locator('dl')).toHaveCount(0);
  await expect(cards.nth(6)).toContainText('Status unavailable');
  const boxes = await cards.evaluateAll(elements => elements.map(element => ({x:element.getBoundingClientRect().x, y:element.getBoundingClientRect().y})));
  expect(boxes[0].y).toBe(boxes[1].y);
  expect(boxes[1].y).toBe(boxes[2].y);
  expect(boxes[3].y).toBeGreaterThan(boxes[0].y);
  expect(boxes[3].x).toBe(boxes[0].x);
  processed = 240;
  await expect(cards.nth(0)).toContainText('240', {timeout:15000});
  mkdirSync('../data/screenshots', {recursive:true});
  await page.screenshot({path:'../data/screenshots/dashboard-cameras-desktop.png', fullPage:true});
  await page.setViewportSize({width:1000, height:1000});
  await expect.poll(() => page.locator('.dashboard-camera-grid').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(2);
  await page.setViewportSize({width:390, height:844});
  await expect.poll(() => page.locator('.dashboard-camera-grid').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(1);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:'../data/screenshots/dashboard-cameras-mobile.png', fullPage:true});
  expect(requests).not.toContain('/api/cameras/6/status');
  expect(requests.some(path => /\/(start|preview)$/.test(path))).toBe(false);
  cameras.splice(0);
  await page.getByRole('button', {name:'Refresh', exact:true}).click();
  await expect(cards).toHaveCount(0);
  await expect(page.locator('.dashboard-camera-grid')).toContainText('No cameras registered.');
  expect(errors).toEqual([]);
});

test('Registered cameras and navigation in the running service', async ({page}) => {
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  await page.goto('/');
  await page.getByLabel('Username', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('Password', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'Log In', exact:true}).click();
  await expect(page.getByRole('heading', {name:'System Readiness', exact:true})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  try {
    const cameras = await (await page.request.get('/api/cameras')).json();
    await expect(page.locator('.dashboard-camera-card')).toHaveCount(cameras.length);
    await expect(page.locator('.dashboard-camera-state').filter({hasText:'Checking status'})).toHaveCount(0);
    for (const camera of cameras) await expect(page.locator('.dashboard-camera-card').filter({has:page.getByRole('heading', {name:camera.name, exact:true})})).toBeVisible();
    if (cameras.length) {
      await page.locator('.dashboard-camera-card').first().getByRole('button', {name:'Open Live Search'}).click();
      await expect(page).toHaveURL(new RegExp(`live/${cameras[0].camera_id}$`));
      await expect(page.getByRole('heading', {name:'Live Search', exact:true})).toBeVisible();
    }
  } finally {
    await page.request.post('/api/auth/logout', {headers:{'X-CSRF-Token':auth.csrf_token}});
  }
});
