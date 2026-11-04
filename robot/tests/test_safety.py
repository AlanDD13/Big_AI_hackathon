"""Behavioral safety tests; a recording output callback never opens the SDK."""

import threading
import time
import unittest

from robot.safety import Limits, SafetyController, SafetyError, Watchdog


class Clock:
    def __init__(self):
        self.now = 10.0
        self.utc = 1000.0

    def advance(self, delta):
        self.now += delta
        self.utc += delta


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.outputs = []
        self.c = SafetyController(lambda *v: self.outputs.append(v),
                                  clock=lambda: self.clock.now,
                                  wall=lambda: self.clock.utc)
        self.c.stop("STARTUP")
        self.confirm()
        self.generation = self.c.rearm()

    def confirm(self):
        for _ in range(4):
            self.clock.advance(0.11)
            self.c.heartbeat()
            self.c.observe(0, 0, 0)

    def drive(self, sequence=1, ttl=0.5, command="test", **kw):
        values = dict(command_id=command, generation=self.generation,
                      sequence=sequence, issued_at=self.clock.utc,
                      expires_at=self.clock.utc + ttl, x=0.05, y=0, yaw=0)
        values["boot_id"] = self.c.boot_id
        values.update(kw)
        return self.c.drive(**values)

    def test_startup_is_latched_until_fresh_stationary_telemetry(self):
        c = SafetyController(lambda *v: None, clock=lambda: self.clock.now)
        self.assertEqual(c.status()["stage"], "UNKNOWN")
        with self.assertRaisesRegex(SafetyError, "STOP_NOT_CONFIRMED"):
            c.rearm()

    def test_silence_cutoff_and_latched_recovery(self):
        self.drive()
        self.clock.advance(0.301)
        self.c.observe(0.05, 0, 0)
        self.c.heartbeat()
        self.c.tick()
        self.assertEqual(self.outputs[-1], (0, 0, 0))
        self.assertEqual(self.c.status()["reason"], "INPUT_SILENCE")
        self.assertEqual(self.c.status()["stage"], "UNKNOWN")
        with self.assertRaisesRegex(SafetyError, "STOP_LOCKED"):
            self.drive(sequence=2)
        self.confirm()
        self.assertEqual(self.c.status()["stage"], "STOPPED")

    def test_deadline_can_stop_before_silence(self):
        self.drive(ttl=0.1)
        self.clock.advance(0.101)
        self.c.tick()
        self.assertEqual(self.c.status()["reason"], "COMMAND_EXPIRED")

    def test_silence_guard_leaves_polling_headroom(self):
        self.drive()
        self.clock.advance(0.24)
        self.c.tick()
        self.assertTrue(self.c.status()["armed"])
        self.clock.advance(0.02)
        self.c.tick()
        self.assertEqual(self.c.status()["reason"], "INPUT_SILENCE")

    def test_stale_telemetry_blocks_and_stops(self):
        self.drive()
        self.clock.advance(0.501)
        self.c.heartbeat()
        self.c.tick()
        self.assertEqual(self.c.status()["reason"], "TELEMETRY_STALE")
        self.assertFalse(self.c.status()["stopConfirmed"])

    def test_heartbeat_lost_despite_fresh_telemetry(self):
        self.clock.advance(1.001)
        self.c.observe(0, 0, 0)
        self.c.tick()
        self.assertEqual(self.c.status()["reason"], "HEARTBEAT_LOST")

    def test_rearm_never_replays_velocity_or_old_command(self):
        self.drive()
        self.c.stop()
        self.confirm()
        count = len(self.outputs)
        old_generation = self.generation
        self.generation = self.c.rearm()
        self.assertGreater(self.generation, old_generation)
        self.assertEqual(len(self.outputs), count)
        self.assertEqual(self.c.status()["stage"], "IDLE")
        with self.assertRaisesRegex(SafetyError, "AUTHORITY_CONFLICT"):
            self.drive(sequence=2)
        self.assertEqual(self.outputs[-1], (0, 0, 0))

    def test_fresh_authority_after_rearm_can_move(self):
        self.drive()
        self.c.stop()
        self.confirm()
        self.generation = self.c.rearm()
        self.drive(command="new")
        self.assertEqual(self.c.status()["stage"], "DRIVING")

    def test_duplicate_or_lower_sequence_stops_without_refreshing(self):
        for sequence in (1, 0, -1, True):
            with self.subTest(sequence=sequence):
                self.setUp()
                self.drive()
                with self.assertRaisesRegex(SafetyError, "STALE_SEQUENCE"):
                    self.drive(sequence=sequence)
                self.assertEqual(self.outputs[-1], (0, 0, 0))

    def test_old_generation_cannot_move(self):
        with self.assertRaisesRegex(SafetyError, "STALE_GENERATION"):
            self.drive(generation=self.generation - 1)
        self.assertEqual(self.outputs[-1], (0, 0, 0))

    def test_expired_future_or_excessive_deadline_rejected(self):
        for issued, expiry in ((999, 999.5), (1001, 1001.2), (1000, 1001)):
            with self.subTest(issued=issued, expiry=expiry):
                self.setUp()
                with self.assertRaisesRegex(SafetyError, "EXPIRED_OR_INVALID_DEADLINE"):
                    self.drive(issued_at=issued, expires_at=expiry)
                self.assertFalse(any(any(v) for v in self.outputs))

    def test_nan_infinity_and_caps_never_reach_motors(self):
        for value in (float("nan"), float("inf"), -1, True):
            with self.subTest(value=value):
                self.setUp()
                with self.assertRaises(SafetyError):
                    self.drive(x=value)
                self.assertFalse(any(any(v) for v in self.outputs))

    def test_wall_clock_jump_cannot_extend_accepted_intent(self):
        self.drive(ttl=0.2)
        self.clock.advance(0.201)
        self.clock.utc -= 10000
        self.c.tick()
        self.assertEqual(self.c.status()["reason"], "COMMAND_EXPIRED")

    def test_wall_clock_is_sampled_once_per_intent(self):
        calls = []
        def wall():
            calls.append(1)
            return self.clock.utc if len(calls) == 1 else self.clock.utc - 100
        self.c._wall = wall
        self.drive(ttl=0.1)
        self.assertEqual(len(calls), 1)
        self.clock.advance(0.101)
        self.c.tick()
        self.assertEqual(self.c.status()["reason"], "COMMAND_EXPIRED")

    def test_sdk_no_ack_false_return_does_not_fake_stop_confirmation(self):
        self.c._write = lambda *v: False
        self.drive()
        self.c.stop()
        self.assertEqual(self.c.status()["stage"], "UNKNOWN")
        self.confirm()
        self.assertEqual(self.c.status()["stage"], "STOPPED")

    def test_stop_requires_new_observations_not_pre_stop_quiet_samples(self):
        self.drive()
        self.c.stop()
        for _ in range(5):
            self.c.observe(0, 0, 0)
        self.assertEqual(self.c.status()["stage"], "UNKNOWN")
        self.confirm()
        self.assertEqual(self.c.status()["stage"], "STOPPED")

    def test_yaw_motion_prevents_stop_confirmation(self):
        self.c.stop()
        for _ in range(5):
            self.clock.advance(0.1)
            self.c.observe(0, 0, 5)
        self.assertEqual(self.c.status()["stage"], "UNKNOWN")

    def test_unexpected_motion_revokes_previously_confirmed_stop(self):
        self.c.stop()
        self.confirm()
        self.clock.advance(0.1)
        self.c.observe(0.1, 0, 0)
        self.assertEqual(self.c.status()["stage"], "UNKNOWN")
        self.assertEqual(self.c.status()["reason"], "UNEXPECTED_MOTION")

    def test_lost_stop_and_missing_telemetry_remain_unknown(self):
        self.drive()
        self.c.stop()
        self.clock.advance(1.6)
        self.c.tick()
        self.assertTrue(self.c.status()["stopUnconfirmed"])
        with self.assertRaisesRegex(SafetyError, "STOP_NOT_CONFIRMED"):
            self.c.rearm()

    def test_transport_failure_does_not_claim_stopped(self):
        def broken(*args):
            raise OSError("offline")
        self.c._write = broken
        with self.assertRaisesRegex(SafetyError, "TRANSPORT_ERROR"):
            self.drive()
        self.confirm()
        self.assertEqual(self.c.status()["stage"], "UNKNOWN")
        self.assertEqual(self.c.status()["transportError"], "OSError")
        self.clock.advance(2)
        self.c.tick()
        self.assertTrue(self.c.status()["stopUnconfirmed"])

    def test_bad_telemetry_stops(self):
        self.drive()
        self.c.observe(float("nan"), 0, 0)
        self.assertEqual(self.c.status()["reason"], "INVALID_TELEMETRY")

    def test_bad_telemetry_revokes_confirmed_stop(self):
        self.c.stop()
        self.confirm()
        self.c.observe(float("nan"), 0, 0)
        self.assertEqual(self.c.status()["stage"], "UNKNOWN")
        self.assertFalse(self.c.status()["telemetryFresh"])
        with self.assertRaises(SafetyError):
            self.c.rearm()

    def test_unexpected_motion_while_rearmed_idle_latches_again(self):
        self.c.observe(0.1, 0, 0)
        self.assertFalse(self.c.status()["armed"])
        self.assertEqual(self.c.status()["reason"], "UNEXPECTED_MOTION")

    def test_telemetry_gap_resets_stationary_confirmation(self):
        self.c.stop()
        self.clock.advance(0.1)
        self.c.observe(0, 0, 0)
        self.clock.advance(0.6)
        self.c.observe(0, 0, 0)
        self.assertEqual(self.c.status()["stage"], "UNKNOWN")

    def test_restart_has_new_boot_and_starts_locked(self):
        c = SafetyController(lambda *v: None)
        self.assertNotEqual(c.boot_id, self.c.boot_id)
        self.assertFalse(c.status()["armed"])

    def test_previous_boot_cannot_reuse_generation(self):
        with self.assertRaisesRegex(SafetyError, "STALE_BOOT"):
            self.drive(boot_id="previous-boot")
        self.assertFalse(any(any(v) for v in self.outputs))

    def test_stop_and_drive_race_ends_zero_and_locked(self):
        self.drive()
        barrier = threading.Barrier(3)
        def drive():
            barrier.wait()
            try:
                self.drive(sequence=2)
            except SafetyError:
                pass
        def stop():
            barrier.wait()
            self.c.stop()
        threads = [threading.Thread(target=drive), threading.Thread(target=stop)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.outputs[-1], (0, 0, 0))
        self.assertFalse(self.c.status()["armed"])

    def test_limits_cannot_disable_watchdog_or_inflate_caps(self):
        for kwargs in ({"silence_s": 1}, {"poll_s": 1}, {"max_x": 1}, {"heartbeat_s": float("nan")}):
            with self.assertRaises(ValueError):
                Limits(**kwargs)

    def test_real_watchdog_thread_detects_without_more_requests(self):
        self.drive()
        stopped = threading.Event()
        self.c._write = lambda *v: stopped.set() if not any(v) else None
        watchdog = Watchdog(self.c)
        watchdog.start()
        try:
            self.clock.advance(0.301)
            self.assertTrue(stopped.wait(0.3))
        finally:
            watchdog.close()
        self.assertFalse(self.c.status()["armed"])


if __name__ == "__main__":
    unittest.main()
