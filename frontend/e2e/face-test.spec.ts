import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';

test('실제 CUDA 전체 프레임 분석·인물별 묶음·복구·빈 영상·분석 중 전체 삭제', async ({page, request}) => {
  test.setTimeout(180000);
  const accountFile = process.env['FACE_TEST_E2E_ACCOUNT'];
  if (!accountFile) throw new Error('Run scripts/check_face_test_browser.py to use an isolated account.');
  const account = JSON.parse(readFileSync(accountFile, 'utf8'));
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({width:1600, height:1000});
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(account.username);
  await page.getByLabel('비밀번호', {exact:true}).fill(account.password);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태', exact:true})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  const headers = {'X-CSRF-Token':auth.csrf_token};
  const menu = page.getByRole('navigation', {name:'주 메뉴'}).getByRole('button');
  expect((await menu.allTextContents()).at(-1)).toContain('얼굴 검출 테스트');
  await menu.filter({hasText:'얼굴 검출 테스트'}).click();
  await expect(page.getByRole('heading', {name:'얼굴 검출 테스트', exact:true})).toBeVisible();
  await expect(page.getByRole('button', {name:'영상 분석', exact:true})).toBeDisabled();
  const initial = await (await page.request.get('/api/face-tests')).json();
  expect(initial.items).toEqual([]);
  expect(initial.can_start).toBe(true);
  await page.getByLabel('분석할 영상 파일', {exact:true}).setInputFiles(account.videos.faces);
  await expect(page.getByRole('button', {name:'영상 분석', exact:true})).toBeEnabled();
  const created = page.waitForResponse(response => response.url().includes('/api/face-tests') && response.request().method() === 'POST');
  await page.getByRole('button', {name:'영상 분석', exact:true}).click();
  expect((await created).status()).toBe(202);
  let job: {job_id:string; state:string; actual_device:string; processed_frames:number; total_frames:number; detections:number; groups:{group_id:number; occurrences:number; embedding_ready:boolean}[]};
  await expect.poll(async () => {
    job = (await (await page.request.get('/api/face-tests')).json()).items[0];
    return job?.state;
  }, {timeout:90000, intervals:[500,1000,1500]}).toBe('completed');
  expect(job!.actual_device).toBe('cuda');
  expect(job!.processed_frames).toBe(40);
  expect(job!.total_frames).toBe(40);
  expect(job!.groups).toHaveLength(2);
  expect(job!.detections).toBe(80);
  expect(job!.groups.every(group => group.embedding_ready && group.occurrences === 40)).toBe(true);
  await expect(page.locator('.face-test-card')).toHaveCount(2);
  await expect(page.locator('.face-test-progress')).toContainText('분석 완료');
  await expect.poll(() => page.locator('.face-test-card img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
  const imageUrl = `/api/face-tests/${job!.job_id}/groups/${job!.groups[0].group_id}/image`;
  expect((await request.get(imageUrl)).status()).toBe(401);
  expect((await page.request.get(imageUrl)).headers()['cache-control']).toBe('no-store');
  expect((await page.request.delete('/api/face-tests')).status()).toBe(403);
  mkdirSync('../data/screenshots', {recursive:true});
  await page.screenshot({path:'../data/screenshots/face-test-desktop.png'});
  await page.reload();
  await expect(page.locator('.face-test-card')).toHaveCount(2);
  await page.setViewportSize({width:390, height:844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({path:'../data/screenshots/face-test-mobile.png', fullPage:true});
  await page.setViewportSize({width:1600, height:1000});

  await expect(page.getByRole('button', {name:'영상 분석', exact:true})).toBeDisabled();
  await page.getByLabel('분석할 영상 파일', {exact:true}).setInputFiles(account.videos.blank);
  await page.getByRole('button', {name:'영상 분석', exact:true}).click();
  await expect.poll(async () => {
    const latest = (await (await page.request.get('/api/face-tests')).json()).items[0];
    return latest.filename === 'blank.mp4' && latest.state === 'completed' && latest.processed_frames === 20 && latest.groups.length === 0;
  }).toBe(true);
  await expect(page.getByText('이 영상에서 검출된 얼굴이 없습니다.', {exact:true})).toBeVisible();
  await expect(page.getByLabel('분석 결과 영상', {exact:true})).toBeVisible();
  await page.getByLabel('분석 결과 영상', {exact:true}).selectOption(job!.job_id);
  await expect(page.locator('.face-test-card')).toHaveCount(2);

  await page.getByLabel('분석할 영상 파일', {exact:true}).setInputFiles(account.videos.faces);
  const again = page.waitForResponse(response => response.url().includes('/api/face-tests') && response.request().method() === 'POST');
  await page.getByRole('button', {name:'영상 분석', exact:true}).click();
  expect((await again).status()).toBe(202);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', {name:'목록 전체 삭제', exact:true}).click();
  await expect(page.getByText('모든 분석 목록과 추출 사진을 삭제했습니다.', {exact:true})).toBeVisible();
  await expect(page.locator('.face-test-card')).toHaveCount(0);
  expect((await (await page.request.get('/api/face-tests')).json()).items).toEqual([]);
  expect((await page.request.get(imageUrl)).status()).toBe(404);
  await page.reload();
  await expect(page.getByText('영상을 선택하고 [영상 분석] 버튼을 눌러 주세요.', {exact:true})).toBeVisible();
  expect(errors).toEqual([]);
  mkdirSync('../data/reports', {recursive:true});
  writeFileSync('../data/reports/face-test-browser.json', JSON.stringify({status:'passed', actual_device:job!.actual_device,
    processed_frames:40, detections:80, person_groups:2, repeated_face_dedup:true, restored_on_reload:true,
    empty_video_frames:20, deletion_during_analysis:true, anonymous_image_rejected:true, mobile_layout:true}, null, 2)+'\n', {mode:0o600});
  await page.request.post('/api/auth/logout', {headers});
});
