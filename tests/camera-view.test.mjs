import assert from 'node:assert/strict';
import test from 'node:test';
import { DEFAULT_CAMERA_STORAGE_KEY, decodeCameraView, readCameraView, writeCameraView } from '../src/viewport/cameraView.ts';

const view = { version: 1, position: [1200, -800, 600], target: [140, -80, 100], zoom: 1.25 };

function useStorage(t, storage) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous);
    else delete globalThis.localStorage;
  });
}

test('相机视角按默认或指定键保存并完整还原', t => {
  const entries = new Map();
  useStorage(t, { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value) });
  assert.equal(readCameraView(), null);
  writeCameraView(view);
  assert.deepEqual(JSON.parse(entries.get(DEFAULT_CAMERA_STORAGE_KEY)), view);
  assert.deepEqual(readCameraView(), view);
  const other = { ...view, position: [-20, 50, 90], zoom: 2 };
  writeCameraView(other, 'isolated-test-camera');
  assert.deepEqual(readCameraView('isolated-test-camera'), other);
  assert.deepEqual(readCameraView(), view);
});

test('相机视角拒绝损坏 JSON、版本和向量结构', () => {
  for (const raw of [null, '', '{', 'null', '[]', '3', '"camera"']) assert.equal(decodeCameraView(raw), null);
  for (const invalid of [
    {}, { ...view, version: 0 }, { ...view, version: 2 }, { ...view, version: '1' },
    { ...view, position: [1, 2] }, { ...view, position: [1, 2, 3, 4] },
    { ...view, position: { 0: 1, 1: 2, 2: 3 } }, { ...view, target: null },
    { ...view, target: [1, '2', 3] }, { ...view, zoom: '1' },
  ]) assert.equal(decodeCameraView(JSON.stringify(invalid)), null);
});

test('相机视角拒绝非有限值、重合点和不安全投影数值', () => {
  for (const invalid of [
    { ...view, position: [NaN, 2, 3] }, { ...view, target: [1, Infinity, 3] },
    { ...view, position: [...view.target] },
    { ...view, position: [1e13, 0, 0] },
    { ...view, position: [1e-10, 0, 0], target: [0, 0, 0] },
    ...[0, -1, NaN, Infinity, 1e-9, 1e9].map(zoom => ({ ...view, zoom })),
  ]) assert.equal(decodeCameraView(JSON.stringify(invalid)), null);
  // Overflow is valid JSON syntax, but it must not reach the renderer.
  assert.equal(decodeCameraView('{"version":1,"position":[1e999,0,0],"target":[0,0,0],"zoom":1}'), null);
});

test('相机视角保留大场景、负坐标和精细变换精度', () => {
  const precise = { version: 1, position: [-1e9, 123.456789123, 1e-5], target: [-1e9 + 100, 0, -200], zoom: 0.125 };
  assert.deepEqual(decodeCameraView(JSON.stringify(precise)), precise);
});

test('无效相机状态不会覆盖上一次有效视角', t => {
  const entries = new Map([[DEFAULT_CAMERA_STORAGE_KEY, JSON.stringify(view)]]);
  useStorage(t, { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value) });
  writeCameraView({ ...view, target: [...view.position] });
  writeCameraView({ ...view, zoom: NaN });
  assert.deepEqual(readCameraView(), view);
});

test('损坏或拒绝访问的存储不会中断视图', t => {
  useStorage(t, { getItem: () => '{broken', setItem: () => { throw new Error('QuotaExceededError'); } });
  assert.equal(readCameraView(), null);
  assert.doesNotThrow(() => writeCameraView(view));
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('SecurityError'); } });
  assert.equal(readCameraView(), null);
  assert.doesNotThrow(() => writeCameraView(view));
});

test('未提供浏览器存储时允许初始化和保存', t => {
  useStorage(t, undefined);
  assert.equal(readCameraView(), null);
  assert.doesNotThrow(() => writeCameraView(view));
});
