import { useEffect, useRef, useState } from 'react';
import { Activity, ArrowDownLeft, ArrowRight, Bell, Bot, Box, Check, CheckCheck, ChevronDown, CircleHelp, Command, Crosshair, Drill, Expand, FlaskConical, Gamepad2, Grid2X2, Layers, ListChecks, Map, Minus, Package, Pause, Play, Plus, Radio, RotateCcw, ScanLine, Search, Send, Settings2, ShieldAlert, Square, Terminal, Wrench, X, Zap } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cancel, initialState, isActive, objects, queueMission, stageLabel, stages, stop, tick } from './domain';
import type { Mission, TaskKind } from './domain';
import WarehouseMap from './WarehouseMap';
import CameraWidget from './CameraWidget';
import ManualControl from './ManualControl';

type View = 'Overview' | 'Manual' | 'Missions' | 'Fleet' | 'Activity';
type Log = { id: number; text: string; time: string; tone: 'normal' | 'error' };
const navItems: { label: View; icon: LucideIcon }[] = [{ label:'Overview',icon:Grid2X2 },{label:'Manual',icon:Gamepad2},{label:'Missions',icon:ListChecks},{label:'Fleet',icon:Bot},{label:'Activity',icon:Activity}];
const timeNow = () => new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
const taskOptions: { kind: TaskKind; icon: LucideIcon; description: string }[] = [
  { kind:'Recovery', icon:Package, description:'Collect a detected object' },
  { kind:'Warehouse scan', icon:ScanLine, description:'Survey aisles and inventory' },
  { kind:'Inspection', icon:Search, description:'Check an area or equipment' },
  { kind:'Tool delivery', icon:Wrench, description:'Bring tools to a workstation' },
];

