import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test.use({actionTimeout:10000, navigationTimeout:20000});

test('기능 설정·비교점수 즉시 반영, 사진 비교, 다중 프레임 후보와 로그', async ({page, request}) => {
  test.setTimeout(120000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  const prefix = `RECOGNITION-E2E-${Date.now()}`;
  let cameraId: number | undefined;
  let personId: number | undefined;
  let originalValues: Record<string, number> | undefined;
  let ownedRevision: number | undefined;
  let headers: Record<string, string> = {};
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({width: 1600, height: 1000});
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태'})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  headers = {'X-CSRF-Token': auth.csrf_token};
  try {
    const original = await (await page.request.get('/api/function-settings')).json();
    originalValues = original.values;
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
    expect((await page.request.post(`/api/cameras/${cameraId}/start`, {headers, data:{source_type:'mp4', loop:true}})).status()).toBe(200);
    await expect.poll(async () => (await (await page.request.get(`/api/cameras/${cameraId}/status`)).json()).state).toBe('running');
    const before = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    await page.getByRole('navigation', {name:'주 메뉴'}).getByRole('button', {name:'기능 설정'}).click();
    await expect(page.getByRole('heading', {name:'기능 설정', exact:true})).toBeVisible();
    await page.getByLabel('얼굴 검사 간격 (초)', {exact:true}).fill('0.1');
    await expect(page.getByRole('button', {name:'설정 저장', exact:true})).toBeDisabled();
    await page.getByLabel('얼굴 검사 간격 (초)', {exact:true}).fill('0.2');
    await page.getByLabel('사람 검출 빈도 (FPS)', {exact:true}).fill('8');
    await page.getByLabel('프레임당 최대 얼굴 검사 인원', {exact:true}).fill('6');
    await page.getByLabel('비교점수 기준', {exact:true}).fill('1.01');
    await expect(page.getByRole('button', {name:'설정 저장', exact:true})).toBeDisabled();
    await page.getByLabel('비교점수 기준', {exact:true}).fill('-1.01');
    await expect(page.getByRole('button', {name:'설정 저장', exact:true})).toBeDisabled();
    await page.getByLabel('비교점수 기준', {exact:true}).fill('0.7');
    const saveResponse = page.waitForResponse(response => response.url().endsWith('/api/function-settings') && response.request().method() === 'PUT');
    await page.getByRole('button', {name:'설정 저장', exact:true}).click();
    ownedRevision = (await (await saveResponse).json()).revision;
    await expect(page.locator('app-function-settings').getByRole('status')).toContainText('설정을 저장');
    await expect(page.locator('.settings-state')).toContainText('반영 완료');
    const configured = await (await page.request.get('/api/function-settings')).json();
    expect(configured.values).toEqual({detection_fps:8, face_analysis_interval:0.2, face_rois_per_frame:6, face_match_threshold:0.7});
    expect(configured.applied).toBe(true);
    const after = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    expect(after.stream_session_id).toBe(before.stream_session_id);
    expect(after.actual_device).toMatch(/^cuda(?::\d+)?$/);
    mkdirSync('../data/screenshots', {recursive:true});
    await page.locator('app-function-settings').screenshot({path:'../data/screenshots/recognition-settings-desktop.png'});
    await expect.poll(async () => {
      const state = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
      return state.result?.tracks.some((track: {face?: {sample_count: number; matches: {person_id:number; supporting_samples:number}[]}}) =>
        track.face?.sample_count && track.face.sample_count >= 2 && track.face.matches.some(match => match.person_id === personId && match.supporting_samples >= 2));
    }, {timeout:20000}).toBe(true);
    let eventId = 0;
    await expect.poll(async () => {
      const event = (await (await page.request.get('/api/events?latest=true&limit=100')).json()).items
        .find((row:{event_id:number; camera_id:number; person_id:number}) => row.camera_id === cameraId && row.person_id === personId);
      eventId = event?.event_id ?? 0;
      return eventId;
    }).toBeGreaterThan(0);

    // Raising the criterion removes current candidates without removing history or
    // restarting the stream. The photo comparison must report the same criterion.
    await page.getByLabel('비교점수 기준', {exact:true}).fill('1');
    const strictSave = page.waitForResponse(response => response.url().endsWith('/api/function-settings') && response.request().method() === 'PUT');
    await page.getByRole('button', {name:'설정 저장', exact:true}).click();
    ownedRevision = (await (await strictSave).json()).revision;
    await expect(page.locator('.settings-state')).toContainText('비교점수 기준 1.00');
    await expect.poll(async () => {
      const state = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
      const tracks = state.result?.tracks ?? [];
      return tracks.some((track:{face?:{comparison?:{threshold:number}; sample_count:number}}) =>
        track.face?.comparison?.threshold === 1 && track.face.sample_count >= 2)
        && tracks.every((track:{face?:{matches:{person_id:number}[]}}) =>
          !track.face?.matches.some(match => match.person_id === personId));
    }).toBe(true);
    expect((await page.request.get(`/api/events/${eventId}`)).status()).toBe(200);
    await page.reload();
    await expect(page.getByLabel('비교점수 기준', {exact:true})).toHaveValue('1');
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.locator('app-function-settings').screenshot({path:'../data/screenshots/match-threshold-settings-mobile.png'});
    await page.setViewportSize({width:1600, height:1000});
    await page.goto(`/#/persons/${personId}/edit`);
    const strictPhoto = page.waitForResponse(response => response.url().endsWith('/api/persons/search') && response.request().method() === 'POST');
    await page.getByLabel('비교할 사진', {exact:true}).setInputFiles('../data/calibration/person_b_variant.jpg');
    // Chrome may evict Blob-upload response bodies from DevTools. Verify the real
    // response as rendered by Angular without replacing the request or result.
    expect((await strictPhoto).status()).toBe(200);
    const strictRow = page.locator('.comparison-results').getByRole('row').filter({hasText:prefix});
    await expect(page.getByText('시험 임계값 1.00 · 인물별 등록 얼굴 중 최대 유사도', {exact:true})).toBeVisible();
    await expect(strictRow).toContainText('임계값 미달');
    expect(Number(await strictRow.getByRole('cell').nth(1).textContent())).toBeLessThan(1);

    await page.getByRole('navigation', {name:'주 메뉴'}).getByRole('button', {name:'기능 설정'}).click();
    await expect(page.getByLabel('비교점수 기준', {exact:true})).toHaveValue('1');
    await page.getByLabel('비교점수 기준', {exact:true}).fill('0.7');
    const relaxedSave = page.waitForResponse(response => response.url().endsWith('/api/function-settings') && response.request().method() === 'PUT');
    await page.getByRole('button', {name:'설정 저장', exact:true}).click();
    ownedRevision = (await (await relaxedSave).json()).revision;
    await expect(page.locator('.settings-state')).toContainText('비교점수 기준 0.70');
    await expect.poll(async () => {
      const state = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
      return state.stream_session_id === before.stream_session_id && state.result?.tracks.some(
        (track:{face?:{comparison?:{threshold:number}; matches:{person_id:number}[]}}) =>
          track.face?.comparison?.threshold === 0.7 && track.face.matches.some(match => match.person_id === personId));
    }).toBe(true);
    await page.goto(`/#/persons/${personId}/edit`);
    const relaxedPhoto = page.waitForResponse(response => response.url().endsWith('/api/persons/search') && response.request().method() === 'POST');
    await page.getByLabel('비교할 사진', {exact:true}).setInputFiles('../data/calibration/person_b_variant.jpg');
    expect((await relaxedPhoto).status()).toBe(200);
    await expect(page.getByText('시험 임계값 0.70 · 인물별 등록 얼굴 중 최대 유사도', {exact:true})).toBeVisible();
    await expect(page.locator('.comparison-results').getByRole('row').filter({hasText:prefix})).toContainText('유사도 후보');
    await page.getByRole('navigation', {name:'주 메뉴'}).getByRole('button', {name:'로그'}).click();
    await expect(page.getByRole('heading', {name:'로그', exact:true})).toBeVisible();
    await page.getByLabel('최신 페이지 자동 갱신 (5초)', {exact:true}).uncheck();
    await page.getByLabel('로그 카메라', {exact:true}).selectOption(String(cameraId));
    await page.getByLabel('검사 결과', {exact:true}).selectOption('matched');
    await page.getByRole('button', {name:'조회', exact:true}).click();
    await expect(page.locator('.recognition-log-table tbody tr').first()).toContainText('유사도 후보');
    const logs = await (await page.request.get(`/api/recognition-logs?camera_id=${cameraId}&outcome=matched`)).json();
    expect(logs.items.length).toBeGreaterThan(0);
    expect(logs.items.every((item:{sample_count:number; settings_revision:number; metrics:{detector_input:number}}) => item.sample_count >= 2 && [320,640].includes(item.metrics.detector_input))).toBe(true);
    expect((await request.get('/api/recognition-logs')).status()).toBe(401);
    await page.locator('.recognition-log-table tbody tr').first().getByText('상세', {exact:true}).click();
    await expect(page.locator('.recognition-log-table tbody tr').first()).toContainText('설정 버전');
    await page.locator('app-recognition-logs').screenshot({path:'../data/screenshots/recognition-logs-desktop.png'});
    await page.getByLabel('검사 결과', {exact:true}).selectOption('quality_rejected');
    await page.getByRole('button', {name:'조회', exact:true}).click();
    await expect(page.locator('.recognition-log-table tbody')).toContainText('품질 기준 미달');
    await page.reload();
    await expect(page.getByRole('heading', {name:'로그', exact:true})).toBeVisible();
    await page.getByLabel('로그 카메라', {exact:true}).selectOption(String(cameraId));
    await page.getByRole('button', {name:'조회', exact:true}).click();
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.screenshot({path:'../data/screenshots/recognition-logs-mobile.png'});
    const menu = await page.getByRole('navigation', {name:'주 메뉴'}).getByRole('button').allTextContents();
    expect(menu.at(-2)).toContain('인물 관리');
    expect(menu.at(-1)).toContain('얼굴 검출 테스트');
    expect(errors).toEqual([]);
  } finally {
    test.setTimeout(180000);
    try {
      if (originalValues && ownedRevision !== undefined) {
        const current = await (await page.request.get('/api/function-settings')).json();
        // A user's newer edit takes precedence over this test's restoration.
        if (current.revision === ownedRevision) {
          const restored = await page.request.put('/api/function-settings', {headers, data:{...originalValues, revision:current.revision}});
          expect([200,202]).toContain(restored.status());
        }
      }
    } finally {
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
