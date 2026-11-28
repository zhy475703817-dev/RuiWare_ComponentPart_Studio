import assert from 'node:assert/strict';
import test from 'node:test';
import { downloadComponentExport, downloadDraftBackup } from '../src/app/documentExport.ts';
import { createComponentDocument } from '../src/domain/component.ts';
import { deleteDraftEvidence } from '../src/library/draftEvidence.ts';

const reference = {
  id: 'evidence-1', fileName: '说明.txt', mediaType: 'text/plain', size: 3,
  sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad', dataUrl: null,
};
const embedded = { ...reference, dataUrl: 'data:text/plain;base64,YWJj' };

test('清理未登记的上传时使用来源草稿和条件删除', async t => {
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push([url, options.method]);
    return new Response(null, { status: 204 });
  });
  await deleteDraftEvidence('draft/1', 'evidence/1');
  assert.deepEqual(requests, [['/api/component-drafts/draft%2F1/evidence/evidence%2F1?unreferencedOnly=true', 'DELETE']]);
});

function captureDownloads(t) {
  const downloads = [];
  globalThis.window = { document: {
    createElement: () => ({
      click() {
        downloads.push({ filename: this.download, blob: fetch(this.href).then(response => response.json()) });
      },
    }),
  } };
  t.after(() => { delete globalThis.window; });
  return downloads;
}

test('普通导出直接保留服务端附件字节，并以响应名称命名', async t => {
  const downloads = captureDownloads(t);
  const exported = { name: '保存/组件', stages: { definition: { evidenceFiles: [embedded] } } };

  downloadComponentExport(exported);

  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].filename, '保存_组件.component.json');
  assert.deepEqual(await downloads[0].blob, exported);
});

test('恢复副本采用点击时的名称和附件清单，嵌入远端附件并保留已有字节', async t => {
  const downloads = captureDownloads(t);
  const source = createComponentDocument();
  const local = { ...embedded, id: 'local', fileName: '已有.txt' };
  source.definition.name = '未保存的名称';
  source.definition.evidenceFiles = [reference, local];
  source.definition.evidence = [{ id: 'legacy-only', name: '旧显示条目', mediaType: 'text/plain', size: 1, addedAt: '2026-11-28' }];

  const originalFetch = globalThis.fetch;
  t.mock.method(globalThis, 'fetch', async (path, ...args) => {
    if (path.startsWith('blob:')) return originalFetch(path, ...args);
    assert.equal(path, '/api/component-drafts/draft%2F1/evidence/evidence-1');
    return new Response('abc', { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
  });

  const pending = downloadDraftBackup('draft/1', source);
  source.definition.name = '点击后改名';
  source.definition.evidenceFiles = [];
  await pending;

  assert.equal(downloads.length, 1);
  const backup = await downloads[0].blob;
  assert.equal(backup.definition.name, '未保存的名称');
  assert.deepEqual(backup.definition.evidenceFiles, [embedded, local]);
  assert.deepEqual(backup.definition.evidence, source.definition.evidence);
  assert.equal(reference.dataUrl, null);
});

test('恢复副本遇到已删除或已替换的附件时报告文件名并停止下载', async t => {
  const downloads = captureDownloads(t);
  const source = createComponentDocument();
  source.definition.evidenceFiles = [reference];
  const request = t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 404 }));

  await assert.rejects(downloadDraftBackup('draft-1', source), /说明\.txt/);
  request.mock.mockImplementation(async () => new Response('xyz'));
  await assert.rejects(downloadDraftBackup('draft-1', source), /说明\.txt/);
  assert.equal(downloads.length, 0);
});
