import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('전체 로그 삭제 확인·취소·실패·페이지 초기화 및 관리자 표시', async ({page}) => {
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  let deleted = false;
  let failDelete = true;
  let deleteCalls = 0;
  let releaseDelete: (() => void) | undefined;
  // All log requests are intercepted: this test never deletes stored user logs.
  await page.route('**/api/recognition-logs*', async route => {
    const request = route.request();
    if (request.method() === 'DELETE') {
      deleteCalls++;
      expect(new URL(request.url()).search).toBe('');
      expect(request.headers()['x-csrf-token']).toBeTruthy();
      if (failDelete) {
        await route.fulfill({status:403, json:{detail:'Administrator permission required'}});
        return;
      }
      await new Promise<void>(resolve => {releaseDelete = resolve;});
      deleted = true;
      await route.fulfill({json:{deleted_count:52}});
      return;
    }
    const older = new URL(request.url()).searchParams.has('before_id');
    await route.fulfill({json:{
      items: deleted ? [] : [{id:older ? 1 : 52, camera_id:1, camera_name:'시험 카메라',
        stream_session_id:'a'.repeat(32), track_id:1, frame_id:1, captured_at:'2026-10-04T00:00:00Z',
        outcome:'quality_rejected', reasons:['blurred'], quality:0.4, top_similarity:null,
        sample_count:0, settings_revision:1, metrics:{blur_score:20}}],
      has_more:!deleted && !older, next_cursor:!deleted && !older ? 52 : null,
      summary:{total:deleted ? 0 : 52, outcomes:deleted ? {} : {quality_rejected:52}, reasons:{}},
      retention_days:7, max_records:10000
    }});
  });
  await page.setViewportSize({width:1600, height:1000});
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태'})).toBeVisible();
  try {
    await page.goto('/#/logs');
    await page.getByLabel('최신 페이지 자동 갱신 (5초)', {exact:true}).uncheck();
    const button = page.getByRole('button', {name:'전체 로그 삭제', exact:true});
    await expect(button).toBeEnabled();
    page.once('dialog', dialog => dialog.dismiss());
    await button.click();
    expect(deleteCalls).toBe(0);
    await page.getByRole('button', {name:'이전 기록', exact:true}).click();
    await expect(page.locator('.log-list-tools')).toContainText('2 페이지');
    page.once('dialog', dialog => dialog.accept());
    await button.click();
    await expect(page.getByRole('alert')).toHaveText('전체 로그를 삭제할 권한이 없습니다.');
    await expect(page.locator('.recognition-log-table tbody')).toContainText('시험 카메라');
    failDelete = false;
    page.once('dialog', dialog => {
      expect(dialog.message()).toContain('검색 조건과 관계없이');
      return dialog.accept();
    });
    await button.click();
    await expect(page.getByRole('button', {name:'삭제 중…', exact:true})).toBeDisabled();
    await expect(page.getByRole('button', {name:'최신 기록', exact:true})).toBeDisabled();
    await expect.poll(() => Boolean(releaseDelete)).toBe(true);
    releaseDelete!();
    await expect(page.getByRole('status')).toHaveText('전체 로그 52건을 삭제했습니다.');
    await expect(page.locator('.log-list-tools')).toContainText('1 페이지');
    await expect(page.locator('.log-counts strong')).toHaveText('전체 0건');
    await expect(page.locator('.recognition-log-table tbody')).toContainText('조건에 맞는 얼굴 검사 로그가 없습니다.');
    expect(deleteCalls).toBe(2);
    await page.setViewportSize({width:390, height:844});
    await expect(button).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    mkdirSync('../data/screenshots', {recursive:true});
    await page.screenshot({path:'../data/screenshots/log-delete-mobile.png'});
    await page.route('**/api/auth/me', async route => {
      const response = await route.fetch();
      const auth = await response.json();
      await route.fulfill({response, json:{...auth, user:{...auth.user, role:'viewer'}}});
    });
    await page.reload();
    await expect(page.getByRole('heading', {name:'얼굴 검사 로그', exact:true})).toBeVisible();
    await expect(button).toHaveCount(0);
  } finally {
    const auth = await (await page.request.get('/api/auth/me')).json();
    await page.request.post('/api/auth/logout', {headers:{'X-CSRF-Token':auth.csrf_token}});
  }
});
