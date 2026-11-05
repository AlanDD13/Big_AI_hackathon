import { test } from 'node:test';
import assert from 'node:assert/strict';
import { previewNotices } from '../src/roverNotices.ts';

test('preview speaks a full grab and a sector handoff', () => {
  const spoken = previewNotices.map(notice => notice.text);
  assert.deepEqual(spoken.slice(0, 5), [
    'Object found.',
    'Classified as a water bottle.',
    'Going to it.',
    'Grabbed.',
    'Finished.',
  ]);
  assert.equal(spoken.at(-1), 'Not grabbable. Grab it in sector B2.');
  assert.equal(new Set(previewNotices.map(notice => notice.id)).size, previewNotices.length);
});
