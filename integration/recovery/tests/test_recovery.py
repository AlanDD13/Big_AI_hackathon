from integration.recovery.graph import Recovery
from integration.recovery.simulator import Simulator, ScriptedPolicy, fixture_mission
import pytest


class ChangedObservation(Simulator):
    def __init__(self, change, **kwargs):
        super().__init__(**kwargs)
        self.change = change

    def observe(self, mission):
        return super().observe(mission).model_copy(update=self.change)


def test_full_recovery_reuses_classification_and_verifies_both_manipulations():
    bridge = Simulator()
    mission = fixture_mission()
    run = Recovery(bridge, ScriptedPolicy(), now=lambda: 100.0).run(mission)
    assert run["phase"] == "COMPLETE"
    assert [c.action for c in bridge.commands] == ["approach", "align", "pickup", "transport", "place"]
    assert run["observation"].pickup_evidence_id
    assert run["observation"].placement_evidence_id
    assert run["mission"].target.classification == mission.target.classification
    assert len(run["events"]) >= 5


def test_physical_adapter_cannot_be_enabled():
    bridge = Simulator()
    bridge.mode = "physical"
    import pytest
    with pytest.raises(ValueError, match="simulator"):
        Recovery(bridge, ScriptedPolicy())


@pytest.mark.parametrize("change", [
    {"object_id": "phone:session:other-can"}, {"identity_confirmed": False},
    {"target_revision": 2}, {"captured_at": 99.0}, {"telemetry_at": 101.0},
    {"living_obstacle": True}, {"stationary": False}, {"safety": "UNKNOWN"},
    {"safety": "HELD"}, {"safety": "STOPPED"},
])
def test_invalid_observation_stops_without_motion(change):
    bridge = ChangedObservation(change)
    run = Recovery(bridge, ScriptedPolicy(), now=lambda: 100.0).run(fixture_mission())
    assert run["phase"] == "BLOCKED"
    assert bridge.stops == 1 and bridge.commands == []


def test_lost_ack_is_not_retried_even_if_subsequent_stop_confirms():
    class LostAck(Simulator):
        def execute(self, command):
            super().execute(command)
            raise TimeoutError("timeout after side effect")
    bridge = LostAck()
    runner = Recovery(bridge, ScriptedPolicy(), now=lambda: 100.0)
    run = runner.run(fixture_mission())
    assert run["phase"] == "UNKNOWN"
    assert run["stop_outcome"] == "STOPPED"
    assert len(bridge.commands) == 1
    with pytest.raises(ValueError, match="reconcile"):
        runner.run(fixture_mission())


@pytest.mark.parametrize("bad", ["pickup", "place", "transport"])
def test_model_cannot_skip_required_phases(bad):
    class BadPolicy(ScriptedPolicy):
        def choose(self, mission, phase, observation):
            return super().choose(mission, bad.upper(), observation)
    bridge = Simulator()
    run = Recovery(bridge, BadPolicy(), now=lambda: 100.0).run(fixture_mission())
    assert run["error"] == "tool_not_allowed_for_phase_or_target"
    assert not bridge.commands


@pytest.mark.parametrize("phase,change,error", [
    ("pickup", {"holding": False}, "pickup_not_verified"),
    ("pickup", {"pickup_evidence_id": None}, "pickup_not_verified"),
    ("place", {"released": False}, "placement_not_verified"),
    ("place", {"placement_evidence_id": None}, "placement_not_verified"),
    ("transport", {"holding": False}, "carried_object_lost"),
])
def test_acknowledgement_does_not_prove_physical_success(phase, change, error):
    class Unverified(Simulator):
        def execute(self, command):
            result = super().execute(command)
            if command.action == phase:
                self.flags.update(change)
            return result
    run = Recovery(Unverified(), ScriptedPolicy(), now=lambda: 100.0).run(fixture_mission())
    assert run["error"] == error and run["phase"] != "COMPLETE"


