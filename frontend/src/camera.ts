export const CAMERA_STATUS = '/api/camera/status';
export const CAMERA_FEED = '/api/camera/feed';
export function parseCameraStatus(value: unknown): { configured: boolean; captureFreshness: 'unverified' } {
  if (!value || typeof value !== 'object') throw new Error('Unsupported camera status.');
  const status = value as Record<string, unknown>;
  if (typeof status.configured !== 'boolean' || status.captureFreshness !== 'unverified') throw new Error('Unsupported camera status.');
  return { configured: status.configured, captureFreshness: 'unverified' };
}
export const cameraRetryDelay = (attempt: number) => Math.min(10_000, 1000 * 2 ** Math.min(4, Math.max(0, attempt)));
