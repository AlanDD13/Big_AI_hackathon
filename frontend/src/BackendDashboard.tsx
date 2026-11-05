import { useEffect, useRef, useState } from 'react';
import { Bell, Box, CircleHelp, RefreshCw, ShieldAlert } from 'lucide-react';
import CameraWidget from './CameraWidget';
import FleetPanel from './FleetPanel';
import ConnectedManual from './ConnectedManual';
import PerceptionDetails from './PerceptionDetails';
import { perceptionFacts } from './perception';
import { visionFixture, visionStages } from './visionFixture';
import type { VisionStage } from './visionFixture';
import { connectBackend, number, objectFacts, text } from './backend';
import type { BackendObject, Connection } from './backend';
import './backend.css';

const label = (value: unknown, fallback?: string) => text(value, fallback).replaceAll('_', ' ');
const time = (value: unknown) => { const n = number(value); const d = new Date(n !== null ? n * 1000 : text(value, '')); return Number.isNaN(d.getTime()) ? 'Unavailable' : d.toLocaleString(); };
function Fact({ title, children }: { title: string; children: React.ReactNode }) { return <div><dt>{title}</dt><dd>{children}</dd></div>; }

export default function BackendDashboard({ view }: { view: 'objects' | 'fleet' | 'manual' }) {
  const [connection, setConnection] = useState<Connection>({ status: 'loading', message: 'Loading backend snapshot…', store: null, updatedAt: null });
  const [preview, setPreview] = useState(false);
  const [visionStage, setVisionStage] = useState<VisionStage>('Observed');
  const useFixtures = preview && view === 'objects';
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [readAlerts, setReadAlerts] = useState<Set<string>>(new Set());
  const client = useRef<ReturnType<typeof connectBackend> | null>(null);
  useEffect(() => {
    setConnection({ status: 'loading', message: 'Loading backend snapshot…', store: null, updatedAt: null });
    if (useFixtures) return;
    if (window.location.protocol === 'file:') {
      setConnection({ status: 'setup', message: 'Open the development server to connect. This standalone file supports Demo mode.', store: null, updatedAt: null });
      return;
    }
    client.current = connectBackend(setConnection);
    return () => client.current?.close();
  }, [useFixtures]);
  const store = useFixtures ? visionFixture(visionStage) : connection.store;
  const objects = store?.objects ?? [];
  const alerts = store?.alerts ?? [];
  const object = objects.find(o => o.objectId === selected);
  const filtered = objects.filter(o => `${o.objectId} ${text(o.detectorLabel, '')} ${text(o.state, '')}`.toLowerCase().includes(query.toLowerCase()));
  const alertKey = (id: string) => `${store?.backendBootId}:${id}`;
  const unread = alerts.filter(a => !readAlerts.has(alertKey(a.alertId))).length;
  function selectObject(id: string) { setSelected(id); }

  if (view === 'manual') return <ConnectedManual ready={Boolean(connection.store)} onRefresh={() => client.current?.retry()}/>;

  if (view === 'fleet') return <FleetPanel connection={connection} onRefresh={() => client.current?.retry()}/>;

  return <div className="backend-workspace">
    <div className="page-heading"><div><div className="eyebrow">WAREHOUSE / BACKEND</div><h1>Objects & alerts</h1><p>Review observations and evidence before planning a recovery.</p></div><div className="vision-source-actions"><button className="button secondary" aria-pressed={useFixtures} onClick={() => { setPreview(!preview); setVisionStage('Observed'); setSelected(preview ? null : visionFixture('Observed').objects[0].objectId); }}>{useFixtures ? 'Return to backend' : 'Review vision fixtures'}</button>{!useFixtures && <button className="button secondary" onClick={() => client.current?.retry()}><RefreshCw size={15}/>Refresh</button>}</div></div>
    {useFixtures ? <section className="vision-fixture-toolbar" aria-label="Vision fixture preview"><div><strong>Labelled vision fixtures</strong><span>No live camera or Grok response. All dispatch and pickup verification remain false.</span></div><div className="vision-stage-buttons">{visionStages.map(stage => <button key={stage} className="button secondary" aria-pressed={visionStage === stage} onClick={() => setVisionStage(stage)}>{stage}</button>)}</div></section> : <div className={`backend-status ${connection.status === 'connected' ? 'is-connected' : ''}`} role="status"><span className="status-dot"/><strong>{connection.message}</strong>{connection.updatedAt && <small>Last sync {new Date(connection.updatedAt).toLocaleTimeString()}</small>}</div>}
    {!useFixtures && (connection.status === 'retrying' || connection.status === 'setup') && store && <p className="backend-stale"><ShieldAlert size={16}/>Retained data may be stale. Eligibility below is the last reported backend state.</p>}
    {!useFixtures && connection.status === 'setup' && <details className="panel connection-help"><summary><CircleHelp size={16}/>Local connection setup</summary><p>No sign-in screen is needed. Run the backend and configure its address and server-side operator token in frontend/.env.local, then restart the development server. Never use a VITE_ prefix for tokens.</p><p>The dashboard uses a backend session cookie. Deployed installations need a session provisioned by their host.</p></details>}
    <section className="stats-grid" aria-label="Backend summary">
      {[['Objects', objects.length, useFixtures ? 'Fixture observations' : 'Backend observations'], ['Potential targets', objects.filter(o => o.potentialTarget === true).length, 'Perception gate only'], ['Dispatch eligible', objects.filter(o => o.dispatchEligible === true).length, 'Backend policy decision'], ['Unread alerts', unread, 'Local read markers']].map(([title, value, note]) => <article className="stat-card" key={title}><span>{title}</span><strong>{store ? value : '—'}</strong><small>{note}</small></article>)}
    </section>
    <div className="backend-grid"><div>
      <section className="panel objects-panel"><div className="panel-heading"><div className="row"><Box size={17}/><h2>Detected objects</h2><span className="count-pill">{objects.length}</span></div><label className="search"><input aria-label="Search backend objects" placeholder="Find an object…" value={query} onChange={e => setQuery(e.target.value)}/></label></div>
        <div className="object-table-wrap"><table><thead><tr><th>OBJECT</th><th>DETECTOR SCORE</th><th>CLASSIFICATION</th><th>POSITION</th><th>PERCEPTION</th><th>DISPATCH</th></tr></thead><tbody>{filtered.map(o => { const facts = objectFacts(o); const perception = perceptionFacts(o); return <tr key={o.objectId} className={selected === o.objectId ? 'selected-row' : ''}><td><button className="object-name" onClick={() => selectObject(o.objectId)}>{label(o.detectorLabel, 'Tracked object')}</button><small className="backend-id">{o.objectId}</small></td><td>{perception.detectorScore === null ? '—' : `${Math.round(perception.detectorScore * 100)}%`}</td><td><strong className="classification-category">{perception.category ? label(perception.category) : '—'}</strong><small className="classification-confidence">{perception.confidence ? `Confidence: ${perception.confidence}` : perception.classificationStatus === 'PENDING' ? 'Classification pending' : 'No classification'}</small></td><td>{facts.valid && facts.position ? `${facts.position[0].toFixed(2)}, ${facts.position[1].toFixed(2)} m` : 'Unverified'}</td><td><span className={`badge ${facts.potential ? 'green' : 'neutral'}`}>{facts.potential ? 'Potential target' : (o.state ?? o.status) === 'OBSERVING' ? 'Observing' : label(o.state ?? o.status, 'Observing')}</span></td><td><span className={`badge ${facts.dispatch ? 'green' : 'gold'}`}>{facts.dispatch ? 'Eligible' : 'Not eligible'}</span></td></tr>; })}</tbody></table></div>
        {filtered.length === 0 && <div className="empty-message">{!store ? 'Waiting for the backend. Demo objects are available in Demo mode.' : query ? 'No matching objects.' : 'No observations received yet.'}</div>}
      </section>
      <section className="panel backend-alerts"><div className="panel-heading"><div className="row"><Bell size={17}/><h2>Alerts</h2><span className="count-pill">{alerts.length}</span></div><button className="text-button" disabled={!unread} onClick={() => setReadAlerts(new Set(alerts.map(a => alertKey(a.alertId))))}>Mark all read</button></div>
        <div className="backend-alert-list">{[...alerts].reverse().map(a => { const revoked = `${a.kind ?? a.type}`.includes('revoked'); const isRead = readAlerts.has(alertKey(a.alertId)); return <article key={a.alertId} className={`backend-alert ${revoked ? 'revoked' : ''} ${isRead ? 'is-read' : ''}`}><ShieldAlert size={18}/><div><strong>{revoked ? 'Target eligibility revoked' : label(a.kind ?? a.type)}</strong>{a.objectId !== null ? <button className="text-button backend-id" onClick={() => { selectObject(a.objectId!); setReadAlerts(r => new Set([...r, alertKey(a.alertId)])); }}>{a.objectId}</button> : <span className="backend-id">{typeof a.cameraId === 'string' ? `Camera ${a.cameraId}` : 'System alert'}</span>}<p>{label(a.reason, 'No reason supplied')}{a.action ? ` · ${label(a.action)}` : ''}</p><small>{a.createdAt ? time(a.createdAt) : 'Backend alert · timestamp unavailable'}</small></div><span className={`badge ${isRead ? 'neutral' : 'blue'}`}>{isRead ? 'Read' : 'New'}</span></article>; })}
        {!alerts.length && <p className="empty-message">{useFixtures ? 'No alerts in this fixture scenario.' : store ? 'No backend alerts yet.' : 'Alerts will appear after connecting.'}</p>}</div><p className="backend-footnote">Read markers are local to this view. Reading an alert sends no acknowledgement or robot command.</p>
      </section>
      <section className="panel backend-boundary"><h2>{useFixtures ? 'Fixture context' : 'Connected operations'}</h2><p>{useFixtures ? 'This scenario contains no robot or mission records.' : store ? `${store.robots.length} robots · ${store.missions.length} missions reported by the backend.` : 'Robot and mission status are unavailable.'} Mission dispatch and live manual control are awaiting their API integration.</p><button className="button secondary" disabled>Dispatch unavailable</button></section>
    </div><aside className="backend-detail-column">
      <section className="panel backend-detail" aria-label="Object details"><div className="panel-heading"><h2>Object details</h2><span className="badge neutral">Read-only</span></div>{object ? <ObjectDetails key={`${store?.backendBootId}:${object.objectId}`} object={object}/> : <div className="empty-message">{selected ? 'This object is no longer present in the current snapshot.' : 'Select an object or alert to inspect its evidence.'}</div>}</section>
      <CameraWidget enabled={!useFixtures && Boolean(connection.store)} onRefreshSession={() => client.current?.retry()}/>
    </aside></div>
    <footer className="page-footer"><span>{useFixtures ? 'Labelled contract fixture · no live observations' : 'Backend data only · no simulated robot telemetry'}</span><span>ROVR / OPERATIONS CONSOLE</span></footer>
  </div>;
}

