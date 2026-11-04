import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyEvent, connectBackend, objectFacts, parseSnapshot } from '../src/backend.ts';
import type { Connection } from '../src/backend.ts';

const boot = 'b'.repeat(32);
const object = { objectId: 'cam:session:track', backendRevision: 3, potentialTarget: true, dispatchEligible: false, floorPointM: null, valid: false, detectorScore: null };
const snapshot = () => ({ schemaVersion: 'backend.v1', backendBootId: boot, cursor: 'opaque / cursor', objects: [object], alerts: [], robots: [], missions: [] });
const event = (body = { ...object, backendRevision: 4 }) => ({ id: 'next', backendBootId: boot, entity: 'object', type: 'object.updated', backendRevision: body.backendRevision, body });

test('unknown location and classification stay unknown; perception is not dispatch permission', () => {
  const facts = objectFacts(parseSnapshot(snapshot()).objects[0]);
  assert.equal(facts.position, null);
  assert.deepEqual(facts.classification, {});
  assert.equal(facts.potential, true);
  assert.equal(facts.dispatch, false);
  assert.equal(facts.samples, null);
  const nested = objectFacts({ ...object, mapping: { floorPointM: [0, 0], valid: true, calibrationValid: false } });
  assert.deepEqual(nested.position, [0, 0]);
  assert.equal(nested.calibrated, false);
});
test('reject unsupported snapshots, stale entity revisions, and cross-boot ordering', () => {
  assert.throws(() => parseSnapshot({ ...snapshot(), schemaVersion: 'other' }));
  assert.throws(() => parseSnapshot({ ...snapshot(), objects: [{ objectId: 'a' }] }));
  const newer = applyEvent(parseSnapshot(snapshot()), event());
  const stale = applyEvent(newer, { ...event(object), id: 'replayed' });
  assert.equal(stale.objects[0].backendRevision, 4);
  assert.equal(stale.cursor, 'replayed');
  assert.throws(() => applyEvent(stale, { ...event(), backendBootId: 'c'.repeat(32) }));
  const restarted = parseSnapshot({ ...snapshot(), backendBootId: 'c'.repeat(32), objects: [{ ...object, backendRevision: 1 }] });
  assert.equal(restarted.objects[0].backendRevision, 1);
});
test('deduplicate alert ID and retained vision event ID without losing newer revisions', () => {
  const body = { alertId: 'alert1', objectId: object.objectId, backendRevision: 1, kind: 'potential_target' };
  const alert = { ...event(), entity: 'alert', backendRevision: 1, visionEventId: 'vision:1', body };
  let state = applyEvent(parseSnapshot(snapshot()), alert);
  state = applyEvent(state, alert);
  state = applyEvent(state, { ...alert, body: { ...body, alertId: 'retry-with-another-id' } });
  assert.equal(state.alerts.length, 1);
  state = applyEvent(state, { ...alert, backendRevision: 2, body: { ...body, backendRevision: 2, reason: 'updated' } });
  assert.equal(state.alerts[0].reason, 'updated');
});

class FakeStream {
  onopen: EventSource['onopen'] = null;
  onerror: EventSource['onerror'] = null;
  closed = false;
  listeners = new Map<string, EventListener>();
  addEventListener(name: string, listener: EventListenerOrEventListenerObject) { this.listeners.set(name, listener as EventListener); }
  close() { this.closed = true; }
  send(name: string, value: unknown) { this.listeners.get(name)?.({ data: JSON.stringify(value) } as MessageEvent); }
}
const flush = () => new Promise(resolve => setImmediate(resolve));
function harness(responses: Response[]) {
  const states: Connection[] = [], urls: string[] = [], streams: FakeStream[] = [], pending: (() => void)[] = [];
  const client = connectBackend(s => states.push(s), {
    fetch: (async (input: unknown) => { urls.push(String(input)); const response = responses.shift(); assert.ok(response, 'unexpected request'); return response; }) as typeof fetch,
    stream: url => { urls.push(url); const stream = new FakeStream(); streams.push(stream); return stream; },
    schedule: cb => { pending.push(cb); return pending.length as unknown as ReturnType<typeof setTimeout>; }, cancel: () => {},
  });
  return { client, states, urls, streams, pending };
}
const response = () => Response.json(snapshot());
test('snapshot precedes stream; replay cursor encoded; snapshot_required closes old stream', async () => {
  const h = harness([response(), response()]);
  await flush();
  assert.equal(h.urls[1], '/api/events?after=opaque%20%2F%20cursor');
  h.streams[0].send('object.updated', event());
  assert.equal(h.states.at(-1)?.store?.objects[0].backendRevision, 4);
  h.streams[0].send('snapshot_required', { reason: 'REPLAY_UNAVAILABLE' });
  await flush();
  assert.equal(h.streams[0].closed, true);
  assert.equal(h.streams.length, 2);
  h.client.close();
  h.streams[1].send('object.updated', event());
  assert.equal(h.states.at(-1)?.store?.objects[0].backendRevision, 3);
});
test('missing session uses local setup once; denial stops retries and never opens stream', async () => {
  const h = harness([new Response(null, { status: 401 }), new Response(null, { status: 503 })]);
  await flush();
  assert.deepEqual(h.urls, ['/api/snapshot', '/__local/session']);
  assert.equal(h.states.at(-1)?.status, 'setup');
  assert.equal(h.pending.length, 0);
  h.client.close();
});
test('backend observed/classified events replace the same row without waiting for classification', async () => {
  const h = harness([response()]);
  await flush();
  h.streams[0].send('object.observed', event({ ...object, backendRevision: 4, classificationStatus: 'PENDING' } as typeof object));
  assert.equal(h.states.at(-1)?.store?.objects[0].classificationStatus, 'PENDING');
  h.streams[0].send('object.classified', event({ ...object, backendRevision: 5, classificationStatus: 'COMPLETED', classification: { category: 'TRASH', confidence: 'HIGH' } } as typeof object));
  assert.equal(h.states.at(-1)?.store?.objects.length, 1);
  assert.equal(h.states.at(-1)?.store?.objects[0].classificationStatus, 'COMPLETED');
  h.client.close();
});
test('session refresh retrieves new snapshot; connection failure retains stale data and schedules reconnect', async () => {
  const h = harness([new Response(null, { status: 401 }), new Response(null, { status: 204 }), response()]);
  await flush();
  assert.equal(h.streams.length, 1);
  // EventSource hides HTTP status; recovery rechecks the snapshot/session.
  h.streams[0].onerror?.call({} as EventSource, new Event('error'));
  assert.equal(h.states.at(-1)?.status, 'retrying');
  assert.equal(h.states.at(-1)?.store?.objects.length, 1);
  assert.equal(h.streams[0].closed, true);
  h.client.close();
});
