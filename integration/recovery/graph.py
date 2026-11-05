"""Actual LangGraph execution; deterministic checks surround every model decision.

This slice accepts simulator adapters only. It deliberately has no retry policy,
checkpoint replay, SDK import, bridge HTTP URL or physical-enable switch.
"""
from time import time
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

from langgraph.graph import END, START, StateGraph

from .types import Bridge, Command, Mission, Observation, Phase, Policy, ToolCall


class State(TypedDict, total=False):
    mission: Mission
    phase: Phase
    observation: Observation
    call: ToolCall
    pending: str | None
    steps: int
    events: list[dict]
    error: str
    stop_outcome: str
    uncertain_action: bool
    issued_at: float
    pickup_command_id: str


class GateError(ValueError):
    pass


EXPECTED = {"APPROACH": "approach", "ALIGN": "align", "PICKUP": "pickup",
            "TRANSPORT": "transport", "PLACE": "place"}


class Recovery:
    def __init__(self, bridge: Bridge, policy: Policy, *, now=time, max_actions=20):
        if bridge.mode != "simulator":
            raise ValueError("This slice accepts simulator adapters only")
        if not isinstance(max_actions, int) or isinstance(max_actions, bool) or not 1 <= max_actions <= 50:
            raise ValueError("max_actions must be 1..50")
        self.bridge, self.policy, self.now, self.max_actions = bridge, policy, now, max_actions
        graph = StateGraph(State)
        for name, node in (("observe", self._observe), ("choose", self._choose),
                           ("act", self._act), ("halt", self._halt)):
            graph.add_node(name, node)
        graph.add_edge(START, "observe")
        graph.add_conditional_edges("observe", lambda s: "halt" if s.get("error") else
                                    END if s["phase"] == "COMPLETE" else "choose")
        graph.add_conditional_edges("choose", lambda s: "halt" if s.get("error") else "act")
        graph.add_conditional_edges("act", lambda s: "halt" if s.get("error") else "observe")
        graph.add_edge("halt", END)
        self._graph = graph.compile()

    def run(self, mission: Mission) -> State:
        if not self.bridge.begin(mission.mission_id):
            raise GateError("Bridge already reserved; reconcile, do not replay")
        return self._graph.invoke({"mission": mission, "phase": "APPROACH", "steps": 0,
                                  "events": [], "pending": None},
                                 {"recursion_limit": 4 * self.max_actions + 10})

    def _fresh(self, state: State) -> Observation:
        if self.bridge.mode != "simulator":
            raise GateError("physical_execution_disabled")
        obs = self.bridge.observe(state["mission"])
        if not isinstance(obs, Observation):
            raise GateError("invalid_observation")
        now = self.now()
        if any(not 0 <= now - stamp <= 0.5 for stamp in (obs.captured_at, obs.telemetry_at)):
            raise GateError("stale_observation_or_telemetry")
        old = state.get("observation")
        if old and ((obs.camera_id, obs.session_id) != (old.camera_id, old.session_id)
                    or obs.frame_id <= old.frame_id):
            raise GateError("replayed_frame_or_camera_session_changed")
        target = state["mission"].target
        if (not obs.identity_confirmed or obs.object_id != target.object_id
                or obs.target_revision != target.revision):
            raise GateError("target_identity_or_revision_unconfirmed")
        if obs.safety != "READY" or obs.living_obstacle:
            raise GateError("safety_not_ready_or_living_obstacle")
        if state.get("pending") and obs.after_command_id != state["pending"]:
            raise GateError("missing_post_action_observation")
        if state.get("pending") and min(obs.captured_at, obs.telemetry_at) < state["issued_at"]:
            raise GateError("pre_action_capture_relabelled_as_new")
        if state["phase"] in {"APPROACH", "ALIGN", "PICKUP"} and not obs.stationary:
            raise GateError("target_not_stationary")
        classification = target.classification
        if (not target.dispatch_eligible or classification.get("category") != "TRASH"
                or classification.get("subject_kind") != "INANIMATE"
                or classification.get("carrying") != "NO"
                or classification.get("confidence") != "HIGH"):
            raise GateError("stored_target_not_eligible")
        return obs

    def _observe(self, state: State):
        try:
            obs = self._fresh(state)
            phase = state["phase"]
            if phase == "APPROACH" and obs.near_target:
                phase = "ALIGN"
            elif phase == "ALIGN" and obs.near_target and obs.aligned:
                phase = "PICKUP"
            elif phase == "VERIFY_PICKUP":
                if (not obs.holding or not obs.pickup_evidence_id
                        or obs.pickup_command_id != state["pending"]):
                    raise GateError("pickup_not_verified")
                phase = "TRANSPORT"
            elif phase == "TRANSPORT":
                if (not obs.holding or not obs.pickup_evidence_id
                        or obs.pickup_command_id != state.get("pickup_command_id")):
                    raise GateError("carried_object_lost")
                if obs.at_destination:
                    phase = "PLACE"
            elif phase == "VERIFY_PLACE":
                if (not obs.at_destination or not obs.released or obs.holding
                        or not obs.placement_evidence_id or obs.placement_command_id != state["pending"]):
                    raise GateError("placement_not_verified")
                phase = "COMPLETE"
            return {"observation": obs, "phase": phase, "pending": None,
                    "pickup_command_id": obs.pickup_command_id or "",
                    "events": state["events"] + [{"kind": "observation", "phase": phase,
                                                    "frame_id": obs.frame_id}]}
        except Exception as exc:
            return {"error": str(exc) if isinstance(exc, GateError) else "observation_unavailable",
                    "uncertain_action": bool(state.get("pending"))}

    def _choose(self, state: State):
        try:
            if state["steps"] >= self.max_actions:
                raise GateError("action_budget_exhausted")
            call = self.policy.choose(state["mission"], state["phase"], state["observation"])
            if not isinstance(call, ToolCall):
                raise GateError("invalid_tool_call")
            if call.action == "stop":
                raise GateError("policy_requested_stop")
            if (call.action != EXPECTED.get(state["phase"])
                    or call.object_id != state["mission"].target.object_id
                    or call.destination_id != state["mission"].destination_id):
                raise GateError("tool_not_allowed_for_phase_or_target")
            return {"call": call}
        except Exception as exc:
            return {"error": str(exc) if isinstance(exc, GateError) else "policy_unavailable_or_invalid"}

    def _act(self, state: State):
        attempted = False
        try:
            # Model latency cannot extend old frame authority: read again AFTER Grok.
            obs = self._fresh(state)
            phase = state["phase"]
            if phase == "ALIGN" and not obs.near_target:
                raise GateError("alignment_pose_not_ready")
            if phase == "PICKUP" and (not obs.near_target or not obs.aligned or obs.holding):
                raise GateError("pickup_pose_not_ready")
            if phase in {"TRANSPORT", "PLACE"} and (not obs.holding or not obs.pickup_evidence_id
                    or obs.pickup_command_id != state.get("pickup_command_id")):
                raise GateError("carried_object_lost")
            if phase == "PLACE" and not obs.at_destination:
                raise GateError("destination_not_confirmed")
            mission, step = state["mission"], state["steps"] + 1
            issued_at = self.now()
            command = Command(command_id=uuid5(NAMESPACE_URL, f"{mission.mission_id}:{step}").hex,
                              mission_id=mission.mission_id, object_id=mission.target.object_id,
                              target_revision=mission.target.revision, destination_id=mission.destination_id,
                              action=state["call"].action, frame_id=obs.frame_id,
                              identity_evidence_id=obs.identity_evidence_id,
                              expires_at=issued_at + 0.3, max_duration_ms=300)
            attempted = True
            receipt = self.bridge.execute(command)  # exactly one attempt; never retry here
            if receipt.command_id != command.command_id or receipt.outcome != "ACKNOWLEDGED":
                return {"error": "action_outcome_unconfirmed",
                        "uncertain_action": receipt.command_id != command.command_id or receipt.outcome != "REJECTED"}
            next_phase = {"PICKUP": "VERIFY_PICKUP", "PLACE": "VERIFY_PLACE"}.get(phase, phase)
            return {"phase": next_phase, "steps": step, "pending": command.command_id,
                    "issued_at": issued_at,
                    "observation": obs, "events": state["events"] + [
                        {"kind": "action_acknowledged", "action": command.action,
                         "command_id": command.command_id}]}
        except Exception as exc:
            # A transport timeout may occur AFTER a side effect. Do not resend.
            return {"error": str(exc) if isinstance(exc, GateError) else "action_outcome_unknown",
                    "uncertain_action": attempted}

    def _halt(self, state: State):
        try:
            outcome = self.bridge.stop(state["mission"].mission_id)
        except Exception:
            outcome = "UNKNOWN"
        if outcome != "STOPPED":
            outcome = "UNKNOWN"
        return {"phase": "BLOCKED" if outcome == "STOPPED" and not state.get("uncertain_action") else "UNKNOWN",
                "stop_outcome": outcome, "events": state["events"] + [
                    {"kind": "halt", "reason": state["error"], "stop_outcome": outcome}]}
