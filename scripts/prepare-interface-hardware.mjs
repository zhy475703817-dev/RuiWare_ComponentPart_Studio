#!/usr/bin/env node
/**
 * Read the reference catalog and a saved draft; emit one native ActionRequest.
 * No network access, database writes, draft edits, or publication operations.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const defaultCatalog = new URL('../docs/interface-hardware-catalog.v1.json', import.meta.url);
const selectionRules = JSON.parse(readFileSync(new URL('../src/domain/hardwareSelectionRules.json', import.meta.url), 'utf8'));
const tokenPattern = /\{([A-Za-z][A-Za-z0-9]*)\}/g;

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
}

export function compileHardwareProfile(catalog, profileId, parameters = {}) {
  if (catalog?.format !== 'ruiware-interface-hardware-reference' || catalog.formatVersion !== 1
      || !Array.isArray(catalog.profiles)) throw new Error('不支持的五金目录格式或版本。');
  const matches = catalog.profiles.filter(profile => profile.id === profileId);
  if (matches.length !== 1) throw new Error('五金方案不存在或 ID 重复：' + profileId);
  const profile = matches[0];
  const required = profile.parameters;
  if (!Array.isArray(required) || new Set(required).size !== required.length) throw new Error('方案参数声明无效。');
  for (const key of Object.keys(parameters)) {
    if (!required.includes(key)) throw new Error('此方案不使用参数：' + key);
  }
  for (const key of required) {
    if (!Object.hasOwn(parameters, key) || typeof parameters[key] !== 'string' || !parameters[key].trim()) {
      throw new Error('缺少实际选型参数：' + key + '，使用 --set ' + key + '=实际规格 提供。');
    }
  }
  const fill = value => {
    if (typeof value !== 'string') throw new Error('五金文字字段必须为字符串。');
    const result = value.replace(tokenPattern, (_, key) => {
      if (!required.includes(key)) throw new Error('未声明的方案占位符：' + key);
      return parameters[key].trim();
    }).trim();
    if (/[{}]/.test(result)) throw new Error('规格中仍有未填写的占位符。');
    if (!result) throw new Error('五金类别、名称和规格不能为空。');
    return result;
  };
  if (!profile.hardware || !['auto', 'manual', 'none'].includes(profile.hardware.mode)
      || !Array.isArray(profile.hardware.items)) throw new Error('方案五金结构无效。');
  const hardware = {
    mode: profile.hardware.mode,
    items: profile.hardware.items.map(item => ({
      category: fill(item.category),
      name: fill(item.name),
      specification: fill(item.specification),
      quantity: item.quantity,
    })),
  };
  if (hardware.items.length > 50 || (hardware.mode === 'manual' && !hardware.items.length)
      || (hardware.mode !== 'manual' && hardware.items.length)) throw new Error('五金模式与清单数量不匹配。');
  for (const item of hardware.items) {
    if (item.category.length > 80 || item.name.length > 80 || item.specification.length > 120
        || !Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 10000) {
      throw new Error('五金字段长度或数量超出平台限制。');
    }
  }
  return hardware;
}

export function buildHardwareRequest(input, catalog, options) {
  const document = input?.document ?? input;
  if (!document || !['component-1.0', 'component-1.1'].includes(document.schemaVersion)
      || document.templateKind !== 'component') throw new Error('需要平台原生组件草稿、读取响应或组件导出 JSON。');
  if (!Number.isSafeInteger(document.revision) || document.revision < 1) throw new Error('草稿缺少有效 revision；请重新读取。');
  for (const key of ['memberId', 'interfaceId']) {
    if (typeof options[key] !== 'string' || !options[key].trim() || options[key].length > 120) {
      throw new Error('成员与接口 ID 必须为 1–120 字符的非空字符串。');
    }
  }
  const exported = !document.assembly && Boolean(document.stages?.assembly);
  const assembly = exported ? document.stages.assembly : document.assembly;
  if (!Array.isArray(assembly?.members)) throw new Error('找不到装配成员列表。');
  const members = assembly.members.filter(member => (exported ? member.memberId : member.id) === options.memberId);
  if (members.length !== 1) throw new Error('成员不存在或 ID 不唯一：' + options.memberId);
  const interfaces = members[0].interfaces?.filter(item => item.id === options.interfaceId);
  if (!interfaces || interfaces.length !== 1) throw new Error('接口不存在或 ID 不唯一：' + options.interfaceId);
  const hardware = compileHardwareProfile(catalog, options.profileId, options.parameters);
  if (options.scenarioId !== undefined) {
    const scenario = selectionRules.scenarios.find(entry => entry.id === options.scenarioId);
    if (!scenario) throw new Error('五金连接场景不存在：' + options.scenarioId);
    if (scenario.mode !== hardware.mode) throw new Error('场景与方案的五金模式不一致。');
    hardware.scenarioId = scenario.id;
  }
  if (options.note !== undefined) {
    if (typeof options.note !== 'string' || options.note.length > 500) throw new Error('配置说明最长 500 字符。');
    hardware.note = options.note.trim();
  }
  return {
    expectedRevision: document.revision,
    action: {
      type: 'interface.hardware.set',
      memberId: options.memberId,
      interfaceId: options.interfaceId,
      hardware,
    },
  };
}

const help = [
  '用法（在项目根目录运行）：',
  'node scripts/prepare-interface-hardware.mjs --draft draft.json --member 真实成员ID --interface 真实接口ID --profile bolt-kit --set "boltSpec=实际螺栓规格" --set "nutSpec=实际螺母规格" --set "washerSpec=实际垫圈规格" --output hardware-action.json',
  '',
  '--draft      本机保存的草稿、GET 草稿响应或组件导出 JSON',
  '--member     草稿第一层的真实 member.id（导出格式使用 memberId）',
  '--interface  该成员的真实 interface.id；子组件使用其对外接口 ID',
  '--profile    目录 profiles[].id，必须明确选择；使用 --list 列出方案',
  '--set        填写 name=value，可重复；不提供时不会猜测规格',
  '--scenario   实际连接场景 ID，见 src/domain/hardwareSelectionRules.json；不按接口类型限制',
  '--note       配置说明，无需五金/自定义场景须填写；未填写完整仍可保存为待确认',
  '--output     可选，写入新 UTF-8 JSON 文件；已有文件不覆盖',
  '--catalog    可选，读取另一份同格式目录',
  '--list       列出方案、参数和适用条件',
  '--help       显示本帮助',
  '',
  '工具只生成一个请求，不连接服务器。提交前重新读取草稿 revision。',
  '一次请求替换整个接口五金配置；多个方案需先合并 items。',
].join('\n');

function main() {
  const { values } = parseArgs({
    options: {
      draft: { type: 'string' }, member: { type: 'string' }, interface: { type: 'string' },
      profile: { type: 'string' }, set: { type: 'string', multiple: true },
      scenario: { type: 'string' }, note: { type: 'string' },
      output: { type: 'string' }, catalog: { type: 'string' },
      help: { type: 'boolean' }, list: { type: 'boolean' },
    },
    strict: true, allowPositionals: false,
  });
  if (values.help) { process.stdout.write(help + '\n'); return; }
  const catalog = readJson(values.catalog ?? defaultCatalog);
  if (values.list) {
    if (catalog?.format !== 'ruiware-interface-hardware-reference' || catalog.formatVersion !== 1
        || !Array.isArray(catalog.profiles)) throw new Error('不支持的五金目录。');
    process.stdout.write(JSON.stringify(catalog.profiles.map(({ id, label, parameters, condition }) =>
      ({ id, label, parameters, condition })), null, 2) + '\n');
    return;
  }
  if (!values.draft || !values.member || !values.interface || !values.profile) throw new Error(help);
  const parameters = Object.create(null);
  for (const entry of values.set ?? []) {
    const position = entry.indexOf('=');
    if (position < 1) throw new Error('--set 格式必须为 name=value。');
    const key = entry.slice(0, position);
    if (Object.hasOwn(parameters, key)) throw new Error('参数重复：' + key);
    parameters[key] = entry.slice(position + 1);
  }
  const request = buildHardwareRequest(readJson(values.draft), catalog, {
    memberId: values.member, interfaceId: values.interface, profileId: values.profile, parameters,
    scenarioId: values.scenario, note: values.note,
  });
  const content = JSON.stringify(request, null, 2) + '\n';
  if (values.output) {
    writeFileSync(values.output, content, { encoding: 'utf8', flag: 'wx' });
    process.stderr.write('已生成请求文件：' + resolve(values.output) + '\n');
  } else process.stdout.write(content);
  process.stderr.write('此请求尚未提交。请核对实际规格、每节点数量、原有五金配置及服务器最新 revision。\n');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(); } catch (error) {
    process.stderr.write('生成失败：' + (error instanceof Error ? error.message : String(error)) + '\n');
    process.exitCode = 1;
  }
}
