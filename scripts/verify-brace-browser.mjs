// Run against verify-brace-browser-server.py and a dedicated headless browser.
// Uses the browser's CDP endpoint and Node built-ins; no browser test dependencies.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {summarizeHardwareUsage} from '../src/domain/hardware.ts';
import {Euler, Vector3} from 'three';

const root = path.resolve(process.argv[2]);
const debuggerBase = process.argv[3] || 'http://127.0.0.1:9319';
const fixture = JSON.parse(await fs.readFile(path.join(root, 'fixture-input.json'), 'utf8'));
const {base} = fixture;
const checkpoints = [], errors = [];
async function record(stage, detail) {
  checkpoints.push({stage, time: new Date().toISOString(), ...detail});
  console.log(JSON.stringify(checkpoints.at(-1)));
  await fs.writeFile(path.join(root, 'browser-progress.json'), JSON.stringify({checkpoints, errors}, null, 2));
}
async function api(route, method = 'GET', body) {
  const response = await fetch(base+route, {method, headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  assert.ok(response.ok, `${route}: ${response.status} ${JSON.stringify(data)}`);
  return data;
}
async function mcp(name, args) {
  const response = await fetch(base+'/mcp', {method: 'POST', headers: {
    'Content-Type': 'application/json', Accept: 'application/json, text/event-stream'},
    body: JSON.stringify({jsonrpc: '2.0', id: 1, method: 'tools/call', params: {name, arguments: args}})});
  assert.ok(response.ok, `MCP HTTP ${response.status}`);
  const result = (await response.json()).result;
  assert.ok(!result.isError, JSON.stringify(result));
  return result.structuredContent ?? JSON.parse(result.content.find(c => c.type === 'text').text);
}

const targets = await (await fetch(debuggerBase+'/json/list')).json();
const candidates = targets.filter(t => t.type === 'page' && (t.url === 'about:blank' || t.url.startsWith(base+'/')));
assert.equal(candidates.length, 1, 'Use a dedicated headless browser profile with one test page');
const ws = new WebSocket(candidates[0].webSocketDebuggerUrl);
const pending = new Map();
let sequence = 0;
ws.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const request = pending.get(message.id); pending.delete(message.id);
    message.error ? request?.reject(Error(JSON.stringify(message.error))) : request?.resolve(message.result);
  } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params);
};
await new Promise((resolve, reject) => {ws.onopen = resolve; ws.onerror = reject;});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence; pending.set(id, {resolve, reject}); ws.send(JSON.stringify({id, method, params}));
});
async function evaluate(expression) {
  const value = await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
  assert.ok(!value.exceptionDetails, JSON.stringify(value.exceptionDetails));
  return value.result.value;
}
async function waitFor(expression, seconds = 180) {
  const end = Date.now()+seconds*1000;
  while (Date.now() < end) {
    const value = await evaluate(expression);
    if (value) return value;
    await new Promise(r => setTimeout(r, 1000));
  }
  throw Error(`Timed out: ${expression}\n${await evaluate('document.body?.innerText')}`);
}
const button = text => `[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)})`;
async function click(text) {
  await waitFor(`(()=>{const b=${button(text)};return b&&!b.disabled})()`);
  await evaluate(`(()=>{const b=${button(text)};b.click();return true})()`);
}
async function screenshot(name) {
  const value = await send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
  await fs.writeFile(path.join(root, name+'.png'), Buffer.from(value.data, 'base64'));
}
async function settleModels() {
  await waitFor(`!['正在更新模型','正在加载模型'].some(s=>document.body.innerText.includes(s))`, 180);
  assert.equal(await evaluate(`document.body.innerText.includes('模型重建失败')`), false);
}
function assertMembers(document, height, horizontals, diagonals) {
  const members = document.assembly.members;
  const cross = members.filter(m => m.id === 'cross-1' || m.id.startsWith('generated-brace-cad-upright-braces-cross-1-'));
  const diagonal = members.filter(m => m.id === 'diagonal-1' || m.id.startsWith('generated-brace-cad-upright-braces-diagonal-1-'));
  assert.equal(cross.length, horizontals); assert.equal(diagonal.length, diagonals);
  assert.deepEqual([...new Set(cross.map(m=>m.parameterValues.length))], [1200]);
  assert.deepEqual([...new Set(diagonal.map(m=>m.parameterValues.length))], [1500]);
  assert.equal(new Set(members.map(m=>m.id)).size, members.length);
  for (const m of members.filter(m=>m.id.startsWith('upright-'))) assert.ok(Math.abs(m.parameterValues.length-height)<.1);
  for (const mate of document.assembly.mates) for (const side of ['from','to']) {
    const end = mate[side], member = members.find(m=>m.id===end.memberId);
    assert.ok(member?.interfaces.some(i=>i.id===end.interfaceId), `Missing real interface: ${JSON.stringify(end)}`);
  }
  const generatedRelations = document.geometryDesign.relations.filter(r=>r.id.startsWith('__brace__cad-upright-braces__'));
  assert.equal(generatedRelations.length, (horizontals+diagonals)*2);
  const snapshots = new Map(document.geometryDesign.interfaceSnapshots.map(s=>[JSON.stringify([s.path,s.interfaceId]),s]));
  const worldPoint = end => {
    const member = members.find(m=>m.id===end.path[0]), snapshot = snapshots.get(JSON.stringify([end.path,end.interfaceId]));
    assert.ok(snapshot, 'Real CAD interface snapshot required');
    return new Vector3(...snapshot.localCenter).applyEuler(new Euler(...member.rotation,'XYZ')).add(new Vector3(...member.position));
  };
  for (const relation of generatedRelations) for (const side of ['a', 'b']) {
    const end = relation[side], member = members.find(m=>m.id===end.path[0]);
    assert.ok(member?.interfaces.some(i=>i.id===end.interfaceId), `Missing geometric interface: ${JSON.stringify(end)}`);
  }
  for (const relation of generatedRelations) {
    assert.ok(worldPoint(relation.a).distanceTo(worldPoint(relation.b)) <= .05, `Brace misses its hole: ${relation.id}`);
  }
  assert.equal(document.assembly.mates.length, 0, 'Geometry rules must not invent assembly contracts');
}

