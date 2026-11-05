import { test } from 'node:test';
import assert from 'node:assert/strict';
import { perceptionFacts } from '../src/perception.ts';
import { visionFixture } from '../src/visionFixture.ts';
import { applyEvent, parseSnapshot } from '../src/backend.ts';

test('an observed object appears before classification with no invented semantics or grasp verdict', () => {
  const observed = visionFixture('Observed').objects[0];
  const facts = perceptionFacts(observed);
  assert.equal(observed.status, 'OBSERVING');
  assert.equal(facts.detectorScore, .87);
  assert.equal(facts.category, null);
  assert.equal(facts.confidence, null);
  assert.equal(facts.advisory, null);
  assert.equal(observed.dispatchEligible, false);
});
test('categorical confidence remains separate from detector score; optional advisory never verifies pickup', () => {
  const classified = visionFixture('Classified').objects[0];
  const facts = perceptionFacts(classified);
  assert.equal(facts.category, 'TRASH');
  assert.equal(facts.confidence, 'HIGH');
  assert.equal(facts.detectorScore, .87);
  assert.equal(facts.provider, 'fixture');
  assert.equal(facts.advisory, null);
  const object = visionFixture('Advisory').objects[0];
  assert.equal(perceptionFacts(object).advisory?.suitability, 'POSSIBLE');
  assert.equal(object.pickupVerified, false);
  assert.equal(object.dispatchEligible, false);
});
test('pending or malformed assessments cannot retain old classification/advisory values', () => {
  const old = visionFixture('Advisory').objects[0];
  const pending = perceptionFacts({ ...old, classificationStatus: 'PENDING', visualPickupStatus: 'PENDING' });
  assert.equal(pending.category, null);
  assert.equal(pending.confidence, null);
  assert.equal(pending.description, null);
  assert.equal(pending.advisory, null);
  assert.equal(perceptionFacts({ ...old, visualPickupAdvisory: null }).advisory, null);
  assert.equal(perceptionFacts({ ...old, visualPickupAdvisory: { ...(old.visualPickupAdvisory as object), pickupVerified: true } }).advisory, null);
  assert.equal(perceptionFacts({ ...old, detectorScore: 87 }).detectorScore, null);
  const state = applyEvent(visionFixture('Advisory'), { id: 'pending', type: 'object.updated', entity: 'object', backendBootId: 'f'.repeat(32), backendRevision: 4, body: visionFixture('Pending review').objects[0] });
  assert.equal(perceptionFacts(state.objects[0]).advisory, null);
  assert.equal(parseSnapshot(state).objects.length, 1);
});
test('grabbability estimate stays separate from detector and classification scores', () => {
  const old = visionFixture('Advisory').objects[0];
  const object = { ...old, visualPickupAdvisory: { ...(old.visualPickupAdvisory as object), grabbability_score: .91 } };
  assert.equal(perceptionFacts(object).advisory?.grabbabilityScore, .91);
  for (const score of [null, true, '0.91', -1, 1.1, NaN]) {
    assert.equal(perceptionFacts({ ...object, visualPickupAdvisory: { ...object.visualPickupAdvisory, grabbability_score: score } }).advisory?.grabbabilityScore, null);
  }
  assert.equal(perceptionFacts({ ...object, visualPickupStatus: 'PENDING' }).advisory, null);
});
