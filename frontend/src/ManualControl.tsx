import {useEffect,useRef,useState} from 'react';
import type {PointerEvent as ReactPointerEvent} from 'react';
import {ArrowDown,ArrowLeft,ArrowRight,ArrowUp,Bot,ChevronRight,Compass,Gamepad2,Hand,LockKeyhole,MapPin,Navigation,Pause,Play,RotateCcw,RotateCw,ScanLine,ShieldAlert,Square,UnlockKeyhole,Video,WifiOff,Wrench} from 'lucide-react';
import {applyGate,createDroids,DEMO_TIMEOUT_MS,integrate,planFixtureRoute,pointerToMap,RACKS,stepRoute,velocityFromInput,WORLD,ZERO} from './manual';
import type {Droid,Point,Velocity} from './manual';
import './manual.css';
import CameraWidget from './CameraWidget';

type Props={active:boolean;online:boolean;stopped:boolean;autonomous:boolean;suspended:boolean;onLeaseChange:(held:boolean)=>void;onStop:()=>void;onRearm:()=>void;onLog:(text:string)=>void};
type Mode='fpv'|'overhead';
const caps=[{name:'Precision',speed:.15},{name:'Standard',speed:.3},{name:'Travel',speed:.5}];
const round=(n:number)=>Math.abs(n)<.0005?'0.00':n.toFixed(2);
const normalizeAngle=(a:number)=>((a*180/Math.PI)%360+360)%360;

function Scene({droid}: {droid:Droid}) {
  const rays=Array.from({length:80},(_,i)=>{
    const offset=(i/79-.5)*Math.PI*.48,angle=droid.pose.yaw-offset;
    let distance=.1;
    for(;distance<25;distance+=.1){
      const x=droid.pose.x+Math.cos(angle)*distance,y=droid.pose.y+Math.sin(angle)*distance;
      if(x<=0||x>=WORLD.width||y<=0||y>=WORLD.height||RACKS.some(r=>x>=r.x&&x<=r.x+r.w&&y>=r.y&&y<=r.y+r.h))break;
    }
    const corrected=Math.max(.3,distance*Math.cos(offset)),height=Math.min(460,350/corrected);
    return {x:i*11.25,height,shade:Math.max(60,85-corrected*2)};
  });
  return <svg viewBox="0 0 900 460" className="fpv-scene" role="img" aria-label={`Rendered first-person warehouse simulation for ${droid.name}. No live camera.`}>
    <rect width="900" height="230" fill="#dce4e0"/><rect y="230" width="900" height="230" fill="#b1beb7"/>
    {[240,260,295,350,435].map(y=><line key={y} x1="0" y1={y} x2="900" y2={y} stroke="#9eafa5" opacity=".6"/>)}
    {[-1200,-600,0,450,900,1500,2100].map(x=><line key={x} x1="450" y1="230" x2={x} y2="460" stroke="#93a79a" opacity=".4"/>)}
    {rays.map((r,i)=><g key={i}><rect x={r.x} y={230-r.height/2} width="11.6" height={r.height} fill={`hsl(145 9% ${r.shade}%)`}/>{[.1,.45,.8].map(f=><rect key={f} x={r.x} y={230-r.height/2+r.height*f} width="11.6" height={Math.max(1,r.height*.035)} fill="#627b6a" opacity=".7"/>)}</g>)}
    <path d="M0 0H900V460H0Z" stroke="#c5d3c8" strokeWidth="2" fill="none"/>
    <path d="M435 230h10m10 0h10m-15-15v10m0 10v10" stroke="#f5fff8" strokeWidth="1.5"/>
    <path d="M30 65V30h35m770 0h35v35M30 395v35h35m770 0h35v-35" stroke="#f4fff6" strokeWidth="2" fill="none" opacity=".5"/>
    <text x="450" y="410" textAnchor="middle" fill="#f4fff5" fontSize="11" letterSpacing="3">RENDERED FIXTURE · NO LIVE CAMERA</text>
  </svg>;
}

