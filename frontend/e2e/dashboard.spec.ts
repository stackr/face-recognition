import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('대시보드 전체 카메라 배치, 상태 갱신, 조회 권한과 반응형 화면', async ({page}) => {
  const cameras = Array.from({length:7}, (_, i) => ({camera_id:i+1, name:`시험 카메라 ${i+1}`, location:'시험 위치', source_type:'mp4', enabled:i !== 4, can_view:i !== 5, can_operate:false, has_test_video:true, video_filename:`시험 영상 ${i+1}.mp4`}));
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
  await expect(cards.nth(0)).toContainText('분석 중');
  await expect(cards.nth(0)).toContainText('120');
  await expect(cards.nth(0)).toContainText('1920 × 1080');
  await expect(cards.nth(1)).toContainText('중지됨');
  await expect(cards.nth(2)).toContainText('연결 또는 분석 실패');
  await expect(cards.nth(3)).toContainText('영상 재생 완료');
  await expect(cards.nth(4)).toContainText('사용 안함');
  await expect(cards.nth(4)).toContainText('기록 없음');
  await expect(cards.nth(5)).toContainText('조회 권한 없음');
  await expect(cards.nth(5).getByRole('button')).toBeDisabled();
  await expect(cards.nth(5).locator('dl')).toHaveCount(0);
  await expect(cards.nth(6)).toContainText('상태 조회 실패');
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
  await page.getByRole('button', {name:'새로고침', exact:true}).click();
  await expect(cards).toHaveCount(0);
  await expect(page.locator('.dashboard-camera-grid')).toContainText('등록된 카메라가 없습니다.');
  expect(errors).toEqual([]);
});

test('실제 서비스의 등록 카메라 전체 조회 및 Live Search 이동', async ({page}) => {
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태', exact:true})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  try {
    const cameras = await (await page.request.get('/api/cameras')).json();
    await expect(page.locator('.dashboard-camera-card')).toHaveCount(cameras.length);
    await expect(page.locator('.dashboard-camera-state').filter({hasText:'상태 확인 중'})).toHaveCount(0);
    for (const camera of cameras) await expect(page.locator('.dashboard-camera-card').filter({has:page.getByRole('heading', {name:camera.name, exact:true})})).toBeVisible();
    if (cameras.length) {
      await page.locator('.dashboard-camera-card').first().getByRole('button', {name:'Live Search 열기'}).click();
      await expect(page).toHaveURL(new RegExp(`live/${cameras[0].camera_id}$`));
      await expect(page.getByRole('heading', {name:'Live Search', exact:true})).toBeVisible();
    }
  } finally {
    await page.request.post('/api/auth/logout', {headers:{'X-CSRF-Token':auth.csrf_token}});
  }
});
