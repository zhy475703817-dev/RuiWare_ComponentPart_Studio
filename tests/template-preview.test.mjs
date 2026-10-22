import assert from 'node:assert/strict';
import test from 'node:test';
import { parsePreviewDescription, parseStlGeometry, loadReconstructionInfo, loadTemplatePreview } from '../src/library/templatePreview.ts';

const publication = { publicationId: 'v-1', templateId: 'd-1', version: 1, code: 'TEST', name: '预览件', createdAt: '2026-10-17T00:00:00Z', sha256: 'a'.repeat(64), size: 100, downloadUrl: '/unused' };
const stl = new TextEncoder().encode('solid test\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 100 0 0\nvertex 0 50 25\nendloop\nendfacet\nendsolid test');
const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', stl)), b=>b.toString(16).padStart(2,'0')).join('');
const description = { publication, unitSystem:'mm-kg-s', coordinateSystem:'right-handed-z-up', parameters:[], interfaces:[], model:{format:'stl',source:'package',url:'/api/published-templates/v-1/preview/model?sha256='+publication.sha256,sha256:hash,size:stl.byteLength} };

test('预览描述固定发布引用、坐标和同源模型路径',()=>{
  assert.equal(parsePreviewDescription(description,publication).model.source,'package');
  for(const patch of [
    {publication:{...publication,sha256:'b'.repeat(64)}},
    {unitSystem:'inch'}, {parameters:null},
    {model:{...description.model,url:'https://evil.local/model.stl'}},
    {model:{...description.model,url:'/api/published-templates/other/preview/model?sha256='+publication.sha256}},
  ]) assert.throws(()=>parsePreviewDescription({...description,...patch},publication));
});

test('STL保持工程原点和毫米尺寸，拒绝无三角形或无效坐标',()=>{
  const geometry=parseStlGeometry(stl.buffer);
  assert.deepEqual(geometry.boundingBox.min.toArray(),[0,0,0]);
  assert.deepEqual(geometry.boundingBox.max.toArray(),[100,50,25]);
  geometry.dispose();
  const padded = new TextEncoder().encode('     '+new TextDecoder().decode(stl));
  const paddedGeometry=parseStlGeometry(padded.buffer);
  assert.equal(paddedGeometry.getAttribute('position').count,3);
  paddedGeometry.dispose();
  for(const content of ['bad','solid empty\nendsolid empty','solid test\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 0 0 0\nvertex 0 0 0\nendloop\nendfacet\nendsolid test']) {
    assert.throws(()=>parseStlGeometry(new TextEncoder().encode(content).buffer),/模型/);
  }
});

test('预览从固定发布版本读取参数并校验重建模型',async t=>{
  const info={publication,parameters:[{id:'length',label:'长度',unit:'mm',valueType:'number',default:100,minimum:20,maximum:500,allowedValues:[]}],interfaces:[]};
  t.mock.method(globalThis,'fetch',async (_url,options)=>options?.method==='POST' ? new Response(stl,{headers:{'X-RuiWare-SHA256':hash}}) : Response.json(info));
  assert.equal((await loadReconstructionInfo(publication,new AbortController().signal)).parameters[0].id,'length');
  const result=await loadTemplatePreview(publication,{length:120},new AbortController().signal);
  assert.equal(result.geometry.getAttribute('position').count,3);
  result.geometry.dispose();
  t.mock.method(globalThis,'fetch',async()=>Response.json({error:{code:'MODEL_MISSING'}},{status:422}));
  await assert.rejects(loadTemplatePreview(publication,{},new AbortController().signal),/检查模板内容/);
});

test('重建接口会映射单体平台的接口类型和兼容标签',async t=>{
  const info={publication,parameters:[],interfaces:[
    {id:'axis',name:'导向轴',interfaceType:'connecting',locatingType:'axisCoincident',compatibilityTags:['round']},
    {id:'pin',name:'定位孔',interfaceType:'locating',locatingType:'pinHole',tags:['hole']},
    {id:'face',name:'安装面',interfaceType:'supporting',locatingType:'faceCoincident'},
  ]};
  t.mock.method(globalThis,'fetch',async()=>Response.json(info));
  const result=await loadReconstructionInfo(publication,new AbortController().signal);
  assert.deepEqual(result.interfaces,[
    {id:'axis',name:'导向轴',type:'axis',tags:['round','interface:connecting','locating:axisCoincident']},
    {id:'pin',name:'定位孔',type:'point',tags:['hole','interface:locating','locating:pinHole']},
    {id:'face',name:'安装面',type:'plane',tags:['interface:supporting','locating:faceCoincident']},
  ]);
});

test('模型字节校验失败或请求取消时不会产生可用预览',async t=>{
  t.mock.method(globalThis,'fetch',async()=>new Response(stl,{headers:{'X-RuiWare-SHA256':'0'.repeat(64)}}));
  await assert.rejects(loadTemplatePreview(publication,{},new AbortController().signal),/校验失败/);
  await assert.rejects(loadTemplatePreview(publication,{},AbortSignal.abort()),{name:'AbortError'});
});
