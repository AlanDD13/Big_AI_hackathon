import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createManualClient, manualVelocity, parseManualStatus } from '../src/manualBackend.ts';
const caps = { maxAbsXMps: .05, maxAbsYMps: .04, maxAbsZDegps: 5, watchdogTimeoutMs: 300, maxIntentTtlMs: 200 };
const boot = 'a'.repeat(32);
function harness(mode = 'simulator') {
  let now = 100_000;
  const calls: { action: string; body: any }[] = [];
  let status: any = { roverId: 'rover-1', bridgeBootId: boot, stage: 'IDLE', telemetryFresh: true, stationaryConfirmed: true, executionMode: mode, manualBootFence: 'bridgeBootId-v1', backendStopLocked: false, caps };
  let failDrive = false;
  let delayDrive: ((value: Response) => void) | null = null;
  let pendingDrive = false;
  let delayLease: ((value: Response) => void) | null = null;
  let pendingLease = false;
  const grant = () => ({ ...status, issuedAt: now / 1000, expiresAt: now / 1000 + 2, leaseId: 'lease-1', leaseGeneration: 1 });
  const deps = { now: () => now, fetch: (async (input: unknown, init?: RequestInit) => {
    assert.equal(init?.credentials, 'same-origin');
    const action = String(input).split('/').at(-1)!.split('?')[0];
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ action, body });
    if (action === 'status') return Response.json(status);
    if (action === 'lease') {
      status = { ...status, stage: 'MANUAL_IDLE', leaseId: 'lease-1', leaseGeneration: 1 };
      if (pendingLease) return await new Promise<Response>(resolve => { delayLease = resolve; });
      return Response.json(grant());
    }
    if (action === 'drive') {
      if (failDrive) throw new Error('lost ACK');
      if (pendingDrive) return await new Promise<Response>(resolve => { delayDrive = resolve; });
      return Response.json({ ...status, stage: 'MANUAL_DRIVING', lastSequence: body.sequence, appliedVelocity: { xMps: body.xMps, yMps: body.yMps, zDegps: body.zDegps } });
    }
    if (action === 'stop' || action === 'release') { status = { ...status, stage: 'UNKNOWN', stationaryConfirmed: false, backendStopLocked: action === 'stop' }; return Response.json(status); }
    if (action === 'rearm') { status = { ...status, stage: 'IDLE', backendStopLocked: false }; return Response.json({ ...status, rearmed: true, motion: false }); }
    throw new Error(action);
  }) as typeof fetch };
  const client = createManualClient('rover-1', () => {}, deps);
  return { client, calls, set: (patch: any) => { status = { ...status, ...patch }; }, advance: (ms: number) => { now += ms; }, fail: () => { failDrive = true; },
    delayDrive: () => { pendingDrive = true; }, completeDrive: () => delayDrive?.(Response.json({ ...status, stage: 'MANUAL_DRIVING', lastSequence: 1, appliedVelocity: { xMps: .05, yMps: 0, zDegps: 0 } })),
    delayLease: () => { pendingLease = true; }, completeLease: () => delayLease?.(Response.json(grant())) };
}
test('manual transport binds grants to boot/lease/generation, lower caps and <=250ms TTL', async () => {
  const h = harness(); await h.client.poll(); await h.client.acquire(); await h.client.tick(1, 1, 1);
  const drive = h.calls.find(c => c.action === 'drive')!.body;
  assert.equal(drive.bridgeBootId, boot); assert.equal(drive.leaseId, 'lease-1'); assert.equal(drive.leaseGeneration, 1);
  assert.equal(drive.sequence, 1); assert.ok(drive.expiresAt - drive.issuedAt <= .201);
  assert.ok(drive.xMps <= .05); assert.ok(drive.yMps <= .04); assert.equal(drive.zDegps, 5);
  await h.client.release('blur'); assert.equal(h.calls.at(-1)?.body.reason, 'blur'); assert.equal(h.client.getState().lease, null); assert.equal(h.client.getState().uncertain, true);
});
test('physical execution mode never admits any writes in simulator checkpoint', async () => {
  const h = harness('physical'); await h.client.poll(); await h.client.acquire(); await h.client.tick(0,1,0); await h.client.rearm(); await h.client.stop();
  assert.deepEqual(h.calls.map(c => c.action), ['status']); assert.equal(h.client.getState().lease, null);
});
test('lost drive ACK invalidates authority, requests stop once, and never replays intent', async () => {
  const h = harness(); await h.client.poll(); await h.client.acquire(); h.fail(); await h.client.tick(0,1,0); await h.client.tick(0,1,0);
  assert.equal(h.calls.filter(c => c.action === 'drive').length, 1); assert.equal(h.calls.filter(c => c.action === 'stop').length, 1); assert.equal(h.client.getState().lease, null);
});
test('priority release bypasses pending drive; late drive ACK cannot restore authority', async () => {
  const h = harness(); await h.client.poll(); await h.client.acquire(); h.delayDrive();
  const first = h.client.tick(0,1,0); await h.client.tick(0,1,0); await h.client.release();
  assert.equal(h.calls.filter(c => c.action === 'drive').length, 1); assert.equal(h.calls.at(-1)?.action, 'release');
  h.completeDrive(); await first; assert.equal(h.client.getState().lease, null);
});
test('cancelled pending grant requests global stop and rejects late lease', async () => {
  const h = harness(); await h.client.poll(); h.delayLease(); const pending = h.client.acquire(); await h.client.release('tab_hidden');
  assert.equal(h.calls.at(-1)?.action, 'stop'); h.completeLease(); await pending; assert.equal(h.client.getState().lease, null);
});
test('new boot, stale telemetry and local lease expiry cannot continue driving', async () => {
  for (const kind of ['boot','telemetry','expiry']) {
    const h = harness(); await h.client.poll(); await h.client.acquire();
    if (kind === 'expiry') { h.advance(2100); await h.client.tick(0,1,0); }
    else { h.set(kind === 'boot' ? { bridgeBootId: 'b'.repeat(32) } : { telemetryFresh: false }); await h.client.poll(); }
    assert.equal(h.client.getState().lease, null); assert.ok(h.calls.some(c => c.action === 'stop'));
  }
});
test('UNKNOWN blocks acquisition/rearm; confirmed stop requires explicit rearm and fresh acquisition', async () => {
  const h = harness(); h.set({ stage: 'UNKNOWN', stationaryConfirmed: false }); await h.client.poll(); await h.client.acquire(); await h.client.rearm();
  assert.deepEqual(h.calls.map(c => c.action), ['status']);
  h.set({ stage: 'STOPPED', stationaryConfirmed: true, backendStopLocked: true }); await h.client.poll(); await h.client.rearm();
  assert.ok(h.calls.some(c => c.action === 'rearm')); assert.equal(h.client.getState().lease, null); assert.equal(h.calls.some(c => c.action === 'drive'), false);
});
test('status never adopts a foreign lease and rejects unsupported capabilities', async () => {
  const h = harness(); h.set({ stage: 'MANUAL_IDLE', leaseId: 'foreign', leaseGeneration: 10 }); await h.client.poll(); await h.client.acquire();
  assert.equal(h.client.getState().lease, null); assert.equal(h.calls.some(c => c.action === 'lease'), false);
  assert.throws(() => parseManualStatus({ roverId: 'rover-1' }, 'rover-1'));
  assert.deepEqual(manualVelocity(NaN,1,0,caps), { xMps: 0, yMps: 0, zDegps: 0 });
});
