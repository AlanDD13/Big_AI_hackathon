export type NoticeKind = 'found' | 'classified' | 'going' | 'grabbed' | 'finished' | 'manual';

export type RoverNotice = {
  id: string;
  kind: NoticeKind;
  text: string;
  audioUrl?: string;
  backendBootId?: string;
  grabbabilityScore?: number | null;
  classificationModel?: string | null;
  classificationConfidence?: string | null;
};

export const noticeLabel: Record<NoticeKind, string> = {
  found: 'Object found',
  classified: 'Classified',
  going: 'Route progress',
  grabbed: 'Pickup sequence',
  finished: 'Finished',
  manual: 'Needs a person',
};

export function parseNotice(value: unknown): RoverNotice {
  const n = value as Record<string, unknown>;
  if (!n || typeof n !== 'object' || typeof n.id !== 'string' || !/^[a-f0-9]{32}$/.test(n.id) ||
      typeof n.kind !== 'string' || !Object.hasOwn(noticeLabel, n.kind) || typeof n.text !== 'string' || !n.text || n.text.length > 500 ||
      n.audioUrl !== `/api/rover/notices/${n.id}/audio` || typeof n.backendBootId !== 'string' || !/^[a-f0-9]{32}$/.test(n.backendBootId))
    throw new Error('Unsupported rover message.');
  const score = typeof n.grabbabilityScore === 'number' && Number.isFinite(n.grabbabilityScore) && n.grabbabilityScore >= 0 && n.grabbabilityScore <= 1 ? n.grabbabilityScore : null;
  return { id: n.id, kind: n.kind as NoticeKind, text: n.text, audioUrl: n.audioUrl as string,
    backendBootId: n.backendBootId, grabbabilityScore: score,
    classificationModel: typeof n.classificationModel === 'string' && /^grok-[A-Za-z0-9_.-]{1,120}$/.test(n.classificationModel) ? n.classificationModel : null,
    classificationConfidence: typeof n.classificationConfidence === 'string' && ['LOW','MEDIUM','HIGH'].includes(n.classificationConfidence) ? n.classificationConfidence : null };
}

type NoticeState = { status: string; notices: RoverNotice[] };
type NoticeStream = Pick<EventSource, 'close' | 'onopen' | 'onerror' | 'addEventListener'>;
export function connectNotices(publish: (state: NoticeState) => void, live: (notice: RoverNotice) => void,
  supplied?: { fetch?: typeof fetch; stream?: (url: string) => NoticeStream }) {
  const request = supplied?.fetch ?? fetch;
  const stream = supplied?.stream ?? ((url: string) => new EventSource(url));
  let stopped = false, source: NoticeStream | null = null, timer: ReturnType<typeof setTimeout> | undefined;
  let generation = 0, retries = 0, boot = '', notices: RoverNotice[] = [];
  let abort: AbortController | null = null;
  const emit = (status: string) => { if (!stopped) publish({ status, notices }); };
  function cleanup() { source?.close(); source = null; clearTimeout(timer); abort?.abort(); }
  async function load() {
    cleanup(); const run = ++generation; abort = new AbortController();
    const current = () => !stopped && generation === run;
    emit('Connecting to rover updates…');
    try {
      const options = { credentials: 'same-origin' as const, cache: 'no-store' as const,
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(10_000)]) };
      let response = await request('/api/rover/notices', options);
      if (response.status === 401) {
        const session = await request('/__local/session', { ...options, method: 'POST', headers: { 'X-Rovr-Local': '1' } });
        if (!current()) return;
        if (!session.ok) { emit('Backend sign-in is unavailable.'); return; }
        response = await request('/api/rover/notices', options);
      }
      if (!response.ok) throw new Error('Backend unavailable.');
      const value = await response.json();
      if (!current()) return;
      if (!/^[a-f0-9]{32}$/.test(value.backendBootId) || typeof value.cursor !== 'string' || !Array.isArray(value.notices)) throw new Error('Unsupported rover snapshot.');
      boot = value.backendBootId;
      notices = value.notices.map(parseNotice).slice(-200);
      if (notices.some(n => n.backendBootId !== boot)) throw new Error('Conflicting backend identity.');
      emit('Waiting for live updates…');
      source = stream(`/api/rover/notices/events?after=${encodeURIComponent(value.cursor)}`);
      source.onopen = () => { if (current()) { retries = 0; emit('Connected · live rover updates'); } };
      source.addEventListener('rover.notice', ((event: MessageEvent<string>) => {
        if (!current()) return;
        try {
          const notice = parseNotice(JSON.parse(event.data));
          if (notice.backendBootId !== boot) { void load(); return; }
          if (notices.some(n => n.id === notice.id)) return;
          notices = [...notices, notice].slice(-200);
          emit('Connected · live rover updates'); live(notice);
        } catch { void load(); }
      }) as EventListener);
      source.addEventListener('snapshot_required', () => { if (current()) void load(); });
      source.onerror = () => { if (current()) retry(); };
    } catch { if (current()) retry(); }
  }
  function retry() {
    cleanup(); ++generation; emit('Connection unavailable · reconnecting');
    timer = setTimeout(() => void load(), Math.min(1000 * 2 ** retries++, 15000));
  }
  void load();
  return { retry: () => { retries = 0; void load(); }, close: () => { stopped = true; ++generation; cleanup(); } };
}
