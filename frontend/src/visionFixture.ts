// Labelled contract fixture, not live camera/Grok evidence.
// Sample from lead branch 8739ac9: contracts/fixtures/vision-recovery.json.
import type { BackendObject } from './backend';
import { parseSnapshot } from './backend.ts';

const sample = {
  "objectId": "phone-1:session1:cccccccccccccccccccccccccccccccc",
  "cameraId": "phone-1",
  "sessionId": "session1",
  "geometryVersion": "geometry1",
  "trackId": "cccccccccccccccccccccccccccccccc",
  "frameId": 11,
  "capturedAt": 1005.0,
  "lastSeenAt": 1005.0,
  "cameraKind": "FIXED",
  "bboxPx": [
    20,
    20,
    50,
    90
  ],
  "detectorLabel": "bottle",
  "detectorScore": 0.87,
  "motionState": "STATIONARY",
  "pixelStationarySeconds": 5.0,
  "metricStationarySeconds": 5.0,
  "status": "POTENTIAL_TARGET",
  "exclusionReason": null,
  "classificationStatus": "COMPLETED",
  "classification": {
    "category": "TRASH",
    "subject_kind": "INANIMATE",
    "carrying": "NO",
    "confidence": "HIGH",
    "description": "Labelled fixture bottle",
    "reason": "Contract fixture, not live Grok"
  },
  "classificationProvider": "fixture",
  "mapping": {
    "cameraId": "phone-1",
    "sessionId": "session1",
    "geometryVersion": "geometry1",
    "frameId": 11,
    "trackId": "cccccccccccccccccccccccccccccccc",
    "mapId": "demo-floor",
    "mapVersion": "1",
    "calibrationId": "cal1",
    "calibrationValid": true,
    "capturedAt": 1005.0,
    "receivedAt": 1005.0,
    "floorPointM": [
      0.2,
      0.3
    ],
    "positionErrorM": 0.01,
    "valid": true,
    "invalidReason": null
  },
  "potentialTarget": true,
  "revision": 3,
  "entityVersion": 4,
  "producerBootId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "eligibilityReason": null,
  "frameRef": "/api/frames/phone-1/session1/11"
};

const observedPayload = {
  "producerBootId": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "entityVersion": 1,
  "revision": 0,
  "potentialTarget": false,
  "eligibilityReason": "NOT_ELIGIBLE",
  "status": "OBSERVING",
  "classification": null,
  "classificationStatus": "PENDING",
  "classificationProvider": null,
  "mapping": null,
  "metricStationarySeconds": 0,
  "frameId": 0,
  "frameRef": "/api/frames/phone-1/session1/0"
};
const pendingPayload = {
  "entityVersion": 8,
  "revision": 4,
  "potentialTarget": false,
  "eligibilityReason": "TARGET_MOVED",
  "status": "STATIC_PENDING_REVIEW",
  "classification": null,
  "classificationStatus": "PENDING",
  "classificationProvider": null,
  "mapping": null,
  "metricStationarySeconds": 0,
  "reason": "TARGET_MOVED",
  "action": "HOLD_BEFORE_PICKUP"
};

export const visionStages = ['Observed', 'Classified', 'Advisory', 'Pending review'] as const;
export type VisionStage = typeof visionStages[number];
export function visionFixture(stage: VisionStage) {
  const body: BackendObject = { ...sample, backendRevision: visionStages.indexOf(stage) + 1,
    dispatchEligible: false, pickupVerified: false, evidenceUrl: null };
  if (stage === 'Observed' || stage === 'Pending review') {
    Object.assign(body, stage === 'Observed' ? observedPayload : pendingPayload,
      { state: stage === 'Observed' ? 'OBSERVING' : 'STATIC_PENDING_REVIEW', visualPickupStatus: 'PENDING', visualPickupAdvisory: null });
  }
  if (stage === 'Advisory') {
    // Optional advisory variant using the canonical schema; the recovery fixture omits it.
    Object.assign(body, { visualPickupStatus: 'COMPLETED', visualPickupAdvisory: {
      object_form: 'OTHER', visual_suitability: 'POSSIBLE', reason: 'Illustrative bottle advisory; size and a physical grip have not been verified.',
      advisory: true, pickupVerified: false, sourceFrameId: body.frameId,
    } });
  }
  return parseSnapshot({ schemaVersion: 'backend.v1', backendBootId: 'ffffffffffffffffffffffffffffffff',
    cursor: 'labelled-fixture-only', objects: [body], alerts: [], robots: [], missions: [] });
}
