import {test} from 'node:test';
import assert from 'node:assert/strict';
import {applyGate,createDroids,integrate,isFree,planFixtureRoute,pointerToMap,stepRoute,velocityFromInput,ZERO} from '../src/manual.ts';

const valid={lease:true,online:true,stopped:false,autonomous:false,fresh:true};
test('joystick diagonal respects app speed cap and DJI speed-axis signs',()=>{
  const v=velocityFromInput(1,1,1,.3);
  assert.ok(Math.abs(Math.hypot(v.x,v.y)-.3)<1e-9);
  assert.ok(v.x>0&&v.y>0);assert.equal(v.z,60);
  assert.equal(velocityFromInput(0,1,0,50).x,.5);
  assert.deepEqual(velocityFromInput(NaN,1,0,.3),ZERO);
});
test('manual intent is zeroed on lost authority, stale telemetry or expiry',()=>{
  const v=velocityFromInput(0,1,0,.3);
  for(const gate of [{...valid,lease:false},{...valid,online:false},{...valid,stopped:true},{...valid,autonomous:true},{...valid,fresh:false}]){
    assert.deepEqual(applyGate(v,gate,100,150),ZERO);
  }
  assert.deepEqual(applyGate(v,valid,100,601),ZERO);
  assert.deepEqual(applyGate(v,valid,100,50),ZERO);
  assert.deepEqual(applyGate(v,valid,100,600),v);
});
test('SDK body-right and clockwise speed transform into map CCW coordinates',()=>{
  const pose={x:2,y:2,yaw:0};
  assert.ok(integrate(pose,{x:0,y:.3,z:0},.1).y<2);
  assert.ok(integrate(pose,{x:.3,y:0,z:0},.1).x>2);
  assert.ok(integrate(pose,{x:0,y:0,z:60},.1).yaw<0);
  assert.equal(integrate({x:19.5,y:2,yaw:0},{x:.3,y:0,z:0},.1).x,19.5);
});
test('overhead screen conversion flips vertical camera coordinates explicitly',()=>{
  const rect={left:100,top:50,width:800,height:480};
  assert.deepEqual(pointerToMap({x:100,y:50},rect),{x:0,y:12});
  assert.deepEqual(pointerToMap({x:500,y:290},rect),{x:10,y:6});
});
test('fixture route avoids racks and other droids; invalid targets fail',()=>{
  const start=createDroids()[0].pose,target={x:17,y:10};
  const route=planFixtureRoute(start,target,[{x:10,y:1.5}]);
  assert.deepEqual(route[route.length-1],target);
  assert.ok(route.every(p=>isFree(p,[{x:10,y:1.5}])));
  assert.throws(()=>planFixtureRoute(start,{x:4,y:4}),/clear floor/);
  assert.throws(()=>planFixtureRoute(start,{x:100,y:4}),/clear floor/);
  assert.throws(()=>planFixtureRoute(start,{x:10,y:1.5},[{x:10,y:1.5}]),/clear floor/);
});
test('fixture click-to-move reaches target without crossing occupied space',()=>{
  let pose=createDroids()[0].pose;
  const target={x:6.2,y:6.3};let route=planFixtureRoute(pose,target).slice(1);
  for(let i=0;i<10000&&route.length;i++){
    const result=stepRoute(pose,route,.5,.1);pose=result.pose;route=result.route;
    assert.ok(isFree(pose));
  }
  assert.equal(route.length,0);assert.ok(Math.hypot(pose.x-target.x,pose.y-target.y)<.05);
});
