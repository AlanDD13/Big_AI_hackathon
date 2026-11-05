import { useEffect, useRef, useState } from 'react';
import type { PointerEvent } from 'react';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Gamepad2, RotateCcw, RotateCw, Square } from 'lucide-react';
import CameraWidget from './CameraWidget';
import { createManualClient } from './manualBackend';
import type { ManualState, ReleaseReason } from './manualBackend';
import './connectedManual.css';

export default function ConnectedManual({ ready, onRefresh }: { ready: boolean; onRefresh: () => void | Promise<void> }) {
  const [rover, setRover] = useState('rover-1');
  const [target, setTarget] = useState('rover-1');
  const [state, setState] = useState<ManualState>({ status: null, lease: null, pending: false, uncertain: true, message: 'Waiting for backend session…', receivedAt: 0 });
  const client = useRef<ReturnType<typeof createManualClient> | null>(null);
  const workspace = useRef<HTMLElement>(null);
  const keys = useRef(new Set<string>());
  const pointer = useRef({ strafe: 0, forward: 0, turn: 0 });
  const [pressed, setPressed] = useState('');
  const clear = () => { keys.current.clear(); pointer.current = { strafe: 0, forward: 0, turn: 0 }; setPressed(''); };
  function release(reason: ReleaseReason = 'user_release') { clear(); void client.current?.release(reason); }
  useEffect(() => {
    clear();
    if (!ready) { setState({ status: null, lease: null, pending: false, uncertain: true, message: 'Waiting for backend session…', receivedAt: 0 }); return; }
    const transport = createManualClient(rover, setState); client.current = transport;
    void transport.poll();
    const statusTimer = window.setInterval(() => { void transport.poll(); }, 500);
    const tickTimer = window.setInterval(() => {
      const k = keys.current, held = k.has('Space');
      void transport.tick(held ? Number(k.has('KeyD')) - Number(k.has('KeyA')) : pointer.current.strafe,
        held ? Number(k.has('KeyW')) - Number(k.has('KeyS')) : pointer.current.forward,
        held ? Number(k.has('KeyE')) - Number(k.has('KeyQ')) : pointer.current.turn);
    }, 100);
    return () => { keys.current.clear(); pointer.current = { strafe: 0, forward: 0, turn: 0 }; window.clearInterval(statusTimer); window.clearInterval(tickTimer); transport.close(); client.current = null; };
  }, [ready, rover]);
  useEffect(() => { if (!state.lease) clear(); }, [state.lease]);
  useEffect(() => {
    function down(e: KeyboardEvent) {
      if (!workspace.current?.contains(document.activeElement) || (e.target instanceof HTMLElement && e.target.closest('input,button,textarea,select'))) return;
      if (['Space','KeyW','KeyA','KeyS','KeyD','KeyQ','KeyE'].includes(e.code)) { e.preventDefault(); if (client.current?.getState().lease) keys.current.add(e.code); }
      if (e.code === 'Escape') { e.preventDefault(); release(); }
    }
    function up(e: KeyboardEvent) { keys.current.delete(e.code); if (e.code === 'Space') release(); }
    const blur = () => release('blur');
    const visibility = () => { if (document.hidden) release('tab_hidden'); };
    const offline = () => { clear(); void client.current?.stop(); };
    window.addEventListener('keydown', down); window.addEventListener('keyup', up); window.addEventListener('blur', blur); window.addEventListener('offline', offline); document.addEventListener('visibilitychange', visibility);
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.removeEventListener('blur', blur); window.removeEventListener('offline', offline); document.removeEventListener('visibilitychange', visibility); };
  }, []);
  const status = state.status, simulator = status?.executionMode === 'simulator';
  const canAcquire = simulator && status?.stage === 'IDLE' && status.telemetryFresh && !status.backendStopLocked && !state.uncertain && !state.pending && !state.lease;
  const canRearm = simulator && status?.stage === 'STOPPED' && status.stationaryConfirmed && status.telemetryFresh && !state.uncertain && !state.pending;
  const caps = state.lease?.caps ?? status?.caps;
  function hold(e: PointerEvent<HTMLButtonElement>, name: string, strafe: number, forward: number, turn: number) {
    if (!client.current?.getState().lease) return;
    e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); clear(); pointer.current = { strafe, forward, turn }; setPressed(name);
  }
  const directions = [
    { name: 'Forward', icon: ArrowUp, strafe: 0, forward: 1, turn: 0 },
    { name: 'Left', icon: ArrowLeft, strafe: -1, forward: 0, turn: 0 },
    { name: 'Right', icon: ArrowRight, strafe: 1, forward: 0, turn: 0 },
    { name: 'Backward', icon: ArrowDown, strafe: 0, forward: -1, turn: 0 },
    { name: 'Turn left', icon: RotateCcw, strafe: 0, forward: 0, turn: -1 },
    { name: 'Turn right', icon: RotateCw, strafe: 0, forward: 0, turn: 1 },
  ];
  return <section className="backend-workspace connected-manual" ref={workspace} tabIndex={-1} aria-label="Connected manual control" onBlurCapture={e => {
    if (!client.current?.getState().lease && keys.current.size === 0) return;
    const next = e.relatedTarget;
    // Window blur does not fire when focus moves to another part of this page.
    // Held keyboard input must lose authority when its workspace loses focus.
    if (!(next instanceof Node) || !e.currentTarget.contains(next) ||
      (keys.current.size > 0 && next instanceof HTMLElement && next.closest('input,button,textarea,select'))) release('blur');
  }}>
    <div className="page-heading"><div><div className="eyebrow">WAREHOUSE / MANUAL</div><h1>Manual control</h1><p>Hold to drive the configured simulator through the backend.</p></div><button className="button secondary" onClick={() => { release(); void onRefresh(); }}>Refresh connection</button></div>
    <div className="fleet-readiness" role="status"><Gamepad2 size={20}/><div><strong>{simulator ? 'Backend simulator' : status?.executionMode === 'physical' ? 'Physical bridge · controls disabled' : 'Bridge not verified'}</strong><p>{state.message}</p></div><span className="badge neutral">{status?.stage ?? 'Unavailable'}</span></div>
    <div className="connected-manual-grid"><section className="panel"><div className="panel-heading"><h2>Joystick control</h2><span className={`badge ${state.lease ? 'green' : 'neutral'}`}>{state.lease ? 'Control held' : 'Released'}</span></div>
      <form className="manual-target" onSubmit={e => { e.preventDefault(); release(); if (/^[A-Za-z0-9_-]{1,128}$/.test(target)) setRover(target); }}><label>Backend rover ID<input aria-label="Backend rover ID" value={target} maxLength={128} pattern="[A-Za-z0-9_-]{1,128}" required onChange={e => setTarget(e.target.value)} disabled={Boolean(state.lease || state.pending)}/></label><button className="button secondary" disabled={Boolean(state.lease || state.pending)}>Select</button></form>
      <div className="connected-drive-pad">{directions.map(({ name, icon: Icon, strafe, forward, turn }) => <button key={name} aria-label={`Hold ${name.toLowerCase()}`} className={`button secondary ${pressed === name ? 'held' : ''}`} disabled={!state.lease} onPointerDown={e => hold(e, name, strafe, forward, turn)} onPointerUp={() => release()} onPointerCancel={() => release()} onLostPointerCapture={() => { if (pressed === name) release(); }}><Icon size={20}/><span>{name}</span></button>)}</div>
      <p className="connected-manual-help">Take control, then hold a direction or Space + WASD · Q/E turns. Release Space or a pointer to release control. Escape releases control.</p>
      <div className="fleet-actions"><button className="button primary" disabled={!canAcquire} onClick={() => { clear(); void client.current?.acquire(); workspace.current?.focus(); }}>Take control</button><button className="button secondary" disabled={!state.lease && !state.pending} onClick={() => release()}>Release</button><button className="button danger" disabled={!simulator} onClick={() => { clear(); void client.current?.stop(); }}><Square size={15}/>Stop rover</button><button className="button secondary" disabled={!canRearm} onClick={() => { clear(); void client.current?.rearm(); }}>Re-arm</button></div>
      <dl className="backend-facts"><div><dt>Stop confirmation</dt><dd>{status?.stationaryConfirmed ? 'Stationary confirmed by bridge' : 'Unconfirmed'}</dd></div><div><dt>Telemetry</dt><dd>{status?.telemetryFresh ? 'Bridge reports fresh' : 'Unavailable / stale'}</dd></div><div><dt>Granted speed limits</dt><dd>{caps ? `X ${Math.min(.15,caps.maxAbsXMps)} · Y ${Math.min(.15,caps.maxAbsYMps)} m/s · yaw ${Math.min(15,caps.maxAbsZDegps)} °/s` : 'Unavailable'}</dd></div></dl>
      <p className="connected-manual-help">Chassis only. Re-arm never restores control or movement. Overhead navigation and arm/gripper actions remain available as simulations in Demo.</p>
    </section><CameraWidget enabled={ready} onRefreshSession={onRefresh}/></div>
  </section>;
}
