"""Failed durable acceptance must leave Vision retries and dashboard state intact."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from backend.postgres_projection import StorageUnavailable
from backend.state import BackendState
from backend.vision_ingest import ACCEPTED, VisionConflict, parse_vision_event


FIXTURE = json.loads(
    Path(__file__).with_name("fixtures").joinpath("vision-recovery.json").read_text(encoding="utf-8")
)
BOOT_A = FIXTURE["producerBootA"]
BOOT_B = FIXTURE["producerBootB"]


def publication(sequence, *, boot=BOOT_A, version=1, event_type="object.updated"):
    if event_type.startswith("object.") or event_type == "alert.potential_target":
        payload = deepcopy(FIXTURE["sampleObject"])
        payload.update(producerBootId=boot, entityVersion=version)
    else:
        payload = {"cameraId": "phone-1", "reason": "CAMERA_UNAVAILABLE"}
    return parse_vision_event({
        "schemaVersion": "vision.v1",
        "producerBootId": boot,
        "eventId": f"{boot}:{sequence}",
        "sequence": sequence,
        "type": event_type,
        "timestamp": 1000.0 + sequence,
        "payload": payload,
    }, ACCEPTED)


class FailingVisionLog:
    def __init__(self):
        self.failure = None
        self.saved = []

    def save(self, event, body):
        if self.failure is not None:
            raise self.failure
        self.saved.append((event.event_id, deepcopy(body)))


class VisionFailureTests(unittest.TestCase):
    def setUp(self):
        self.log = FailingVisionLog()
        self.state = BackendState(vision_log=self.log, now=lambda: 1100.0)
        self.session = self.state.open_session()

    def memory(self):
        # Include both public state and retry/order/cursor bookkeeping.
        return deepcopy({
            name: value for name, value in vars(self.state).items()
            if name not in {"_lock", "_changed", "_now", "_token", "_vision_log", "_observation_log"}
        })

    def fail_then_retry(self, event, *, discarded=False):
        snapshot = self.state.snapshot(self.session)
        before = self.memory()
        saved = deepcopy(self.log.saved)
        for failure, expected in (
            (StorageUnavailable("injected outage"), StorageUnavailable),
            (ValueError("injected conflict"), VisionConflict),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.log.failure = failure
                with self.assertRaises(expected):
                    self.state.ingest_vision(event)
                self.assertEqual(self.memory(), before)
                self.assertEqual(self.log.saved, saved)
                self.assertEqual(self.state.snapshot(self.session), snapshot)
                self.assertEqual(list(self.state.follow(
                    self.session, snapshot["cursor"], idle_timeout=0,
                )), [])
        self.log.failure = None
        accepted = self.state.ingest_vision(event)
        self.assertFalse(accepted.replayed)
        self.assertEqual(accepted.discarded, discarded)
        self.assertEqual(len(self.log.saved), len(saved) + 1)
        after = self.memory()
        replay = self.state.ingest_vision(event)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.backend_revision, accepted.backend_revision)
        self.assertEqual(self.memory(), after)
        self.assertEqual(len(self.log.saved), len(saved) + 1)
        return accepted

    def test_first_object_failure_does_not_consume_revision_or_boot(self):
        accepted = self.fail_then_retry(publication(1))
        self.assertEqual(accepted.backend_revision, 1)

    def test_update_failure_does_not_consume_revision(self):
        self.state.ingest_vision(publication(1))
        accepted = self.fail_then_retry(publication(2, version=2))
        self.assertEqual(accepted.backend_revision, 2)

    def test_new_boot_failure_does_not_retire_current_boot(self):
        self.state.ingest_vision(publication(1))
        self.fail_then_retry(publication(1, boot=BOOT_B))
        self.assertEqual(self.state._current_vision_boot, BOOT_B)
        self.assertEqual(self.state._stale_vision_boots, {BOOT_A})

    def test_old_boot_can_continue_after_failed_boot_replacement(self):
        self.state.ingest_vision(publication(1))
        self.log.failure = StorageUnavailable("injected outage")
        with self.assertRaises(StorageUnavailable):
            self.state.ingest_vision(publication(1, boot=BOOT_B))
        self.log.failure = None
        result = self.state.ingest_vision(publication(2, version=2))
        self.assertFalse(result.discarded)
        self.assertEqual(result.backend_revision, 2)

    def test_stale_entity_failure_does_not_advance_sequence(self):
        self.state.ingest_vision(publication(1, version=2))
        self.fail_then_retry(publication(2, version=1), discarded=True)
        result = self.state.ingest_vision(publication(3, version=3))
        self.assertFalse(result.discarded)
        self.assertEqual(result.backend_revision, 2)

    def test_stale_boot_failure_does_not_remember_event(self):
        self.state.ingest_vision(publication(1))
        self.state.ingest_vision(publication(1, boot=BOOT_B))
        self.fail_then_retry(publication(2, version=2), discarded=True)
        self.assertEqual(self.state._current_vision_boot, BOOT_B)

    def test_old_sequence_failure_does_not_remember_event(self):
        self.state.ingest_vision(publication(2, version=2))
        self.fail_then_retry(publication(1), discarded=True)

    def test_alert_failure_does_not_consume_revision_or_alert_episode(self):
        accepted = self.fail_then_retry(publication(1, event_type="alert.potential_target"))
        snapshot = self.state.snapshot(self.session)
        self.assertEqual(snapshot["objects"][0]["backendRevision"], 1)
        self.assertEqual(len(snapshot["alerts"]), 1)
        self.assertEqual(accepted.backend_revision, 1)

    def test_new_boot_notice_failure_does_not_change_epoch_or_alerts(self):
        self.state.ingest_vision(publication(1))
        self.fail_then_retry(publication(1, boot=BOOT_B, event_type="alert.camera_unavailable"))
        self.assertEqual(len(self.state.snapshot(self.session)["alerts"]), 1)


if __name__ == "__main__":
    unittest.main()
