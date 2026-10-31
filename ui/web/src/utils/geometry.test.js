import test from 'node:test';
import assert from 'node:assert/strict';
import { polylinePoints, arcPoints, boundsOf, geometrySignature, hitDistance, dimensionGeometry } from './geometry.js';
import { fromScreen } from './coords.js';
import { fitView, zoomAt } from './viewport.js';

test('bulge 반원과 음수 bulge 방향', () => {
  const p = polylinePoints([[0, 0], [10, 0]], [1, 0], false);
  assert.ok(Math.min(...p.map(v => v[1])) < -4.9);
  const q = polylinePoints([[0, 0], [10, 0]], [-1, 0], false);
  assert.ok(Math.max(...q.map(v => v[1])) > 4.9);
  assert.deepEqual(p[0], [0, 0]);
  assert.ok(Math.abs(p.at(-1)[0] - 10) < 1e-8);
});
test('0도를 지나는 호와 화면 맞춤 범위', () => {
  const points = arcPoints([10, 20], 10, 350, 10);
  assert.ok(points.every(p => p[0] > 19));
  assert.equal(boundsOf([{kind:'circle', center:[10,20], radius:5}]).minX, 5);
  assert.ok(boundsOf([{kind:'polyline',points:[[0,0],[10,0]],bulges:[1,0]}]).minY < -4.9);
});
test('확대 기준점과 Y축 역변환', () => {
  const view = {x:100,y:200,scale:0.5};
  const p = {x:180,y:150};
  const before = fromScreen(p,view);
  assert.deepEqual(fromScreen(p,zoomAt(view,p,2)), before);
  assert.equal(before.y,100);
  const fit = fitView({minX:0,minY:0,maxX:100,maxY:100},{width:500,height:500});
  assert.equal(fit.x, 250 - 50 * fit.scale);
});
test('형상 비교는 원본 출처 변경에 흔들리지 않음', () => {
  const a = {kind:'line',points:[[0,0],[1,0]],sources:[],oid:'a'};
  assert.equal(geometrySignature(a),geometrySignature({...a,sources:[{item:'items[0]'}]}));
  assert.notEqual(geometrySignature(a),geometrySignature({...a,points:[[0,0],[2,0]]}));
  assert.equal(hitDistance(a,[0.5,0]),0);
  assert.ok(hitDistance(a,[0.5,10]) > 9);
});
test('회전된 치수의 투영과 표시 값', () => {
  const dim = dimensionGeometry({points:[[0,0],[100,0],[0,20]],angle:0});
  assert.equal(dim.value,100);
  assert.deepEqual(dim.a,[0,20]);
  assert.deepEqual(dim.b,[100,20]);
});

test('회전·정렬된 문자 전체 범위에서 기준 객체를 선택', () => {
  const p={kind:'text',at:[0,0],height:100,text:'abcdef',align:'MC',rotation:90};
  assert.equal(hitDistance(p,[0,200]),0);
  assert.ok(hitDistance(p,[200,0])>90);
});

test('회전 문자 bounds가 전체 글줄을 포함', () => {
  const p={kind:'text',at:[0,0],height:100,text:'abcdef',align:'L',rotation:90};
  assert.ok(boundsOf([p]).maxY>=599);
  assert.ok(boundsOf([p]).maxX<=1);
});