export default function App() {
  const [view,setView] = useState<View>('Manual');
  const [sim,setSim] = useState(initialState);
  const [manualLease,setManualLease] = useState(false);
  const [manualReset,setManualReset] = useState(0);
  const [selected,setSelected] = useState(objects[0].id);
  const [running,setRunning] = useState(false);
  const [zoom,setZoom] = useState(1);
  const [routes,setRoutes] = useState(true);
  const [query,setQuery] = useState('');
  const [composer,setComposer] = useState(false);
  const [settings,setSettings] = useState(false);
  const [help,setHelp] = useState(false);
  const [kind,setKind] = useState<TaskKind>('Recovery');
  const [destination,setDestination] = useState('Tool station');
  const [prompt,setPrompt] = useState('');
  const [assistantNotice,setAssistantNotice] = useState('');
  const [error,setError] = useState('');
  const [logs,setLogs] = useState<Log[]>([{id:0,text:'Fixture snapshot loaded. Simulation ready.',time:timeNow(),tone:'normal'}]);
  const object = objects.find(o=>o.id===selected)!;
  const active = [...sim.missions].reverse().find(isActive);
  const completed = sim.missions.filter(m=>m.stage==='COMPLETED').length;
  const previous = useRef(sim.missions);
  const addLog = (text: string, tone: Log['tone'] = 'normal') => setLogs(l=>[{id:Date.now()+Math.random(),text,time:timeNow(),tone},...l].slice(0,40));
  useEffect(()=>{
    if (!running) return;
    const timer = window.setInterval(()=>setSim(s=>tick(s)),3000);
    return ()=>window.clearInterval(timer);
  },[running]);
  useEffect(()=>{
    for (const mission of sim.missions) {
      const before = previous.current.find(m=>m.id===mission.id);
      if (before && before.stage!==mission.stage) addLog(`${mission.id} · ${stageLabel(mission.stage)} (simulated)`);
    }
    previous.current = sim.missions;
  },[sim.missions]);
  useEffect(()=>{
    if (!composer && !settings && !help) return;
    const handler=(e: KeyboardEvent)=>{
      if(e.key==='Escape'){setComposer(false);setSettings(false);setHelp(false)}
      if(e.key==='Tab') {
        const controls=Array.from(document.querySelectorAll<HTMLElement>('.modal button:not(:disabled), .modal input, .modal select'));
        const first=controls[0], last=controls[controls.length-1];
        if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}
        else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}
      }
    };
    document.addEventListener('keydown',handler);
    return ()=>document.removeEventListener('keydown',handler);
  },[composer,settings,help]);
  function attempt(action: ()=>void) { setError(''); try { action(); } catch(e) { const message=(e as Error).message; setError(message);addLog(message,'error'); } }
  function createMission() {
    attempt(()=>{
      if(manualLease) throw new Error('Release manual control before creating an autonomous mission.');
      if(kind!=='Recovery') throw new Error('This task is a future workflow preview. Only object recovery runs in this draft.');
      const next=queueMission(sim,object,destination);
      setSim(next);setComposer(false);addLog(`${next.missions[0].id} · Recovery queued for ${object.name.toLowerCase()}`);
    });
  }
  function stopRover() { attempt(()=>{setSim(stop(sim));setRunning(false);addLog('Simulated stop confirmed. Rover requires explicit re-arm.');}); }
  function reset() { setManualReset(n=>n+1); setSim(initialState());setRunning(false);setError('');setSelected(objects[0].id);setLogs([{id:Date.now(),text:'Simulation reset to the initial fixture snapshot.',time:timeNow(),tone:'normal'}]);setSettings(false); }
  function openTask() {setError('');setKind('Recovery');setComposer(true);}
  const filtered = objects.filter(o=>`${o.name} ${o.id} ${o.category}`.toLowerCase().includes(query.toLowerCase()));

  function missionCard(m: Mission) { return <article className="mission-card" key={m.id}>
    <div className="row between"><span className="mono muted">{m.id}</span><span className={`badge ${m.stage==='COMPLETED'?'green':m.stage==='STOPPED'?'red':'neutral'}`}>{stageLabel(m.stage)}</span></div>
    <h4>{m.title}</h4><p><ArrowDownLeft size={13}/>{m.destination} <span>· ROVER 01</span></p>
    {isActive(m) && <><div className="progress-track"><span style={{width:`${Math.max(5,stages.indexOf(m.stage)/7*100)}%`}}/></div><div className="row between small"><span className="muted">{m.stage==='QUEUED'?'Waiting for simulation':`${stages.indexOf(m.stage)} of 7 stages`}</span><button className="text-button" onClick={()=>attempt(()=>setSim(cancel(sim,m.id)))}>Cancel</button></div></>}
  </article> }

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#" onClick={e=>{e.preventDefault();setView('Overview')}} aria-label="ROVR home"><div className="brand-mark"><Command size={22}/></div><span>rovr<span className="brand-dot">.</span></span></a>
      <div className="workspace"><div className="workspace-icon"><Box size={19}/></div><div><strong>Recovery workspace</strong><small>Warehouse operations</small></div><ChevronDown size={14}/></div>
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{navItems.map(({label,icon:Icon})=><button key={label} aria-label={label} aria-current={view===label ? 'page' : undefined} className={`nav-item ${view===label?'active':''}`} onClick={()=>setView(label)}><Icon size={18}/><span>{label}</span>{label==='Missions'&&<span className="nav-count">{sim.missions.filter(isActive).length}</span>}</button>)}</nav>
      <div className="sidebar-bottom">
        <button className="nav-item" onClick={()=>setSettings(true)}><Settings2 size={17}/>Simulation settings</button><button className="nav-item" onClick={()=>setHelp(true)}><CircleHelp size={17}/>Quick guide<ArrowRight size={15}/></button>
        <div className="profile"><div className="avatar">LS</div><div><strong>LcyShox</strong><small>Dashboard operator</small></div><span className="profile-status"/></div>
      </div>
    </aside>
    <div className={`main-shell ${view==='Manual'?'manual-page':''}`}>
      <header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>{view}</strong></div></header>
      <main>
        <div className="page-heading"><div><div className="eyebrow">MISSION CONTROL / WAREHOUSE 01</div><h1>{view==='Manual'?'Manual control':view==='Overview'?'Warehouse overview':view==='Missions'?'Mission workspace':view==='Fleet'?'Fleet':'Operations activity'}</h1><p>{view==='Manual'?'First-person precision or an overhead perspective. One control workspace.':view==='Overview'?'A clear view of your floor, your rover, and the next move.':view==='Missions'?'Track recovery requests from queue to confirmed completion.':view==='Fleet'?'One simulated rover today. Room for a connected fleet tomorrow.':'Status changes and operator actions from this local session.'}</p></div>{view!=='Manual'&&<button className="button primary" onClick={openTask}><Plus size={17}/>New task</button>}</div>
        <div className="simulation-strip"><span><FlaskConical size={14}/><strong>Simulation</strong><span>No hardware connected.</span></span></div>
        {error&&<div className="error-banner" role="alert"><ShieldAlert size={18}/><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={()=>setError('')}><X size={16}/></button></div>}
        {view!=='Manual'&&<section className="stats-grid" aria-label="Operations summary">
          {[{label:'Fleet availability',value:sim.online&&!sim.stopped?'01 / 01':'00 / 01',note:sim.stopped?'Stop latch active':!sim.online?'Backend unavailable':'Rover ready',icon:Bot,color:'green'}, {label:'Objects detected',value:'04',note:'3 mapped · 1 needs review',icon:ScanLine,color:'blue'}, {label:'Active missions',value:String(sim.missions.filter(isActive).length).padStart(2,'0'),note:active?`${active.id} · ${stageLabel(active.stage)}`:'Ready for your next task',icon:ListChecks,color:'gold'}, {label:'Recoveries completed',value:String(completed).padStart(2,'0'),note:'This simulated session',icon:CheckCheck,color:'green'}].map(({label,value,note,icon:Icon,color})=><article className="stat-card" key={label}><div className="row between"><span>{label}</span><div className={`stat-icon ${color}`}><Icon size={17}/></div></div><strong>{value}</strong><small><span className={`tiny-dot ${color}`}/>{note}</small></article>)}
        </section>}

        {view==='Overview'&&<>
          <div className="operations-grid">
            <section className="panel map-panel"><div className="panel-heading"><div className="row"><Map size={17}/><h2>Floor map</h2><span className="badge neutral">Floor 01</span></div><div className="row"><span className="map-live"><span className={`status-dot ${!sim.online?'amber':''}`}/>{sim.online?'Fixture map':'Snapshot offline'}</span><button className="icon-button" aria-label="Reset map view" onClick={()=>setZoom(1)}><Expand size={16}/></button></div></div>
              <div className="map-container"><WarehouseMap selected={selected} onSelect={setSelected} stage={active?.stage} routeObjectId={active?.objectId} zoom={zoom} routes={routes} stopped={sim.stopped}/><div className="map-controls"><button aria-label="Zoom in" disabled={zoom>=1.4} onClick={()=>setZoom(z=>Math.min(1.4,+(z+.1).toFixed(1)))}><Plus size={16}/></button><button aria-label="Zoom out" disabled={zoom<=.8} onClick={()=>setZoom(z=>Math.max(.8,+(z-.1).toFixed(1)))}><Minus size={16}/></button><button aria-label="Center map" onClick={()=>setZoom(1)}><Crosshair size={16}/></button></div><button className={`map-layers ${routes?'selected':''}`} aria-pressed={routes} onClick={()=>setRoutes(!routes)}><Layers size={14}/>Routes</button></div>
              <div className="map-footer"><div><span className="legend rover"/>Rover<span className="legend object"/>Object<span className="legend zone"/>Drop zone<span className="legend route"/>Route preview</div><span>20 × 12 m</span></div>
            </section>
            <div className="overview-side"><CameraWidget/><section className="panel mission-panel"><div className="panel-heading"><div className="row"><Radio size={17}/><h2>Mission activity</h2></div><span className="count-pill">{sim.missions.length}</span></div><div className="mission-panel-body">{active?missionCard(active):<div className="ready-state"><div className="ready-icon"><ListChecks size={25}/></div><h3>{sim.stopped?'Rover is stopped':!sim.online?'Connection unavailable':'Ready for the next move'}</h3><p>{sim.stopped?'Re-arm explicitly before starting a new recovery.':!sim.online?'Reconnect the mock backend to request a recovery.':'Select an object on the map or create a recovery task.'}</p></div>}{sim.missions.filter(m=>!isActive(m)).slice(0,1).map(missionCard)}</div><button className="panel-link" onClick={()=>setView('Missions')}>View all missions<ArrowRight size={14}/></button></section></div>
          </div>
          <div className="objects-section"><section className="panel objects-panel"><div className="panel-heading"><div className="row"><Box size={17}/><h2>Detected objects</h2><span className="count-pill">04</span></div><label className="search"><Search size={14}/><input aria-label="Search detected objects" placeholder="Find an object…" value={query} onChange={e=>setQuery(e.target.value)}/></label></div><div className="object-table-wrap"><table><thead><tr><th>OBJECT</th><th>LOCATION</th><th>CONFIDENCE</th><th>STATUS</th><th><span className="sr-only">Action</span></th></tr></thead><tbody>{filtered.map(o=><tr key={o.id} className={selected===o.id?'selected-row':''} onClick={()=>setSelected(o.id)}><td><div className="object-cell"><div className={`object-icon ${o.point?'':'uncertain'}`}>{o.name==='Cordless drill'?<Drill size={19}/>:o.point?<Package size={19}/>:<ShieldAlert size={19}/>}</div><div><button className="object-name" onClick={()=>setSelected(o.id)}>{o.name}</button><small>{o.id} · {o.category}</small></div></div></td><td>{o.zone}</td><td><div className="confidence"><span>{o.confidence}%</span><i><b style={{width:`${o.confidence}%`,background:o.point?'#639985':'#c29b56'}}/></i></div></td><td><span className={`badge ${!o.point?'gold':sim.missions.some(m=>isActive(m)&&m.objectId===o.id)?'blue':'green'}`}>{!o.point?'Needs review':sim.missions.some(m=>isActive(m)&&m.objectId===o.id)?'Reserved':'Available'}</span></td><td><button className="icon-button" aria-label={`Create task for ${o.name}`} onClick={e=>{e.stopPropagation();setSelected(o.id);openTask()}}><ArrowRight size={16}/></button></td></tr>)}</tbody></table>{filtered.length===0&&<p className="empty-message">No objects match “{query}”.</p>}</div></section>
</div>
        </>}

        {view==='Missions'&&<section className="panel full-panel"><div className="panel-heading"><h2>All missions</h2><span className="badge neutral">Local session</span></div><div className="mission-grid">{sim.missions.map(missionCard)}</div></section>}
        {view==='Fleet'&&<div className="fleet-grid"><section className="panel fleet-detail"><div className="row between"><div className="robot-illustration"><Bot size={55} strokeWidth={1.3}/></div><span className={`badge ${sim.stopped?'red':sim.online?'green':'gold'}`}>{sim.stopped?'Stopped':sim.online?'Connected · mock':'Offline · mock'}</span></div><h2>Rover 01</h2><p>DJI EP-family · fixture profile</p><div className="robot-metrics"><div><span>Battery</span><strong>86% <small>fixture</small></strong></div><div><span>Cargo</span><strong>{active&&['TRANSPORTING','DROPPING'].includes(active.stage)?'Simulated object':'Empty'}</strong></div><div><span>Current task</span><strong>{active?.id||'Unassigned'}</strong></div></div><div className="row"><button className="button secondary" onClick={openTask}>Assign recovery</button><button className="button danger" onClick={stopRover}><Square size={14}/>Stop rover</button></div></section><section className="panel future-fleet"><div className="future-icon"><Bot size={30}/><Plus size={19}/></div><span className="badge neutral">Future capability</span><h2>Better, together.</h2><p>Multi-robot task allocation and cooperative warehouse operations will appear here once the backend supports fleet coordination.</p><ul><li>Shared warehouse awareness</li><li>Robot-specific task assignment</li><li>Coordinated scanning and delivery</li></ul><small>The draft runs one simulated rover.</small></section></div>}
        {view==='Activity'&&<section className="panel full-panel"><div className="panel-heading"><h2>Session event log</h2><span className="badge neutral">{logs.length} events</span></div><div className="event-list">{logs.map(l=><div key={l.id} className={l.tone==='error'?'event error-event':'event'}><div className="event-icon">{l.tone==='error'?<ShieldAlert size={16}/>:<Check size={16}/>}</div><div><strong>{l.text}</strong><p>Local simulator · ROVER 01</p></div><time>{l.time}</time></div>)}</div></section>}

        <ManualControl key={manualReset} active={view==='Manual'} online={sim.online} stopped={sim.stopped} autonomous={Boolean(active)} suspended={composer||settings||help} onLeaseChange={setManualLease} onStop={stopRover} onRearm={()=>attempt(()=>{if(!sim.online)throw new Error('Cannot re-arm while offline.');setSim({...sim,stopped:false});addLog('Explicitly re-armed. Manual control remains released.');})} onLog={addLog}/>
        <footer className="page-footer"><span><span className={`status-dot ${!sim.online?'amber':''}`}/>{sim.online?'Mock backend connected':'Mock backend offline'}</span><span>ROVR / OPERATIONS CONSOLE <span className="version">v0.1 draft</span></span></footer>
      </main>
      {view!=='Manual'&&<div className="control-bar"><div className="control-rover"><div className="control-bot"><Bot size={20}/></div><div><strong>Rover 01<span className={`badge ${sim.stopped?'red':'neutral'}`}>{sim.stopped?'Stopped':active?stageLabel(active.stage):'Idle'}</span></strong><small>{sim.stopped?'Explicit re-arm required':!sim.online?'Connection lost · simulation paused':'Local simulator · 86% battery'}</small></div></div><div className="control-actions"><button className="button secondary" disabled={!active||sim.stopped||!sim.online} onClick={()=>setRunning(!running)}>{running?<Pause size={15}/>:<Play size={15}/>}<span>{running?'Pause':'Run simulation'}</span></button><button className="button secondary step-button" disabled={!active||sim.stopped||!sim.online||running} onClick={()=>setSim(tick(sim))}>Next stage<ArrowRight size={14}/></button>{sim.stopped?<button className="button secondary" onClick={()=>attempt(()=>{if(!sim.online)throw new Error('Cannot re-arm while the mock backend is offline.');setSim({...sim,stopped:false});addLog('Rover explicitly re-armed. Stopped missions stay stopped.');})}><RotateCcw size={15}/>Re-arm</button>:null}<button className="button danger" onClick={stopRover}><Square size={14} fill="currentColor"/>Stop rover</button></div></div>}
    </div>

    {composer&&<div className="modal-backdrop" onClick={()=>setComposer(false)}><section className="modal task-modal" role="dialog" aria-modal="true" aria-labelledby="task-title" onClick={e=>e.stopPropagation()}><div className="modal-heading"><div><div className="eyebrow">SIMULATED OPERATIONS</div><h2 id="task-title">What’s the next task?</h2></div><button className="icon-button" aria-label="Close task composer" onClick={()=>setComposer(false)} autoFocus><X size={20}/></button></div><p className="modal-intro">Create a recovery mission, or explore what comes next.</p><div className="task-options">{taskOptions.map(({kind:k,icon:Icon,description})=><button key={k} className={`task-option ${kind===k?'chosen':''}`} onClick={()=>{setKind(k);setError('')}}><Icon size={20}/><strong>{k}{k!=='Recovery'&&<small>Planned</small>}</strong><span>{description}</span></button>)}</div>
      {kind==='Recovery'?<div className="form-grid"><label>Detected object<select value={selected} onChange={e=>setSelected(e.target.value)}>{objects.map(o=><option key={o.id} value={o.id}>{o.name} · {o.id}</option>)}</select></label><label>Destination zone<select value={destination} onChange={e=>setDestination(e.target.value)}><option>Tool station</option><option>Drop zone A</option><option>Workstation B</option></select></label><div className="object-summary"><Crosshair size={17}/><div><strong>{object.point?`${object.point.x.toFixed(1)} m, ${object.point.y.toFixed(1)} m · ${object.zone}`:'Location not verified'}</strong><small>{object.point?'Fixture position · simulated pickup profile':'This object cannot be dispatched.'}</small></div></div></div>:<div className="future-notice"><ScanLine size={25}/><h3>{kind} is on the roadmap</h3><p>{kind==='Warehouse scan'?'Select aisles, set coverage, and review a warehouse inventory report.':kind==='Inspection'?'Choose a checkpoint and ask a rover to inspect equipment or an area.':'Choose a tool, a destination, and an available rover for delivery.'} This preview does not dispatch this task.</p></div>}
      <div className="language-preview"><label htmlFor="task-prompt"><Zap size={15}/>Or describe it naturally<span className="badge neutral">Future · Grok</span></label><div className="prompt-input"><input id="task-prompt" placeholder="Bring the drill to workstation B…" value={prompt} onChange={e=>setPrompt(e.target.value)}/><button className="icon-button" aria-label="Preview natural-language capability" onClick={()=>setAssistantNotice('Grok is not connected. In the planned flow, the backend will interpret your request, ask about ambiguity, and validate it before dispatch.')}><Send size={17}/></button></div>{assistantNotice&&<p role="status">{assistantNotice}</p>}</div>
      {error&&<div className="inline-error" role="alert">{error}</div>}<div className="modal-footer"><span><FlaskConical size={14}/>No physical movement</span><button className="button primary" disabled={kind!=='Recovery'} onClick={createMission}>Queue recovery<ArrowRight size={15}/></button></div></section></div>}
    {settings&&<div className="modal-backdrop" onClick={()=>setSettings(false)}><section className="modal settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" onClick={e=>e.stopPropagation()}><div className="modal-heading"><h2 id="settings-title">Simulation settings</h2><button className="icon-button" aria-label="Close settings" onClick={()=>setSettings(false)} autoFocus><X size={20}/></button></div><p className="modal-intro">Try the workflow under normal and unavailable conditions.</p><div className="setting-row"><div><strong>Mock backend connection</strong><p>Offline requests fail; stop is not reported as confirmed.</p></div><button className={`toggle ${sim.online?'on':''}`} role="switch" aria-label="Mock backend connection" aria-checked={sim.online} onClick={()=>{setSim({...sim,online:!sim.online});addLog(sim.online?'Mock backend disconnected. Simulation paused.':'Mock backend reconnected. Current local snapshot restored.');setRunning(false)}}><span/></button></div><button className="button secondary reset-button" onClick={reset}><RotateCcw size={16}/>Reset simulation</button><p className="small muted">Reset clears local missions and the session log. Reloading the page also resets the draft.</p></section></div>}
    {help&&<div className="modal-backdrop" onClick={()=>setHelp(false)}><section className="modal settings-modal" role="dialog" aria-modal="true" aria-labelledby="help-title" onClick={e=>e.stopPropagation()}><div className="modal-heading"><h2 id="help-title">Try your first recovery</h2><button className="icon-button" aria-label="Close guide" onClick={()=>setHelp(false)} autoFocus><X size={20}/></button></div><ol className="guide-list"><li>Select a mapped object and create a new recovery task.</li><li>Choose a destination, then queue the recovery.</li><li>Use Run simulation or Next stage to follow its progress.</li><li>Try cancel, stop, and an offline backend in Simulation settings.</li></ol><p className="small muted">All data is a local fixture. No camera feed, AI service, backend or physical robot is connected. Future task types are previews.</p></section></div>}
  </div>;
}
