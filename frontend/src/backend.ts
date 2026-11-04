// Backend wire data is validated at the boundary; absent perception fields stay absent.
export type RecordData = Record<string, unknown>;
export type BackendObject = RecordData & { objectId: string; backendRevision: number };
export type BackendAlert = RecordData & { alertId: string; objectId: string; backendRevision: number };
export type Snapshot = {
  schemaVersion: 'backend.v1'; backendBootId: string; cursor: string;
  objects: BackendObject[]; alerts: BackendAlert[]; missions: RecordData[]; robots: RecordData[];
};
export type Store = Snapshot & { seen: Set<string> };
export function record(value: unknown): RecordData {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as RecordData : {};
}
export const text = (value: unknown, fallback = 'Unavailable') => typeof value === 'string' && value ? value : fallback;
export const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : null;
function entity(value: unknown, id: string): RecordData {
  const row = record(value);
  if (typeof row[id] !== 'string' || !row[id] || !Number.isInteger(row.backendRevision) || (row.backendRevision as number) < 0)
    throw new Error('Unsupported backend entity.');
  return row;
}
export function parseSnapshot(value: unknown): Store {
  const s = record(value);
  if (s.schemaVersion !== 'backend.v1' || typeof s.backendBootId !== 'string' || !/^[a-f0-9]{32}$/.test(s.backendBootId) || typeof s.cursor !== 'string' || !s.cursor ||
      !Array.isArray(s.objects) || !Array.isArray(s.alerts) || !Array.isArray(s.missions) || !Array.isArray(s.robots)) throw new Error('Unsupported backend snapshot.');
  const objects = s.objects.map(o => entity(o, 'objectId') as BackendObject);
  const alerts = s.alerts.map(a => {
    const row = entity(a, 'alertId');
    if (typeof row.objectId !== 'string') throw new Error('Unsupported backend alert.');
    return row as BackendAlert;
  });
  return { schemaVersion: 'backend.v1', backendBootId: s.backendBootId, cursor: s.cursor, objects, alerts,
    missions: s.missions.map(record), robots: s.robots.map(record), seen: new Set(alerts.flatMap(a => typeof a.visionEventId === 'string' ? [a.visionEventId] : [])) };
}
export function applyEvent(state: Store, value: unknown): Store {
  const event = record(value);
  if (event.backendBootId !== state.backendBootId) throw new Error('Backend restarted; a new snapshot is required.');
  if (typeof event.id !== 'string' || !event.id) throw new Error('Missing event cursor.');
  const next = { ...state, cursor: event.id };
  if (event.entity !== 'object' && event.entity !== 'alert') throw new Error('Unsupported backend event.');
  const key = event.entity === 'object' ? 'objectId' : 'alertId';
  const body = entity(event.body, key);
  if (body.backendRevision !== event.backendRevision) throw new Error('Conflicting entity revision.');
  if (event.entity === 'object') {
    const old = state.objects.find(o => o.objectId === body.objectId);
    if (!old || old.backendRevision < (body.backendRevision as number)) next.objects = [...state.objects.filter(o => o.objectId !== body.objectId), body as BackendObject];
  } else {
    if (typeof body.objectId !== 'string') throw new Error('Missing alert object.');
    const old = state.alerts.find(a => a.alertId === body.alertId);
    const visionId = typeof event.visionEventId === 'string' ? event.visionEventId : typeof body.visionEventId === 'string' ? body.visionEventId : null;
    if (visionId && state.seen.has(visionId) && !old) return next;
    if (!old || old.backendRevision < (body.backendRevision as number)) next.alerts = [...state.alerts.filter(a => a.alertId !== body.alertId), body as BackendAlert];
    if (visionId) next.seen = new Set([...state.seen, visionId]);
  }
  return next;
}
export function objectFacts(object: BackendObject) {
  const mapping = Object.keys(record(object.mapping)).length ? record(object.mapping) : object;
  const stationary = record(object.stationary);
  const point = mapping.floorPointM;
  const position = Array.isArray(point) && point.length === 2 && point.every(v => number(v) !== null) ? point as number[] : null;
  return { mapping, position, samples: number(stationary.sampleCount), seconds: number(stationary.spanSeconds) ?? number(object.metricStationarySeconds),
    valid: mapping.valid === true, calibrated: mapping.calibrationValid === true,
    potential: object.potentialTarget === true, dispatch: object.dispatchEligible === true,
    classification: record(object.classification) };
}

