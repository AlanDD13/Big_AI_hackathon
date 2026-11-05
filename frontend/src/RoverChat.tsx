import { useEffect, useRef, useState } from 'react';
import { CheckCheck, Hand, MapPin, Navigation, RefreshCw, ScanLine, Tag, Volume2, VolumeX } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { connectNotices, noticeLabel } from './roverNotices';
import type { NoticeKind, RoverNotice } from './roverNotices';
import { playNotice, stopSpeaking } from './voice';

const icons: Record<NoticeKind, LucideIcon> = { found: ScanLine, classified: Tag, going: Navigation, grabbed: Hand, finished: CheckCheck, manual: MapPin };
export default function RoverChat() {
  const [notices, setNotices] = useState<RoverNotice[]>([]);
  const [status, setStatus] = useState('Connecting to rover updates…');
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [voiceOn, setVoiceOn] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState('Enable voice to hear new completed steps.');
  const [model, setModel] = useState('');
  const [voiceConfigured, setVoiceConfigured] = useState<boolean | null>(null);
  const client = useRef<ReturnType<typeof connectNotices> | null>(null);
  const enabled = useRef(false), mounted = useRef(true), playing = useRef(false);
  const queue = useRef<RoverNotice[]>([]);
  const latest = useRef<HTMLDivElement | null>(null);
  async function drain() {
    if (!enabled.current || playing.current || !queue.current.length) return;
    playing.current = true;
    const notice = queue.current.shift()!;
    setSpeakingId(notice.id);
    try { await playNotice(notice.audioUrl!); }
    catch (error) {
      enabled.current = false; queue.current = [];
      if (mounted.current) { setVoiceOn(false); setVoiceStatus((error as Error).message); }
    } finally {
      playing.current = false;
      if (mounted.current) { setSpeakingId(null); void drain(); }
    }
  }
  useEffect(() => {
    mounted.current = true;
    client.current = connectNotices(value => { setNotices(value.notices); setStatus(value.status); }, notice => {
      if (enabled.current) { queue.current.push(notice); void drain(); }
    });
    return () => { mounted.current = false; enabled.current = false; queue.current = []; client.current?.close(); stopSpeaking(); };
  }, []);
  useEffect(() => { latest.current?.scrollIntoView({ block: 'nearest' }); }, [notices.length]);
  useEffect(() => {
    if (!status.startsWith('Connected')) return;
    const controller = new AbortController();
    void fetch('/api/models', { credentials: 'same-origin', signal: controller.signal }).then(r => r.ok ? r.json() : null).then(value => {
      if (value?.provider === 'xai' && typeof value.vision === 'string' && value.vision.startsWith('grok-')) setModel(value.vision);
      if (typeof value?.speech?.configured === 'boolean') setVoiceConfigured(value.speech.configured);
    }).catch(() => {});
    return () => controller.abort();
  }, [status]);
  function toggleVoice() {
    enabled.current = !enabled.current; setVoiceOn(enabled.current);
    if (!enabled.current) { queue.current = []; stopSpeaking(); setVoiceStatus('Voice paused. Updates remain visible.'); }
    else setVoiceStatus('Grok voice enabled · new completed steps play automatically.');
  }
  return <section className="operator-chat" aria-labelledby="rover-chat-title">
    <div className="panel-heading"><div className="row"><Volume2 size={16}/><h2 id="rover-chat-title">Rover</h2></div><div className="row">
      <button className="icon-button" aria-label="Reconnect rover updates" onClick={() => client.current?.retry()}><RefreshCw size={15}/></button>
      <button className={`button ${voiceOn ? 'secondary' : 'primary'}`} disabled={voiceConfigured !== true} aria-pressed={voiceOn} onClick={toggleVoice}>{voiceOn ? <VolumeX size={14}/> : <Volume2 size={14}/>} {voiceOn ? 'Mute voice' : 'Enable voice'}</button>
    </div></div>
    <p className="operator-voice-status" role="status">{status}{model ? ` · Configured: ${model} via xAI` : ''}<br/>{voiceConfigured === false ? 'Voice unavailable · configure XAI_API_KEY on the backend.' : speakingId ? 'Speaking the latest completed step…' : voiceStatus}</p>
    <div className="operator-log" aria-live="polite">
      {!notices.length && <article className="operator-bubble"><ScanLine size={18}/><p>{status.startsWith('Connected') ? 'Ready. Waiting for a camera observation.' : 'Waiting for the backend connection.'}</p></article>}
      {notices.map(notice => { const Icon = icons[notice.kind]; return <article key={notice.id} className={`operator-bubble ${notice.id === speakingId ? 'speaking' : ''} ${notice.kind === 'manual' ? 'manual' : ''}`}>
        <div className={`operator-bubble-icon ${notice.kind === 'manual' ? 'gold' : 'green'}`}><Icon size={15}/></div>
        <div><div className="operator-bubble-label"><span>{noticeLabel[notice.kind]}</span></div><p>{notice.text}</p>{notice.kind === 'classified' && <small>{notice.classificationModel ? `Used ${notice.classificationModel} · ` : ''}{notice.classificationConfidence ? `${notice.classificationConfidence.toLowerCase()} classification confidence` : ''}<br/></small>}{notice.grabbabilityScore != null && <small>Grabbability: {Math.round(notice.grabbabilityScore * 100)}% · model estimate</small>}</div>
      </article>; })}<div ref={latest}/>
    </div><p className="operator-chat-note">Live backend messages. Voice uses the native Grok Voice API.</p>
  </section>;
}
