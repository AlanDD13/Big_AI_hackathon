import { useEffect, useRef, useState } from 'react';
import { Bot, Check, ChevronRight, CircleHelp, Edit3, Network, Plus, Radio, ShieldAlert, Square, Trash2, X } from 'lucide-react';
import CameraWidget from './CameraWidget';
import { makeProfile, PROFILE_KEY, readProfiles } from './fleet';
import type { RobotProfile } from './fleet';
import type { Connection } from './backend';
import './fleet.css';

export default function FleetPanel({ connection, onRefresh }: { connection: Connection; onRefresh: () => void | Promise<void> }) {
  const [profiles, setProfiles] = useState<RobotProfile[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ id: string; name: string; serial: string } | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const modal = useRef<HTMLElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const chosen = profiles.find(p => p.id === selected);
  useEffect(() => {
    const reload = () => {
      try {
        const saved = readProfiles(localStorage.getItem(PROFILE_KEY)); setProfiles(saved);
        setSelected(id => saved.some(p => p.id === id) ? id : saved[0]?.id ?? null);
      } catch { setError('Saved setups could not be loaded. They have not been replaced; browser storage may be unavailable or contain an older format.'); }
    };
    const changed = (event: StorageEvent) => { if (event.key === PROFILE_KEY || event.key === null) reload(); };
    reload(); window.addEventListener('storage', changed);
    return () => window.removeEventListener('storage', changed);
  }, []);
  useEffect(() => {
    if (!editor) return;
    restoreFocus.current = document.activeElement as HTMLElement;
    const first = modal.current?.querySelector<HTMLInputElement>('input'); first?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setEditor(null);
      if (event.key !== 'Tab') return;
      const items = Array.from(modal.current?.querySelectorAll<HTMLElement>('input,button:not(:disabled)') ?? []);
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keydown);
    return () => { document.removeEventListener('keydown', keydown); restoreFocus.current?.focus(); };
  }, [Boolean(editor)]);
  function persist(next: RobotProfile[]) {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(next));
    setProfiles(next);
  }
  function save(event: React.FormEvent) {
    event.preventDefault(); if (!editor) return;
    try {
      const latest = readProfiles(localStorage.getItem(PROFILE_KEY));
      const profile = makeProfile(editor, latest);
      persist([...latest.filter(p => p.id !== profile.id), profile]);
      setSelected(profile.id); setEditor(null); setError(''); setNotice('Setup saved on this browser. No robot connection was opened.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save this setup.'); }
  }
  function remove(profile: RobotProfile) {
    try { const next = readProfiles(localStorage.getItem(PROFILE_KEY)).filter(p => p.id !== profile.id); persist(next); setSelected(next[0]?.id ?? null); setNotice(`${profile.name} setup removed locally.`); setError(''); }
    catch { setError('Browser storage is unavailable. This setup has not been removed.'); }
  }
  function edit(profile?: RobotProfile) { setError(''); setEditor(profile ?? { id: crypto.randomUUID(), name: '', serial: '' }); }
  const reported = connection.store?.robots ?? [];
  return <div className="backend-workspace fleet-workspace">
    <div className="page-heading"><div><div className="eyebrow">WAREHOUSE / FLEET</div><h1>Robot connections</h1><p>Prepare your EP Core setup and review connection readiness.</p></div><button className="button primary" onClick={() => edit()}><Plus size={16}/>Add robot setup</button></div>
    <div className="fleet-connection-path" aria-label="Connection path"><div><Network size={19}/><strong>Dashboard & backend</strong><small>This browser uses HTTP</small></div><ChevronRight size={17}/><div><Radio size={19}/><strong>Robot bridge · SDK host</strong><small>One active host on the LAN</small></div><ChevronRight size={17}/><div><Bot size={19}/><strong>EP Core · rmepcore</strong><small>Shared router · STA / UDP</small></div></div>
    <div className="fleet-readiness" role="status"><ShieldAlert size={18}/><div><strong>Connection API pending</strong><p>Saved setups do not connect a robot. The robot bridge must discover and verify its serial before reporting a connection.</p></div><button className="text-button" onClick={onRefresh}>Refresh backend</button></div>
    {notice && <div className="fleet-notice" role="status"><Check size={16}/>{notice}</div>}
    {error && !editor && <div className="error-banner" role="alert">{error}</div>}
    <div className="fleet-setup-grid"><section className="panel"><div className="panel-heading"><div className="row"><Bot size={17}/><h2>Saved robot setups</h2><span className="count-pill">{profiles.length}</span></div><span className="badge neutral">This browser</span></div>
      <div className="fleet-profile-list">{profiles.map(p => <button className={`fleet-profile ${selected === p.id ? 'chosen' : ''}`} aria-pressed={selected === p.id} key={p.id} onClick={() => { setSelected(p.id); setNotice(''); }}><span className="fleet-robot-icon"><Bot size={24}/></span><span><strong>{p.name}</strong><small>EP Core · SN {p.serial}</small><span className="badge gold">Setup only</span></span><ChevronRight size={16}/></button>)}
      {!profiles.length && <div className="fleet-empty"><div className="fleet-robot-icon"><Bot size={30}/></div><h3>Add your first EP Core</h3><p>Use the serial on its label or in the DJI app. The bridge discovers its LAN address; you do not need to guess an IP.</p><button className="button secondary" onClick={() => edit()}><Plus size={15}/>Add robot setup</button></div>}</div>
      <div className="fleet-backend-report"><strong>Backend fleet</strong><span className={`badge ${connection.status === 'connected' ? 'green' : 'gold'}`}>{connection.status === 'connected' ? 'Backend online' : 'Backend unavailable / syncing'}</span><p>{connection.store ? `${reported.length} robot records in the last backend snapshot.` : 'No backend snapshot available.'} Robot registration and telemetry integration are pending.</p></div>
    </section><section className="panel fleet-selected" aria-label="Selected robot connection"><div className="panel-heading"><h2>{chosen?.name ?? 'Robot details'}</h2><span className="badge neutral">{chosen ? 'Connection unverified' : 'No selection'}</span></div>
      {chosen ? <><div className="fleet-device-heading"><div className="fleet-robot-icon"><Bot size={35}/></div><div><strong>DJI RoboMaster EP Core</strong><small>rmepcore · chassis, arm & gripper</small></div><button className="icon-button" aria-label={`Edit ${chosen.name} setup`} onClick={() => edit(chosen)}><Edit3 size={16}/></button></div>
        <dl className="backend-facts fleet-connection-facts"><div><dt>Robot serial</dt><dd>{chosen.serial}<small>Entered locally · bridge verification pending</small></dd></div><div><dt>Network mode</dt><dd>STA · shared router / LAN</dd></div><div><dt>SDK transport</dt><dd>UDP · managed by robot bridge</dd></div><div><dt>Robot address</dt><dd>Awaiting serial-based discovery</dd></div><div><dt>SDK host</dt><dd>Robot owner’s host<small>Mac in team tests; Windows only after an agreed host handover</small></dd></div><div><dt>Control owner</dt><dd>Unknown</dd></div><div><dt>Last telemetry</dt><dd>Unavailable</dd></div></dl>
        <div className="fleet-telemetry"><div><span>Battery</span><strong>—</strong></div><div><span>Position</span><strong>—</strong></div><div><span>Arm / gripper</span><strong>—</strong></div><div><span>Stop confirmation</span><strong>Unknown</strong></div></div>
        <div className="fleet-actions"><button className="button secondary" disabled>Connect unavailable</button><button className="button secondary" disabled>Take control</button><button className="button secondary" disabled>Release</button><button className="button danger" disabled><Square size={14}/>Stop robot</button><button className="button secondary" disabled>Re-arm</button></div><p className="fleet-action-note">Connection and control actions become available after backend and bridge integration. No commands are sent from this screen.</p><button className="text-button fleet-remove" onClick={() => remove(chosen)}><Trash2 size={13}/>Remove local setup</button>
      </> : <div className="fleet-empty"><Bot size={32}/><h3>No robot selected</h3><p>Add or select a setup to review the serial, connection path and telemetry placeholders.</p></div>}
    </section><aside className="fleet-connection-guide"><section className="panel"><div className="panel-heading"><CircleHelp size={17}/><h2>Before connecting</h2></div><ol><li>Join the robot to the shared router using DJI’s STA setup.</li><li>Keep the robot bridge’s SDK host on that LAN.</li><li>Close the DJI app and other robot controllers.</li><li>Confirm the robot’s serial from its label or app.</li><li>Let the bridge verify the discovered serial and firmware.</li></ol><p>Only one SDK host is allowed in the team’s current setup. This browser never opens a DJI SDK session.</p><details><summary>Connection reference</summary><p>Serial discovery uses LAN broadcasts. DHCP addresses can change; keep the SDK host and robot on the same subnet.</p><a href="https://robomaster-dev.readthedocs.io/en/latest/python_sdk/connection.html" target="_blank" rel="noreferrer">DJI connection guide</a></details></section><CameraWidget enabled={Boolean(connection.store)} onRefreshSession={onRefresh}/></aside></div>
    {editor && <div className="modal-backdrop" onClick={() => setEditor(null)}><section ref={modal} className="modal fleet-setup-modal" role="dialog" aria-modal="true" aria-labelledby="fleet-setup-title" onClick={e => e.stopPropagation()}><form onSubmit={save}><div className="modal-heading"><div><div className="eyebrow">LOCAL CONNECTION SETUP</div><h2 id="fleet-setup-title">{profiles.some(p => p.id === editor.id) ? 'Edit robot setup' : 'Add robot setup'}</h2></div><button type="button" className="icon-button" aria-label="Close robot setup" onClick={() => setEditor(null)}><X size={20}/></button></div><p className="modal-intro">Save a profile for a DJI RoboMaster EP Core on the shared router. This does not pair or connect the robot.</p><div className="fleet-form"><label>Robot name<input value={editor.name} maxLength={48} placeholder="e.g. Warehouse rover" onChange={e => setEditor({ ...editor, name: e.target.value })} required/></label><label>Robot serial number<input value={editor.serial} maxLength={14} spellCheck={false} autoComplete="off" placeholder="14 letters and digits" aria-describedby="serial-help" onChange={e => setEditor({ ...editor, serial: e.target.value })} required/><small id="serial-help">Read it from the robot’s physical label or DJI app. The bridge will verify the device matches.</small></label><div className="fleet-fixed-mode"><span>Model <strong>EP Core / rmepcore</strong></span><span>Connection <strong>STA / UDP · shared router</strong></span></div></div>{error && <p className="inline-error" role="alert">{error}</p>}<div className="modal-footer"><span>Saved only in this browser</span><button className="button primary" type="submit">Save setup<Check size={15}/></button></div></form></section></div>}
  </div>;
}
