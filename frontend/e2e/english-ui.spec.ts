import {expect, test} from '@playwright/test';
import {mkdirSync} from 'node:fs';

test('English interface preserves user names and filenames on every page', async ({page}) => {
  const cameraName = '사용자가 입력한 카메라';
  const personName = '등록한 인물';
  const filename = '시험 영상.mp4';
  const timestamp = '2026-10-05T10:00:00Z';
  const camera = {camera_id:1, name:cameraName, location:'', description:'', source_type:'mp4', enabled:true, can_view:true, can_operate:true, has_test_video:true, video_filename:filename};
  const person = {id:1, name:personName, description:'', enabled:true, sync_status:'ready', faces:[{id:1, quality:0.9, state:'expired', image_available:false, image_expires_at:timestamp, embedding_expires_at:timestamp}]};
  const values = {person_all_frames:false, face_detection_fps:5, face_all_frames:false, detection_fps:5, face_analysis_interval:0.2, face_rois_per_frame:4, face_match_threshold:0.75, detection_confidence:0.1, person_detection_enabled:true, video_face_detection_threshold:0.5, video_face_min_size:8};
  const errors: string[] = [];
  let loggedIn = true;
  page.on('pageerror', error => errors.push(error.message));
  await page.routeWebSocket('**/ws/events', socket => socket.send(JSON.stringify({type:'ready'})));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') {
      if (!loggedIn) {await route.fulfill({status:401, json:{detail:'Login required'}}); return;}
      body = {user:{id:1, username:'english-test', role:'admin'}, csrf_token:'test'};
    } else if (path === '/api/auth/login') {await route.fulfill({status:429, json:{detail:'Too many attempts'}}); return;}
    else if (path === '/api/auth/logout') loggedIn = false;
    else if (path === '/api/cameras') body = [camera];
    else if (path === '/api/persons') body = [person];
    else if (path === '/api/persons/1') body = person;
    else if (path === '/api/system/status') body = {services:{mariadb:{status:'ok'}, qdrant:{status:'ok'}}, gpu:{status:'passed'}, worker:{status:'ok'}, checked_at:timestamp};
    else if (path === '/api/cameras/1/status') body = {camera_id:1, state:'stopped', person_detection_enabled:true, face_detection_enabled:true};
    else if (path === '/api/function-settings') body = {revision:1, values, applied:true, active:null, features:{sample_count:5, sample_window_seconds:3, minimum_samples:2, retry_detector_size:640, person_track_start_threshold:0.6}};
    else if (path === '/api/recognition-logs') body = {items:[{id:1, camera_name:cameraName, captured_at:timestamp, track_id:1, frame_id:1, outcome:'quality_rejected', reasons:['blurred'], quality:0.2, top_similarity:null, sample_count:0, settings_revision:1, stream_session_id:'a'.repeat(32), metrics:{face_size:[32,32]}}], summary:{total:1, outcomes:{quality_rejected:1}, reasons:{blurred:1}}, retention_days:7, max_records:10000, has_more:false};
    else if (path === '/api/face-tests') body = {items:[{job_id:'test', filename, state:'completed', processed_frames:20, total_frames:20, detections:0, progress_percent:100, threshold:0.75, actual_device:'cuda', groups:[], groups_before_merge:0, merged_group_count:0}], can_start:true, defaults:{detection_threshold:0.5, min_face_size:8, match_threshold:0.75}, limits:{upload_max_mb:200, retention_hours:24, max_duration_seconds:3600}};
    else if (path === '/api/events') body = {items:[], has_more:false, next_cursor:0, snapshot_watermark:0};
    await route.fulfill({json:body});
  });
  const assertEnglish = async () => {
    const text = await page.locator('body').innerText();
    const interfaceText = [cameraName, personName, filename].reduce((value, data) => value.replaceAll(data, ''), text);
    expect(interfaceText).not.toMatch(/[가-힣]/);
    const attributes = await page.locator('[aria-label], [placeholder], img[alt]').evaluateAll(elements => elements.flatMap(element => ['aria-label','placeholder','alt'].map(name => element.getAttribute(name) || '')));
    for (const label of attributes) expect([cameraName, personName, filename].reduce((value, data) => value.replaceAll(data, ''), label)).not.toMatch(/[가-힣]/);
  };
  await page.setViewportSize({width:1600, height:1000});
  mkdirSync('../data/screenshots', {recursive:true});
  for (const [path, title] of [['dashboard','System Readiness'], ['cameras','Cameras'], ['persons','People'], ['persons/1/edit','Edit Person'], ['persons/new','Add Person'], ['settings','Feature Settings'], ['logs','Logs'], ['face-test','Face Detection Test'], ['live/1','Live Search']]) {
    await page.goto(`/#/${path}`);
    await expect(page.getByRole('heading', {name:title, exact:true})).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('lang','en');
    await assertEnglish();
    if (path === 'dashboard') await expect(page.locator('.dashboard-camera-card')).toContainText(cameraName);
    if (path === 'persons/1/edit') await expect(page.getByLabel('Person Name', {exact:true})).toHaveValue(personName);
    if (path === 'live/1') await expect(page.locator('.uploaded-video-name')).toContainText(filename);
    await page.screenshot({path:`../data/screenshots/english-${path.replaceAll('/','-')}.png`});
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await assertEnglish();
    await page.setViewportSize({width:1600, height:1000});
  }
  await page.getByRole('button', {name:'Log Out', exact:true}).click();
  await expect(page.getByRole('heading', {name:'Log In', exact:true})).toBeVisible();
  await page.getByLabel('Username', {exact:true}).fill('test');
  await page.getByLabel('Password', {exact:true}).fill('test');
  await page.getByRole('button', {name:'Log In', exact:true}).click();
  await expect(page.getByRole('alert')).toHaveText('Too many login attempts. Please try again shortly.');
  await assertEnglish();
  expect(errors).toEqual([]);
});
