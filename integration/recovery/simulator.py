"""Labelled offline fixture, not a robot-physics or perception simulation."""
from threading import Lock

from .types import Command, Mission, Observation, Receipt, Target, ToolCall


def fixture_mission() -> Mission:
    return Mission(mission_id="fixture-recovery-1", destination_id="fixture-drop-zone",
                   target=Target(object_id="phone:session:paper", revision=1, dispatch_eligible=True,
                                 classification={"category": "TRASH", "subject_kind": "INANIMATE",
                                                 "carrying": "NO", "confidence": "HIGH",
                                                 "description": "LABELLED FIXTURE paper"}))


class ScriptedPolicy:
    """Deterministic stand-in for Grok tool calls; never classifies an image."""
    def choose(self, mission, phase, observation):
        return ToolCall(action=phase.lower(), object_id=mission.target.object_id,
                        destination_id=mission.destination_id)


class Simulator:
    mode = "simulator"

    def __init__(self, *, now=lambda: 100.0):
        self.now = now
        self.commands: list[Command] = []
        self.stops = 0
        self.frame = 0
        self.flags = {}
        self._bindings: dict[str, Command] = {}
        self._mission_id = None
        self._lock = Lock()

    def begin(self, mission_id):
        # One mission per fixture lifetime, including after failure/completion.
        with self._lock:
            if self._mission_id is not None:
                return False
            self._mission_id = mission_id
            return True

    def observe(self, mission):
        self.frame += 1
        return Observation(camera_id="fixture-rover", session_id="fixture-session", frame_id=self.frame,
                           captured_at=self.now(), telemetry_at=self.now(), object_id=mission.target.object_id,
                           target_revision=mission.target.revision, identity_evidence_id=f"fixture-id-{self.frame}",
                           identity_confirmed=True, stationary=True, living_obstacle=False,
                           safety="STOPPED" if self.stops else "READY", **self.flags)

    def execute(self, command):
        old = self._bindings.get(command.command_id)
        if old is not None:
            return Receipt(command_id=command.command_id,
                           outcome="ACKNOWLEDGED" if old == command else "REJECTED")
        if (self.stops or self.now() > command.expires_at
                or command.mission_id != self._mission_id):
            return Receipt(command_id=command.command_id, outcome="REJECTED")
        self._bindings[command.command_id] = command
        self.commands.append(command)
        self.flags["after_command_id"] = command.command_id
        if command.action == "approach":
            self.flags["near_target"] = True
        elif command.action == "align":
            self.flags["aligned"] = True
        elif command.action == "pickup":
            self.flags.update(holding=True, pickup_evidence_id=f"fixture-pickup-{command.command_id}",
                              pickup_command_id=command.command_id)
        elif command.action == "transport":
            self.flags["at_destination"] = True
        elif command.action == "place":
            self.flags.update(holding=False, released=True,
                              placement_command_id=command.command_id,
                              placement_evidence_id=f"fixture-place-{command.command_id}")
        return Receipt(command_id=command.command_id, outcome="ACKNOWLEDGED")

    def stop(self, mission_id):
        self.stops += 1
        return "STOPPED"
