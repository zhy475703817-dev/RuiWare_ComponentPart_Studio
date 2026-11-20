import assert from 'node:assert/strict';
import test from 'node:test';
import { withReconstructionSlot } from '../src/library/reconstructionQueue.ts';

test('模型请求最多并行两项，为五金配置保存和草稿同步保留连接，取消和失败均释放队列', async () => {
  let active = 0, maximum = 0;
  const gates = [];
  const run = () => withReconstructionSlot(new AbortController().signal, async () => {
    active++; maximum = Math.max(maximum, active);
    await new Promise(resolve => gates.push(resolve));
    active--;
  });
  const first = run(), second = run(), third = run();
  const cancelled = new AbortController();
  let cancelledStarted = false;
  const queued = withReconstructionSlot(cancelled.signal, async () => { cancelledStarted = true; });
  const rejected = assert.rejects(queued, { name: 'AbortError' });
  cancelled.abort();
  await rejected;
  await Promise.resolve();
  assert.equal(active, 2);
  assert.equal(gates.length, 2);
  gates.shift()();
  await first;
  await Promise.resolve();
  assert.equal(active, 2);
  while (gates.length) gates.shift()();
  await Promise.all([second, third]);
  assert.equal(maximum, 2);
  assert.equal(cancelledStarted, false);
  await assert.rejects(withReconstructionSlot(new AbortController().signal, async () => { throw Error('network failed'); }), /network failed/);
  await withReconstructionSlot(new AbortController().signal, async () => {});
});
