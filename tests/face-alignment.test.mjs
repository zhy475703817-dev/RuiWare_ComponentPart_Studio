import test from 'node:test';
import assert from 'node:assert/strict';
import { Euler, Quaternion, Vector3 } from 'three';
import { alignInterfaceFaces, interfaceFrame } from '../src/domain/faceAlignment.ts';

test('不同尺寸和旋转的连接面中心重合且法向相反，不带预览偏移', () => {
  const bounds = { min: [-25, -40, 0], max: [25, 40, 1000] };
  const targetBounds = { min: [-35, -15, 0], max: [35, 15, 2400] };
  const a = { geometryRefs: ['part.endFace.end'] };
  const b = { geometryRefs: ['part.endFace.start'] };
  const source = { position: [80, -3, 20], rotation: [Math.PI / 3, 0.2, 0.5] };
  const target = { position: [0, 0, 0], rotation: [0.1, 0.9, 0.4] };
  const patch = alignInterfaceFaces(source, bounds, a, target, targetBounds, b);
  const from = interfaceFrame(bounds, a), to = interfaceFrame(targetBounds, b);
  const qa = new Quaternion().setFromEuler(new Euler(...source.rotation));
  const qb = new Quaternion().setFromEuler(new Euler(...patch.rotation));
  assert.ok(from.center.applyQuaternion(qa).add(new Vector3(...source.position)).distanceTo(to.center.applyQuaternion(qb).add(new Vector3(...patch.position))) < 1e-8);
  assert.ok(from.normal.applyQuaternion(qa).dot(to.normal.applyQuaternion(qb)) < -0.99999999);
  assert.equal(interfaceFrame(bounds, a).center.z, 1000);
});
