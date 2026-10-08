import {expect, test} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync, mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

test('Find registered faces inside a group photo and show their locations', async ({page}) => {
  test.setTimeout(90000);
  const directory = mkdtempSync(join(tmpdir(), 'photo-search-'));
  const group = join(directory, 'group.jpg');
  const blank = join(directory, 'blank.png');
  execFileSync('../.venv/bin/python', ['-c', `
from PIL import Image, ImageOps
import sys
photos = [Image.open(path).convert('RGB') for path in ['../data/calibration/person_b_variant.jpg', '../data/calibration/person_a_variant.jpg', '../data/calibration/person_b_reference.jpg']]
canvas = Image.new('RGB', (1536, 512), 'white')
for index, photo in enumerate(photos):
    canvas.paste(ImageOps.fit(photo, (512, 512)), (index * 512, 0))
canvas.save(sys.argv[1], quality=95)
Image.new('RGB', (640, 480), 'white').save(sys.argv[2])
`, group, blank]);
  const credentials = readFileSync('../data/local-admin.txt', 'utf8');
  let personId: number | undefined;
  let otherId: number | undefined;
  let headers: Record<string,string> = {};
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto('/');
    await page.getByLabel('Username', {exact:true}).fill(credentials.match(/^username: (.+)$/m)![1]);
    await page.getByLabel('Password', {exact:true}).fill(credentials.match(/^password: (.+)$/m)![1]);
    await page.getByRole('button', {name:'Log In', exact:true}).click();
    await expect(page.getByRole('heading', {name:/System (Status|Readiness)/})).toBeVisible();
    headers = {'X-CSRF-Token': (await (await page.request.get('/api/auth/me')).json()).csrf_token};
    const name = `PHOTO-SEARCH-${Date.now()}`;
    for (const [label, image] of [[name, 'person_b_reference.jpg'], [name + '-other', 'person_b_variant.jpg']]) {
      const created = await page.request.post('/api/persons', {headers, data:{name:label}});
      expect(created.status()).toBe(201);
      const id = (await created.json()).id;
      if (label === name) personId = id; else otherId = id;
      const uploaded = await page.request.post(`/api/persons/${id}/faces`, {headers:{...headers, 'Content-Type':'image/jpeg'}, data:readFileSync('../data/calibration/' + image)});
      expect(uploaded.status()).toBe(201);
    }
    await page.goto(`/#/persons/${personId}/edit`);
    await expect(page.getByRole('heading', {name:'Edit Person', exact:true})).toBeVisible();
    const upload = page.getByLabel('Photo to Compare', {exact:true});
    const search = async (file: string) => {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/persons/search-photo' && r.request().method() === 'POST');
      await upload.setInputFiles(file);
      const result = await response;
      expect(result.status()).toBe(200);
      await expect(upload).toBeEnabled();
    };
    await search(group);
    await expect(page.locator('.photo-search-summary')).toContainText('3 faces detected · 2 matching faces');
    await expect(page.locator('.photo-search-preview svg rect')).toHaveCount(3);
    await expect(page.locator('.photo-face-candidate')).toHaveCount(2);
    const boxes = await page.locator('.photo-face-candidate rect').evaluateAll(elements => elements.map(element => Number(element.getAttribute('x'))));
    expect(boxes.some(x => x < 512)).toBe(true);
    expect(boxes.some(x => x > 1024)).toBe(true);
    await expect(page.locator('.comparison-results tr').filter({hasText:name}).filter({hasText:'Similarity candidate'})).toHaveCount(2);
    await expect(page.locator('.comparison-results tr').filter({hasText:name + '-other'})).toHaveCount(0);
    const preview = page.locator('.photo-search-preview image');
    await expect(preview).toHaveAttribute('href', /^data:image\/jpeg;base64,/);
    const candidateRow = page.locator('.comparison-results tr').filter({hasText:'Similarity candidate'}).first();
    await candidateRow.getByRole('button').click();
    await expect(page.locator('.photo-face-selected')).toHaveCount(1);
    await page.setViewportSize({width:390, height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    mkdirSync('../data/screenshots', {recursive:true});
    await page.locator('.photo-search-preview').screenshot({path:'../data/screenshots/photo-search-group-mobile.png'});
    await page.setViewportSize({width:1280, height:900});
    await page.locator('.person-editor').screenshot({path:'../data/screenshots/photo-search-group.png'});
    await page.getByLabel('Compare all registered people', {exact:true}).check();
    await expect(page.locator('.photo-search-preview')).toHaveCount(0);
    await search(group);
    await expect(page.locator('.comparison-results tr').filter({hasText:name + '-other'}).filter({hasText:'Similarity candidate'})).toHaveCount(2);
    await page.getByLabel('Compare all registered people', {exact:true}).uncheck();
    await search('../data/calibration/person_a_reference.jpg');
    await expect(page.locator('.photo-face-candidate')).toHaveCount(0);
    await expect(page.locator('.photo-no-match')).toBeVisible();
    await search(blank);
    await expect(page.locator('.photo-search-preview rect')).toHaveCount(0);
    await expect(page.getByText('No faces detected. Choose a photo with larger, clearly visible faces.', {exact:true})).toBeVisible();
    const person = await (await page.request.get(`/api/persons/${personId}`)).json();
    expect(person.faces.length).toBe(1);
    expect(errors).toEqual([]);
  } finally {
    for (const id of [personId, otherId]) {
      if (id) expect((await page.request.delete(`/api/persons/${id}`, {headers})).status()).toBe(204);
    }
    if (headers['X-CSRF-Token']) await page.request.post('/api/auth/logout', {headers});
    rmSync(directory, {recursive:true, force:true});
  }
});