async function assertHardware(document, expectedTotal) {
  const items = summarizeHardwareUsage(document.assembly.members, document.assembly.mates, document.geometryDesign);
  assert.equal(items.reduce((sum,item)=>sum+item.quantity,0), expectedTotal, 'Shared fastening nodes must count once');
  await waitFor(`Number(document.querySelector('[aria-label="五金用量统计"] .stats > div:last-child b')?.textContent)===${expectedTotal}`, 30);
}

let draftId;
try {
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {width: 1600, height: 1050, deviceScaleFactor: 1, mobile: false});
  const created = await mcp('component_create_draft', {document: fixture.document});
  draftId = created.draftId;
  const edited = await mcp('component_set_brace_generation_rule', {draftId, expectedRevision: created.document.revision, rule: fixture.rule});
  await mcp('component_configure_authoring', {draftId, expectedRevision: edited.document.revision, config: {axes: [2]}});
  const draftRoute = '/api/component-drafts/'+draftId;
  await record('mcp-rule-saved', {draftId, revision: edited.document.revision});
  await send('Page.navigate', {url: `${base}/?draft=${draftId}&stage=parameters`});
  await waitFor(`document.body?.innerText.includes('柱片撑杆浏览器验收')`);
  let lastMessage = '';
  const qualificationEnd = Date.now()+1800*1000;
  let qualification;
  while (Date.now() < qualificationEnd) {
    qualification = await api(draftRoute+'/qualification');
    if (qualification.message !== lastMessage) {
      await record('qualification', {status: qualification.status, message: qualification.message});
      lastMessage = qualification.message;
    }
    if (!['queued','running','stale','pending'].includes(qualification.status)) break;
    await new Promise(r=>setTimeout(r, 2000));
  }
  await fs.writeFile(path.join(root, 'qualification.json'), JSON.stringify(qualification, null, 2));
  assert.equal(qualification.deliveryReady, true, JSON.stringify(qualification));
  assert.deepEqual(qualification.cases.map(c=>Math.round(c.dimensions[2])).sort((a,b)=>a-b), [2100,2400,2700]);
  await waitFor(`document.body.innerText.includes('当前修订已通过')`, 30);
  await record('qualification-passed', {cases: qualification.cases.map(c=>c.dimensions)});

  await click('读取当前实际尺寸');
  await waitFor(`Boolean(document.querySelector('input[aria-label="目标高度 Z"]'))`);
  async function setHeight(height) {
    await evaluate(`(()=>{const i=document.querySelector('input[aria-label="目标高度 Z"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,${height});i.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
  }
  async function previewHeight(height) {
    await setHeight(height);
    await waitFor(`document.body.innerText.includes('预览完成，尚未应用')`, 900);
    await settleModels();
  }
  async function pauseAutomaticTrials() {
    // Pause newly queued qualification while checking refresh/undo; the initial
    // full qualification above has already proved automatic creation-time trials.
    await api(draftRoute+'/qualification', 'DELETE');
  }
  const nominal = await api(draftRoute);
  assertMembers(nominal.document, 2400, 15, 4);
  await assertHardware(nominal.document, 128);
  const toggleLeftLock = `(()=>{const row=[...document.querySelectorAll('fieldset[aria-label="几何联动"] details > div')].find(d=>[...d.children].find(c=>c.tagName==='SMALL')?.textContent==='upright-left');if(!row)return false;row.closest('details').open=true;row.querySelector('input[type="checkbox"]').click();return true})()`;
  assert.ok(await evaluate(toggleLeftLock));
  await setHeight(3000);
  await waitFor(`document.body.innerText.includes('已锁定')`, 90);
  const lockFailure = await api(draftRoute);
  assert.deepEqual(lockFailure.document.assembly, nominal.document.assembly);
  assert.equal(lockFailure.document.revision, nominal.document.revision);
  await record('locked-height-rejected', {unchangedRevision: lockFailure.document.revision});
  assert.ok(await evaluate(toggleLeftLock));
  await click('重新预览');
  await waitFor(`document.body.innerText.includes('预览完成，尚未应用')`, 900);
  await settleModels();
  await waitFor(`document.body.innerText.includes('新增 5 个，移除 0 个')`);
  assert.equal((await api(draftRoute)).document.revision, nominal.document.revision);
  await screenshot('grown-preview');
  await click('应用');
  await waitFor(`document.body.innerText.includes('几何联动已整体保存')`, 90);
  await pauseAutomaticTrials();
  const grown = await api(draftRoute);
  assertMembers(grown.document, 3000, 19, 5);
  await assertHardware(grown.document, 164);
  await record('grown-applied', {revision: grown.document.revision, members: grown.document.assembly.members.length});

  await send('Page.reload');
  await waitFor(`document.body.innerText.includes('恢复应用前状态')`);
  assertMembers((await api(draftRoute)).document, 3000, 19, 5);
  await click('读取当前实际尺寸');
  await waitFor(`Boolean(document.querySelector('input[aria-label="目标高度 Z"]'))`);
  await previewHeight(1950);
  await waitFor(`document.body.innerText.includes('新增 0 个，移除 9 个')`);
  await screenshot('shortened-preview');
  await click('应用');
  await waitFor(`document.body.innerText.includes('几何联动已整体保存')`, 90);
  await pauseAutomaticTrials();
  const shortened = await api(draftRoute);
  assertMembers(shortened.document, 1950, 12, 3);
  await assertHardware(shortened.document, 104);
  await record('shortened-applied', {revision: shortened.document.revision, members: shortened.document.assembly.members.length});
  await send('Page.reload');
  await waitFor(`document.body.innerText.includes('恢复应用前状态')`);
  await settleModels();
  assertMembers((await api(draftRoute)).document, 1950, 12, 3);
  await screenshot('shortened-persisted');
  await click('恢复应用前状态');
  await waitFor(`!document.body.innerText.includes('恢复应用前状态')`);
  await pauseAutomaticTrials();
  const restored = await api(draftRoute);
  assert.deepEqual(restored.document.assembly, grown.document.assembly);
  assertMembers(restored.document, 3000, 19, 5);
  await record('undo-restored', {revision: restored.document.revision, members: restored.document.assembly.members.length});
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(root, 'browser-result.json'), JSON.stringify({passed: true, draftId, checkpoints, errors}, null, 2));
} catch (error) {
  await screenshot('failure').catch(()=>{});
  const text = await evaluate('document.body?.innerText').catch(()=>null);
  await fs.writeFile(path.join(root, 'browser-result.json'), JSON.stringify({passed: false, draftId, error: error.stack, checkpoints, errors, pageText: text}, null, 2));
  throw error;
} finally { ws.close(); }
