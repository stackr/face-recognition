import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';

test('CUDA face detection settings, frame analysis, grouping and deletion', async ({page, request}) => {
  test.setTimeout(180000);
  const accountFile = process.env['FACE_TEST_E2E_ACCOUNT'];
  if (!accountFile) throw new Error('Run scripts/check_face_test_browser.py to use an isolated account.');
  const account = JSON.parse(readFileSync(accountFile, 'utf8'));
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({width:1600, height:1000});
  await page.goto('/');
  await page.getByLabel('Username', {exact:true}).fill(account.username);
  await page.getByLabel('Password', {exact:true}).fill(account.password);
  await page.getByRole('button', {name:'Log In', exact:true}).click();
  await expect(page.getByRole('heading', {name:'System Readiness', exact:true})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  const headers = {'X-CSRF-Token':auth.csrf_token};
  await page.goto('/#/face-test');
  await expect(page.getByRole('heading', {name:'Face Detection Test', exact:true})).toBeVisible();
  await expect(page.getByRole('button', {name:'Analyze Video', exact:true})).toBeDisabled();
  const initial = await (await page.request.get('/api/face-tests')).json();
  expect(initial.items).toEqual([]);
  expect(initial.can_start).toBe(true);
  const threshold = page.getByLabel('Detection Threshold', {exact:true});
  const minimum = page.getByLabel('Minimum Face Size (px)', {exact:true});
  const match = page.getByLabel('Similarity Threshold', {exact:true});
  const analyze = page.getByRole('button', {name:'Analyze Video', exact:true});
  await expect(threshold).toHaveValue(String(initial.defaults.detection_threshold));
  await expect(minimum).toHaveValue(String(initial.defaults.min_face_size));
  await expect(match).toHaveValue(String(initial.defaults.match_threshold));
  expect(await threshold.evaluate(input => !!(input.compareDocumentPosition(document.querySelector('#face-test-video')!) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  expect(await minimum.evaluate(input => !!(input.compareDocumentPosition(document.querySelector('#face-test-video')!) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  expect(await match.evaluate(input => !!(input.compareDocumentPosition(document.querySelector('#face-test-video')!) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  await expect(page.locator('.live-search-layout')).toHaveCount(0);
  await page.getByLabel('Video File to Analyze', {exact:true}).setInputFiles(account.videos.faces);
  for (const invalid of ['', '0.09', '1']) {
    await threshold.fill(invalid);
    await expect(analyze).toBeDisabled();
  }
  await threshold.fill('0.5');
  for (const invalid of ['', '7', '513', '32.5']) {
    await minimum.fill(invalid);
    await expect(analyze).toBeDisabled();
  }
  await minimum.fill('32');
  for (const invalid of ['', '-1.1', '1.1']) {
    await match.fill(invalid);
    await expect(analyze).toBeDisabled();
  }
  await match.fill('0.8');
  await expect(page.getByRole('button', {name:'Analyze Video', exact:true})).toBeEnabled();
  const created = page.waitForResponse(response => response.url().includes('/api/face-tests') && response.request().method() === 'POST');
  await page.getByRole('button', {name:'Analyze Video', exact:true}).click();
  expect((await created).status()).toBe(202);
  let job: {job_id:string; state:string; actual_device:string; processed_frames:number; total_frames:number; detections:number; detection_threshold:number; min_face_size:number; threshold:number; groups_before_merge:number; merged_group_count:number; groups:{group_id:number; occurrences:number; embedding_ready:boolean}[]};
  await expect.poll(async () => {
    job = (await (await page.request.get('/api/face-tests')).json()).items[0];
    return job?.state;
  }, {timeout:90000, intervals:[500,1000,1500]}).toBe('completed');
  expect(job!.actual_device).toBe('cuda');
  expect(job!.detection_threshold).toBe(0.5);
  expect(job!.min_face_size).toBe(32);
  expect(job!.threshold).toBe(0.8);
  expect(job!.groups_before_merge).toBe(2);
  expect(job!.merged_group_count).toBe(0);
  expect(job!.processed_frames).toBe(40);
  expect(job!.total_frames).toBe(40);
  expect(job!.groups).toHaveLength(2);
  expect(job!.detections).toBe(80);
  expect(job!.groups.every(group => group.embedding_ready && group.occurrences === 40)).toBe(true);
  await expect(page.locator('.face-test-card')).toHaveCount(2);
  await expect(page.locator('.face-test-progress')).toContainText('Analysis complete');
  await expect(page.locator('.face-test-applied-settings')).toHaveText('Applied Detection Threshold 0.50 · Minimum Face Size 32 px');
  await expect(page.locator('.face-test-applied-match')).toContainText('Similarity Threshold 0.80');
  await expect(page.locator('.face-test-merge-result')).toHaveText('Group comparison complete · 2 items → 2 items · 0 groups merged');
  await match.fill('0.95');
  await expect(page.locator('.face-test-applied-match')).toContainText('0.80');
  await minimum.fill('512');
  await expect(page.locator('.face-test-applied-settings')).toContainText('32 px');
  await expect.poll(() => page.locator('.face-test-card img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
  const imageUrl = `/api/face-tests/${job!.job_id}/groups/${job!.groups[0].group_id}/image`;
  expect((await request.get(imageUrl)).status()).toBe(401);
  expect((await page.request.get(imageUrl)).headers()['cache-control']).toBe('no-store');
  expect((await page.request.delete('/api/face-tests')).status()).toBe(403);
  mkdirSync('../data/screenshots', {recursive:true});
  await page.screenshot({path:'../data/screenshots/face-test-desktop.png'});
  await page.reload();
  await expect(page.locator('.face-test-card')).toHaveCount(2);
  await expect(threshold).toHaveValue('0.5');
  await expect(minimum).toHaveValue('32');
  await expect(match).toHaveValue('0.8');
  await page.setViewportSize({width:390, height:844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:'../data/screenshots/face-test-mobile.png', fullPage:true});
  await page.setViewportSize({width:1600, height:1000});

  // A stricter confidence cutoff excludes the lower-confidence face in this public fixture.
  await threshold.fill('0.8');
  await page.getByLabel('Video File to Analyze', {exact:true}).setInputFiles(account.videos.faces);
  await analyze.click();
  await expect.poll(async () => {
    const latest = (await (await page.request.get('/api/face-tests')).json()).items[0];
    return latest.job_id !== job!.job_id && latest.state === 'completed' && latest.processed_frames === 40
      && latest.detections === 40 && latest.groups.length === 1 && latest.detection_threshold === 0.8
      && latest.min_face_size === 32;
  }, {timeout:90000}).toBe(true);
  await expect(page.locator('.face-test-applied-settings')).toContainText('0.80');
  const original = await (await page.request.get(`/api/face-tests/${job!.job_id}`)).json();
  expect(original.detection_threshold).toBe(0.5);
  expect(original.min_face_size).toBe(32);
  expect(original.groups).toHaveLength(2);
  expect(original.threshold).toBe(0.8);

  // The same real faces are excluded when their original-frame size is below the setting.
  await minimum.fill('512');
  await page.getByLabel('Video File to Analyze', {exact:true}).setInputFiles(account.videos.faces);
  await analyze.click();
  await expect.poll(async () => {
    const latest = (await (await page.request.get('/api/face-tests')).json()).items[0];
    return latest.job_id !== job!.job_id && latest.state === 'completed' && latest.processed_frames === 40
      && latest.detections === 0 && latest.groups.length === 0 && latest.min_face_size === 512;
  }, {timeout:90000}).toBe(true);
  await expect(page.getByText('No faces detected in this video.', {exact:true})).toBeVisible();
  await expect(page.locator('.face-test-applied-settings')).toContainText('512 px');
  await minimum.fill('32');

  await page.reload();
  await expect(threshold).toHaveValue('0.8');
  await expect(minimum).toHaveValue('512');
  await minimum.fill('32');
  await expect(page.getByRole('button', {name:'Analyze Video', exact:true})).toBeDisabled();
  await page.getByLabel('Video File to Analyze', {exact:true}).setInputFiles(account.videos.blank);
  await page.getByRole('button', {name:'Analyze Video', exact:true}).click();
  await expect.poll(async () => {
    const latest = (await (await page.request.get('/api/face-tests')).json()).items[0];
    return latest.filename === 'blank.mp4' && latest.state === 'completed' && latest.processed_frames === 20 && latest.groups.length === 0;
  }).toBe(true);
  await expect(page.getByText('No faces detected in this video.', {exact:true})).toBeVisible();
  await expect(page.getByLabel('Analyzed Video', {exact:true})).toBeVisible();
  await page.getByLabel('Analyzed Video', {exact:true}).selectOption(job!.job_id);
  await expect(page.locator('.face-test-card')).toHaveCount(2);

  // Even at the lowest comparison cutoff, simultaneous people stay separate.
  await threshold.fill('0.5');
  await match.fill('-1');
  await page.getByLabel('Video File to Analyze', {exact:true}).setInputFiles(account.videos.faces);
  await analyze.click();
  await expect.poll(async () => {
    const latest = (await (await page.request.get('/api/face-tests')).json()).items[0];
    return latest.state === 'completed' && latest.threshold === -1 && latest.processed_frames === 40
      && latest.groups.length === 2 && latest.groups_before_merge === 2 && latest.merged_group_count === 0;
  }, {timeout:90000}).toBe(true);
  await expect(page.locator('.face-test-merge-result')).toHaveText('Group comparison complete · 2 items → 2 items · 0 groups merged');
  await page.reload();
  await expect(match).toHaveValue('-1');
  const after = await (await page.request.get('/api/face-tests')).json();
  expect(after.defaults.match_threshold).toBe(initial.defaults.match_threshold);

  await page.getByLabel('Video File to Analyze', {exact:true}).setInputFiles(account.videos.faces);
  const again = page.waitForResponse(response => response.url().includes('/api/face-tests') && response.request().method() === 'POST');
  await page.getByRole('button', {name:'Analyze Video', exact:true}).click();
  expect((await again).status()).toBe(202);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', {name:'Delete All Results', exact:true}).click();
  await expect(page.getByText('All analysis results and extracted images deleted.', {exact:true})).toBeVisible();
  await expect(page.locator('.face-test-card')).toHaveCount(0);
  expect((await (await page.request.get('/api/face-tests')).json()).items).toEqual([]);
  expect((await page.request.get(imageUrl)).status()).toBe(404);
  await page.reload();
  await expect(page.getByText('Select a video and click Analyze Video.', {exact:true})).toBeVisible();
  expect(errors).toEqual([]);
  mkdirSync('../data/reports', {recursive:true});
  writeFileSync('../data/reports/face-test-browser.json', JSON.stringify({status:'passed', actual_device:job!.actual_device,
    detection_threshold:0.5, min_face_size:32, invalid_settings_rejected:true, settings_restored_on_reload:true,
    stricter_detection_threshold:0.8, stricter_threshold_detections:40, stricter_threshold_person_groups:1,
    stricter_min_size:512, stricter_min_size_detections:0, per_job_settings_preserved:true,
    match_threshold:0.8, match_threshold_restored:true, remerge_completed:true,
    simultaneous_people_preserved_at_minus_one:true, shared_match_default_preserved:true,
    processed_frames:40, detections:80, person_groups:2, repeated_face_dedup:true, restored_on_reload:true,
    empty_video_frames:20, deletion_during_analysis:true, anonymous_image_rejected:true, mobile_layout:true}, null, 2)+'\n', {mode:0o600});
  await page.request.post('/api/auth/logout', {headers});
});
