// Frontend-only view models. These are not agreed backend wire contracts.
export type TaskKind = 'Recovery' | 'Warehouse scan' | 'Inspection' | 'Tool delivery';
export type Stage = 'QUEUED' | 'NAVIGATING' | 'ALIGNING' | 'PICKING' | 'TRANSPORTING' | 'DROPPING' | 'RETURNING' | 'COMPLETED' | 'STOPPED' | 'CANCELLED';
export type MapPoint = { x: number; y: number };
export type DetectedObject = { id: string; name: string; category: string; confidence: number; point: MapPoint | null; profile: string | null; zone: string };
export type Mission = { id: string; kind: TaskKind; title: string; objectId: string | null; destination: string; stage: Stage; createdAt: string };
export type SimState = { missions: Mission[]; stopped: boolean; online: boolean };
export const stages: Stage[] = ['QUEUED', 'NAVIGATING', 'ALIGNING', 'PICKING', 'TRANSPORTING', 'DROPPING', 'RETURNING', 'COMPLETED'];
export const objects: DetectedObject[] = [
  { id: 'OBJ-014', name: 'Cordless drill', category: 'Power tool', confidence: 98, point: { x: 8.4, y: 5.5 }, profile: 'demo-drill', zone: 'Aisle B' },
  { id: 'OBJ-015', name: 'Safety helmet', category: 'Safety equipment', confidence: 96, point: { x: 12.1, y: 6.1 }, profile: 'demo-helmet', zone: 'Aisle C' },
  { id: 'OBJ-016', name: 'Tool case', category: 'Tool storage', confidence: 94, point: { x: 5.1, y: 3 }, profile: 'demo-case', zone: 'Aisle A' },
  { id: 'OBJ-017', name: 'Unidentified item', category: 'Needs review', confidence: 62, point: null, profile: null, zone: 'Position uncertain' },
];
export const initialState = (): SimState => ({ online: true, stopped: false, missions: [
  { id: 'MSN-001', kind: 'Recovery', title: 'Return torque wrench', objectId: null, destination: 'Tool station', stage: 'COMPLETED', createdAt: 'Earlier this session' },
] });
export const isActive = (m: Mission) => !['COMPLETED', 'STOPPED', 'CANCELLED'].includes(m.stage);
export const stageLabel = (s: Stage) => s.toLowerCase().replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
export function queueMission(state: SimState, object: DetectedObject, destination: string): SimState {
  if (!state.online) throw new Error('Mock backend unavailable. Request was not sent.');
  if (state.stopped) throw new Error('Rover is stopped. Explicitly re-arm before requesting a new mission.');
  if (!object.point || !object.profile) throw new Error('This object needs a verified position and tested pickup profile. No mission created.');
  if (state.missions.some(m => isActive(m) && m.objectId === object.id)) throw new Error('This object already has an active mission.');
  const mission: Mission = { id: `MSN-${String(state.missions.length + 1).padStart(3, '0')}`, kind: 'Recovery', title: `Recover ${object.name.toLowerCase()}`, objectId: object.id, destination, stage: 'QUEUED', createdAt: 'This session' };
  return { ...state, missions: [mission, ...state.missions] };
}
export function tick(state: SimState): SimState {
  if (state.stopped || !state.online) return state;
  // A single rover runs one mission at a time, oldest first.
  const current = [...state.missions].reverse().find(isActive);
  if (!current) return state;
  const next = stages[Math.min(stages.indexOf(current.stage) + 1, stages.length - 1)];
  return { ...state, missions: state.missions.map(m => m.id === current.id ? { ...m, stage: next } : m) };
}
export function stop(state: SimState): SimState {
  if (!state.online) throw new Error('Stop could not be confirmed: mock backend is unavailable. Physical rover state would be unknown.');
  return { ...state, stopped: true, missions: state.missions.map(m => isActive(m) ? { ...m, stage: 'STOPPED' } : m) };
}
export function cancel(state: SimState, id: string): SimState {
  if (!state.online) throw new Error('Cancellation could not be confirmed: mock backend is unavailable.');
  return { ...state, missions: state.missions.map(m => m.id === id && isActive(m) ? { ...m, stage: 'CANCELLED' } : m) };
}
