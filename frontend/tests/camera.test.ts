import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CAMERA_FEED, CAMERA_STATUS, cameraRetryDelay, parseCameraStatus } from '../src/camera.ts';

test('camera configuration never becomes a live/freshness claim', () => {
  assert.deepEqual(parseCameraStatus({ configured: true, captureFreshness: 'unverified' }), { configured: true, captureFreshness: 'unverified' });
  assert.equal(parseCameraStatus({ configured: false, captureFreshness: 'unverified' }).configured, false);
  assert.throws(() => parseCameraStatus({ configured: true, captureFreshness: 'live' }));
  assert.throws(() => parseCameraStatus({ configured: 'yes', captureFreshness: 'unverified' }));
});
test('camera uses fixed same-origin routes and bounded reconnect backoff', () => {
  assert.equal(CAMERA_STATUS, '/api/camera/status'); assert.equal(CAMERA_FEED, '/api/camera/feed');
  assert.deepEqual([0, 1, 2, 3, 4, 99].map(cameraRetryDelay), [1000, 2000, 4000, 8000, 10000, 10000]);
});
