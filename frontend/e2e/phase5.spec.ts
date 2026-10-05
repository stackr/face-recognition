import {expect, test} from '@playwright/test';
import {spawn} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {createInterface} from 'node:readline';

function rtspFixture() {
  const child = spawn('../.venv/bin/python', ['../scripts/rtsp_fixture.py', '--stdio'], {stdio:['pipe', 'pipe', 'pipe']});
  const queued: string[] = [];
  let receive: ((value: string) => void) | undefined;
  const reader = createInterface({input:child.stdout});
  reader.on('line', line => {
    if (receive) { const deliver = receive; receive = undefined; deliver(line); }
    else queued.push(line);
  });
  async function next() {
    if (queued.length) return JSON.parse(queued.shift()!);
    return JSON.parse(await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => {receive = undefined; reject(new Error('Local RTSP fixture response timed out'));}, 15000);
      receive = value => {clearTimeout(timer); resolve(value);};
    }));
  }
  async function command(command: string) {
    child.stdin.write(JSON.stringify({command}) + '\n');
    const response = await next();
    expect(response.status).toBe('ok');
    expect(response.command).toBe(command);
  }
  return {next, command, async close() {
    child.stdin.end();
    if (child.exitCode === null) {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => { child.kill('SIGTERM'); reject(new Error('RTSP fixture cleanup timed out')); }, 12000);
        child.once('exit', () => { clearTimeout(timer); resolve(); });
      });
    }
    reader.close();
  }};
}

test('RTSP 사람 검출 전용, 이미지 초기화, 자동 복구와 재시도 중 중지', async ({page}) => {
  test.setTimeout(120000);
  const fixture = rtspFixture();
  let cameraId: number | undefined;
  let headers: Record<string,string> | undefined;
  try {
    const ready = await fixture.next();
    expect(ready.status).toBe('ready');
    const credentials = readFileSync('../data/local-admin.txt', 'utf8');
    await page.goto('/');
    await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
    await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
    await page.getByRole('button', {name:'로그인', exact:true}).click();
    await expect(page.getByRole('heading', {name:'시스템 준비 상태'})).toBeVisible();
    const auth = await (await page.request.get('/api/auth/me')).json();
    headers = {'X-CSRF-Token':auth.csrf_token};
    const name = `RTSP-E2E-${Date.now()}`;
    const created = await page.request.post('/api/cameras', {headers, data:{name, source_type:'rtsp', rtsp_url:ready.url}});
    expect(created.status()).toBe(201);
    cameraId = (await created.json()).camera_id;
    await page.getByRole('button', {name:'새로고침', exact:true}).click();
    await page.getByRole('button', {name:'카메라 관리 열기'}).click();
    await page.getByRole('row').filter({hasText:name}).getByRole('button', {name:'영상 분석', exact:true}).click();
    await expect(page.locator('#live-person-detection')).toBeChecked();
    await expect(page.locator('#live-person-detection')).toBeDisabled();
    await expect(page.locator('#live-face-detection')).not.toBeChecked();
    await expect(page.locator('#live-face-detection')).toBeDisabled();
    await page.getByRole('button', {name:'분석 시작', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('분석 중', {timeout:20000});
    const thumb = page.locator('.person-track-image').first();
    await expect.poll(() => thumb.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
    const original = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    expect(original.person_detection_enabled).toBe(true);
    expect(original.face_detection_enabled).toBe(false);
    expect(original.face_detection_frames).toBe(0);
    expect(original.face_counts.embeddings_created ?? 0).toBe(0);
    await expect(page.getByRole('heading', {name:'얼굴 검출', exact:true})).toHaveCount(0);
    const track = original.result.person_tracks[0];
    const oldThumb = `/api/cameras/${cameraId}/people/${track.track_id}?stream_session_id=${original.stream_session_id}`;
    const preview = page.locator('.preview-screen img');
    await expect.poll(() => preview.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
    await fixture.command('pause');
    await expect(page.locator('.analysis-state')).toHaveText('자동 재연결 중', {timeout:20000});
    await expect(page.locator('.reconnect-notice')).toContainText('영상 연결이 끊겼습니다.');
    await expect(page.locator('.face-card')).toHaveCount(0);
    await expect(page.locator('.person-card')).toHaveCount(0);
    await expect(preview).toHaveCount(0);
    await expect(page.getByRole('button', {name:'분석 시작', exact:true})).toBeDisabled();
    await expect(page.getByRole('button', {name:'분석 중지', exact:true})).toBeEnabled();
    await expect(page.getByLabel('시험 MP4 업로드')).toBeDisabled();
    expect((await page.request.get(oldThumb)).status()).toBe(404);
    await fixture.command('resume');
    await expect(page.locator('.analysis-state')).toHaveText('분석 중', {timeout:25000});
    await expect(page.locator('.reconnect-notice')).toHaveCount(0);
    await expect.poll(() => thumb.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
    await expect.poll(() => preview.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
    const recovered = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
    expect(recovered.stream_session_id).not.toBe(original.stream_session_id);
    expect(recovered.reconnects).toBeGreaterThan(0);
    expect((await page.request.get(oldThumb)).status()).toBe(404);
    await fixture.command('down');
    await expect(page.locator('.analysis-state')).toHaveText('자동 재연결 중', {timeout:15000});
    await page.getByRole('button', {name:'분석 중지', exact:true}).click();
    await expect(page.locator('.analysis-state')).toHaveText('중지됨');
    await fixture.command('up');
    await expect.poll(async () => (await (await page.request.get(`/api/cameras/${cameraId}/status`)).json()).state).toBe('stopped');
    await expect(page.getByRole('button', {name:'분석 시작', exact:true})).toBeEnabled();
    await expect(page.locator('.face-card')).toHaveCount(0);
    await expect(page.locator('.person-card')).toHaveCount(0);
  } finally {
    try {
      if (cameraId !== undefined && headers) {
        await page.request.post(`/api/cameras/${cameraId}/stop`, {headers});
        expect((await page.request.delete(`/api/cameras/${cameraId}`, {headers})).status()).toBe(204);
      }
      if (headers) await page.request.post('/api/auth/logout', {headers});
    } finally { await fixture.close(); }
  }
});
