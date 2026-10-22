import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import test from 'node:test';
import { fetchAvailableTemplates, fetchPublishedTemplates, parsePublishedTemplates } from '../src/library/publishedLibrary.ts';

const publication = {
  publicationId: 'version-1', templateId: 'draft-1', version: 1,
  name: '横梁', code: 'BEAM-001', createdAt: '2026-10-05T00:00:00Z',
  sha256: 'a'.repeat(64), size: 1024,
  downloadUrl: '/api/public/v1/templates/version-1/download',
};

test('保留不同发布版本并按发布时间排序', () => {
  const items = parsePublishedTemplates({ items: [publication, {
    ...publication, publicationId: 'version-2', version: 2, createdAt: '2026-10-06T00:00:00Z',
  }], count: 2 });
  assert.deepEqual(items.map(item => [item.publicationId, item.version]), [['version-2', 2], ['version-1', 1]]);
  assert.deepEqual(parsePublishedTemplates({ items: [], count: 0 }), []);
});

test('拒绝缺失、重复或无效的发布元数据', () => {
  for (const response of [
    {}, { items: [null], count: 1 }, { items: [publication], count: 0 },
    { items: [publication, publication], count: 2 },
    ...[{ publicationId: '' }, { templateId: null }, { name: 42 }, { version: 0 },
      { createdAt: 'invalid' }, { size: -1 }, { sha256: 'invalid' }].map(override => ({
        items: [{ ...publication, ...override }], count: 1,
      })),
  ]) assert.throws(() => parsePublishedTemplates(response), /发布库返回的数据格式不正确/);
});

async function serve(t, handler) {
  const server = createServer(handler).listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  return `http://127.0.0.1:${server.address().port}`;
}

test('客户端读取真实响应，拒绝异常数据并显示明确的服务错误', async t => {
  let payload = { items: [publication], count: 1 };
  let status = 200;
  const endpoint = await serve(t, (_req, res) => {
    res.statusCode = status;
    res.end(JSON.stringify(payload));
  });
  const nativeFetch = globalThis.fetch;
  t.mock.method(globalThis, 'fetch', (url, options) => nativeFetch(new URL(url, endpoint), options));
  const signal = new AbortController().signal;
  assert.equal((await fetchPublishedTemplates(signal))[0].publicationId, 'version-1');
  payload = { items: [], count: 0 };
  assert.deepEqual(await fetchPublishedTemplates(signal), []);
  payload = { items: [{ ...publication, sha256: 'bad' }], count: 1 };
  await assert.rejects(fetchPublishedTemplates(signal), /数据格式不正确/);
  status = 401;
  payload = { error: { code: 'PUBLIC_API_KEY_INVALID', detail: 'secret-debug-details' } };
  await assert.rejects(fetchPublishedTemplates(signal), /密钥不正确/);
  status = 503;
  payload = { error: { code: 'REMOTE_STORAGE_NOT_CONFIGURED' } };
  await assert.rejects(fetchPublishedTemplates(signal), /尚未配置共享发布库/);
  await assert.rejects(fetchPublishedTemplates(AbortSignal.abort()), { name: 'AbortError' });
});

test('列表只逐步展示重建成功的模板，检查并发受限，刷新移除旧项', async t => {
  const items = Array.from({length:5}, (_,i)=>({...publication,publicationId:`version-${i}`,sha256:String(i).repeat(64)}));
  const updates=[];
  let running=0, peak=0;
  t.mock.method(globalThis,'fetch',async url=>{
    if(String(url)==='/api/published-templates') return Response.json({items,count:items.length});
    running++; peak=Math.max(peak,running);
    await new Promise(resolve=>setTimeout(resolve,5));
    running--;
    const id=String(url).match(/version-(\d+)/)[1];
    assert.ok(String(url).endsWith(`?sha256=${id.repeat(64)}`));
    return Response.json({available:Number(id)%2===0});
  });
  const result=await fetchAvailableTemplates(new AbortController().signal, items=>updates.push([...items]));
  assert.deepEqual(updates[0],[]);
  assert.deepEqual(result.map(item=>item.publicationId).sort(),['version-0','version-2','version-4']);
  assert.ok(updates.every(items=>items.every(item=>['version-0','version-2','version-4'].includes(item.publicationId))));
  assert.ok(peak<=2);
});

test('检查返回格式错误或连接失败时不能将模板放入列表，取消不发布迟到结果',async t=>{
  let reply=()=>Response.json({available:'yes'});
  t.mock.method(globalThis,'fetch',async url=>String(url)==='/api/published-templates'
    ? Response.json({items:[publication],count:1}) : reply());
  const updates=[];
  await assert.rejects(fetchAvailableTemplates(new AbortController().signal,items=>updates.push(items)),/数据格式不正确/);
  assert.ok(updates.every(items=>items.length===0));
  reply=()=>Response.json({error:{code:'LIBRARY_UNREACHABLE'}},{status:502});
  await assert.rejects(fetchAvailableTemplates(new AbortController().signal),/无法连接/);
  const controller=new AbortController();
  reply=()=>{controller.abort();return Response.json({available:true});};
  await assert.rejects(fetchAvailableTemplates(controller.signal,items=>updates.push(items)),{name:'AbortError'});
  assert.ok(updates.every(items=>items.length===0));
});

test('前两个模板读取失败仍继续检查后续模板，成功项保留且失败可重试',async t=>{
  const items=Array.from({length:4},(_,i)=>({...publication,publicationId:`v${i}`}));
  const checked=[]; const updates=[];
  t.mock.method(globalThis,'fetch',async url=>{
    if(String(url)==='/api/published-templates')return Response.json({items,count:items.length});
    const id=String(url).match(/\/v(\d)\//)[1]; checked.push(Number(id));
    return Number(id)<2 ? Response.json({error:{code:'LIBRARY_TIMEOUT'}},{status:504}) : Response.json({available:true});
  });
  await assert.rejects(fetchAvailableTemplates(new AbortController().signal,items=>updates.push(items)),/超时/);
  assert.deepEqual(checked.sort(),[0,1,2,3]);
  assert.deepEqual(updates.at(-1).map(item=>item.publicationId),['v2','v3']);
});
