import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('MP4 업로드, GPU 사람 탐지, 인증 미리보기와 시작·중지', async ({page, request}) => {
  test.setTimeout(60000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태'})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  const headers = {'X-CSRF-Token':auth.csrf_token};
  const name = `MP4-E2E-${Date.now()}`;
  let cameraId: number | undefined;
  try {
    await page.getByRole('button', {name:'카메라 관리 열기'}).click();
    await page.getByLabel('카메라 이름', {exact:true}).fill(name);
    await page.getByLabel('입력 유형').selectOption('mp4');
    await page.getByRole('button', {name:'카메라 저장'}).click();
    const row = page.getByRole('row').filter({hasText:name});
    await expect(row).toBeVisible();
    const cameras = await (await page.request.get('/api/cameras')).json();
    cameraId = cameras.find((camera: {name: string}) => camera.name === name).camera_id;
    await row.getByRole('button', {name:'영상 분석', exact:true}).click();
    await page.getByLabel('시험 MP4 업로드').setInputFiles('../data/videos/people.mp4');
    await expect(page.getByText('시험 영상을 업로드했습니다. 분석 시작을 눌러 주세요.')).toBeVisible();
    await expect(page.getByLabel('분석할 카메라').locator('option:checked')).toHaveText(name);
    await page.getByRole('button', {name:'분석 시작', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('분석 중', {timeout:15000});
    const image = page.getByAltText('사람 탐지와 추적 번호가 표시된 카메라 영상');
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
    await expect.poll(async () => (await (await page.request.get(`/api/cameras/${cameraId}/status`)).json()).max_people).toBeGreaterThan(0);
    expect((await request.get(`/api/cameras/${cameraId}/preview`)).status()).toBe(401);
    const first = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    mkdirSync('../data/screenshots', {recursive:true});
    await page.screenshot({path:'../data/screenshots/phase2-live.png', fullPage:true});
    await page.getByRole('button', {name:'분석 중지', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('중지됨');
    await expect(image).toHaveCount(0);
    await page.getByRole('button', {name:'분석 시작', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('분석 중', {timeout:15000});
    const restarted = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    expect(restarted.stream_session_id).not.toBe(first.stream_session_id);
    await page.getByRole('button', {name:'분석 중지', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('중지됨');
  } finally {
    if (cameraId !== undefined) {
      await page.request.post(`/api/cameras/${cameraId}/stop`, {headers});
      expect((await page.request.delete(`/api/cameras/${cameraId}`, {headers})).status()).toBe(204);
    }
    await page.request.post('/api/auth/logout', {headers});
  }
});
