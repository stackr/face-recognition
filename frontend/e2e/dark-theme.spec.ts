import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('전체 페이지 다크 테마와 모바일 레이아웃', async ({page}) => {
  test.setTimeout(120000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  mkdirSync('../data/screenshots', {recursive:true});
  await page.setViewportSize({width:1600, height:1000});
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('data-bs-theme', 'dark');
  const background = (selector:string) => page.locator(selector).first().evaluate(element => getComputedStyle(element).backgroundColor);
  await expect.poll(() => background('body')).toBe('rgb(13, 20, 32)');
  await expect.poll(() => background('.login-card')).toBe('rgb(23, 34, 53)');
  await expect.poll(() => background('#username')).toBe('rgb(13, 20, 32)');
  await page.screenshot({path:'../data/screenshots/dark-login.png'});
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태', exact:true})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  try {
    for (const [path, title] of [
      ['dashboard', '시스템 준비 상태'], ['cameras', '카메라 관리'], ['live', 'Live Search'],
      ['logs', '로그'], ['settings', '기능 설정'], ['persons', '인물 관리'], ['persons/new', '인물 추가']
    ]) {
      await page.goto(`/#/${path}`);
      await expect(page.getByRole('heading', {name:title, exact:true})).toBeVisible();
      await expect.poll(() => background('.sidebar')).toBe('rgb(23, 34, 53)');
      for (const color of await page.locator('.workspace .card').evaluateAll(elements => elements.map(element => getComputedStyle(element).backgroundColor))) {
        expect(color).toBe('rgb(23, 34, 53)');
      }
      await page.screenshot({path:`../data/screenshots/dark-${path.replace('/', '-')}.png`});
      await page.setViewportSize({width:390, height:844});
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
      await page.setViewportSize({width:1600, height:1000});
    }
    await page.goto('/#/persons/new');
    await page.getByLabel('얼굴 사진 추가 (여러 장 선택 가능)', {exact:true}).setInputFiles('../data/calibration/person_b_reference.jpg');
    await expect(page.locator('.face-cropper')).toBeVisible();
    await expect.poll(() => background('.face-cropper')).toBe('rgb(30, 44, 66)');
    await page.screenshot({path:'../data/screenshots/dark-face-crop.png'});
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    expect(errors).toEqual([]);
  } finally {
    await page.request.post('/api/auth/logout', {headers:{'X-CSRF-Token':auth.csrf_token}});
  }
});
