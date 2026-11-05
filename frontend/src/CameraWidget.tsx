import { useEffect, useRef, useState } from 'react';
import { RefreshCw, Video, VideoOff } from 'lucide-react';
import { CAMERA_FEED, CAMERA_STATUS, cameraRetryDelay, parseCameraStatus } from './camera';
import './camera.css';

type Phase = 'idle' | 'loading' | 'unverified' | 'disabled' | 'unavailable' | 'session';
type CameraWidgetProps = {
  enabled?: boolean;
  onRefreshSession?: () => void | Promise<void>;
  title?: string;
  statusUrl?: string;
  feedUrl?: string;
  waitingMessage?: string;
  quiet?: boolean;
};
export default function CameraWidget({
  enabled = false,
  onRefreshSession,
  title = 'Main camera',
  statusUrl = CAMERA_STATUS,
  feedUrl = CAMERA_FEED,
  waitingMessage,
  quiet = false,
}: CameraWidgetProps) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [message, setMessage] = useState('');
  const [retry, setRetry] = useState(0);
  const refreshSession = useRef(onRefreshSession);
  refreshSession.current = onRefreshSession;
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  useEffect(() => {
    let closed = false, failures = 0, renewedSession = false;
    let retryTimer: number | undefined, deadline: number | undefined, lifetime: number | undefined;
    let request: AbortController | null = null, objectUrl: string | null = null;
    const clearFrame = () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = null;
      if (!closed) setImageUrl(null);
    };
    async function connect() {
      if (closed) return;
      window.clearTimeout(retryTimer); window.clearTimeout(deadline); window.clearTimeout(lifetime);
      clearFrame(); request?.abort();
      const controller = new AbortController(); request = controller;
      const current = () => !closed && request === controller;
      setPhase('loading'); setMessage('Connecting to camera…');
      deadline = window.setTimeout(() => controller.abort(), 5000);
      try {
        const statusResponse = await fetch(statusUrl, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
        if (!current()) return;
        if (statusResponse.status === 401) {
          window.clearTimeout(deadline);
          if (!renewedSession && refreshSession.current) {
            renewedSession = true; await refreshSession.current();
            if (current()) void connect();
          } else { setPhase('session'); setMessage('Operator session unavailable. Refresh the connection and retry.'); }
          return;
        }
        if (statusResponse.status === 404) { window.clearTimeout(deadline); setPhase('disabled'); setMessage(waitingMessage || 'Camera proxy is not installed on this backend.'); return; }
        if (!statusResponse.ok) throw new Error('Camera status unavailable.');
        const status = parseCameraStatus(await statusResponse.json());
        if (!status.configured) { window.clearTimeout(deadline); setPhase('disabled'); setMessage(waitingMessage || 'Camera source is not configured on the backend.'); return; }
        const response = await fetch(feedUrl, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
        if (!current()) return;
        if (!response.ok || !response.body) throw new Error('Camera feed unavailable.');
        const media = response.headers.get('content-type')?.split(';')[0].trim().toLowerCase();
        if (media !== 'image/jpeg' && media !== 'multipart/x-mixed-replace') throw new Error('Unsupported camera image format.');
        const reader = response.body.getReader();
        let pending = new Uint8Array(0), decoded = false;
        const display = async (bytes: Uint8Array) => {
          const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: 'image/jpeg' }));
          const probe = new Image(); probe.src = url;
          try { await probe.decode(); } catch { URL.revokeObjectURL(url); throw new Error('Camera image could not be decoded.'); }
          if (!current() || controller.signal.aborted) { URL.revokeObjectURL(url); return; }
          const previous = objectUrl; objectUrl = url; setImageUrl(url);
          if (previous) URL.revokeObjectURL(previous);
          if (!decoded) {
            decoded = true; failures = 0; renewedSession = false; window.clearTimeout(deadline);
            setPhase('unverified'); setMessage('Source freshness unverified');
            lifetime = window.setTimeout(() => { if (current()) void connect(); }, 55_000);
          }
        };
        try {
          while (current()) {
            const { done, value } = await reader.read();
            if (done) {
              if (media === 'image/jpeg' && pending.length) await display(pending);
              if (!decoded) throw new Error('No decoded camera frame received.');
              // A completed stream is no longer receiving images. Clear before reconnecting.
              if (media !== 'image/jpeg') { clearFrame(); setPhase('loading'); setMessage('Refreshing camera image…'); }
              window.clearTimeout(lifetime);
              retryTimer = window.setTimeout(() => { if (current()) void connect(); }, 1000);
              return;
            }
            const combined = new Uint8Array(pending.length + value.length); combined.set(pending); combined.set(value, pending.length); pending = combined;
            if (pending.length > 8 * 1024 * 1024 + 8192) throw new Error('Camera image exceeds display limit.');
            if (media === 'multipart/x-mixed-replace') {
              // The proxy validates multipart framing. Extract complete JPEG payloads;
              // byte arrival and decoding establish transport only, never capture age.
              for (;;) {
                let start = -1, end = -1;
                for (let i = 0; i + 1 < pending.length; i++) {
                  if (start < 0 && pending[i] === 255 && pending[i + 1] === 216) start = i;
                  if (start >= 0 && pending[i] === 255 && pending[i + 1] === 217) { end = i + 2; break; }
                }
                if (end < 0) break;
                await display(pending.slice(start, end)); pending = pending.slice(end);
              }
            }
          }
        } finally { await reader.cancel().catch(() => {}); }
      } catch {
        if (!current()) return;
        controller.abort(); clearFrame(); window.clearTimeout(deadline); window.clearTimeout(lifetime);
        setPhase('unavailable'); setMessage(waitingMessage || 'Camera disconnected or image unavailable. Retrying…');
        retryTimer = window.setTimeout(() => { void connect(); }, cameraRetryDelay(failures++));
      }
    }
    if (enabled) void connect(); else { setPhase('idle'); setMessage(''); }
    return () => {
      closed = true; request?.abort(); window.clearTimeout(retryTimer);
      window.clearTimeout(deadline); window.clearTimeout(lifetime); clearFrame();
    };
  }, [enabled, retry, statusUrl, feedUrl, waitingMessage]);

  const titleId = `${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-title`;
  const showing = enabled && phase === 'unverified';
  const waiting = quiet && (phase === 'disabled' || phase === 'unavailable');
  const badge = !enabled ? 'Not connected' : phase === 'loading' ? 'Connecting' : phase === 'unverified' ? 'Unverified' : phase === 'session' ? 'Session required' : waiting ? 'Waiting' : phase === 'disabled' ? 'Not configured' : 'Disconnected';
  return <section className="panel main-camera-widget" aria-labelledby={titleId}>
    <div className="panel-heading"><div className="row"><Video size={16}/><h2 id={titleId}>{title}</h2></div><span className={`badge ${showing ? 'gold' : 'neutral'}`}>{badge}</span></div>
    {!enabled ? <div className="main-camera-empty" role="img" aria-label={`${title} feed, empty`}/> : <>
      <div className="camera-preview">
        {imageUrl !== null && <img src={imageUrl} alt={`${title} preview; source capture freshness unverified`} className={showing ? '' : 'camera-loading-image'}/>}
        {!showing && <div className="camera-placeholder"><VideoOff size={24}/><p role="status">{message || 'Camera unavailable.'}</p></div>}
      </div>
      <div className="camera-footer"><small>{showing ? 'Source freshness unverified · display only' : 'No capture freshness or robot authority claimed'}</small><button className="icon-button" aria-label={`Retry ${title.toLowerCase()}`} onClick={() => setRetry(n => n + 1)}><RefreshCw size={15}/></button></div>
    </>}
  </section>;
}
