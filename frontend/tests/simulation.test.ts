import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cancel, initialState, isActive, objects, queueMission, stop, tick } from '../src/domain.ts';

test('a mapped object can complete a recovery without advancing another queued mission',()=>{
  let state=queueMission(initialState(),objects[0],'Tool station');
  state=queueMission(state,objects[1],'Drop zone A');
  const first=state.missions.find(m=>m.objectId===objects[0].id)!;
  const second=state.missions.find(m=>m.objectId===objects[1].id)!;
  for(let i=0;i<7;i++)state=tick(state);
  assert.equal(state.missions.find(m=>m.id===first.id)!.stage,'COMPLETED');
  assert.equal(state.missions.find(m=>m.id===second.id)!.stage,'QUEUED');
  state=tick(state);
  assert.equal(state.missions.find(m=>m.id===second.id)!.stage,'NAVIGATING');
});
test('uncertain location and duplicate reservations never create a mission',()=>{
  assert.throws(()=>queueMission(initialState(),objects[3],'Tool station'),/verified position/);
  const state=queueMission(initialState(),objects[0],'Tool station');
  assert.throws(()=>queueMission(state,objects[0],'Tool station'),/already has/);
  assert.equal(state.missions.filter(isActive).length,1);
});
test('stop latches; re-arm does not restart stopped missions',()=>{
  let state=tick(queueMission(initialState(),objects[0],'Tool station'));
  state=stop(state);
  assert.equal(state.stopped,true);
  assert.equal(state.missions[0].stage,'STOPPED');
  assert.deepEqual(tick(state),state);
  assert.throws(()=>queueMission(state,objects[1],'Tool station'),/re-arm/);
  const rearmed={...state,stopped:false};
  assert.equal(tick(rearmed).missions[0].stage,'STOPPED');
  assert.equal(queueMission(rearmed,objects[0],'Tool station').missions[0].stage,'QUEUED');
});
test('offline stop and cancel cannot claim success; offline progress pauses',()=>{
  const state={...queueMission(initialState(),objects[0],'Tool station'),online:false};
  assert.throws(()=>stop(state),/could not be confirmed/);
  assert.throws(()=>cancel(state,state.missions[0].id),/could not be confirmed/);
  assert.throws(()=>queueMission(state,objects[1],'Tool station'),/not sent/);
  assert.deepEqual(tick(state),state);
  assert.equal(state.stopped,false);
  assert.equal(state.missions[0].stage,'QUEUED');
});
test('confirmed simulated cancel releases an object for a new request',()=>{
  const state=queueMission(initialState(),objects[0],'Tool station');
  const cancelled=cancel(state,state.missions[0].id);
  assert.equal(cancelled.missions[0].stage,'CANCELLED');
  assert.equal(queueMission(cancelled,objects[0],'Tool station').missions[0].stage,'QUEUED');
});
