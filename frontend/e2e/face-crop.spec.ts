import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('인물 생성 전 로컬 사진 크롭, 선택 영역만 전송, 수정 화면에서 취소와 재등록', async ({page}) => {
  test.setTimeout(90000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  await page.goto('/');
  await page.getByLabel('아이디', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('비밀번호', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'로그인', exact:true}).click();
  await expect(page.getByRole('heading', {name:'시스템 준비 상태'})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  const headers = {'X-CSRF-Token':auth.csrf_token};
  let personId: number | undefined;
  try {
    await page.getByRole('button', {name:/인물 관리/}).click();
    await page.getByRole('button', {name:'인물 추가', exact:true}).click();
    let writes = 0;
    page.on('request', request => {if (request.method() === 'POST' && /\/api\/persons(?:$|\/\d+\/faces$)/.test(new URL(request.url()).pathname)) writes++;});
    await page.getByLabel('얼굴 사진 추가', {exact:false}).setInputFiles('../data/samples/zidane.jpg');
    const save = page.getByRole('button', {name:'크롭한 얼굴 저장', exact:true});
    await expect(save).toBeEnabled();
    expect(writes).toBe(0);
    await save.click();
    await expect(page.getByRole('alert')).toContainText('인물 이름을 입력');
    expect(writes).toBe(0);
    await page.getByLabel('인물 이름', {exact:true}).fill(`CROP-E2E-${Date.now()}`);
    const stage = page.locator('.crop-stage');
    await stage.scrollIntoViewIfNeeded();
    const bounds = (await stage.boundingBox())!;
    const dimensions = await stage.locator('img').evaluate((img: HTMLImageElement) => ({width:img.naturalWidth, height:img.naturalHeight}));
    await page.mouse.move(bounds.x + 895 / dimensions.width * bounds.width, bounds.y + 65 / dimensions.height * bounds.height);
    await page.mouse.down();
    await page.mouse.move(bounds.x + 1075 / dimensions.width * bounds.width, bounds.y + 305 / dimensions.height * bounds.height, {steps:10});
    await page.mouse.up();
    expect(Number(await page.getByLabel('크롭 가로', {exact:true}).inputValue())).toBeGreaterThanOrEqual(178);
    expect(Number(await page.getByLabel('크롭 가로', {exact:true}).inputValue())).toBeLessThanOrEqual(182);
    for (const [label, value] of [['크롭 가로','180'], ['크롭 세로','240'], ['크롭 시작 X','895'], ['크롭 시작 Y','65']]) {
      await page.getByLabel(label, {exact:true}).fill(value);
    }
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await expect(page.locator('.crop-preview')).toBeVisible();
    mkdirSync('../data/screenshots', {recursive:true});
    await page.screenshot({path:'../data/screenshots/face-crop-mobile.png', fullPage:true});
    const created = page.waitForResponse(response => response.url().endsWith('/api/persons') && response.request().method() === 'POST');
    const sent = page.waitForRequest(request => /\/api\/persons\/\d+\/faces$/.test(new URL(request.url()).pathname) && request.method() === 'POST');
    await save.click();
    const person = await created;
    personId = (await person.json()).id;
    expect(person.status()).toBe(201);
    const uploaded = await sent;
    const bytes = [...uploaded.postDataBuffer()!];
    const cropped = await page.evaluate(async bytes => {
      const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], {type:'image/jpeg'}));
      const size = {width:bitmap.width, height:bitmap.height}; bitmap.close(); return size;
    }, bytes);
    expect(cropped).toEqual({width:180, height:240});
    await expect(page).toHaveURL(new RegExp(`#/persons/${personId}/edit$`));
    await expect(page.locator('.face-card')).toHaveCount(1);
    await expect(page.locator('app-face-cropper')).toHaveCount(0);
    await expect.poll(() => page.locator('.face-card img').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(112);
    await page.getByLabel('얼굴 사진 추가', {exact:false}).setInputFiles('../data/calibration/person_b_variant.jpg');
    await expect(save).toBeEnabled();
    await page.getByRole('button', {name:'이 사진 취소', exact:true}).click();
    await expect(page.locator('.face-card')).toHaveCount(1);
    await expect(page.locator('app-face-cropper')).toHaveCount(0);
    await page.getByLabel('얼굴 사진 추가', {exact:false}).setInputFiles('../data/calibration/person_b_variant.jpg');
    await expect(save).toBeEnabled(); await save.click();
    await expect(page.locator('.face-card')).toHaveCount(2);
  } finally {
    if (personId !== undefined) expect([204,202]).toContain((await page.request.delete(`/api/persons/${personId}`, {headers})).status());
    await page.request.post('/api/auth/logout', {headers});
  }
});
