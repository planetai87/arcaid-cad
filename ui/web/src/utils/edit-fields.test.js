import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const compiled = await build({
  entryPoints: [
    fileURLToPath(new URL('../components/EditFields.jsx', import.meta.url)),
  ],
  bundle: true,
  external: ['react'],
  platform: 'node',
  format: 'cjs',
  write: false,
});
const compiledModule = { exports: {} };
new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(
  require,
  compiledModule,
  compiledModule.exports,
);
const { EditField } = compiledModule.exports;

test('대칭만 null/x/y 선택이며 다른 null 원본 필드는 직접 입력한다', () => {
  const html = renderToStaticMarkup(
    React.createElement(EditField, {
      label: 'linetype',
      value: null,
      onSave: () => {},
    }),
  );
  assert.ok(html.includes('<input'));
  assert.ok(!html.includes('<select'));
  const mirror = renderToStaticMarkup(
    React.createElement(EditField, {
      label: 'mirror',
      value: 'x',
      onSave: () => {},
    }),
  );
  assert.ok(mirror.includes('<select'));
  assert.ok(mirror.includes('selected=""'));
  assert.ok(mirror.includes('없음'));
});

test('그리드 참조 입력은 축 이름과 오프셋을 나누어 보여 준다', () => {
  const html = renderToStaticMarkup(
    React.createElement(EditField, {
      label: 'at[0]',
      value: 'X2-150',
      coordinate: true,
      onSave: () => {},
    }),
  );
  assert.ok(html.includes('X2'));
  assert.ok(html.includes('value="-150"'));
});
