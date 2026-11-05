import { useEffect, useRef, useState } from 'react';
import { CheckCheck, Hand, MapPin, Navigation, ScanLine, Tag, Volume2, VolumeX } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { noticeLabel, previewNotices } from './roverNotices';
import type { NoticeKind } from './roverNotices';
import { canSpeak, speak, stopSpeaking } from './voice';

const icons: Record<NoticeKind, LucideIcon> = {
  found: ScanLine,
  classified: Tag,
  going: Navigation,
  grabbed: Hand,
  finished: CheckCheck,
  manual: MapPin,
};

export default function RoverChat() {
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [voiceReady, setVoiceReady] = useState(canSpeak());
  const stop = useRef(false);
  const bubble = useRef<HTMLElement | null>(null);
  const playing = speakingId !== null;
  const speaking = previewNotices.find(notice => notice.id === speakingId);
  useEffect(() => { bubble.current?.scrollIntoView({ block: 'nearest' }); }, [speakingId]);

  async function play() {
    if (!canSpeak()) {
      setVoiceReady(false);
      return;
    }
    stop.current = false;
    for (const notice of previewNotices) {
      if (stop.current) break;
      setSpeakingId(notice.id);
      await speak(notice.text);
    }
    setSpeakingId(null);
  }

  function halt() {
    stop.current = true;
    stopSpeaking();
    setSpeakingId(null);
  }

  return <section className="operator-chat" aria-labelledby="rover-chat-title">
    <div className="panel-heading">
      <div className="row"><Volume2 size={16}/><h2 id="rover-chat-title">Rover</h2></div>
      {playing
        ? <button className="button secondary" onClick={halt}><VolumeX size={14}/>Stop</button>
        : <button className="button primary" onClick={() => void play()}><Volume2 size={14}/>Play voice</button>}
    </div>
    <p className="operator-voice-status" role="status">
      {speaking ? <>Speaking. {speaking.text}</> : voiceReady ? 'Browser voice until the backend sends Grok audio.' : 'Voice playback is unavailable in this browser.'}
    </p>
    <div className="operator-log">
      {previewNotices.map((notice, index) => {
        const Icon = icons[notice.kind];
        const active = notice.id === speakingId;
        const freshScene = index > 0 && notice.kind === 'found';
        return <div key={notice.id}>
          {freshScene && <div className="operator-scene">Next object</div>}
          <article ref={active ? bubble : undefined} className={`operator-bubble ${active ? 'speaking' : ''} ${notice.kind === 'manual' ? 'manual' : ''}`}>
            <div className={`operator-bubble-icon ${notice.kind === 'manual' ? 'gold' : 'green'}`}><Icon size={15}/></div>
            <div>
              <div className="operator-bubble-label">
                <span>{noticeLabel[notice.kind]}</span>
                {active && <span className="voice-bars" aria-hidden="true"><span/><span/><span/></span>}
              </div>
              <p>{notice.text}</p>
            </div>
          </article>
        </div>;
      })}
    </div>
    <p className="operator-chat-note">Preview lines. Backend events will replace them.</p>
  </section>;
}
