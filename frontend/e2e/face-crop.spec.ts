import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('Local face cropping before registration, editor retry and cancellation', async ({page}) => {
  test.setTimeout(90000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  await page.goto('/');
  await page.getByLabel('Username', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('Password', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'Log In', exact:true}).click();
  await expect(page.getByRole('heading', {name:'System Readiness'})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  const headers = {'X-CSRF-Token':auth.csrf_token};
  let personId: number | undefined;
  try {
    await page.getByRole('button', {name:/People/}).click();
    await page.getByRole('button', {name:'Add Person', exact:true}).click();
    let writes = 0;
    page.on('request', request => {if (request.method() === 'POST' && /\/api\/persons(?:$|\/\d+\/faces$)/.test(new URL(request.url()).pathname)) writes++;});
    await page.getByLabel('Add Face Photos', {exact:false}).setInputFiles('../data/samples/zidane.jpg');
    const save = page.getByRole('button', {name:'Save Cropped Face', exact:true});
    await expect(save).toBeEnabled();
    expect(writes).toBe(0);
    await save.click();
    await expect(page.getByRole('alert')).toContainText('Enter the person name');
    expect(writes).toBe(0);
    await page.getByLabel('Person Name', {exact:true}).fill(`CROP-E2E-${Date.now()}`);
    const stage = page.locator('.crop-stage');
    await stage.scrollIntoViewIfNeeded();
    const bounds = (await stage.boundingBox())!;
    const dimensions = await stage.locator('img').evaluate((img: HTMLImageElement) => ({width:img.naturalWidth, height:img.naturalHeight}));
    await page.mouse.move(bounds.x + 895 / dimensions.width * bounds.width, bounds.y + 65 / dimensions.height * bounds.height);
    await page.mouse.down();
    await page.mouse.move(bounds.x + 1075 / dimensions.width * bounds.width, bounds.y + 305 / dimensions.height * bounds.height, {steps:10});
    await page.mouse.up();
    expect(Number(await page.getByLabel('Crop Width', {exact:true}).inputValue())).toBeGreaterThanOrEqual(178);
    expect(Number(await page.getByLabel('Crop Width', {exact:true}).inputValue())).toBeLessThanOrEqual(182);
    for (const [label, value] of [['Crop Width','180'], ['Crop Height','240'], ['Crop Origin X','895'], ['Crop Origin Y','65']]) {
      await page.getByLabel(label, {exact:true}).fill(value);
    }
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await expect(page.locator('.crop-preview')).toBeVisible();
    mkdirSync('../data/screenshots', {recursive:true});
    await page.screenshot({path:'../data/screenshots/face-crop-mobile.png', fullPage:true});
    // Chrome can omit a Blob request's bytes from DevTools. Inspect the same Blob
    // passed to the real XHR transport without replacing or modifying the upload.
    await page.evaluate(() => {
      const state = window as typeof window & {faceUploadSize?: Promise<{width: number; height: number}>};
      const send = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.send = function (body) {
        if (body instanceof Blob && body.type === 'image/jpeg') {
          state.faceUploadSize = createImageBitmap(body).then(bitmap => {
            const size = {width: bitmap.width, height: bitmap.height};
            bitmap.close();
            return size;
          });
        }
        return send.call(this, body);
      };
    });
    const created = page.waitForResponse(response => response.url().endsWith('/api/persons') && response.request().method() === 'POST');
    const sent = page.waitForRequest(request => /\/api\/persons\/\d+\/faces$/.test(new URL(request.url()).pathname) && request.method() === 'POST');
    await save.click();
    const person = await created;
    personId = (await person.json()).id;
    expect(person.status()).toBe(201);
    await sent;
    const cropped = await page.evaluate(() =>
      (window as typeof window & {faceUploadSize?: Promise<{width: number; height: number}>}).faceUploadSize);
    expect(cropped).toEqual({width:180, height:240});
    await expect(page).toHaveURL(new RegExp(`#/persons/${personId}/edit$`));
    await expect(page.locator('.face-card')).toHaveCount(1);
    await expect(page.locator('app-face-cropper')).toHaveCount(0);
    await expect.poll(() => page.locator('.face-card img').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(112);
    await page.getByLabel('Add Face Photos', {exact:false}).setInputFiles('../data/calibration/person_b_variant.jpg');
    await expect(save).toBeEnabled();
    await page.getByRole('button', {name:'Cancel This Image', exact:true}).click();
    await expect(page.locator('.face-card')).toHaveCount(1);
    await expect(page.locator('app-face-cropper')).toHaveCount(0);
    await page.getByLabel('Add Face Photos', {exact:false}).setInputFiles('../data/calibration/person_b_variant.jpg');
    await expect(save).toBeEnabled(); await save.click();
    await expect(page.locator('.face-card')).toHaveCount(2);
  } finally {
    if (personId !== undefined) expect([204,202]).toContain((await page.request.delete(`/api/persons/${personId}`, {headers})).status());
    await page.request.post('/api/auth/logout', {headers});
  }
});
