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
    def test_clean_release_allows_braking_but_blocks_rearm_and_new_drive(self):
        self.drive()
        self.c.release('user_release')
        self.clock.advance(.08)
        self.c.observe(.07,0,0)
        status=self.c.status()
        self.assertEqual(status['stage'],'UNKNOWN')
        self.assertTrue(status['releasePending'])
        self.assertFalse(status['armed'])
        self.assertEqual(status['stationarySamples'],0)
        with self.assertRaises(SafetyError):self.c.rearm()
        with self.assertRaises(SafetyError):self.drive(sequence=2)
        self.confirm()
        self.assertEqual(self.c.status()['stage'],'IDLE')
        self.assertEqual(self.outputs[-1],(0,0,0))

    def test_release_motion_after_confirmation_deadline_latches_fault(self):
        self.drive()
        self.c.release('user_release')
        self.clock.advance(self.c.limits.stop_confirmation_s+.01)
        self.c.heartbeat()
        self.c.observe(.05,0,0)
        self.assertIsNone(self.c.status()['transportError'])
        self.assertFalse(self.c.status()['releasePending'])
        self.assertEqual(self.c.status()['reason'],'UNEXPECTED_MOTION')
        self.confirm()
        self.assertEqual(self.c.status()['stage'],'STOPPED')
        self.assertFalse(self.c.status()['armed'])

    def test_invalid_sample_zero_write_does_not_deadlock_worker_on_observe_lock(self):
        self.c.observe(float('nan'),0,0)
        self.assertEqual(self.c.status()['reason'],'INVALID_TELEMETRY')
        self.assertIsNone(self.c.status()['transportError'])
        self.assertFalse(self.c.status()['writeStuck'])
        self.assertEqual(self.outputs[-1],(0,0,0))

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
        with self.assertRaisesRegex(SafetyError, "STOP_NOT_CONFIRMED") as ctx:
            c.rearm()
        self.assertEqual(c.manual_http_error(ctx.exception), (409, "NOT_STOPPED"))

    def test_silence_cutoff_and_latched_recovery(self):
        self.drive()
        self.clock.advance(0.301)
        self.c.observe(0.05, 0, 0)
        self.c.heartbeat()
        self.c.tick()
        self.assertEqual(self.outputs[-1], (0, 0, 0))
        self.assertEqual(self.c.status()["reason"], "INPUT_SILENCE")
        self.assertEqual(self.c.status()["stage"], "UNKNOWN")
        with self.assertRaisesRegex(SafetyError, "STOP_LOCKED") as ctx:
            self.drive(sequence=2)
        self.assertEqual(self.c.manual_http_error(ctx.exception), (503, "TELEMETRY_STALE"))
        self.confirm()
        self.assertEqual(self.c.status()["stage"], "STOPPED")
        with self.assertRaisesRegex(SafetyError, "STOP_LOCKED") as stopped_ctx:
            self.drive(sequence=2)
        self.assertEqual(self.c.manual_http_error(stopped_ctx.exception), (409, "STOP_LOCKED"))

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
        with self.assertRaisesRegex(SafetyError, "AUTHORITY_CONFLICT") as ctx:
            self.drive(sequence=2)
        self.assertEqual(self.c.manual_http_error(ctx.exception), (409, "LEASE_HELD"))
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
                with self.assertRaisesRegex(SafetyError, "STALE_SEQUENCE") as ctx:
                    self.drive(sequence=sequence)
                self.assertEqual(self.c.manual_http_error(ctx.exception), (409, "STALE_INTENT"))
                self.assertEqual(self.outputs[-1], (0, 0, 0))

    def test_old_generation_cannot_move(self):
        with self.assertRaisesRegex(SafetyError, "STALE_GENERATION") as ctx:
            self.drive(generation=self.generation - 1)
        self.assertEqual(self.c.manual_http_error(ctx.exception), (409, "STALE_INTENT"))
        self.assertEqual(self.outputs[-1], (0, 0, 0))

    def test_rejected_drive_during_active_session_trips_fail_closed(self):
        cases = (
            ("STALE_GENERATION", {"generation": self.generation - 1}, (409, "STALE_INTENT")),
            ("STALE_BOOT", {"boot_id": "stale-boot"}, (409, "STALE_INTENT")),
            ("AUTHORITY_CONFLICT", {"command": "competing-cmd"}, (409, "LEASE_HELD")),
            ("STALE_SEQUENCE", {"sequence": 1}, (409, "STALE_INTENT")),
            ("EXPIRED_OR_INVALID_DEADLINE", {"issued_at": 999.0, "expires_at": 999.2}, (409, "LEASE_EXPIRED")),
            ("VELOCITY_LIMIT", {"x": 0.9}, (422, "VELOCITY_LIMIT")),
        )
        for expected_code, overrides, expected_http in cases:
            with self.subTest(code=expected_code):
                self.setUp()
                status = self.drive(sequence=1, command="active-session")
                self.assertEqual(status["stage"], "DRIVING")
                self.assertTrue(status["armed"])
                self.assertEqual(status["commandId"], "active-session")
                call_args = {"sequence": 2, "command": "active-session"}
                call_args.update(overrides)
                with self.assertRaisesRegex(SafetyError, expected_code) as ctx:
                    self.drive(**call_args)
                self.assertEqual(self.c.manual_http_error(ctx.exception), expected_http)
                after = self.c.status()
                self.assertFalse(after["armed"])
                self.assertIsNone(after["commandId"])
                self.assertEqual(after["stage"], "UNKNOWN")
                self.assertEqual(after["reason"], expected_code)
                self.assertEqual(self.outputs[-1], (0.0, 0.0, 0.0))

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

    def test_monotonic_sample_id_dedupes_replays_and_allows_distinct_samples(self):
        self.drive()
        self.c.stop()
        self.clock.advance(0.05)
        self.c.observe(0, 0, 0, sample_id=10)
        # Replaying sample_id=10 or lower must not add stationary evidence even if clock advances.
        for _ in range(5):
            self.clock.advance(0.1)
            self.c.observe(0, 0, 0, sample_id=10)
            self.c.observe(0, 0, 0, sample_id=9)
        self.assertEqual(self.c.status()["stage"], "UNKNOWN")
        self.assertEqual(self.c.status()["stationarySamples"], 1)
        # Distinct sample IDs spanning >= 200 ms confirm stop.
        self.clock.advance(0.1)
        self.c.observe(0, 0, 0, sample_id=11)
        self.clock.advance(0.11)
        self.c.observe(0, 0, 0, sample_id=12)
        self.assertEqual(self.c.status()["stage"], "STOPPED")
        self.assertEqual(self.c.status()["lastSampleId"], 12)

    def test_voluntary_release_transitions_unknown_to_idle_only_after_confirmation(self):
        self.drive()
        old_gen = self.generation
        rel = self.c.release("user_release")
        self.assertEqual(rel["stage"], "UNKNOWN")
        self.assertTrue(rel["releasePending"])
        self.assertEqual(self.outputs[-1], (0.0, 0.0, 0.0))
        with self.assertRaisesRegex(SafetyError, "STOP_NOT_CONFIRMED") as rearm_ctx:
            self.c.rearm()
        self.assertEqual(self.c.manual_http_error(rearm_ctx.exception), (409, "NOT_STOPPED"))
        self.confirm()
        after = self.c.status()
        self.assertEqual(after["stage"], "IDLE")
        self.assertTrue(after["armed"])
        self.assertFalse(after["releasePending"])
        self.assertGreater(after["generation"], old_gen)

    def test_unconfirmed_voluntary_release_timeout_stays_unknown(self):
        self.drive()
        self.c.release("user_release")
        self.clock.advance(1.6)
        self.c.tick()
        status = self.c.status()
        self.assertEqual(status["stage"], "UNKNOWN")
        self.assertTrue(status["stopUnconfirmed"])
        self.assertFalse(status["stopConfirmed"])

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

    def test_stalled_output_does_not_block_stop_tick_or_status_and_recovers_to_zero(self):
        write_started = threading.Event()
        unblock_write = threading.Event()
        recorded = []

        def stalled_write(*v):
            recorded.append(v)
            if any(v):
                write_started.set()
                unblock_write.wait(timeout=2.0)

        self.c._write = stalled_write
        drive_err = []
        drive_thread = threading.Thread(
            target=lambda: self._run_catching(lambda: self.drive(sequence=1), drive_err)
        )
        drive_thread.start()
        self.assertTrue(write_started.wait(timeout=0.5))

        t0 = time.monotonic()
        status_during = self.c.status()
        status_elapsed = time.monotonic() - t0
        self.assertLess(status_elapsed, 0.05)
        self.assertIsNotNone(status_during["bridgeBootId"])

        t_stop = time.monotonic()
        stopped_status = self.c.stop("OPERATOR_STOP")
        stop_elapsed = time.monotonic() - t_stop
        # Must stay well below the 400 ms probe / 250 ms silence gate.
        self.assertLess(stop_elapsed, 0.15)
        self.assertFalse(stopped_status["armed"])
        self.assertEqual(stopped_status["stage"], "UNKNOWN")
        self.assertTrue(stopped_status["writeStuck"])
        self.assertEqual(stopped_status["transportError"], "WRITE_TIMEOUT")

        drive_thread.join(timeout=0.5)
        self.assertFalse(drive_thread.is_alive())
        self.assertEqual(len(drive_err), 1)
        self.assertEqual(drive_err[0].code, "TRANSPORT_ERROR")

        # Subsequent tick() while write is still hung returns without blocking.
        t_tick = time.monotonic()
        self.c.tick()
        self.assertLess(time.monotonic() - t_tick, 0.05)

        # Once the stalled write unblocks, the pending zero is immediately written.
        unblock_write.set()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and self.c.status()["writeStuck"]:
            time.sleep(0.01)
        self.assertFalse(self.c.status()["writeStuck"])
        self.assertEqual(recorded[-1], (0.0, 0.0, 0.0))

    def test_lock_held_or_slow_drive_racing_tick_never_delays_watchdog_or_applies_stale_velocity(self):
        in_write = threading.Event()
        release_write = threading.Event()

        def slow_write(*v):
            self.outputs.append(v)
            if any(v):
                in_write.set()
                release_write.wait(timeout=1.0)

        self.c._write = slow_write
        t = threading.Thread(target=lambda: self._run_catching(lambda: self.drive(sequence=1), []))
        t.start()
        self.assertTrue(in_write.wait(timeout=0.5))

        # Advance past silence threshold while drive's write is still blocked.
        self.clock.advance(0.301)
        t0 = time.monotonic()
        tick_status = self.c.tick()
        self.assertLess(time.monotonic() - t0, 0.15)
        self.assertFalse(tick_status["armed"])
        self.assertEqual(tick_status["stage"], "UNKNOWN")

        # Attempt a competing drive from the old generation while disarmed; must be rejected.
        with self.assertRaisesRegex(SafetyError, "STOP_LOCKED"):
            self.drive(sequence=2)

        release_write.set()
        t.join(timeout=0.5)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and self.outputs[-1] != (0.0, 0.0, 0.0):
            time.sleep(0.01)
        self.assertEqual(self.outputs[-1], (0.0, 0.0, 0.0))
        self.assertFalse(self.c.status()["armed"])

    def _run_catching(self, fn, out):
        try:
            fn()
        except SafetyError as exc:
            out.append(exc)

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
        for kwargs in (
            {"silence_s": 1},
            {"poll_s": 1},
            {"write_timeout_s": 0.2},
            {"stationary_span_s": 0.05},
            {"max_x": 1},
            {"heartbeat_s": float("nan")},
        ):
            with self.assertRaises(ValueError):
                Limits(**kwargs)

    def test_real_watchdog_thread_detects_without_more_requests(self):
        self.drive()
        stopped = threading.Event()
        self.c._write = lambda *v: stopped.set() if not any(v) else None
        watchdog = Watchdog(self.c)
        watchdog.start()
        try:
            self.assertTrue(watchdog.alive)
            self.assertTrue(watchdog.healthy)
            self.clock.advance(0.301)
            self.assertTrue(stopped.wait(0.3))
        finally:
            watchdog.close()
        self.assertFalse(watchdog.alive)
        self.assertFalse(self.c.status()["armed"])

    def test_watchdog_survives_tick_and_stop_exceptions_and_reports_health(self):
        self.drive()
        real_tick = self.c.tick
        real_stop = self.c.stop
        fail_Stop = {"boom": True}
        caught = threading.Event()

        def bad_tick():
            if fail_Stop["boom"]:
                raise RuntimeError("simulated tick failure")
            return real_tick()

        def bad_stop(reason="OPERATOR_STOP"):
            if fail_Stop["boom"]:
                caught.set()
                raise RuntimeError("simulated stop failure")
            return real_stop(reason)

        self.c.tick = bad_tick
        self.c.stop = bad_stop
        watchdog = Watchdog(self.c)
        watchdog.start()
        try:
            self.assertTrue(caught.wait(timeout=0.5))
            # Thread must still be alive even after both tick() and stop() raised!
            self.assertTrue(watchdog.alive)
            self.assertFalse(watchdog.healthy)
            self.assertIn("simulated stop failure", watchdog.last_error)
            status = self.c.status()
            self.assertFalse(status["armed"])
            self.assertEqual(status["stage"], "UNKNOWN")
            self.assertEqual(status["reason"], "WATCHDOG_EXCEPTION")
            self.assertTrue(status["watchdogAlive"])
            self.assertIsNotNone(status["watchdogError"])

            # Restore tick/stop and verify watchdog recovers health on subsequent cycle.
            fail_Stop["boom"] = False
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline and not watchdog.healthy:
                time.sleep(0.01)
            self.assertTrue(watchdog.healthy)
            self.assertIsNone(self.c.status()["watchdogError"])
        finally:
            fail_Stop["boom"] = False
            watchdog.close()

    def test_watchdog_close_sets_done_and_joins_even_when_stop_raises(self):
        watchdog = Watchdog(self.c)
        watchdog.start()
        real_stop = self.c.stop

        def failing_shutdown(reason="OPERATOR_STOP"):
            if reason == "SHUTDOWN":
                raise RuntimeError("shutdown write error")
            return real_stop(reason)

        self.c.stop = failing_shutdown
        try:
            with self.assertRaisesRegex(RuntimeError, "Watchdog shutdown stop failed"):
                watchdog.close()
            self.assertTrue(watchdog._done.is_set())
            self.assertFalse(watchdog.alive)
            self.assertFalse(self.c.status()["armed"])
            self.assertEqual(self.c.status()["stage"], "UNKNOWN")
        finally:
            self.c.stop = real_stop


if __name__ == "__main__":
    unittest.main()
