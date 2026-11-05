export type ManualCaps = { maxAbsXMps: number; maxAbsYMps: number; maxAbsZDegps: number; watchdogTimeoutMs: number; maxIntentTtlMs: number };
export type ManualStatus = { roverId: string; bridgeBootId: string; stage: string; telemetryFresh: boolean; stationaryConfirmed: boolean; executionMode: 'simulator' | 'physical'; manualBootFence: string; backendStopLocked: boolean; caps: ManualCaps; leaseId?: string; leaseGeneration?: number };
export type ManualLease = { roverId: string; bridgeBootId: string; leaseId: string; leaseGeneration: number; issuedAt: number; expiresAt: number; caps: ManualCaps };
export type ManualState = { status: ManualStatus | null; lease: ManualLease | null; pending: boolean; uncertain: boolean; message: string; receivedAt: number };
type Velocity = { xMps: number; yMps: number; zDegps: number };
export type ReleaseReason = 'user_release' | 'blur' | 'tab_hidden';
type Dependencies = { fetch: typeof fetch; now: () => number };
const record = (v: unknown): Record<string, unknown> => { if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error('Invalid manual response.'); return v as Record<string, unknown>; };
const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
const identity = (v: Record<string, unknown>, rover: string) => v.roverId === rover && typeof v.bridgeBootId === 'string' && /^[a-f0-9]{32}$/.test(v.bridgeBootId);
export function parseManualCaps(v: unknown): ManualCaps {
  const c = record(v);
  for (const [key, max] of Object.entries({ maxAbsXMps: .35, maxAbsYMps: .35, maxAbsZDegps: 45, watchdogTimeoutMs: 300, maxIntentTtlMs: 500 })) {
    if (!finite(c[key]) || c[key] <= 0 || c[key] > max) throw new Error('Unsupported manual control limits.');
  }
  return c as ManualCaps;
}
export function parseManualStatus(value: unknown, rover: string): ManualStatus {
  const s = record(value);
  if (!identity(s, rover) || !['IDLE','MANUAL_IDLE','MANUAL_DRIVING','UNKNOWN','STOPPED'].includes(String(s.stage)) ||
    !['physical','simulator'].includes(String(s.executionMode)) || s.manualBootFence !== 'bridgeBootId-v1' ||
    typeof s.telemetryFresh !== 'boolean' || typeof s.stationaryConfirmed !== 'boolean' || typeof s.backendStopLocked !== 'boolean') throw new Error('Manual status/capability unavailable.');
  return { ...s, caps: parseManualCaps(s.caps) } as ManualStatus;
}
export function parseManualLease(value: unknown, rover: string): ManualLease {
  const l = record(value);
  if (!identity(l, rover) || typeof l.leaseId !== 'string' || !l.leaseId.length || !Number.isSafeInteger(l.leaseGeneration) || Number(l.leaseGeneration) < 1 ||
    !finite(l.issuedAt) || !finite(l.expiresAt) || l.expiresAt <= l.issuedAt || l.expiresAt - l.issuedAt > 10) throw new Error('Invalid manual lease.');
  return { ...l, caps: parseManualCaps(l.caps) } as ManualLease;
}
export function manualVelocity(strafe: number, forward: number, turn: number, caps: ManualCaps): Velocity {
  if (![strafe, forward, turn].every(finite)) return { xMps: 0, yMps: 0, zDegps: 0 };
  const length = Math.max(1, Math.hypot(strafe, forward));
  return { xMps: forward / length * Math.min(.15, caps.maxAbsXMps), yMps: strafe / length * Math.min(.15, caps.maxAbsYMps), zDegps: Math.max(-1, Math.min(1, turn)) * Math.min(15, caps.maxAbsZDegps) };
}

