export type RobotProfile = { id: string; name: string; serial: string; model: 'rmepcore'; connection: 'sta'; transport: 'udp' };
export const PROFILE_KEY = 'rovr.epcore.setup.v1';
export function makeProfile(input: { id: string; name: string; serial: string }, existing: RobotProfile[]): RobotProfile {
  const name = input.name.trim();
  const serial = input.serial.trim().toUpperCase();
  if (!name || name.length > 48) throw new Error('Enter a robot name between 1 and 48 characters.');
  if (!/^[A-Z0-9]{14}$/.test(serial)) throw new Error('Enter the 14 letters and digits from the robot’s serial label or DJI app.');
  if (existing.some(p => p.id !== input.id && p.serial === serial)) throw new Error('This serial number already has a saved setup.');
  return { id: input.id, name, serial, model: 'rmepcore', connection: 'sta', transport: 'udp' };
}
export function readProfiles(value: string | null): RobotProfile[] {
  if (!value) return [];
  const rows: unknown = JSON.parse(value);
  if (!Array.isArray(rows)) throw new Error('Saved robot setups could not be read.');
  const result: RobotProfile[] = [];
  for (const value of rows) {
    if (!value || typeof value !== 'object') throw new Error('Invalid saved robot setup.');
    const row = value as Record<string, unknown>;
    if (typeof row.id !== 'string' || !row.id || result.some(p => p.id === row.id) || typeof row.name !== 'string' || typeof row.serial !== 'string' || row.model !== 'rmepcore' || row.connection !== 'sta' || row.transport !== 'udp') throw new Error('Invalid saved robot setup.');
    result.push(makeProfile({ id: row.id, name: row.name, serial: row.serial }, result));
  }
  return result;
}
