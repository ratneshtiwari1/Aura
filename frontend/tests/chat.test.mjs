import { test } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

test('supporting data renders nested fields without raw JSON or numeric rounding', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    server: { middlewareMode: true },
    appType: 'custom',
  });
  try {
    const { EvidenceValue } = await server.ssrLoadModule('/src/App.tsx');
    const markup = renderToStaticMarkup(React.createElement(EvidenceValue, {
      value: { category: 'North', group: { Region: 'North' }, value: 2039834, rate: 0.123456789, missing: null },
    }));
    assert.match(markup, /<dt>Region<\/dt><dd>North<\/dd>/);
    assert.match(markup, /2,039,834/);
    assert.match(markup, /0\.123456789/);
    assert.match(markup, /—/);
    assert.doesNotMatch(markup, /\{"Region"/);
  } finally {
    await server.close();
  }
});
