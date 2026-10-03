import '@angular/compiler';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/app/event-panel.component.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,
  module:ts.ModuleKind.ES2022, experimentalDecorators:true}}).outputText
  .replace(/from ['"]([^'"]+)['"]/g, (_, name) => `from '${import.meta.resolve(name)}'`);
const {EventFeed} = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const item = (event_id, change_id = event_id, status = 'candidate') => ({type:'person_match', event_id, change_id, status});
const turn = () => new Promise(resolve => setImmediate(resolve));

function setup(read) {
  let rows = [], expired = 0;
  const sockets = [];
  const create = () => {
    const socket = {readyState:1, close(code = 1000) {this.readyState=3; this.onclose?.({code});},
      emit(value) {this.onmessage?.({data:JSON.stringify(value)});}};
    sockets.push(socket); return socket;
  };
  const feed = new EventFeed(read, value => {rows=value;}, () => {}, () => {expired++;}, create);
  feed.start();
  return {feed, sockets, rows:() => rows, expired:() => expired};
}

test('event IDs coalesce duplicates and older updates cannot undo review', async () => {
  const c = setup(async path => path.includes('latest')
    ? {items:[item(1)],change_cursor:1} : {items:[],next_cursor:1,has_more:false});
  try {
    await c.feed.sync(true);
    c.sockets[0].emit(item(1,3,'confirmed')); c.sockets[0].emit(item(1,2)); c.sockets[0].emit(item(1,3,'confirmed'));
    assert.equal(c.rows().length,1); assert.equal(c.rows()[0].status,'confirmed');
    assert.equal(c.rows()[0].change_id,3);
  } finally {c.feed.stop();}
});

test('HTTP cursor recovers missed changes across pages while WS messages are buffered', async () => {
  const paths = [];
  let resolveFirst;
  const c = setup(path => {
    paths.push(path);
    if (path.includes('latest')) return Promise.resolve({items:[item(1)],change_cursor:5});
    if (path.includes('after_change_id=5')) return new Promise(resolve => {resolveFirst=resolve;});
    return Promise.resolve({items:[item(2,7,'rejected')],next_cursor:7,has_more:false});
  });
  try {
    const sync = c.feed.sync(true); await turn();
    c.sockets[0].emit(item(3,20));
    resolveFirst({items:[item(2,6)],next_cursor:6,has_more:true}); await sync;
    assert.deepEqual(c.rows().map(row => row.event_id),[3,2,1]);
    assert.equal(c.rows()[1].status,'rejected');
    assert.ok(paths.some(path => path.includes('after_change_id=6')));
    assert.ok(!paths.some(path => path.includes('after_change_id=20')));
  } finally {c.feed.stop();}
});

test('ready on reconnection resumes the HTTP cursor and recovers a missed review', async () => {
  let phase = 0;
  const paths = [];
  const c = setup(async path => {
    paths.push(path);
    if (path.includes('latest')) return {items:[item(1)],change_cursor:1};
    return phase ? {items:[item(1,2,'confirmed')],next_cursor:2,has_more:false}
      : {items:[],next_cursor:1,has_more:false};
  });
  try {
    await c.feed.sync(true); phase=1;
    c.sockets[0].close();
    await new Promise(resolve => setTimeout(resolve,1100));
    c.sockets[1].emit({type:'ready'}); await turn();
    assert.equal(c.rows()[0].status,'confirmed');
    assert.equal(paths.filter(path => path.includes('latest')).length,1);
    assert.ok(paths.at(-1).includes('after_change_id=1'));
  } finally {c.feed.stop();}
});

test('logout fences pending responses and authentication closure clears history', async () => {
  let resolve;
  const c = setup(() => new Promise(done => {resolve=done;}));
  const sync = c.feed.sync(true); c.feed.stop();
  resolve({items:[item(1)],change_cursor:1}); await sync;
  assert.deepEqual(c.rows(),[]);
  c.feed.start(); c.sockets.at(-1).emit(item(1)); c.sockets.at(-1).close(1008);
  assert.equal(c.expired(),1); assert.deepEqual(c.rows(),[]);
  c.feed.stop();
});

test('permission resync discards an in-flight stale snapshot', async () => {
  let resolve;
  let snapshots = 0;
  const c = setup(path => {
    if (path.includes('latest') && !snapshots++) return new Promise(done => {resolve=done;});
    return Promise.resolve(path.includes('latest')
      ? {items:[],change_cursor:2} : {items:[],next_cursor:2,has_more:false});
  });
  try {
    const pending = c.feed.sync(true);
    c.sockets[0].emit({type:'resync'});
    resolve({items:[item(1)],change_cursor:1}); await pending; await turn();
    assert.deepEqual(c.rows(),[]); assert.equal(snapshots,2);
  } finally {c.feed.stop();}
});

test('displayed history stays bounded at the most recent 100 event IDs', () => {
  const c = setup(async () => ({items:[],change_cursor:0,next_cursor:0,has_more:false}));
  try {
    for (let id=1; id<=150; id++) c.sockets[0].emit(item(id));
    assert.equal(c.rows().length,100);
    assert.equal(c.rows()[0].event_id,150); assert.equal(c.rows().at(-1).event_id,51);
  } finally {c.feed.stop();}
});

test('a browser-hidden authentication rejection is detected through HTTP', async () => {
  const c = setup(async () => {throw {status:401};});
  c.sockets[0].close(1006); await turn();
  assert.equal(c.expired(),1); assert.deepEqual(c.rows(),[]);
  c.feed.stop();
});
