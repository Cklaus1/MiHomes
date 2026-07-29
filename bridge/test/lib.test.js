// M29 · bridge log compaction — run with `node --test` from bridge/.
const test = require('node:test');
const assert = require('node:assert');
const { compactMessages } = require('../lib');

test('keeps only the newest max records', () => {
  const lines = Array.from({ length: 100 }, (_, i) => JSON.stringify({ id: i })).join('\n') + '\n';
  const { records, text } = compactMessages(lines, 10);
  assert.strictEqual(records.length, 10);
  assert.strictEqual(records[0].id, 90);
  assert.strictEqual(records[9].id, 99);
  // Round-trips: recompacting the output is a no-op.
  assert.strictEqual(compactMessages(text, 10).records.length, 10);
});

test('drops blank and corrupt lines', () => {
  const content = '{"id":1}\n\n  \nnot json\n{"id":2}\n';
  const { records } = compactMessages(content, 100);
  assert.deepStrictEqual(records.map((r) => r.id), [1, 2]);
});

test('empty input yields empty output', () => {
  const { records, text } = compactMessages('', 100);
  assert.strictEqual(records.length, 0);
  assert.strictEqual(text, '');
});

test('fewer records than max are all kept', () => {
  const { records } = compactMessages('{"id":1}\n{"id":2}\n', 1000);
  assert.strictEqual(records.length, 2);
});

test('output always ends with a single trailing newline when non-empty', () => {
  const { text } = compactMessages('{"id":1}\n{"id":2}\n', 1000);
  assert.ok(text.endsWith('\n'));
  assert.ok(!text.endsWith('\n\n'));
});
