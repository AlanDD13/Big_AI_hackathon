export const CAMERA_STATUS = '/api/camera/status';
export const CAMERA_FEED = '/api/camera/feed';
// Rover feed paths are the intended backend link. They are not served yet.
export const ROVER_CAMERA_STATUS = '/api/rover/camera/status';
export const ROVER_CAMERA_FEED = '/api/rover/camera/feed';
export function parseCameraStatus(value: unknown): { configured: boolean; captureFreshness: 'unverified' } {
  if (!value || typeof value !== 'object') throw new Error('Unsupported camera status.');
  const status = value as Record<string, unknown>;
  if (typeof status.configured !== 'boolean' || status.captureFreshness !== 'unverified') throw new Error('Unsupported camera status.');
  return { configured: status.configured, captureFreshness: 'unverified' };
}
export const cameraRetryDelay = (attempt: number) => Math.min(10_000, 1000 * 2 ** Math.min(4, Math.max(0, attempt)));
