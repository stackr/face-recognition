import {expect, test} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';

test('Dark theme and mobile layout on every page', async ({page}) => {
  test.setTimeout(120000);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  mkdirSync('../data/screenshots', {recursive:true});
  await page.setViewportSize({width:1600, height:1000});
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('data-bs-theme', 'dark');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  const background = (selector:string) => page.locator(selector).first().evaluate(element => getComputedStyle(element).backgroundColor);
  await expect.poll(() => background('body')).toBe('rgb(13, 20, 32)');
  await expect.poll(() => background('.login-card')).toBe('rgb(23, 34, 53)');
  await expect.poll(() => background('#username')).toBe('rgb(13, 20, 32)');
  await page.screenshot({path:'../data/screenshots/dark-login.png'});
  await page.getByLabel('Username', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
  await page.getByLabel('Password', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
  await page.getByRole('button', {name:'Log In', exact:true}).click();
  await expect(page.getByRole('heading', {name:'System Readiness', exact:true})).toBeVisible();
  const auth = await (await page.request.get('/api/auth/me')).json();
  try {
    for (const [path, title] of [
      ['dashboard', 'System Readiness'], ['cameras', 'Cameras'], ['live', 'Live Search'],
      ['logs', 'Logs'], ['settings', 'Feature Settings'], ['persons', 'People'], ['persons/new', 'Add Person'], ['face-test', 'Face Detection Test']
    ]) {
      await page.goto(`/#/${path}`);
      await expect(page.getByRole('heading', {name:title, exact:true})).toBeVisible();
      await expect(page.locator('.live-search-layout')).toHaveCount(path === 'live' ? 1 : 0);
      await expect(page.locator('app-event-panel')).toHaveCount(path === 'live' ? 1 : 0);
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
    await page.getByLabel('Add Face Photos (multiple selection allowed)', {exact:true}).setInputFiles('../data/calibration/person_b_reference.jpg');
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
