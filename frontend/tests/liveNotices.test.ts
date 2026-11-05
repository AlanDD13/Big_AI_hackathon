import { test } from 'node:test';
import assert from 'node:assert/strict';
import { connectNotices, parseNotice } from '../src/roverNotices.ts';

const boot = 'a'.repeat(32);
const notice = (id = '1'.repeat(32)) => ({ id, kind: 'going', text: 'I completed the forward leg.',
  backendBootId: boot, audioUrl: `/api/rover/notices/${id}/audio`, grabbabilityScore: .91 });
class Stream {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (e: unknown) => void>();
  addEventListener(name: string, fn: (e: unknown) => void) { this.listeners.set(name, fn); }
  close() {}
  send(value: unknown) { this.listeners.get('rover.notice')?.({ data: JSON.stringify(value) }); }
}
test('notice validation rejects external audio and unsupported kinds', () => {
  assert.equal(parseNotice(notice()).grabbabilityScore, .91);
  for (const bad of [{ ...notice(), audioUrl: 'https://evil.example/audio' }, { ...notice(), kind: 'toString' },
    { ...notice(), id: 'not-id' }, { ...notice(), backendBootId: 'wrong' }]) assert.throws(() => parseNotice(bad));
});
test('snapshot history stays silent; a new live notice speaks once despite replay', async () => {
  const stream = new Stream(), live: string[] = [], counts: number[] = [];
  const client = connectNotices(state => counts.push(state.notices.length), n => live.push(n.id), {
    fetch: (async () => Response.json({ backendBootId: boot, cursor: 'opaque', notices: [notice()] })) as typeof fetch,
    stream: () => stream as unknown as EventSource,
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(live, []);
  stream.send(notice());
  stream.send(notice('2'.repeat(32)));
  stream.send(notice('2'.repeat(32)));
  assert.deepEqual(live, ['2'.repeat(32)]);
  assert.equal(counts.at(-1), 2);
  client.close();
});