function Overhead({droids,selected,route,target,onSelect,onDestination,calibrated}: {droids:Droid[];selected:string;route:Point[];target:Point|null;onSelect:(id:string)=>void;onDestination:(p:Point)=>void;calibrated:boolean}) {
  const sx=(x:number)=>x*40,sy=(y:number)=>(12-y)*40;
  return <svg viewBox="0 0 800 480" preserveAspectRatio="none" className={`command-map ${calibrated?'':'uncalibrated'}`} role="group" aria-label="Overhead fixture camera. Click clear floor to preview a destination." onClick={e=>{if(e.target instanceof Element&&e.target.closest('[data-droid]'))return;onDestination(pointerToMap({x:e.clientX,y:e.clientY},e.currentTarget.getBoundingClientRect()))}}>
    <defs><pattern id="manual-grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" fill="none" stroke="#d0dcd4" strokeWidth=".7"/></pattern></defs>
    <rect width="800" height="480" fill="#eaf0eb"/><rect width="800" height="480" fill="url(#manual-grid)"/>
    <rect x="20" y="20" width="760" height="440" rx="3" fill="none" stroke="#9cb5a5" strokeWidth="3"/>
    {RACKS.map((r,i)=><g key={i}><rect x={sx(r.x)} y={sy(r.y+r.h)} width={sx(r.w)} height={sx(r.h)} rx="3" fill="#bdcec0" stroke="#a9bfaf"/>{[.25,.5,.75].map(f=><line key={f} x1={sx(r.x+r.w*f)} y1={sy(r.y+r.h)+5} x2={sx(r.x+r.w*f)} y2={sy(r.y)-5} stroke="#9eb6a3"/>)}<text x={sx(r.x+1)} y={sy(r.y+1)+3} textAnchor="middle" fill="#6e8a74" fontSize="9">RACK {i+1}</text></g>)}
    <rect x="25" y="392" width="72" height="42" rx="4" fill="#cee0ce" stroke="#9dbda3" strokeDasharray="4 3"/><text x="61" y="417" fill="#6b9172" fontSize="8" textAnchor="middle">BASE</text>
    <rect x="655" y="30" width="100" height="60" rx="4" fill="#d9dfc7" stroke="#aebd90" strokeDasharray="4 3"/><text x="705" y="65" fill="#7a885b" fontSize="8" textAnchor="middle">TOOL STATION</text>
    {route.length>0&&<polyline points={route.map(p=>`${sx(p.x)},${sy(p.y)}`).join(' ')} fill="none" stroke="#55945f" strokeWidth="2.5" strokeDasharray="6 4"/>}
    {target&&<g transform={`translate(${sx(target.x)} ${sy(target.y)})`}><circle r="15" fill="#d7e7a9" fillOpacity=".6" stroke="#85a147"/><path d="M-6 0H6M0-6V6" stroke="#6b8d37"/><text y="-23" textAnchor="middle" fill="#6a7e46" fontSize="9">DESTINATION</text></g>}
    {droids.map(d=><g key={d.id} data-droid={d.id} role="button" tabIndex={0} aria-label={`Select ${d.name}`} onClick={e=>{e.stopPropagation();onSelect(d.id)}} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(d.id)}}} transform={`translate(${sx(d.pose.x)} ${sy(d.pose.y)})`}>
      {d.id===selected&&<circle r="26" fill="#c6d9b3" fillOpacity=".6" stroke="#95b178" strokeDasharray="3 3"/>}
      <g transform={`rotate(${-d.pose.yaw*180/Math.PI})`}><rect x="-13" y="-12" width="26" height="24" rx="7" fill={!d.online?'#a8b2ac':d.id===selected?'#2e6548':'#698e74'} stroke="#f8fff9" strokeWidth="2"/><path d="M-3-5L6 0L-3 5Z" fill="white"/></g>
      <rect x="-29" y="29" width="58" height="17" rx="4" fill="#f9fcf7" stroke="#d6e0d4"/><text y="40" textAnchor="middle" fontSize="8" fill="#5a785e">{d.name.toUpperCase()}</text>
    </g>)}
    <g transform="translate(760 410)"><path d="M0 20V0M-4 6L0 0L4 6" fill="none" stroke="#7e9985"/><text x="0" y="-7" textAnchor="middle" fill="#7e9985" fontSize="9">N</text></g>
    <text x="25" y="45" fill="#7b9580" fontSize="8" letterSpacing="1.5">CAM-01 / ILLUSTRATIVE CALIBRATED FLOOR</text>
  </svg>;
}

