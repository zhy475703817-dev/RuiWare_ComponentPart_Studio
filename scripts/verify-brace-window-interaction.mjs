// Browser regression for slow previews. All resize writes are intercepted;
// the selected draft supplies only the saved template and its built-in rules.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';

const base = process.argv[2] || 'http://127.0.0.1:5180';
const draftId = process.argv[3] || 'bcb64711-0589-4057-aec1-8e90c4c23cda';
const debuggerBase = process.argv[4] || 'http://127.0.0.1:9335';
const output = process.argv[5] || '.runtime/verification/brace-window-unlock';
await fs.mkdir(output, {recursive: true});
const before = await (await fetch(`${base}/api/component-drafts/${draftId}`)).json();
const page = (await (await fetch(debuggerBase + '/json/list')).json()).find(p => p.type === 'page');
const socket = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map(), errors = [], checks = [];
let seq = 0;
socket.onmessage = e => {
  const x = JSON.parse(e.data);
  if (x.id) { const p = pending.get(x.id); pending.delete(x.id); x.error ? p.reject(Error(JSON.stringify(x.error))) : p.resolve(x.result); }
  else if (x.method === 'Runtime.exceptionThrown') errors.push(x.params);
};
await new Promise(r => socket.onopen = r);
const call = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq; pending.set(id, {resolve, reject}); socket.send(JSON.stringify({id, method, params}));
});
async function evaluate(expression) {
  const r = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  assert.ok(!r.exceptionDetails, JSON.stringify(r.exceptionDetails)); return r.result.value;
}
async function until(expression, seconds = 15) {
  const start = Date.now();
  while (Date.now() - start < seconds * 1000) { if (await evaluate(expression)) return; await new Promise(r => setTimeout(r, 100)); }
  throw Error('Timed out: ' + expression + '\n' + await evaluate('document.body.innerText'));
}
const button = name => `[...document.querySelector('dialog[open]').querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(name)})`;
async function click(name) { await until(`Boolean(${button(name)}&&!${button(name)}.disabled)`); await evaluate(`${button(name)}.click()`); }
async function setHeight(value) {
  await evaluate(`(()=>{const i=document.querySelector('.brace-height-label input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,${JSON.stringify(String(value))});i.dispatchEvent(new Event('input',{bubbles:true}));})()`);
}
async function start() { await click('预览高度调整'); await until(`document.querySelector('.brace-preview-status')?.innerText.includes('反算零件尺寸')`); }
try {
  await call('Page.enable'); await call('Runtime.enable');
  await call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1050, deviceScaleFactor: 1, mobile: false});
  await call('Page.addScriptToEvaluateOnNewDocument', {source: `
    const originalFetch=window.fetch.bind(window);
    window.previewRequests=[];window.previewAborts=0;window.previewMode='slow';
    window.fetch=async (url,options={})=>{
      if(!String(url).includes('/resize-jobs'))return originalFetch(url,options);
      window.previewRequests.push({url:String(url),method:options.method,body:options.body});
      const body=options.body?JSON.parse(options.body):{};
      if(options.method==='DELETE')return Response.json({status:'cancelled'});
      if(options.method==='POST')return Response.json(body.inspectOnly?
        {jobId:'inspection',expectedRevision:${before.document.revision},status:'ready',message:'读取完成',result:{dimensions:[1216,101,2400],members:[],interfaces:[]}}:
        {jobId:'preview',expectedRevision:${before.document.revision},status:'running',message:'反算零件尺寸、位置和角度'});
      return new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>resolve(Response.json({jobId:'preview',expectedRevision:${before.document.revision},status:'running',message:'仍在计算'})),60000);
        const cancel=()=>{clearTimeout(timer);window.previewAborts++;reject(options.signal.reason);};
        if(options.signal?.aborted)cancel();else options.signal?.addEventListener('abort',cancel,{once:true});
      });
    };`});
  await call('Page.navigate', {url: `${base}/?draft=${draftId}&stage=parameters`});
  await until(`document.querySelector('.brace-entry small')?.innerText.includes('模板自带')`);
  await evaluate(`document.querySelector('.brace-entry button').click()`);
  await until(`Boolean(document.querySelector('dialog[open]'))`);
  await click('读取当前实际尺寸');
  await until(`!document.querySelector('.brace-height-label input').disabled`);
  await setHeight(3000); await start();
  assert.equal(await evaluate(`document.querySelector('.brace-height-label input').disabled`), false);
  assert.equal(await evaluate(`document.querySelector('.brace-mode input').disabled`), false);
  assert.equal(await evaluate(`${button('应用')}.disabled`), true);
  await new Promise(r => setTimeout(r, 1000));
  await setHeight(2100);
  await until(`!document.querySelector('.brace-preview-status')`);
  await until(`window.previewAborts>0`);
  checks.push('计算中修改高度可停止旧任务，并中断挂起的轮询');
  await start(); await evaluate(`document.querySelector('.brace-mode input').click()`);
  await until(`!document.querySelector('.brace-preview-status')`);
  checks.push('计算中切换自动补齐可停止旧任务');
  await start(); await click('停止计算');
  await until(`!document.querySelector('.brace-preview-status')`);
  assert.equal(await evaluate(`document.querySelector('.brace-height-label input').value`), '2100');
  checks.push('停止计算保留窗口和目标高度');
  await start(); await evaluate(`document.querySelector('[aria-label="关闭柱片调整窗口"]').click()`);
  await until(`!document.querySelector('dialog[open]')`);
  await evaluate(`document.querySelector('.brace-entry button').click()`);
  await until(`Boolean(document.querySelector('dialog[open]'))`);
  assert.equal(await evaluate(`document.querySelector('.brace-height-label input').disabled`), false);
  await start();
  await call('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27});
  await call('Input.dispatchKeyEvent', {type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27});
  await until(`!document.querySelector('dialog[open]')`);
  checks.push('关闭按钮和 Esc 均取消计算，重新打开即可编辑');
  await evaluate(`document.querySelector('.brace-entry button').click()`);
  await until(`Boolean(document.querySelector('dialog[open]'))`);
  await start();
  await until(`document.querySelector('.brace-preview-status')?.innerText.includes('后台响应超时')`, 40);
  assert.equal(await evaluate(`document.querySelector('.brace-height-label input').disabled`), false);
  checks.push('请求超时恢复操作并提供中文重试提示');
  const png = await call('Page.captureScreenshot', {format: 'png'});
  await fs.writeFile(output + '/editable-after-timeout.png', Buffer.from(png.data, 'base64'));
  const after = await (await fetch(`${base}/api/component-drafts/${draftId}`)).json();
  assert.deepEqual(after.document, before.document);
  assert.equal(errors.length, 0, JSON.stringify(errors));
  await fs.writeFile(output + '/result.json', JSON.stringify({passed: true, checks, unchangedRevision: after.document.revision, errors}, null, 2));
  console.log(JSON.stringify({passed: true, checks}));
} finally { socket.close(); }
