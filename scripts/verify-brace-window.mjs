// Acceptance test against a dedicated fixture server and headless CDP browser.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve(process.argv[2]);
const debuggerBase = process.argv[3] || 'http://127.0.0.1:9324';
const fixture = JSON.parse(await fs.readFile(path.join(root, 'fixture-input.json'), 'utf8'));
const {base} = fixture;
async function api(route, body) {
  const response = await fetch(base + route, {method: body ? 'POST' : 'GET', headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  assert.ok(response.ok, `${route}: ${response.status}`);
  return response.json();
}
const pages = await (await fetch(debuggerBase + '/json/list')).json();
const page = pages.find(item => item.type === 'page' && (item.url === 'about:blank' || item.url.startsWith(base + '/')));
assert.ok(page, 'A dedicated blank headless page is required');
const socket = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map(), errors = [], checkpoints = [];
let sequence = 0;
socket.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const entry = pending.get(message.id); pending.delete(message.id);
    message.error ? entry.reject(Error(JSON.stringify(message.error))) : entry.resolve(message.result);
  } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params);
};
await new Promise((resolve, reject) => {socket.onopen = resolve; socket.onerror = reject;});
const send = (method, params = {}) => new Promise((resolve, reject) => {const id = ++sequence; pending.set(id, {resolve, reject}); socket.send(JSON.stringify({id, method, params}));});
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  assert.ok(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function waitFor(expression, seconds = 180) {
  const end = Date.now() + seconds * 1000;
  while (Date.now() < end) {
    const result = await evaluate(expression);
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, 750));
  }
  throw Error(`Timed out: ${expression}\n${await evaluate('document.body.innerText')}`);
}
const button = (text, scope = 'document') => `[...${scope}.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)})`;
async function click(text, inside = true) {
  const selector = button(text, inside ? 'document.querySelector("dialog[open]")' : 'document');
  await waitFor(`(()=>{const b=${selector};return b&&!b.disabled})()`);
  await evaluate(`${selector}.click()`);
}
async function screenshot(name) {
  const result = await send('Page.captureScreenshot', {format: 'png'});
  await fs.writeFile(path.join(root, name), Buffer.from(result.data, 'base64'));
}
async function record(stage, detail = {}) {
  checkpoints.push({stage, ...detail});
  console.log(JSON.stringify(checkpoints.at(-1)));
  await fs.writeFile(path.join(root, 'window-progress.json'), JSON.stringify({checkpoints, errors}, null, 2));
}
let draftId;
async function adjust(height, count, enabled, label) {
  await evaluate(`(()=>{const input=document.querySelector('.brace-height-label input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(String(height))});input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await click('预览高度调整');
  await waitFor(`(()=>{const b=${button('应用', 'document.querySelector("dialog[open]")')};return b&&!b.disabled})()`);
  await screenshot(`${label}-preview.png`);
  await click('应用');
  await waitFor(`document.querySelector('dialog[open]')?.innerText.includes('几何联动已整体保存')`);
  const saved = await api('/api/component-drafts/' + draftId);
  assert.equal(saved.document.assembly.members.length, count);
  assert.equal(saved.document.braceGenerationRules[0].enabled, enabled);
  const columns = saved.document.assembly.members.filter(member => member.id.startsWith('upright-'));
  assert.ok(columns.every(column => Math.abs(column.parameterValues.length - height) < .1));
  for (const member of saved.document.assembly.members) {
    if (member.id.includes('cross-1')) assert.equal(member.parameterValues.length, 1200);
    if (member.id.includes('diagonal-1')) assert.equal(member.parameterValues.length, 1500);
  }
  await record(label, {height, count, enabled});
}
try {
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1050, deviceScaleFactor: 1, mobile: false});
  const source = structuredClone(fixture.document);
  source.definition.code = 'BRACEWINDOWQA-' + crypto.randomUUID().slice(0, 8).toUpperCase();
  source.braceGenerationRules = [fixture.rule];
  const created = await api('/api/component-drafts', {document: source}); draftId = created.draftId;
  await send('Page.navigate', {url: `${base}/?draft=${draftId}&stage=parameters`});
  await click('打开柱片调整窗口', false);
  await waitFor('Boolean(document.querySelector("dialog[open]"))');
  assert.equal(await evaluate('document.querySelector(".brace-mode input").checked'), true);
  await click('读取当前实际尺寸');
  await waitFor('!document.querySelector(".brace-height-label input").disabled');
  await screenshot('window-open.png');
  await adjust(1200, 10, true, 'shortened');
  await waitFor('!document.querySelector(".brace-mode input").disabled');
  await evaluate('document.querySelector(".brace-mode input").click()');
  await adjust(1500, 10, false, 'ordinary-height');
  await waitFor('!document.querySelector(".brace-mode input").disabled');
  await evaluate('document.querySelector(".brace-mode input").click()');
  await adjust(1500, 13, true, 'reenabled');
  await evaluate('document.querySelector("[aria-label=关闭柱片调整窗口]").click()');
} catch (error) {
  await screenshot('failure.png').catch(() => {});
  await fs.writeFile(path.join(root, 'window-result.json'), JSON.stringify({passed: false, draftId, checkpoints, errors, error: String(error)}, null, 2));
  socket.close(); throw error;
}
try {
  await click('打开柱片调整窗口', false);
  await waitFor('Boolean(document.querySelector("dialog[open]"))');
  assert.equal(await evaluate('document.querySelector(".brace-mode input").checked'), true);
  await send('Emulation.setDeviceMetricsOverride', {width: 420, height: 860, deviceScaleFactor: 1, mobile: false});
  await screenshot('window-narrow.png');
  assert.ok(await evaluate('document.querySelector("dialog").getBoundingClientRect().width <= innerWidth'));
  await send('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27});
  await send('Input.dispatchKeyEvent', {type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27});
  await waitFor('!document.querySelector("dialog[open]")');
  assert.equal(errors.length, 0, JSON.stringify(errors));
  await fs.writeFile(path.join(root, 'window-result.json'), JSON.stringify({passed: true, draftId, checkpoints, errors}, null, 2));
  console.log('PASS');
} finally {socket.close();}
