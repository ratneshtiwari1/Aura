import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { uploadExcel } from '../src/services/api.ts';

class FakeRequest {
  static current;
  upload = {};
  headers = {};
  constructor() { FakeRequest.current = this; }
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader(name, value) { this.headers[name] = value; }
  send(body) { this.body = body; }
  abort() { this.onabort(); }
}

globalThis.XMLHttpRequest = FakeRequest;
globalThis.sessionStorage = {
  getItem(key) { return key === 'autobi_user' ? 'test-user' : key.startsWith('autobi_chat_session:') ? 'session-id' : null; },
  setItem() {},
};

beforeEach(() => { FakeRequest.current = undefined; });

test('reports measured transfer and real server stages from fragmented stream events', async () => {
  const updates = [];
  const pending = uploadExcel(new File(['workbook'], 'data.xlsx'), undefined, (progress) => updates.push(progress));
  const request = FakeRequest.current;
  assert.equal(request.method, 'POST');
  assert.equal(request.url, '/api/datasource/excel/stream');
  assert.equal(request.headers['x-aura-user'], 'test-user');
  request.upload.onprogress({ lengthComputable: true, loaded: 25, total: 100 });
  request.status = 200;
  request.responseText = '{"type":"progress","stage":"reading_work';
  request.onprogress();
  request.responseText += 'book"}\n{"type":"progress","stage":"detecting_tables"}\n';
  request.onprogress();
  request.responseText += JSON.stringify({ type: 'result', result: {
    sourceId: 'src-12345678', sourceType: 'excel', sheets: [], formulaCellCount: 214, aiEnabled: false, aiAnalyzed: false, warnings: [],
  } }) + '\n';
  request.onload();
  assert.deepEqual(updates, [
    { phase: 'uploading', percent: null }, { phase: 'uploading', percent: 25 },
    { phase: 'processing', stage: 'reading_workbook' }, { phase: 'processing', stage: 'detecting_tables' },
  ]);
  const result = await pending;
  assert.equal(result.sourceId, 'src-12345678');
  assert.equal(result.formulaCellCount, 214);
});

test('preserves zero and rejects invalid formula counts', async () => {
  const payload = { sourceId: 'src-12345678', sourceType: 'excel', sheets: [], formulaCellCount: 0, aiEnabled: false, aiAnalyzed: false, warnings: [] };
  const pending = uploadExcel(new File([], 'data.xlsx'));
  FakeRequest.current.status = 200;
  FakeRequest.current.responseText = JSON.stringify({ type: 'result', result: payload }) + '\n';
  FakeRequest.current.onload();
  assert.equal((await pending).formulaCellCount, 0);

  const invalid = uploadExcel(new File([], 'data.xlsx'));
  FakeRequest.current.status = 200;
  FakeRequest.current.responseText = JSON.stringify({ type: 'result', result: { ...payload, formulaCellCount: -1 } }) + '\n';
  FakeRequest.current.onload();
  await assert.rejects(invalid, /invalid response/);
});

test('surfaces stream errors and incomplete responses', async () => {
  const failed = uploadExcel(new File([], 'data.xlsx'));
  FakeRequest.current.status = 200;
  FakeRequest.current.responseText = '{"type":"error","detail":"Unable to read workbook"}\n';
  FakeRequest.current.onload();
  await assert.rejects(failed, /Unable to read workbook/);

  const incomplete = uploadExcel(new File([], 'data.xlsx'));
  FakeRequest.current.status = 200;
  FakeRequest.current.responseText = '{"type":"progress","stage":"reading_workbook"}\n';
  FakeRequest.current.onload();
  await assert.rejects(incomplete, /incomplete response/);

  const invalid = uploadExcel(new File([], 'data.xlsx'));
  FakeRequest.current.status = 200;
  FakeRequest.current.responseText = '{"type":"progress","stage":"unknown"}\n';
  FakeRequest.current.onload();
  await assert.rejects(invalid, /invalid response/);
});

test('surfaces server and timeout errors', async () => {
  const pending = uploadExcel(new File([], 'data.xlsx'));
  FakeRequest.current.status = 400;
  FakeRequest.current.responseText = JSON.stringify({ detail: 'Invalid workbook' });
  FakeRequest.current.onload();
  await assert.rejects(pending, /Invalid workbook/);
  const timedOut = uploadExcel(new File([], 'data.xlsx'));
  FakeRequest.current.ontimeout();
  await assert.rejects(timedOut, /timed out/);
});

test('aborts an active or already cancelled upload', async () => {
  const controller = new AbortController();
  const pending = uploadExcel(new File([], 'data.xlsx'), controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  await assert.rejects(uploadExcel(new File([], 'data.xlsx'), controller.signal), { name: 'AbortError' });
});
