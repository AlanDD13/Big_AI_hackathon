import { number, record } from './backend.ts';
import type { BackendObject } from './backend.ts';

const choice = (value: unknown, values: string[]) => typeof value === 'string' && values.includes(value) ? value : null;
const string = (value: unknown) => typeof value === 'string' && value ? value : null;
export function perceptionFacts(object: BackendObject) {
  const classification = record(object.classification);
  const completed = object.classificationStatus === 'COMPLETED';
  const score = number(object.detectorScore);
  const advisory = record(object.visualPickupAdvisory);
  const form = choice(advisory.object_form, ['FLAT_SHEET', 'CRUMPLED', 'OTHER', 'UNCERTAIN']);
  const suitability = choice(advisory.visual_suitability, ['POSSIBLE', 'UNSUITABLE', 'UNCERTAIN']);
  const validAdvisory = object.visualPickupStatus === 'COMPLETED' && advisory.advisory === true && advisory.pickupVerified === false && form !== null && suitability !== null && string(advisory.reason) !== null;
  return {
    detectorScore: score !== null && score >= 0 && score <= 1 ? score : null,
    classificationStatus: choice(object.classificationStatus, ['PENDING', 'COMPLETED', 'UNAVAILABLE']),
    category: completed ? choice(classification.category, ['TRASH', 'POTENTIALLY_LOST_PROPERTY', 'OTHER', 'UNCERTAIN']) : null,
    confidence: completed ? choice(classification.confidence, ['LOW', 'MEDIUM', 'HIGH']) : null,
    description: completed ? string(classification.description) : null,
    reason: completed ? string(classification.reason) : null,
    provider: completed ? string(object.classificationProvider) : null,
    visualStatus: choice(object.visualPickupStatus, ['PENDING', 'COMPLETED', 'UNAVAILABLE']),
    advisory: validAdvisory ? { form, suitability, reason: string(advisory.reason)!, sourceFrameId: number(advisory.sourceFrameId) } : null,
  };
}