// Physical writes require an explicit per-view opt-in and low bench caps.
export function createManualClient(rover: string, update: (state: ManualState) => void, deps: Dependencies = { fetch: (input, init) => globalThis.fetch(input, init), now: Date.now }, options: { allowPhysical?: boolean } = {}) {
  let state: ManualState = { status: null, lease: null, pending: false, uncertain: false, message: 'Checking manual bridge…', receivedAt: 0 };
  let epoch = 0, sequence = 0, closed = false, driveBusy = false, pollBusy = false;
  let leaseDeadline = 0;
  const emit = (patch: Partial<ManualState>) => { state = { ...state, ...patch }; if (!closed) update(state); };
  const binding = (l: ManualLease) => ({ roverId: rover, bridgeBootId: l.bridgeBootId, leaseId: l.leaseId, leaseGeneration: l.leaseGeneration });
  async function call(action: string, body?: unknown, keepalive = false) {
    const response = await deps.fetch(`/api/manual/${action}`, { method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(800), keepalive });
    if (!response.ok) {
      if (response.status === 401) throw new Error('Operator session expired. Refresh the backend connection; take control explicitly again.');
      let code = 'MANUAL_UNAVAILABLE'; try { const result = record(await response.json()); const error = record(result.error); if (typeof error.code === 'string' && /^[A-Z_]+$/.test(error.code)) code = error.code; } catch { /* generic */ }
      throw new Error(code.replaceAll('_', ' '));
    }
    return record(await response.json());
  }
  function admitted() {
    return !closed && Boolean(state.status && (state.status.executionMode === 'simulator' || (options.allowPhysical && state.status.executionMode === 'physical' && state.status.caps.maxAbsXMps <= .05 && state.status.caps.maxAbsYMps <= .05 && state.status.caps.maxAbsZDegps <= 5)) && state.status.manualBootFence === 'bridgeBootId-v1');
  }
  const operatorWritesAllowed = () => !closed && Boolean(state.status && (state.status.executionMode === 'simulator' || (options.allowPhysical && state.status.executionMode === 'physical')));
  const fresh = () => admitted() && state.status?.telemetryFresh === true && deps.now() - state.receivedAt < 1500 && deps.now() >= state.receivedAt;
  async function stop() {
    const allowed = operatorWritesAllowed(); ++epoch; leaseDeadline = 0;
    emit({ lease: null, pending: false, uncertain: true, message: allowed ? 'Stop requested · stationary confirmation pending.' : 'Physical control disabled in this checkpoint.' });
    if (!allowed) return;
    try { await call('stop', { roverId: rover, reason: 'operator_stop' }, true); }
    catch { emit({ message: 'Stop unconfirmed. Connection lost; no motion will be replayed.' }); }
  }
  async function lose(message: string) {
    const hadAuthority = Boolean(state.lease || state.pending);
    ++epoch; leaseDeadline = 0; emit({ lease: null, pending: false, uncertain: true, message });
    if (hadAuthority && operatorWritesAllowed()) { try { await call('stop', { roverId: rover, reason: 'operator_stop' }, true); } catch { /* stop remains unconfirmed */ } }
  }
  async function poll() {
    if (closed || pollBusy) return;
    pollBusy = true; const gen = epoch;
    try {
      const status = parseManualStatus(await call(`status?roverId=${encodeURIComponent(rover)}`), rover);
      if (closed || gen !== epoch) return;
      const lease = state.lease;
      const lost = lease && (status.bridgeBootId !== lease.bridgeBootId || status.leaseId !== lease.leaseId || status.leaseGeneration !== lease.leaseGeneration ||
        !status.telemetryFresh || !['MANUAL_IDLE', 'MANUAL_DRIVING'].includes(status.stage) || status.backendStopLocked || !admitted());
      emit({ status, receivedAt: deps.now() });
      if (lost) { await lose('Control authority changed. Stop unconfirmed; take control again only after reconciliation.'); return; }
      if (!state.lease && !state.pending) emit({ uncertain: !(status.stationaryConfirmed && ['IDLE','STOPPED'].includes(status.stage)), message:
        status.executionMode === 'physical' && !admitted() ? 'Physical bridge detected · opt-in and low bench caps required.' : status.stage === 'UNKNOWN' ? 'Stop confirmation pending · no drive authority.' : status.backendStopLocked || status.stage === 'STOPPED' ? 'Stop latched · explicit re-arm required.' : `${status.executionMode === 'physical' ? 'Physical' : 'Simulator'} bridge ready · take control explicitly.` });
    } catch (error) { if (!closed && gen === epoch) await lose(error instanceof Error ? error.message : 'Manual status unavailable.'); }
    finally { pollBusy = false; }
  }
  async function acquire() {
    if (!fresh() || state.pending || state.lease || state.uncertain || state.status?.stage !== 'IDLE' || state.status.backendStopLocked) return;
    const gen = ++epoch, boot = state.status.bridgeBootId; emit({ pending: true, message: 'Requesting simulator control…' });
    try {
      const lease = parseManualLease(await call('lease', { roverId: rover, requestedTtlMs: 2000 }), rover);
      if (closed || gen !== epoch) return; // backend priority stop reconciles cancelled grants
      if (lease.bridgeBootId !== boot || Math.abs(lease.issuedAt - deps.now() / 1000) > .2 || lease.caps.maxIntentTtlMs < 100 || lease.caps.watchdogTimeoutMs < 200) throw new Error('Lease identity, clock or timing limits unsupported.');
      if (state.status?.executionMode === 'physical' && (lease.caps.maxAbsXMps > .05 || lease.caps.maxAbsYMps > .05 || lease.caps.maxAbsZDegps > 5)) throw new Error('Physical grant exceeds supervised bench limits.');
      sequence = 0; leaseDeadline = deps.now() + (lease.expiresAt - lease.issuedAt) * 1000;
      emit({ lease, pending: false, uncertain: false, message: `${state.status?.executionMode === 'physical' ? 'Physical' : 'Simulator'} control held · hold an input to drive.` });
    } catch (error) { if (!closed && gen === epoch) await lose(error instanceof Error ? error.message : 'Lease unavailable.'); }
  }
  async function release(reason: ReleaseReason = 'user_release') {
    const lease = state.lease;
    if (!lease) { if (state.pending) await stop(); return; }
    ++epoch; leaseDeadline = 0; emit({ lease: null, pending: false, uncertain: true, message: 'Control released · stationary confirmation pending.' });
    try {
      const result = await call('release', { ...binding(lease), reason }, true);
      if (!identity(result, rover) || result.bridgeBootId !== lease.bridgeBootId || result.stage !== 'UNKNOWN') throw new Error();
    } catch { emit({ message: 'Release unconfirmed · no drive authority.' }); if (operatorWritesAllowed()) { try { await call('stop', { roverId: rover, reason: 'operator_stop' }, true); } catch { /* unconfirmed */ } } }
  }
  async function tick(strafe: number, forward: number, turn: number) {
    const lease = state.lease;
    if (!lease || closed) return;
    if (!fresh() || deps.now() >= leaseDeadline) { await lose('Telemetry or lease expired. Stop unconfirmed; no replay.'); return; }
    if (driveBusy) return; // drop input; never queue it behind the old request
    driveBusy = true; const gen = epoch, now = deps.now(), seq = ++sequence;
    const velocity = manualVelocity(strafe, forward, turn, lease.caps);
    try {
      const result = await call('drive', { ...binding(lease), sequence: seq, issuedAt: now / 1000, expiresAt: now / 1000 + Math.min(250, lease.caps.maxIntentTtlMs) / 1000, ...velocity, frame: 'BODY' });
      if (closed || gen !== epoch) return;
      const applied = record(result.appliedVelocity);
      if (!identity(result, rover) || result.bridgeBootId !== lease.bridgeBootId || result.leaseId !== lease.leaseId || result.leaseGeneration !== lease.leaseGeneration || result.lastSequence !== seq ||
        result.telemetryFresh !== true || !['MANUAL_IDLE','MANUAL_DRIVING'].includes(String(result.stage)) || deps.now() - now > 250 ||
        Object.entries(velocity).some(([k,v]) => applied[k] !== v)) throw new Error('Drive acknowledgement ambiguous.');
      leaseDeadline = deps.now() + (lease.expiresAt - lease.issuedAt) * 1000;
    } catch (error) { if (!closed && gen === epoch) await lose(error instanceof Error ? error.message : 'Drive unconfirmed.'); }
    finally { driveBusy = false; }
  }
  async function rearm() {
    if (!fresh() || state.uncertain || state.pending || state.lease || state.status?.stage !== 'STOPPED' || !state.status.stationaryConfirmed) return;
    const gen = ++epoch, boot = state.status.bridgeBootId; emit({ pending: true });
    try {
      const result = await call('rearm', { roverId: rover, bridgeBootId: boot });
      if (closed || gen !== epoch) return;
      if (!identity(result, rover) || result.bridgeBootId !== boot || result.stage !== 'IDLE' || result.rearmed !== true || result.motion !== false) throw new Error('Re-arm unconfirmed.');
      emit({ pending: false, message: 'Re-armed · take control explicitly. No motion resumed.' }); await poll();
    } catch (error) { if (!closed && gen === epoch) await lose(error instanceof Error ? error.message : 'Re-arm unavailable.'); }
  }
  return { poll, acquire, tick, release, stop, rearm, getState: () => state, close() { if (closed) return; void release('blur'); closed = true; ++epoch; } };
}
