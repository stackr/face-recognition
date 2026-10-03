import '@angular/compiler';
import {HttpClient} from '@angular/common/http';
import {createEnvironmentInjector, runInInjectionContext} from '@angular/core';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {of, throwError} from 'rxjs';
import ts from 'typescript';

function compile(file, relativeImports = {}) {
  const source = readFileSync(new URL(file, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {compilerOptions:{
    target:ts.ScriptTarget.ES2022, module:ts.ModuleKind.ES2022, experimentalDecorators:true
  }}).outputText.replace(/from ['"]([^'"]+)['"]/g, (_, name) => `from '${relativeImports[name] || import.meta.resolve(name)}'`);
  return `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`;
}
const cropper = compile('../src/app/face-cropper.component.ts');
const events = compile('../src/app/event-panel.component.ts');
const labels = compile('../src/app/recognition-labels.ts');
const settings = compile('../src/app/function-settings.component.ts');
const logs = compile('../src/app/recognition-logs.component.ts', {'./recognition-labels':labels});
const {AppComponent} = await import(compile('../src/app/app.component.ts', {
  './face-cropper.component':cropper, './event-panel.component':events,
  './function-settings.component':settings, './recognition-logs.component':logs
}));

function setup() {
  const oldWindow = globalThis.window;
  const location = {hash:'#/persons/new', pathname:'/', search:''};
  globalThis.window = {location, setInterval:() => 1, clearInterval:() => {},
    addEventListener:() => {}, removeEventListener:() => {},
    history:{replaceState:(_, __, path) => {location.hash=path;}}};
  const person = {id:42,name:'crop-test',description:'',enabled:true,sync_status:'ready',faces:[]};
  const calls = [];
  let rejectFace = true;
  const http = {
    get: path => path === '/api/auth/me' ? throwError(() => ({status:401})) : of(path === '/api/persons' ? [person] : {status:'ok'}),
    post: (path, body) => {
      calls.push({path,body});
      if (path === '/api/persons') return of(person);
      if (rejectFace) return throwError(() => ({status:422,error:{detail:{code:'quality_rejected'}}}));
      const face = {id:1,state:'ready',image_available:true}; person.faces = [face]; return of(face);
    }
  };
  const injector = createEnvironmentInjector([{provide:HttpClient,useValue:http}]);
  const app = runInInjectionContext(injector, () => new AppComponent());
  app.user.set({id:1,username:'unit',role:'admin'});
  app.personForm.name='crop-test'; app.cropFiles.set([{}]);
  return {app,calls,location,allowFace:() => {rejectFace=false;}, close() {app.ngOnDestroy(); injector.destroy(); globalThis.window=oldWindow;}};
}

test('failed crop upload preserves the new person and retries only the photo', async () => {
  const context = setup();
  try {
    const photo = new Blob(['local cropped image'], {type:'image/jpeg'});
    await context.app.saveCroppedReference(photo);
    assert.equal(context.app.selectedPerson().id,42);
    assert.equal(context.location.hash,'#/persons/42/edit');
    assert.equal(context.app.cropFiles().length,1);
    assert.equal(context.app.busy(),false);
    assert.match(context.app.error(),/인물 정보는 저장되었습니다/);
    context.allowFace(); await context.app.saveCroppedReference(photo);
    assert.deepEqual(context.calls.map(call => call.path),['/api/persons','/api/persons/42/faces','/api/persons/42/faces']);
    assert.equal(context.calls[2].body,photo);
    assert.equal(context.app.cropFiles().length,0);
    assert.equal(context.app.selectedPerson().faces.length,1);
  } finally {context.close();}
});

test('missing name or missing edited person cannot create or upload a face', async () => {
  const context = setup();
  try {
    context.app.personForm.name=''; await context.app.saveCroppedReference(new Blob(['photo']));
    assert.equal(context.calls.length,0);
    context.app.personForm.name='crop-test'; context.app.personEditingId=42;
    await context.app.saveCroppedReference(new Blob(['photo']));
    assert.equal(context.calls.length,0);
    assert.match(context.app.error(),/다시 불러온/);
  } finally {context.close();}
});
