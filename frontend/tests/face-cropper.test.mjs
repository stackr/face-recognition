import '@angular/compiler';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/app/face-cropper.component.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {
  target:ts.ScriptTarget.ES2022, module:ts.ModuleKind.ES2022, experimentalDecorators:true
}}).outputText.replace(/from ['"](@angular\/[^'"]+)['"]/g, (_, name) => `from '${import.meta.resolve(name)}'`);
const {FaceCropperComponent} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

function cropper() {
  const component = new FaceCropperComponent();
  component.width = 1280; component.height = 720; component.image.set({});
  component.updatePreview = () => {};
  component.resetCrop();
  const stage = {getBoundingClientRect:() => ({left:10, top:20, width:640, height:360}),
    setPointerCapture:() => {}, hasPointerCapture:() => false};
  const pointer = (x, y, type = 'outside') => ({pointerId:1, button:0,
    clientX:10 + x / 2, clientY:20 + y / 2, currentTarget:stage,
    target:{closest: selector => type === 'handle' && selector === '.crop-handle' || type === 'inside' && selector === '.crop-selection'},
    preventDefault:() => {}});
  return {component, pointer};
}

test('display scaling maps a drawn face rectangle to original pixels', () => {
  const {component, pointer} = cropper();
  component.beginCrop(pointer(895,65,'inside'));
  component.moveCrop(pointer(1075,305)); component.endCrop(pointer(1075,305));
  assert.deepEqual(component.area, {x:895,y:65,width:180,height:240});
});

test('moving and resizing cannot leave the image or shrink below 80 pixels', () => {
  const {component, pointer} = cropper();
  component.area = {x:895,y:65,width:180,height:240};
  component.beginCrop(pointer(960,150,'inside')); component.moveCrop(pointer(1280,720)); component.endCrop(pointer(1280,720));
  assert.deepEqual(component.area, {x:1100,y:480,width:180,height:240});
  component.beginCrop(pointer(1280,720,'handle')); component.moveCrop(pointer(1100,480)); component.endCrop(pointer(1100,480));
  assert.deepEqual(component.area, {x:1100,y:480,width:80,height:80});
  component.disabled = true;
  component.adjust('x',0); assert.equal(component.area.x,1100);
});

test('crop export preserves original resolution and the selected source rectangle', () => {
  const {component} = cropper();
  component.area = {x:895,y:65,width:180,height:240};
  const calls = [];
  const previous = globalThis.document;
  globalThis.document = {createElement:() => ({width:0,height:0,getContext:() => ({drawImage:(...args) => calls.push(args)})})};
  try {
    const canvas = component.canvas(true);
    assert.equal(canvas.width,180); assert.equal(canvas.height,240);
    assert.deepEqual(calls[0].slice(1),[895,65,180,240,0,0,180,240]);
  } finally {globalThis.document = previous;}
});