export type Connection = { status: 'loading' | 'connected' | 'retrying' | 'setup'; message: string; store: Store | null; updatedAt: number | null };
type Stream = Pick<EventSource, 'close' | 'onopen' | 'onerror'> & { addEventListener: (name: string, listener: EventListener) => void };
type Dependencies = {
  fetch: typeof fetch; stream: (url: string) => Stream;
  schedule: (callback: () => void, ms: number) => ReturnType<typeof setTimeout>;
  cancel: typeof clearTimeout;
};
export function connectBackend(publish: (state: Connection) => void, supplied?: Partial<Dependencies>) {
  const deps: Dependencies = { fetch: (...args) => fetch(...args), stream: url => new EventSource(url), schedule: (cb, ms) => setTimeout(cb, ms), cancel: timer => clearTimeout(timer), ...supplied };
  let stopped = false, source: Stream | null = null, timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | null = null, generation = 0, retries = 0;
  let state: Connection = { status: 'loading', message: 'Loading backend snapshot…', store: null, updatedAt: null };
  const emit = (patch: Partial<Connection>) => { state = { ...state, ...patch }; if (!stopped) publish(state); };
  const cleanup = () => { source?.close(); source = null; deps.cancel(timer); controller?.abort(); };
  async function load() {
    cleanup(); const run = ++generation; controller = new AbortController();
    const current = () => !stopped && generation === run;
    emit({ status: state.store ? 'retrying' : 'loading', message: 'Synchronizing backend snapshot…' });
    try {
      const options = { credentials: 'same-origin' as const, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10_000)]), cache: 'no-store' as const };
      let response = await deps.fetch('/api/snapshot', options);
      if (response.status === 401) {
        // Optional loopback-only Vite helper holds OPERATOR_TOKEN on the server.
        const session = await deps.fetch('/__local/session', { ...options, method: 'POST', headers: { 'X-Rovr-Local': '1' } });
        if (!current()) return;
        if (!session.ok) { emit({ status: 'setup', message: 'Backend session unavailable. Configure the local connection and retry.' }); return; }
        response = await deps.fetch('/api/snapshot', options);
      }
      if (!current()) return;
      if (response.status === 401) { emit({ status: 'setup', message: 'Backend session expired or was rejected. Check the local connection and retry.' }); return; }
      if (!response.ok) throw new Error(`Backend unavailable (HTTP ${response.status}).`);
      const store = parseSnapshot(await response.json());
      if (!current()) return;
      emit({ store, updatedAt: Date.now(), message: 'Opening event stream…' });
      source = deps.stream(`/api/events?after=${encodeURIComponent(store.cursor)}`);
      source.onopen = () => { if (current()) { retries = 0; emit({ status: 'connected', message: 'Backend connected · read-only' }); } };
      for (const name of ['object.observed', 'object.classified', 'object.updated', 'alert.potential_target', 'alert.target_revoked']) source.addEventListener(name, ((event: MessageEvent<string>) => {
        if (!current() || !state.store) return;
        try { emit({ store: applyEvent(state.store, JSON.parse(event.data)), updatedAt: Date.now() }); }
        catch { void load(); }
      }) as EventListener);
      source.addEventListener('snapshot_required', () => { if (current()) void load(); });
      source.onerror = () => { if (current()) retry('Connection lost. Showing the last snapshot while reconnecting.'); };
      // Validate idle sessions and refresh backend stationarity even without stream traffic.
      timer = deps.schedule(() => { if (current()) void load(); }, 60_000);
    } catch (error) {
      if (current()) retry(error instanceof Error ? error.message : 'Backend connection failed.');
    }
  }
  function retry(message: string) {
    cleanup(); ++generation;
    emit({ status: 'retrying', message });
    timer = deps.schedule(() => { void load(); }, Math.min(1000 * 2 ** retries++, 15_000));
  }
  void load();
  return { close() { stopped = true; ++generation; cleanup(); }, retry() { retries = 0; void load(); } };
}