def test_model_latency_requires_new_frame_before_action():
    clock = [100.0]
    class SlowPolicy(ScriptedPolicy):
        def choose(self, mission, phase, observation):
            clock[0] += 10.0
            return super().choose(mission, phase, observation)
    bridge = Simulator(now=lambda: clock[0])
    run = Recovery(bridge, SlowPolicy(), now=lambda: clock[0]).run(fixture_mission())
    assert run["phase"] == "COMPLETE"
    assert bridge.commands[0].expires_at == 110.3
    assert bridge.commands[0].frame_id == 2  # newer than the model's input frame


def test_replayed_frame_is_rejected_before_action():
    bridge = ChangedObservation({"frame_id": 1})
    run = Recovery(bridge, ScriptedPolicy(), now=lambda: 100.0).run(fixture_mission())
    assert run["error"] == "replayed_frame_or_camera_session_changed"
    assert not bridge.commands


def test_missing_post_action_frame_stops_without_next_action():
    bridge = ChangedObservation({"after_command_id": None})
    run = Recovery(bridge, ScriptedPolicy(), now=lambda: 100.0).run(fixture_mission())
    assert run["error"] == "missing_post_action_observation"
    assert run["phase"] == "UNKNOWN"
    assert len(bridge.commands) == 1


def test_loop_budget_and_stop_confirmation():
    class NoProgress(Simulator):
        def execute(self, command):
            result = super().execute(command)
            self.flags["near_target"] = False
            return result
        def stop(self, mission_id):
            return "UNKNOWN"
    bridge = NoProgress()
    run = Recovery(bridge, ScriptedPolicy(), now=lambda: 100.0, max_actions=2).run(fixture_mission())
    assert run["phase"] == "UNKNOWN" and run["error"] == "action_budget_exhausted"
    assert len(bridge.commands) == 2


@pytest.mark.parametrize("change", [
    {"dispatch_eligible": False},
    {"classification": {"category": "TRASH", "subject_kind": "PERSON", "confidence": "HIGH", "carrying": "NO"}},
])
def test_stored_classification_does_not_bypass_backend_gate(change):
    mission = fixture_mission()
    mission = mission.model_copy(update={"target": mission.target.model_copy(update=change)})
    bridge = Simulator()
    run = Recovery(bridge, ScriptedPolicy(), now=lambda: 100.0).run(mission)
    assert run["error"] == "stored_target_not_eligible" and not bridge.commands


def test_two_wrappers_cannot_run_different_missions_on_same_bridge():
    from concurrent.futures import ThreadPoolExecutor
    bridge = Simulator()
    missions = [fixture_mission().model_copy(update={"mission_id": f"m-{n}"}) for n in range(2)]
    def attempt(mission):
        try:
            return Recovery(bridge, ScriptedPolicy(), now=lambda: 100.0).run(mission)["phase"]
        except ValueError:
            return "RESERVED"
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(attempt, missions))
    assert sorted(outcomes) == ["COMPLETE", "RESERVED"]
    assert len(bridge.commands) == 5


def test_pre_action_capture_cannot_be_relabelled_with_new_frame_id():
    class OldCapture(Simulator):
        def observe(self, mission):
            obs = super().observe(mission)
            return obs.model_copy(update={"captured_at": 99.9}) if self.commands else obs
    bridge = OldCapture()
    run = Recovery(bridge, ScriptedPolicy(), now=lambda: 100.0).run(fixture_mission())
    assert run["error"] == "pre_action_capture_relabelled_as_new"
    assert len(bridge.commands) == 1


def test_align_rechecks_proximity_after_model_latency():
    class LoseProximity(ScriptedPolicy):
        def choose(self, mission, phase, observation):
            if phase == "ALIGN":
                bridge.flags["near_target"] = False
            return super().choose(mission, phase, observation)
    bridge = Simulator()
    run = Recovery(bridge, LoseProximity(), now=lambda: 100.0).run(fixture_mission())
    assert run["error"] == "alignment_pose_not_ready"
    assert [c.action for c in bridge.commands] == ["approach"]


def test_old_pickup_evidence_cannot_verify_new_command():
    class OldEvidence(Simulator):
        def execute(self, command):
            result = super().execute(command)
            if command.action == "pickup":
                self.flags["pickup_command_id"] = "old-command"
            return result
    run = Recovery(OldEvidence(), ScriptedPolicy(), now=lambda: 100.0).run(fixture_mission())
    assert run["error"] == "pickup_not_verified"
