import test from 'node:test';
import assert from 'node:assert/strict';
import { compatibleMemberIds, interfacesCompatible } from '../src/domain/interfaceCompatibility.ts';

const iface = (id, fields = {}) => ({ id, name: id, type: 'axis', tags: [], interfaceType: 'connecting', ...fields });
const member = (id, interfaces) => ({ id, interfaces });

test('male and female interfaces match when size and connection mode agree', () => {
  const result = interfacesCompatible(
    iface('pin', { connectionGender: 'male', interfaceSize: { diameter: 12 }, connectionMethod: '插接' }),
    iface('socket', { connectionGender: 'female', interfaceSize: { diameter: 12 }, connectionMethod: '插接' }),
  );
  assert.equal(result.compatible, true);
});

test('undeclared gender remains a candidate when the declared connector and size match', () => {
  const result = interfacesCompatible(
    iface('pin', { connectionGender: 'male', interfaceSize: { diameter: 12 } }),
    iface('unknown-gender', { interfaceSize: { diameter: 12 } }),
  );
  assert.equal(result.compatible, true);
  assert.match(result.reason, /公母属性未完整声明/);
});

test('compatibility rejects equal genders, mismatched sizes, and different methods', () => {
  const male = iface('pin', { connectionGender: 'male', interfaceSize: { diameter: 12 }, connectionMethod: '插接' });
  assert.equal(interfacesCompatible(male, { ...male, id: 'pin2' }).reason, '公母属性不匹配');
  assert.equal(interfacesCompatible(male, iface('socket', { connectionGender: 'female', interfaceSize: { diameter: 10 }, connectionMethod: '插接' })).reason, '接口尺寸不匹配');
  assert.equal(interfacesCompatible(male, iface('socket', { connectionGender: 'female', interfaceSize: { diameter: 12 }, connectionMethod: '螺纹' })).reason, '连接方式不匹配');
});

test('sizes normalize to millimetres and incompatible geometry or missing dimensions are not highlighted', () => {
  assert.equal(interfacesCompatible(
    iface('cm-pin', { connectionGender: 'male', interfaceSize: { diameter: 1, unit: 'cm' } }),
    iface('mm-hole', { connectionGender: 'female', interfaceSize: { diameter: 10, unit: 'mm' } }),
  ).compatible, true);
  assert.equal(interfacesCompatible(
    iface('plane', { type: 'plane', connectionGender: 'male', interfaceSize: { width: 10, height: 10 } }),
    iface('axis', { type: 'axis', connectionGender: 'female', interfaceSize: { diameter: 10 } }),
  ).compatible, false);
  assert.equal(interfacesCompatible(
    iface('unknown-size', { connectionGender: 'male' }),
    iface('unknown-size-mate', { connectionGender: 'female' }),
  ).sizeMatch, false);
});

test('rectangular regions compare independently of their local U/V orientation', () => {
  assert.equal(interfacesCompatible(
    iface('a', { connectionGender: 'male', region: { uSpan: 40, vSpan: 20 } }),
    iface('b', { connectionGender: 'female', region: { uSpan: 20, vSpan: 40 } }),
  ).compatible, true);
});

test('compatibleMemberIds reports only other members with at least one matching interface', () => {
  const members = [
    member('source', [iface('pin', { connectionGender: 'male', interfaceSize: { diameter: 8 } })]),
    member('compatible', [iface('hole', { connectionGender: 'female', interfaceSize: { diameter: 8 } })]),
    member('wrong-size', [iface('hole', { connectionGender: 'female', interfaceSize: { diameter: 10 } })]),
  ];
  assert.deepEqual([...compatibleMemberIds(members, 'source')], ['compatible']);
});
