# Vision object review checkpoint

Board request: #6638580865. Frontend scope on draft PR #12. The next checkpoint
is live vision on the dashboard, while the backend owner implements ingest.
No browser-to-vision calls, robot connection changes or dispatch are added.

## Screen

Connected → Objects & alerts → **Review vision fixtures**. The preview is
explicitly labelled as fixtures, closes the backend stream while viewing it,
and never substitutes fixtures for a failed backend connection automatically.
**Return to backend** clears fixture selection and starts a new backend snapshot.

Four selectable scenarios use the same bottle object identity:

- Observed: immediate Observing row, detector score 87%, classification PENDING,
  no category/confidence/advisory, unknown mapping, no dispatch eligibility.
- Classified: category TRASH, confidence HIGH, provider fixture, detector score
  still separately 87%. Missing visual advice stays unassessed.
- Advisory: optional labelled advisory variant showing object_form OTHER,
  visual_suitability POSSIBLE and a reason. Pickup is not physically verified;
  visual appearance is not a successful grab, graspability or dispatch permission.
- Pending review: classification and visual assessment clear again; no stale
  category/confidence/advisory survives. Position becomes unverified.

`src/visionFixture.ts` copies the sample object, new_boot_reset and revocation
payload overrides from the lead's `contracts/fixtures/vision-recovery.json` at
8739ac9 on `task/AlanDD13/vision-pipeline`. The optional advisory variant is
frontend test data matching the same commit's visualPickupAdvisory schema;
the shared recovery fixture itself omits an advisory. This is not live Grok.
The fixture wrapper adds local backendRevision/cursor metadata solely for UI
review; it is not a new shared wire schema. Shared files were not changed.

## Backend projection

The live screen renders the same classification and visual-advisory fields
from backend object rows. It accepts backend `object.observed`,
`object.classified` and `object.updated` with the existing full entity envelope.
It does not consume producer vision events directly or order their revisions.
LOW/MEDIUM/HIGH confidence stays categorical and separate from detectorScore.
PENDING suppresses stale classification data even if a producer retains it.

A visual assessment displays only when visualPickupStatus is COMPLETED and its
body explicitly declares advisory=true and pickupVerified=false with valid
object_form/visual_suitability/reason fields. Missing, pending or malformed
advisories never become a grasp verdict. Evidence remains same-origin backend
only. No recognition/pickup value can enable a robot action in this screen.

Main synced through 94cb8bc, including mission queue and watchdog merges. Their
merge does not create a live HTTP robot bridge. Robot registration/status wire
is still pending and Fleet profiles stay browser-local.

## Verification

23 frontend tests, TypeScript/Vite production build and diff checks. Browser
checks cover immediate observations, completed classification without advisory,
the advisory variant, clearing after pending review, mobile width and return
to an unavailable backend without fixture leakage. No camera, Grok, shared
database or physical robot is contacted by the fixture preview.
