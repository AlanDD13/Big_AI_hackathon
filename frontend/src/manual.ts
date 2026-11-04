// Local manual-control simulator. No robot/backend transport is implemented here.
export type Point = { x: number; y: number };
export type Pose = Point & { yaw: number }; // map yaw: radians CCW from +x
export type Velocity = { x: number; y: number; z: number }; // DJI drive_speed convention
export type Droid = { id: string; name: string; online: boolean; battery: number; pose: Pose; arm: {x:number;y:number}; gripper: 'Open'|'Closed' };
export type ManualGate = { lease: boolean; online: boolean; stopped: boolean; autonomous: boolean; fresh: boolean };
export const ZERO: Velocity = {x:0,y:0,z:0};
export const DEMO_TIMEOUT_MS = 500;
export const WORLD = {width:20,height:12};
export const RACKS = [3,8,13].flatMap(x=>[3,7].map(y=>({x,y,w:2,h:2})));
export const createDroids = (): Droid[] => [
  {id:'rm-01',name:'Droid 01',online:true,battery:86,pose:{x:2,y:1.5,yaw:Math.PI/2},arm:{x:80,y:40},gripper:'Open'},
  {id:'rm-02',name:'Droid 02',online:true,battery:74,pose:{x:10,y:1.5,yaw:Math.PI/2},arm:{x:80,y:40},gripper:'Closed'},
  {id:'rm-03',name:'Droid 03',online:false,battery:0,pose:{x:17,y:10,yaw:0},arm:{x:80,y:40},gripper:'Open'},
];
export function commandAllowed(gate: ManualGate) {return gate.lease&&gate.online&&!gate.stopped&&!gate.autonomous&&gate.fresh;}
export function velocityFromInput(strafe: number, forward: number, turn: number, speed: number): Velocity {
  if (![strafe,forward,turn,speed].every(Number.isFinite)) return {...ZERO};
  const length=Math.max(1,Math.hypot(strafe,forward));
  const cap=Math.max(0,Math.min(.5,speed));
  return {x:forward/length*cap,y:strafe/length*cap,z:Math.max(-1,Math.min(1,turn))*60};
}
export function applyGate(velocity:Velocity,gate:ManualGate,lastInput:number,now:number):Velocity {
  return commandAllowed(gate)&&now-lastInput<=DEMO_TIMEOUT_MS&&now>=lastInput?velocity:{...ZERO};
}
export function isFree(p:Point,otherDroids:Point[]=[]):boolean {
  if(!Number.isFinite(p.x)||!Number.isFinite(p.y)||p.x<.5||p.y<.5||p.x>19.5||p.y>11.5)return false;
  return !RACKS.some(r=>p.x>=r.x-.35&&p.x<=r.x+r.w+.35&&p.y>=r.y-.35&&p.y<=r.y+r.h+.35)
    &&!otherDroids.some(d=>Math.hypot(d.x-p.x,d.y-p.y)<.7);
}
export function integrate(pose:Pose,velocity:Velocity,seconds:number,others:Point[]=[]):Pose {
  const dt=Math.max(0,Math.min(.1,seconds));
  // +y in DJI body coordinates is right; +z speed is clockwise in the official example.
  const dx=velocity.x*Math.cos(pose.yaw)+velocity.y*Math.sin(pose.yaw);
  const dy=velocity.x*Math.sin(pose.yaw)-velocity.y*Math.cos(pose.yaw);
  const p={x:pose.x+dx*dt,y:pose.y+dy*dt};
  return {...(isFree(p,others)?p:{x:pose.x,y:pose.y}),yaw:pose.yaw-velocity.z*Math.PI/180*dt};
}
export function pointerToMap(client:Point,rect:{left:number;top:number;width:number;height:number}):Point {
  return {x:(client.x-rect.left)/rect.width*WORLD.width,y:(1-(client.y-rect.top)/rect.height)*WORLD.height};
}
// Small fixture-only grid planner. Production transforms and routes belong to mapping/.
export function planFixtureRoute(start:Point,target:Point,others:Point[]=[]):Point[] {
  if(!isFree(target,others))throw new Error('Choose clear floor inside the calibrated fixture area.');
  const cell=(p:Point)=>({x:Math.round(p.x*2),y:Math.round(p.y*2)});
  const a=cell(start),b=cell(target),key=(p:Point)=>`${p.x},${p.y}`;
  const queue=[a],parents=new Map<string,Point|null>([[key(a),null]]);
  for(let i=0;i<queue.length;i++){
    const current=queue[i];
    if(key(current)===key(b)) {
      const path:Point[]=[];let p:Point|null=current;
      while(p){path.unshift({x:p.x/2,y:p.y/2});p=parents.get(key(p))??null;}
      // Preserve the real start and exact clicked endpoint; validate their short links.
      const result=[start,...path.slice(1),target];
      for(let j=1;j<result.length;j++)for(let k=1;k<=10;k++){
        const t=k/10,p={x:result[j-1].x+(result[j].x-result[j-1].x)*t,y:result[j-1].y+(result[j].y-result[j-1].y)*t};
        if(!isFree(p,others))throw new Error('No clear fixture route from this position.');
      }
      return result;
    }
    for(const [dx,dy] of [[1,0],[-1,0],[0,1],[0,-1]]){
      const next={x:current.x+dx,y:current.y+dy};
      if(!parents.has(key(next))&&isFree({x:next.x/2,y:next.y/2},others)) {parents.set(key(next),current);queue.push(next);}
    }
  }
  throw new Error('No clear fixture route to this destination.');
}
export function stepRoute(pose:Pose,route:Point[],speed:number,seconds:number,others:Point[]=[]):{pose:Pose;route:Point[]} {
  if(!route.length)return {pose,route};
  const target=route[0],dx=target.x-pose.x,dy=target.y-pose.y,d=Math.hypot(dx,dy);
  if(d<.04)return {pose,route:route.slice(1)};
  const step=Math.min(d,Math.max(0,Math.min(.5,speed))*Math.min(.1,Math.max(0,seconds)));
  const next={x:pose.x+dx/d*step,y:pose.y+dy/d*step,yaw:Math.atan2(dy,dx)};
  return isFree(next,others)?{pose:next,route}:{pose,route:[]};
}
