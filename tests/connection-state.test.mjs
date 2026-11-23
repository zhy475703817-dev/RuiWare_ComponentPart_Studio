import test from 'node:test';
import assert from 'node:assert/strict';
import { createComponentDocument } from '../src/domain/component.ts';
import { connectionStateKey } from '../src/domain/connectionState.ts';

test('无内容变化的轮询和修订更新不会取消连接，实际输入变化才失效', () => {
  const original = createComponentDocument();
  const key = connectionStateKey('draft', original);
  const polled = structuredClone(original);
  polled.revision++;
  assert.equal(connectionStateKey('draft', polled), key);
  assert.notEqual(connectionStateKey('other', polled), key);
  polled.parameters[0].value = 3456;
  assert.notEqual(connectionStateKey('draft', polled), key);
  const moved = structuredClone(original);
  moved.assembly.members.push({ id: 'a', position: [1, 2, 3] });
  assert.notEqual(connectionStateKey('draft', moved), key);
});