function ObjectDetails({ object }: { object: BackendObject }) {
  const f = objectFacts(object);
  const [evidence, setEvidence] = useState<{ url: string; state: string }>({ url: '', state: '' });
  const rawUrl = typeof object.evidenceUrl === 'string' && /^\/api\/evidence\/[^/?#]+$/.test(object.evidenceUrl) ? object.evidenceUrl : '';
  const evidenceState = evidence.url === rawUrl ? evidence.state : '';
  return <div className="backend-detail-body"><span className="eyebrow">{label(object.state ?? object.status, 'OBSERVING')}</span><h3>{label(object.detectorLabel, 'Tracked object')}</h3><p className="backend-id">{object.objectId}</p>
    <div className="eligibility-cards"><div><span>Potential target</span><strong>{f.potential ? 'Yes' : 'No'}</strong><small>Perception gate</small></div><div><span>Dispatch eligible</span><strong>{f.dispatch ? 'Yes' : 'No'}</strong><small>Backend policy</small></div></div>
    <p className="backend-reason">{label(object.eligibilityReason ?? object.exclusionReason, 'No eligibility reason supplied')}</p>
    <h4>Stationary observation</h4><div className="stationarity-progress"><span style={{ width: `${Math.min(100, Math.max(0, (f.seconds ?? 0) / 5 * 100))}%` }}/></div><p className="small muted">{f.seconds === null ? 'Duration unavailable' : `${f.seconds.toFixed(1)} / 5.0 seconds`}{f.samples !== null ? ` · ${f.samples} / 10 samples` : ' · Sample count unavailable'}</p><p className="small muted">Progress is informational; only the backend determines eligibility.</p>
    <h4>Position & validity</h4><dl className="backend-facts"><Fact title="Position">{f.position ? `${f.position[0].toFixed(3)}, ${f.position[1].toFixed(3)} m` : 'Unknown'}</Fact><Fact title="Mapping valid">{f.valid ? 'Yes' : 'No / unverified'}</Fact><Fact title="Calibration valid">{f.calibrated ? 'Yes' : 'No / unverified'}</Fact><Fact title="Position uncertainty">{number(f.mapping.positionErrorM) === null ? 'Unknown' : `${f.mapping.positionErrorM} m`}</Fact><Fact title="Invalid reason">{label(f.mapping.invalidReason, 'None supplied')}</Fact><Fact title="Map / version">{text(f.mapping.mapId)} / {text(f.mapping.mapVersion)}</Fact></dl>
    <PerceptionDetails object={object}/>
    <h4>Evidence</h4>{rawUrl && evidenceState !== 'gone' ? <img className="backend-evidence" src={rawUrl} alt="Exact object evidence frame" onError={() => setEvidence({ url: rawUrl, state: 'gone' })}/> : <div className="evidence-empty">{rawUrl ? 'Exact frame unavailable or expired.' : 'No exact evidence frame supplied.'}</div>}
    <dl className="backend-facts"><Fact title="Captured">{time(object.capturedAt ?? f.mapping.capturedAt)}</Fact><Fact title="Camera / frame">{text(object.cameraId)} / {String(object.frameId ?? 'Unavailable')}</Fact><Fact title="Backend revision">{object.backendRevision}</Fact></dl>
  </div>;
}