export default function ManualControl({active,online,stopped,autonomous,suspended,onLeaseChange,onStop,onRearm,onLog}:Props) {
  const workspaceRef=useRef<HTMLElement>(null);
  const [mode,setMode]=useState<Mode>('fpv');
  const [droids,setDroids]=useState(createDroids);
  const droidsRef=useRef(droids);
  const [selected,setSelected]=useState('rm-01');
  const [lease,setLease]=useState(false);
  const [cap,setCap]=useState(.3);
  const [stick,setStick]=useState({x:0,y:0});
  const [wire,setWire]=useState<Velocity>(ZERO);
  const [target,setTarget]=useState<Point|null>(null);
  const [destinationInput,setDestinationInput]=useState<Point>({x:6,y:6});
  const [preview,setPreview]=useState<Point[]>([]);
  const [route,setRoute]=useState<Point[]>([]);
  const routeRef=useRef<Point[]>([]);
  const [calibrated,setCalibrated]=useState(true);
  const [fresh,setFresh]=useState(true);
  const [notice,setNotice]=useState('Select a droid and take simulated control to begin.');
  const [warning,setWarning]=useState('');
  const [packet,setPacket]=useState(0);
  const keys=useRef(new Set<string>());
  const intent=useRef({strafe:0,forward:0,turn:0,pointer:false,last:0});
  const current=droids.find(d=>d.id===selected)!;
  const callbacks=useRef({onLog,onLeaseChange});callbacks.current={onLog,onLeaseChange};
  const wasStopped=useRef(stopped);
  const ready=active&&!suspended&&online&&current.online&&!stopped&&!autonomous&&fresh;
  const enabled=ready&&lease;
  function halt(message?:string) {
    keys.current.clear();intent.current={strafe:0,forward:0,turn:0,pointer:false,last:0};
    setStick({x:0,y:0});setWire({...ZERO});routeRef.current=[];setRoute([]);
    if(message)setNotice(message);
  }
  function release(message:string) {halt(message);setLease(false);}
  useEffect(()=>{callbacks.current.onLeaseChange(lease);return ()=>callbacks.current.onLeaseChange(false)},[lease]);
  useEffect(()=>{if(wasStopped.current&&!stopped)setNotice('Stop latch cleared. Take control again; previous movement will not resume.');wasStopped.current=stopped},[stopped]);
  useEffect(()=>{
    if(!active||suspended||!online||stopped||autonomous||!fresh)release(stopped?'Stop latch active. Re-arm explicitly; control will not resume automatically.':!fresh?'Telemetry marked stale. Control released.':!online?'Mock backend unavailable. Physical stop would be unconfirmed.':'Control released.');
  },[active,suspended,online,stopped,autonomous,fresh]);
  useEffect(()=>{
    if(!active)return;
    const down=(e:KeyboardEvent)=>{
      if(e.target instanceof HTMLElement&&e.target.closest('input,select,textarea,button,[contenteditable=true]'))return;
      if(['Space','KeyW','KeyA','KeyS','KeyD','KeyQ','KeyE'].includes(e.code)){
        if(enabled&&mode==='fpv'){e.preventDefault();keys.current.add(e.code);intent.current.last=performance.now();}
      }
      if(e.code==='Escape'){e.preventDefault();release('Control released with Escape.');}
    };
    const up=(e:KeyboardEvent)=>{keys.current.delete(e.code);if(e.code==='Space'){halt('Drive key released. Velocity zeroed.');}};
    const blur=()=>release('Window focus lost. Control released; take control again to continue.');
    const visibility=()=>{if(document.hidden)blur()};
    window.addEventListener('keydown',down);window.addEventListener('keyup',up);window.addEventListener('blur',blur);document.addEventListener('visibilitychange',visibility);
    return()=>{window.removeEventListener('keydown',down);window.removeEventListener('keyup',up);window.removeEventListener('blur',blur);document.removeEventListener('visibilitychange',visibility)};
  },[active,enabled,mode]);
  useEffect(()=>{
    if(!active)return;
    const timer=window.setInterval(()=>{
      const now=performance.now(),k=keys.current;
      const driveKey=k.has('Space');
      let velocity={...ZERO};
      if(mode==='fpv'){
        if(driveKey||intent.current.pointer){intent.current.last=now;}
        const x=driveKey?Number(k.has('KeyD'))-Number(k.has('KeyA')):intent.current.strafe;
        const y=driveKey?Number(k.has('KeyW'))-Number(k.has('KeyS')):intent.current.forward;
        const z=driveKey?Number(k.has('KeyE'))-Number(k.has('KeyQ')):intent.current.turn;
        velocity=applyGate(velocityFromInput(x,y,z,cap),{lease,online:online&&current.online,stopped,autonomous,fresh},intent.current.last,now);
      }
      const next=droidsRef.current.map(d=>{
        if(d.id!==selected||!enabled)return d;
        const others=droidsRef.current.filter(o=>o.id!==d.id).map(o=>o.pose);
        if(mode==='overhead'&&routeRef.current.length){
          const result=stepRoute(d.pose,routeRef.current,cap,.1,others);routeRef.current=result.route;setRoute(result.route);
          if(!result.route.length)setNotice('Fixture route ended. Review the current position before another command.');
          return {...d,pose:result.pose};
        }
        return {...d,pose:integrate(d.pose,velocity,.1,others)};
      });
      droidsRef.current=next;setDroids(next);setWire(velocity);
      if(enabled)setPacket(p=>p+1);
    },100);
    return()=>window.clearInterval(timer);
  },[active,mode,selected,lease,online,stopped,autonomous,fresh,cap,enabled,current.online]);
  useEffect(()=>()=>{keys.current.clear();callbacks.current.onLeaseChange(false)},[]);
  if(!active)return null;
  function selectDroid(id:string) {if(id===selected)return;release('Droid changed. Take control of the selected unit explicitly.');setSelected(id);setTarget(null);setPreview([]);setWarning('');}
  function changeMode(next:Mode){if(mode===next)return;release('Mode changed. Take control again; no previous command is resumed.');setMode(next);setTarget(null);setPreview([]);setWarning('');}
  function acquire(){if(!ready)return;halt();setLease(true);setWarning('');setNotice(`Simulated exclusive control acquired for ${current.name}.`);workspaceRef.current?.focus({preventScroll:true});onLog(`${current.name} · manual simulator control acquired`);}
  function joystick(e:ReactPointerEvent<HTMLDivElement>){
    if(!enabled||!intent.current.pointer)return;
    const r=e.currentTarget.getBoundingClientRect(),limit=r.width*.32;
    const dx=(e.clientX-r.left-r.width/2)/limit,dy=(e.clientY-r.top-r.height/2)/limit,n=Math.max(1,Math.hypot(dx,dy));
    intent.current.strafe=dx/n;intent.current.forward=-dy/n;intent.current.last=performance.now();setStick({x:dx/n,y:dy/n});
  }
  function destination(p:Point){
    halt('No destination confirmed. Preview a valid point before moving.');setTarget(null);setPreview([]);setWarning('');
    if(!calibrated){setWarning('Camera calibration is invalid. No pixel-to-map command can be created.');return;}
    try{const planned=planFixtureRoute(current.pose,p,droids.filter(d=>d.id!==selected).map(d=>d.pose));setTarget(p);setDestinationInput(p);setPreview(planned);setNotice('Destination preview ready. Take control and confirm the move.');}
    catch(e){setWarning((e as Error).message);}
  }
  function moveRoute(){
    if(!enabled||!calibrated||!preview.length||!target)return;
    try{
      const planned=planFixtureRoute(current.pose,target,droidsRef.current.filter(d=>d.id!==selected).map(d=>d.pose));
      routeRef.current=planned.slice(1);setRoute(routeRef.current);setPreview([]);
      setNotice('Following the fixture route. Stop or release control to cancel.');
      onLog(`${current.name} · simulated move to ${target.x.toFixed(1)}, ${target.y.toFixed(1)} m`);
    }catch(e){setWarning((e as Error).message);}
  }
  function tool(action:'open'|'close'|'forward'|'back'|'up'|'down'){
    if(!enabled||routeRef.current.length||wire.x||wire.y||wire.z)return;
    const next=droidsRef.current.map(d=>d.id!==selected?d:{...d,gripper:action==='open'?'Open' as const:action==='close'?'Closed' as const:d.gripper,arm:{x:Math.max(0,Math.min(180,d.arm.x+(action==='forward'?10:action==='back'?-10:0))),y:Math.max(0,Math.min(120,d.arm.y+(action==='up'?10:action==='down'?-10:0)))}});
    droidsRef.current=next;setDroids(next);setNotice(`Simulated ${action==='open'||action==='close'?'gripper':'arm'} input: ${action}. No physical action.`);
  }
  const canTool=enabled&&!route.length&&!wire.x&&!wire.y&&!wire.z;
  const displayRoute=route.length?[current.pose,...route]:preview;

  return <section ref={workspaceRef} tabIndex={-1} className="manual-workspace" aria-label="Manual droid control">
    <div className="manual-toolbar">
      <div className="mode-switch" role="group" aria-label="Control view">
        <button aria-pressed={mode==='fpv'} className={mode==='fpv'?'chosen':''} onClick={()=>changeMode('fpv')}><Gamepad2 size={16}/>First person</button>
        <button aria-pressed={mode==='overhead'} className={mode==='overhead'?'chosen':''} onClick={()=>changeMode('overhead')}><ScanLine size={16}/>Overhead command</button>
      </div>
      <span className="manual-hardware">DJI RoboMaster EP Core</span>
    </div>
    <div className="droid-strip">{droids.map(d=><button className={`droid-chip ${selected===d.id?'selected':''}`} key={d.id} onClick={()=>selectDroid(d.id)} aria-pressed={selected===d.id}>
      <div className="droid-chip-icon"><Bot size={22}/></div><div><strong>{d.name}<span className={`status-dot ${d.online?'':'amber'}`}/></strong><small>{d.online?`${d.battery}% battery`:'Offline'}</small></div>
    </button>)}</div>
    <div className="manual-grid">
      <div className="manual-primary">
        <section className="manual-viewport panel">
          <div className="manual-camera-heading"><span><Video size={16}/>{mode==='fpv'?`${current.name} / front view`:'Overhead floor view'}</span><span className="camera-fixture">SIMULATED</span></div>
          <div className="viewport-content">
            {mode==='fpv'?<Scene droid={current}/>:<Overhead droids={droids} selected={selected} route={displayRoute} target={target} onSelect={selectDroid} onDestination={destination} calibrated={calibrated}/>}
            <div className="viewport-tags"><span><Compass size={12}/>{normalizeAngle(current.pose.yaw).toFixed(0)}°</span><span><MapPin size={12}/>{current.pose.x.toFixed(2)}, {current.pose.y.toFixed(2)} m</span></div>
            {(!online||!current.online||!fresh)&&<div className="view-unavailable"><WifiOff size={30}/><strong>{!fresh?'Telemetry stale':'Connection unavailable'}</strong><span>Control released</span></div>}
          </div>
          {mode==='overhead'&&<div className="viewport-footer"><span>Select a droid, then click clear floor to preview a move.</span><span>20 × 12 m</span></div>}
        </section>

        {mode==='fpv'?<section className="panel unified-console" aria-labelledby="joystick-title">
          <div className="panel-heading"><div className="row"><Gamepad2 size={17}/><h2 id="joystick-title">Joystick control</h2></div><span className="badge neutral">{current.name}</span></div>
          <div className="unified-console-body">
            <div className="chassis-controls">
              <span className="console-eyebrow">DRIVE & TURN</span>
              <div className="joystick" role="group" aria-label="Translation joystick. Drag and hold to drive." aria-disabled={!enabled}
                onPointerDown={e=>{if(!enabled)return;e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);intent.current.pointer=true;joystick(e)}}
                onPointerMove={joystick} onPointerUp={()=>halt('Joystick released. Velocity zeroed.')} onPointerCancel={()=>halt('Pointer cancelled. Velocity zeroed.')} onLostPointerCapture={()=>halt()}>
                <ArrowUp className="stick-up" size={14}/><ArrowRight className="stick-right" size={14}/><ArrowDown className="stick-down" size={14}/><ArrowLeft className="stick-left" size={14}/>
                <div className="joystick-cross"/><div className="joystick-thumb" style={{transform:`translate(${stick.x*44}px,${stick.y*44}px)`}}><Gamepad2 size={21}/></div>
              </div>
              <div className="yaw-buttons">{[-1,1].map(turn=><button key={turn} aria-label={turn<0?'Hold to turn left':'Hold to turn right'} disabled={!enabled}
                onPointerDown={e=>{e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);intent.current={strafe:0,forward:0,turn,pointer:true,last:performance.now()}}}
                onPointerUp={()=>halt()} onPointerCancel={()=>halt()} onLostPointerCapture={()=>halt()}>{turn<0?<RotateCcw size={18}/>:<RotateCw size={18}/>}</button>)}</div>
              <p>Hold <kbd>Space</kbd> + WASD · Q / E turns.<br/>Release input to stop.</p>
            </div>
            <div className="integrated-tools">
              <div className="tool-heading"><Wrench size={14}/><h3>Arm & gripper</h3><span>Stationary only</span></div>
              <div className="arm-preview">
                <svg viewBox="0 0 220 110" role="img" aria-label={`Simulated arm extension ${current.arm.x} millimetres and height ${current.arm.y} millimetres`}>
                  <path d="M40 95H170" stroke="#d2ddcf" strokeWidth="3"/><rect x="40" y="78" width="60" height="15" rx="4" fill="#a9bea5"/>
                  <path d={`M70 78 L95 ${66-current.arm.y*.18} L${115+current.arm.x*.22} ${66-current.arm.y*.3}`} stroke="#6f956c" strokeWidth="11" fill="none" strokeLinejoin="round"/>
                  <circle cx="70" cy="78" r="9" fill="#517b4b"/><circle cx="95" cy={66-current.arm.y*.18} r="7" fill="#6f956c"/>
                  <path d={`M${115+current.arm.x*.22} ${66-current.arm.y*.3} l13 -${current.gripper==='Open'?12:4} m-13 ${current.gripper==='Open'?12:4} l13 ${current.gripper==='Open'?12:4}`} stroke="#527d4c" strokeWidth="4" fill="none"/>
                </svg><span>{current.gripper}</span>
              </div>
              <div className="arm-controls">
                <button aria-label="Simulate arm retract 10 millimetres" disabled={!canTool} onClick={()=>tool('back')}><ArrowLeft size={14}/></button><span>Reach {current.arm.x} mm</span><button aria-label="Simulate arm extend 10 millimetres" disabled={!canTool} onClick={()=>tool('forward')}><ArrowRight size={14}/></button>
                <button aria-label="Simulate arm down 10 millimetres" disabled={!canTool} onClick={()=>tool('down')}><ArrowDown size={14}/></button><span>Height {current.arm.y} mm</span><button aria-label="Simulate arm up 10 millimetres" disabled={!canTool} onClick={()=>tool('up')}><ArrowUp size={14}/></button>
              </div>
              <div className="gripper-buttons"><button className="button secondary" disabled={!canTool} onClick={()=>tool('open')}>Open gripper</button><button className="button secondary" disabled={!canTool} onClick={()=>tool('close')}>Close gripper</button></div>
              <small>10 mm jogs · illustrative travel limits</small>
            </div>
          </div>
        </section>:<>
          <section className="destination-console panel"><div className="destination-icon"><Navigation size={22}/></div><div><span className="console-eyebrow">DESTINATION</span><strong>{target?`${target.x.toFixed(2)} m east · ${target.y.toFixed(2)} m north`:'Choose a point on the floor'}</strong></div><button className="button primary" disabled={!enabled||!calibrated||!target||!preview.length||!!route.length} onClick={moveRoute}><Play size={14}/>Move here</button><button className="button secondary" disabled={!route.length} onClick={()=>halt('Fixture route cancelled.')}><Pause size={14}/>Halt</button></section>
          <details className="coordinate-details"><summary>Enter coordinates</summary><div className="coordinate-entry"><label>East (m)<input aria-label="Destination east in metres" type="number" min="0.5" max="19.5" step="0.1" value={Number.isNaN(destinationInput.x)?'':destinationInput.x} onChange={e=>setDestinationInput({...destinationInput,x:e.target.valueAsNumber})}/></label><label>North (m)<input aria-label="Destination north in metres" type="number" min="0.5" max="11.5" step="0.1" value={Number.isNaN(destinationInput.y)?'':destinationInput.y} onChange={e=>setDestinationInput({...destinationInput,y:e.target.valueAsNumber})}/></label><button className="button secondary" onClick={()=>destination(destinationInput)}>Preview destination</button></div></details>
        </>}
        <div className="manual-notice" role="status"><span className={`status-dot ${lease?'':'amber'}`}/>{notice}</div>
        {warning&&<div className="manual-warning" role="alert"><ShieldAlert size={15}/>{warning}</div>}
      </div>

      <aside className="manual-side">
        <CameraWidget/>
        <section className="panel authority-panel">
          <div className="manual-section-title"><Hand size={16}/><h2>Control & speed</h2><span className={`badge ${lease?'green':'neutral'}`}>{lease?'Held':'Released'}</span></div>
          <div className="authority-status"><span className={`status-dot ${enabled?'':'amber'}`}/>{stopped?'Stop latch active':autonomous?'Autonomous mission active':!online||!current.online?'Backend or droid offline':!fresh?'Telemetry stale':lease?`Controlling ${current.name}`:'Ready to take control'}</div>
          <button className={`button ${lease?'secondary':'primary'}`} disabled={!lease&&!ready} onClick={()=>lease?release('Operator released control. No automatic resume.'):acquire()}>{lease?<UnlockKeyhole size={15}/>:<LockKeyhole size={15}/>}{lease?'Release control':'Take control'}</button>
          {stopped&&<button className="button secondary" disabled={!online} onClick={onRearm}><RotateCcw size={15}/>Re-arm</button>}
          <div className="speed-presets">{caps.map(c=><button key={c.speed} aria-pressed={cap===c.speed} className={cap===c.speed?'chosen':''} onClick={()=>{halt('Speed changed. Release and reapply input.');setCap(c.speed)}}><span>{c.name}</span><strong>{c.speed.toFixed(2)}<small> m/s</small></strong></button>)}</div>
          <button className="button manual-stop" onClick={()=>{halt();setLease(false);onStop()}}><Square size={14} fill="currentColor"/>Stop all droids</button>
        </section>
      </aside>
    </div>

    <details className="manual-diagnostics"><summary>Simulation tools</summary><div className="diagnostic-body">
      <div className="manual-test-switches"><button role="switch" aria-label="Fresh manual telemetry" aria-checked={fresh} onClick={()=>setFresh(!fresh)}><span className={`mini-toggle ${fresh?'on':''}`}/>{fresh?'Telemetry fresh':'Telemetry stale'}</button>{mode==='overhead'&&<button role="switch" aria-label="Valid overhead calibration" aria-checked={calibrated} onClick={()=>{halt();setTarget(null);setPreview([]);setCalibrated(!calibrated)}}><span className={`mini-toggle ${calibrated?'on':''}`}/>{calibrated?'Calibration valid':'Calibration invalid'}</button>}</div>
      <div className="payload-panel"><span className="console-eyebrow">LOCAL INPUT PREVIEW</span><code>{mode==='fpv'?`drive_speed(x=${round(wire.x)}, y=${round(wire.y)}, z=${round(wire.z)}, timeout=0.5)`:target?`destination: { x: ${target.x.toFixed(2)}, y: ${target.y.toFixed(2)}, map: "fixture-01" }`:'destination: not selected'}</code><small>{packet} local ticks · {DEMO_TIMEOUT_MS} ms draft expiry · app limits are not hardware-validated.</small></div>
      <a href="https://robomaster-dev.readthedocs.io/en/latest/python_sdk/robomaster.html#robomaster.chassis" target="_blank" rel="noreferrer">DJI SDK reference<ChevronRight size={12}/></a>
    </div></details>
  </section>;
}
