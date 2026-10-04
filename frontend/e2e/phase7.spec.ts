import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test.use({actionTimeout: 10000, navigationTimeout: 20000});

test('Live Search 상단 전체 너비 영상, 얼굴 카드, 필터, 카메라 URL 복원 및 모바일', async ({page, request}) => {
  test.setTimeout(120000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  const prefix = `LIVE7-E2E-${Date.now()}`;
  const personName = `${prefix}-PERSON`;
  let personId: number | undefined;
  const cameraIds: number[] = [];
  let headers: Record<string, string> | undefined;
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' && message.text().startsWith('ERROR')) errors.push(message.text().split('\n')[0]);
  });
  await page.setViewportSize({width: 1600, height: 1000});
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태'})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  headers = {'X-CSRF-Token': auth.csrf_token};
  try {
    const person = await page.request.post('/api/persons', {headers, data:{name:personName}});
    expect(person.status()).toBe(201);
    personId = (await person.json()).id;
    const faceResponse = await page.request.post(`/api/persons/${personId}/faces`, {
      headers:{...headers, 'Content-Type':'image/jpeg'},
      data:readFileSync('../data/calibration/person_b_reference.jpg')
    });
    expect(faceResponse.status()).toBe(201);
    const face = await faceResponse.json();
    expect(face.state).toBe('ready');
    const referencePath = `/api/persons/${personId}/faces/${face.id}/image`;
    for (const suffix of ['A', 'B']) {
      const camera = await page.request.post('/api/cameras', {headers, data:{name:`${prefix}-${suffix}`, source_type:'mp4'}});
      expect(camera.status()).toBe(201);
      cameraIds.push((await camera.json()).camera_id);
    }
    const [firstCamera, secondCamera] = cameraIds;
    expect((await page.request.put(`/api/cameras/${firstCamera}/video`, {
      headers:{...headers, 'Content-Type':'video/mp4'}, data:readFileSync('../data/videos/face-smoke.mp4')
    })).status()).toBe(200);
    await page.goto(`/#/live/${firstCamera}`);
    await expect(page.getByRole('heading', {name:'Live Search', exact:true})).toBeVisible();
    await expect(page.getByLabel('분석할 카메라').locator('option:checked')).toHaveText(`${prefix}-A`);
    await page.getByLabel('카메라 찾기', {exact:true}).fill(prefix);
    const cameras = page.locator('.live-camera-list');
    const selected = cameras.getByRole('button').filter({hasText:`${prefix}-A`});
    const other = cameras.getByRole('button').filter({hasText:`${prefix}-B`});
    await expect(selected).toHaveAttribute('aria-pressed', 'true');
    await expect(other).toBeVisible();
    const panel = page.locator('app-event-panel');
    await expect(panel.getByRole('status')).toHaveText('연결됨', {timeout:15000});
    await panel.getByLabel('인물 이름', {exact:true}).fill(personName);
    const boxes = await Promise.all([cameras, page.locator('.live-main'), panel].map(locator => locator.boundingBox()));
    expect(boxes.every(box => box !== null)).toBe(true);
    expect(boxes[0]!.x + boxes[0]!.width).toBeLessThan(boxes[1]!.x);
    expect(boxes[1]!.x + boxes[1]!.width).toBeLessThan(boxes[2]!.x);
    const layout = page.locator('.live-search-layout');
    const previewCard = page.locator('.live-preview .preview-card');
    const layoutBox = (await layout.boundingBox())!;
    const previewBox = (await previewCard.boundingBox())!;
    expect(previewBox.x).toBeCloseTo(layoutBox.x, 0);
    expect(previewBox.width).toBeCloseTo(layoutBox.width, 0);
    for (const box of boxes) expect(box!.y).toBeGreaterThan(previewBox.y + previewBox.height);
    await expect(previewCard.getByRole('heading')).toHaveText(`${prefix}-A`);
    await expect(page.locator('.live-detection-confidence')).toHaveText('—');
    mkdirSync('../data/screenshots', {recursive:true});

    await page.getByRole('button', {name:'분석 시작', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('분석 중', {timeout:20000});
    const preview = page.getByAltText('사람 탐지와 추적 번호가 표시된 카메라 영상');
    await expect.poll(() => preview.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
    const card = panel.locator('.event-card').filter({hasText:personName});
    await expect(card).toHaveCount(1, {timeout:30000});
    await card.scrollIntoViewIfNeeded();
    for (const selector of ['.event-reference-image', '.event-detected-image']) {
      await expect.poll(() => card.locator(selector).evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(112);
    }
    await expect(page.locator('.candidate-track').first()).toBeVisible();
    await expect(page.locator('.candidate-count')).not.toHaveText('유사도 후보 0명');
    const faces = page.locator('.faces-panel .face-card');
    await expect.poll(() => faces.count()).toBeGreaterThan(1);
    const faceBoxes = await Promise.all([faces.nth(0), faces.nth(1)].map(face => face.boundingBox()));
    expect(faceBoxes[0]!.y).toBeCloseTo(faceBoxes[1]!.y, 0);
    expect(faceBoxes[0]!.x + faceBoxes[0]!.width).toBeLessThan(faceBoxes[1]!.x);
    const firstFace = faces.first();
    const photoBox = (await firstFace.locator('img, .face-empty').boundingBox())!;
    const detailsBox = (await firstFace.locator('.face-details').boundingBox())!;
    expect(detailsBox.y).toBeGreaterThan(photoBox.y + photoBox.height);
    await layout.screenshot({path:'../data/screenshots/phase7-layout-desktop.png'});
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    const mobileLayoutBox = (await layout.boundingBox())!;
    const mobilePreviewBox = (await previewCard.boundingBox())!;
    expect(mobilePreviewBox.width).toBeCloseTo(mobileLayoutBox.width, 0);
    const mobileFaceBoxes = await Promise.all([faces.nth(0), faces.nth(1)].map(face => face.boundingBox()));
    expect(mobileFaceBoxes[0]!.x).toBeCloseTo(mobileFaceBoxes[1]!.x, 0);
    expect(mobileFaceBoxes[1]!.y).toBeGreaterThan(mobileFaceBoxes[0]!.y + mobileFaceBoxes[0]!.height);
    await page.locator('.faces-panel').screenshot({path:'../data/screenshots/phase7-faces-mobile.png'});
    await page.setViewportSize({width:1600, height:1000});
    const state = await (await page.request.get(`/api/cameras/${firstCamera}/status`)).json();
    expect(state.actual_device).toMatch(/^cuda(?::\d+)?$/);
    expect((await request.get(referencePath)).status()).toBe(401);
    await page.getByRole('button', {name:'분석 중지', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('중지됨');
    await expect(faces).toHaveCount(0);
    await expect(page.locator('.live-detection-confidence')).toHaveText('—');

    await panel.getByLabel('이벤트 상태', {exact:true}).selectOption('confirmed');
    await expect(panel.getByLabel('이벤트 상태', {exact:true})).toHaveValue('confirmed');
    await expect(card).toHaveCount(0);
    await expect(panel.locator('.event-empty')).toContainText('조건에 맞는 이벤트가 없습니다.');
    await panel.getByLabel('이벤트 상태', {exact:true}).selectOption('candidate');
    await expect(card).toHaveCount(1);
    await panel.getByLabel('인물 이름', {exact:true}).fill('no-such-e2e-person');
    await expect(card).toHaveCount(0);
    await panel.getByRole('button', {name:'필터 초기화', exact:true}).click();
    await panel.getByLabel('인물 이름', {exact:true}).fill(personName);
    await expect(card).toHaveCount(1);

    await other.click();
    await expect(page).toHaveURL(new RegExp(`#/live/${secondCamera}$`));
    await expect(other).toHaveAttribute('aria-pressed', 'true');
    await expect(previewCard.getByRole('heading')).toHaveText(`${prefix}-B`);
    await expect(card).toHaveCount(0);
    await panel.getByLabel('카메라 범위', {exact:true}).selectOption('all');
    await expect(card).toHaveCount(1);
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`#/live/${firstCamera}$`));
    await expect(selected).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByLabel('분석할 카메라').locator('option:checked')).toHaveText(`${prefix}-A`);
    await page.reload();
    await expect(selected).toHaveAttribute('aria-pressed', 'true');
    await panel.getByLabel('인물 이름', {exact:true}).fill(personName);
    await expect(card).toHaveCount(1);
    await page.getByLabel('카메라 찾기', {exact:true}).fill(prefix);
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await card.scrollIntoViewIfNeeded();
    await expect(card.getByRole('button', {name:'확인', exact:true})).toBeVisible();
    await card.screenshot({path:'../data/screenshots/phase7-comparison-mobile.png'});

    // Exercise the UI's missing-photo path without changing the stored reference.
    await page.route(`**${referencePath}`, route => route.fulfill({status:404, body:''}));
    await page.reload();
    await panel.getByLabel('인물 이름', {exact:true}).fill(personName);
    await expect(card).toHaveCount(1);
    await card.scrollIntoViewIfNeeded();
    await expect(card.getByText('등록 사진 없음', {exact:true})).toBeVisible();
    await expect(card.locator('.event-detected-image')).toBeVisible();

    // Capability responses drive visibility; API grant enforcement is tested against SQL separately.
    await page.route('**/api/cameras', async route => {
      const response = await route.fetch();
      const values = await response.json();
      await route.fulfill({response, json:values.map((camera: {camera_id:number}) => ({
        ...camera, ...(camera.camera_id === firstCamera ? {can_operate:false} : {}),
        ...(camera.camera_id === secondCamera ? {can_view:false, can_operate:false} : {})
      }))});
    });
    await page.reload();
    await expect(selected).toHaveAttribute('aria-pressed', 'true');
    await expect(other).toHaveCount(0);
    await expect(page.getByRole('button', {name:'분석 시작', exact:true})).toHaveCount(0);
    await expect(page.getByRole('button', {name:'분석 중지', exact:true})).toHaveCount(0);
    await page.goto('/#/live/999999999');
    await expect(page.getByRole('alert')).toContainText('카메라를 찾을 수 없거나 영상 접근 권한이 없습니다.');
    await expect(page.locator('.preview-screen')).toHaveCount(0);
    expect(errors).toEqual([]);
  } catch (error) {
    await page.locator('.live-search-layout').screenshot({path:'../data/screenshots/phase7-layout-failed.png'});
    throw error;
  } finally {
    // Keep the initial session for cleanup; no extra login consumes the rate limit.
    try {
      for (const id of cameraIds) {
        await page.request.post(`/api/cameras/${id}/stop`, {headers});
        expect((await page.request.delete(`/api/cameras/${id}`, {headers})).status()).toBe(204);
      }
    } finally {
      try {
        if (personId !== undefined) expect([202,204]).toContain((await page.request.delete(`/api/persons/${personId}`, {headers})).status());
      } finally {await page.getByRole('button', {name:'로그아웃', exact:true}).click();}
    }
  }
  await expect(page.getByRole('heading', {name:'로그인', exact:true})).toBeVisible();
  await expect(page.locator('app-event-panel')).toHaveCount(0);
});
