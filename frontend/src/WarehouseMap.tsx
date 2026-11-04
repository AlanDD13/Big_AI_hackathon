import { objects } from './domain';
import type { Stage } from './domain';

export default function WarehouseMap({ selected, onSelect, stage, zoom, routes, stopped, routeObjectId }: { selected: string; onSelect: (id: string) => void; stage?: Stage; zoom: number; routes: boolean; stopped: boolean; routeObjectId?: string | null }) {
  const selectedObject = objects.find(o => o.id === (routeObjectId || selected));
  const point = selectedObject?.point;
  const targetX = point ? 65 + point.x * 29 : 308;
  const targetY = point ? 340 - point.y * 25 : 203;
  const moving = stage && !['QUEUED', 'COMPLETED', 'STOPPED', 'CANCELLED'].includes(stage);
  const robotX = moving ? targetX - 35 : 126;
  const robotY = moving ? targetY + 38 : 335;
  return <svg viewBox="0 0 720 425" className="warehouse-map" role="group" aria-label="Simulated warehouse floor map. Select an object marker to inspect it.">
    <defs>
      <pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" fill="none" stroke="#e0e5e4" strokeWidth=".65"/></pattern>
      <pattern id="zone" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="7" stroke="#a7d6c4" strokeWidth="2"/></pattern>
      <filter id="shadow"><feDropShadow dx="0" dy="3" stdDeviation="5" floodOpacity=".12"/></filter>
    </defs>
    <rect width="720" height="425" fill="#f7f9f8"/><rect width="720" height="425" fill="url(#grid)"/>
    <g transform={`translate(${360 - 360 * zoom} ${212 - 212 * zoom}) scale(${zoom})`}>
      <path d="M55 65 H665 V365 H55 Z" stroke="#aebbb7" strokeWidth="3" fill="#fff" fillOpacity=".2"/>
      <path d="M89 365 H164" stroke="#f7f9f8" strokeWidth="5"/>
      <text x="55" y="46" className="svg-caption">NORTH WAREHOUSE · FLOOR 01</text>
      {[120, 285, 450].map((x,i) => <g key={x}>
        <text x={x+40} y="92" textAnchor="middle" className="svg-aisle">AISLE {String.fromCharCode(65+i)}</text>
        {[112,220].map(y => <g key={y}><rect x={x} y={y} width="85" height="70" rx="4" fill="#e9edeb" stroke="#d5dcd9"/>
          {[1,2,3].map(n=><line key={n} x1={x+n*21} y1={y+6} x2={x+n*21} y2={y+64} stroke="#d4dbd7"/>)}
          <line x1={x+6} y1={y+35} x2={x+79} y2={y+35} stroke="#d4dbd7"/></g>)}
      </g>)}
      <rect x="576" y="112" width="65" height="100" rx="4" fill="#edf7f2" stroke="#b8d8c8" strokeDasharray="4 3"/>
      <rect x="576" y="112" width="65" height="100" rx="4" fill="url(#zone)" opacity=".35"/>
      <text x="608" y="154" textAnchor="middle" className="svg-zone">DROP</text><text x="608" y="168" textAnchor="middle" className="svg-zone">ZONE</text>
      <rect x="80" y="308" width="94" height="43" rx="5" fill="#e0eeeb" stroke="#b5ccc4"/>
      <text x="127" y="329" textAnchor="middle" className="svg-zone">BASE 01</text><text x="127" y="341" textAnchor="middle" className="svg-small">DOCKING STATION</text>
      {routes && point && <path d={`M126 335 H247 V203 H${targetX} V${targetY}`} fill="none" stroke="#48a58a" strokeWidth="2.5" strokeDasharray="6 6" className={moving ? 'route-moving' : ''}/>}
      {objects.filter(o=>o.point).map(o => {
        const x = 65 + o.point!.x*29, y = 340-o.point!.y*25, active = selected===o.id;
        return <g key={o.id} role="button" tabIndex={0} aria-label={`Select ${o.name}`} onClick={()=>onSelect(o.id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(o.id)}}} className="map-marker">
          {active && <circle cx={x} cy={y} r="25" fill="#d7eae0" opacity=".7"/>}
          <circle cx={x} cy={y} r="13" fill={active?'#183f35':'#fff'} stroke={active?'#183f35':'#cfb67c'} strokeWidth="1.5" filter="url(#shadow)"/>
          <path d={`M${x-4} ${y-4} h8 v8 h-8z`} fill="none" stroke={active?'#fff':'#a58745'} strokeWidth="1.5"/>
          {active && <g><rect x={x-51} y={y-53} width="102" height="24" rx="5" fill="#183f35"/><text x={x} y={y-37} textAnchor="middle" fill="white" fontSize="10" fontWeight="600">{o.name}</text></g>}
        </g>;
      })}
      <g transform={`translate(${robotX} ${robotY})`} className="robot-position">
        <circle r="26" fill={stopped?'#f4dcd8':'#d6ebe3'} opacity=".7"/>
        <rect x="-14" y="-14" width="28" height="28" rx="8" fill={stopped?'#b6574c':'#3d806b'} stroke="#fff" strokeWidth="3" filter="url(#shadow)"/>
        <path d="M-5 3 L0 -5 L5 3 Z" fill="white"/>
        <rect x="-27" y="28" width="54" height="20" rx="4" fill="white" stroke="#dbe3df"/><text y="41" textAnchor="middle" fontSize="9" fill="#36544b" fontWeight="600">ROVER 01</text>
      </g>
    </g>
    <g transform="translate(677 28)"><path d="M0 21 V0 M-4 6 L0 0 L4 6" fill="none" stroke="#697c74"/><text x="0" y="-7" textAnchor="middle" className="svg-small">N</text></g>
    <path d="M586 394 V399 H646 V394" fill="none" stroke="#7d8b85"/><text x="616" y="389" textAnchor="middle" className="svg-small">2 METRES · FIXTURE SCALE</text>
  </svg>;
}
