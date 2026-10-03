import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';

test('Phase 11 선택 외형 분석 상태, 특징 메타데이터와 중지 후 제거', async ({page}) => {
  test.setTimeout(60000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  let cameraId: number | undefined;
  let headers: Record<string,string> = {};
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태'})).toBeVisible();
  headers = {'X-CSRF-Token':(await (await page.request.get('/api/auth/me')).json()).csrf_token};
  const system = await (await page.request.get('/api/system/status')).json();
  const mode = system.worker.person_reid.status;
  expect(['disabled','ready']).toContain(mode);
  try {
    cameraId = (await (await page.request.post('/api/cameras', {headers,data:{name:`REID-E2E-${Date.now()}`,source_type:'mp4'}})).json()).camera_id;
    expect((await page.request.put(`/api/cameras/${cameraId}/video`, {headers:{...headers,'Content-Type':'video/mp4'},data:readFileSync('../data/videos/face-smoke.mp4')})).status()).toBe(200);
    await page.goto(`/#/live/${cameraId}`);
    const start = await page.request.post(`/api/cameras/${cameraId}/start`, {headers,data:{source_type:'mp4',loop:true}});
    expect((await start.json()).actual_device).toMatch(/^cuda/);
    let status: any;
    await expect.poll(async () => {
      status = await (await page.request.get(`/api/cameras/${cameraId}/status`)).json();
      return mode === 'ready' ? status.reid_counts?.embeddings_created ?? 0 : status.processed_frames ?? 0;
    }, {timeout:20000}).toBeGreaterThan(1);
    if (mode === 'ready') {
      expect(system.worker.person_reid.actual_device).toBe('cuda:0');
      await expect(page.getByText('외형 특징 준비 · 인물 확인 전').first()).toBeVisible();
      const ready = status.result.tracks.filter((track:any) => track.reid?.embedding_ready);
      expect(ready.length).toBeGreaterThan(0);
      for (const track of ready) {
        expect(track.reid.identity_assignment).toBe(false);
        expect(track.reid.embedding).toBeUndefined();
        expect(track.reid.vector).toBeUndefined();
      }
      mkdirSync('../data/screenshots', {recursive:true});
      await page.locator('.faces-panel').screenshot({path:'../data/screenshots/phase11-body-ready.png'});
    } else {
      expect(status.reid_cache_tracks).toBe(0);
      await expect(page.getByText('외형 특징 준비 · 인물 확인 전')).toHaveCount(0);
    }
    const stopped = await (await page.request.post(`/api/cameras/${cameraId}/stop`, {headers})).json();
    expect(stopped.reid_cache_tracks).toBe(0);
    mkdirSync('../data/reports', {recursive:true});
    writeFileSync(`../data/reports/phase11-browser-${mode}.json`, JSON.stringify({status:'passed',mode,device:system.worker.person_reid.actual_device ?? null,checks:['optional_mode','cuda_detection','metadata_only','stop_erases_cache']}, null, 2));
  } finally {
    if (cameraId) {await page.request.post(`/api/cameras/${cameraId}/stop`, {headers}); await page.request.delete(`/api/cameras/${cameraId}`, {headers});}
    await page.request.post('/api/auth/logout', {headers});
  }
});
