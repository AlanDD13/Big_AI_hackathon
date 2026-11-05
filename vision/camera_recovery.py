"""Explicitly armed, camera-bound measured demo route; no generic navigation.

Model estimates are inputs to deterministic policy, never physical evidence.
The journal prevents repeated tracks, concurrent jobs and restart retries.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import signal
import sqlite3
import uuid


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class RouteBinding:
    camera_id: str
    session_id: str
    geometry_version: str
    pickup_area: tuple[float, float, float, float] | None
    python: Path
    state_dir: Path
    setup_confirmed: bool = False
    threshold: float = .8
    model: str = 'grok-4.7'

    def __post_init__(self):
        import re
        if not all(value == '*' or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value or '')
                   for value in (self.camera_id, self.session_id, self.geometry_version)):
            raise ValueError('Route binding needs exact camera/session/geometry IDs')
        box = self.pickup_area
        if box is not None and (len(box) != 4 or not all(_number(v) and v >= 0 for v in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise ValueError('Route binding needs a measured pickup image area')
        if not _number(self.threshold) or not .5 <= self.threshold <= 1:
            raise ValueError('Grabbability threshold must be between .5 and 1')
        if not self.python.is_absolute() or not self.state_dir.is_absolute():
            raise ValueError('Robot Python and state directory must be absolute paths')
        if not isinstance(self.setup_confirmed, bool) or not self.model or len(self.model) > 200:
            raise ValueError('Invalid route setup')

    @classmethod
    def load(cls, path):
        body = json.loads(Path(path).read_text())
        expected = {'cameraId', 'sessionId', 'geometryVersion', 'pickupAreaPx',
                    'robotPython', 'stateDir', 'setupConfirmed', 'threshold', 'model'}
        if not isinstance(body, dict) or set(body) != expected:
            raise ValueError('Invalid camera recovery configuration fields')
        return cls(body['cameraId'], body['sessionId'], body['geometryVersion'],
                   tuple(body['pickupAreaPx']) if body['pickupAreaPx'] is not None else None,
                   Path(body['robotPython']), Path(body['stateDir']),
                   body['setupConfirmed'], body['threshold'], body['model'])


def _inside(box, area):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(_number(v) for v in box)
            and area[0] <= box[0] < box[2] <= area[2]
            and area[1] <= box[1] < box[3] <= area[3])


def _overlap(box, area):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(_number(v) for v in box)
            and box[0] < area[2] and box[2] > area[0]
            and box[1] < area[3] and box[3] > area[1])


def decision(obj, binding, objects, now):
    """Return an admission decision and stable frontend reason code."""
    if obj.get('classificationStatus') != 'COMPLETED':
        return False, 'CLASSIFICATION_UNAVAILABLE'
    if obj.get('classificationProvider') != 'grok':
        return False, 'LIVE_CLASSIFICATION_REQUIRED'
    classification = obj.get('classification') or {}
    if (classification.get('subject_kind') != 'INANIMATE'
            or classification.get('carrying') != 'NO'
            or classification.get('category') != 'TRASH'
            or classification.get('confidence') != 'HIGH'):
        return False, 'CLASSIFICATION_EXCLUDED'
    advisory = obj.get('visualPickupAdvisory') or {}
    score = advisory.get('grabbability_score')
    threshold = binding.threshold if binding else .8
    if (obj.get('visualPickupStatus') != 'COMPLETED'
            or advisory.get('visual_suitability') != 'POSSIBLE'
            or advisory.get('robot_grasp_estimate') != 'LIKELY'
            or not _number(score) or not threshold <= score <= 1):
        return False, 'GRABBABILITY_LOW_OR_UNKNOWN'
    seen = obj.get('lastSeenAt')
    if not _number(seen) or not 0 <= now - seen <= 1:
        return False, 'STALE_OBSERVATION'
    stationary = obj.get('pixelStationarySeconds')
    if (obj.get('cameraKind') != 'FIXED' or obj.get('motionState') != 'STATIONARY'
            or obj.get('status') in ('LOST', 'EXCLUDED') or obj.get('exclusionReason')
            or not _number(stationary) or stationary < 5):
        return False, 'STATIONARITY_REQUIRED'
    if obj.get('detectorLabel', '').strip().lower() != 'can':
        return False, 'UNSUPPORTED_PICKUP_PROFILE'
    if binding is None or not binding.setup_confirmed:
        return False, 'RECOVERY_NOT_ARMED'
    if any(expected != '*' and actual != expected for actual, expected in zip(
            (obj.get('cameraId'), obj.get('sessionId'), obj.get('geometryVersion')),
            (binding.camera_id, binding.session_id, binding.geometry_version))):
        return False, 'ROUTE_SETUP_CHANGED'
    if binding.pickup_area is not None and not _inside(obj.get('bboxPx'), binding.pickup_area):
        return False, 'OUTSIDE_PICKUP_AREA'
    mapping = obj.get('mapping')
    if mapping is not None and (mapping.get('valid') is not True or mapping.get('calibrationValid') is not True):
        return False, 'MAPPING_REJECTED'
    source = advisory.get('sourceFrameId')
    if type(source) is not int or not 0 <= source <= obj.get('frameId', -1):
        return False, 'ASSESSMENT_FRAME_MISMATCH'
    for other in objects:
        if (other.get('objectId') != obj.get('objectId')
                and other.get('cameraId') == obj.get('cameraId')
                and other.get('sessionId') == obj.get('sessionId')
                and other.get('status') != 'LOST'
                and (binding.pickup_area is not None and _overlap(other.get('bboxPx'), binding.pickup_area)
                     or binding.pickup_area is None and other.get('detectorLabel', '').lower() in
                     {'can', 'person', 'people', 'dog', 'cat', 'animal'})):
            return False, 'PICKUP_AREA_OCCUPIED'
    return True, 'GRABBABLE'


class CameraRecovery:
    def __init__(self, binding, notify, *, clock, execute=None, reference=None):
        self.binding, self.notify, self.clock = binding, notify, clock
        self.execute = execute or self._execute
        self.reference = reference or (lambda _: None)
        self.objects = []
        self.task = None
        self.process = None
        self.active_id = None
        self.pickup_started = False
        self.closed = False
        self.notices = OrderedDict()
        self.clear_since = self.last_clear_frame = None
        self.db = None
        if binding is not None and binding.setup_confirmed:
            binding.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.db = sqlite3.connect(binding.state_dir / 'recovery.sqlite3')
            self.db.execute('CREATE TABLE IF NOT EXISTS jobs (object_id TEXT PRIMARY KEY, status TEXT NOT NULL, directory TEXT NOT NULL)')
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs ((1)) WHERE status IN ('RUNNING', 'UNKNOWN')")
            self.db.execute('CREATE TABLE IF NOT EXISTS scene_latch (id INTEGER PRIMARY KEY CHECK (id=1), consumed INTEGER NOT NULL)')
            self.db.execute('INSERT OR IGNORE INTO scene_latch VALUES (1, 0)')
            self.db.commit()

    def _notice(self, kind, obj, reason, step=None):
        key = (obj['objectId'], obj['revision'], kind, step)
        if key not in self.notices:
            self.notices[key] = None
            if len(self.notices) > 4096:
                self.notices.popitem(last=False)
            if step:
                self.notify(kind, obj['objectId'], reason, step)
            else:
                self.notify(kind, obj['objectId'], reason)

    def update(self, objects):
        self.objects = objects
        if self.closed:
            return
        for obj in objects:
            if obj.get('cameraKind') != 'FIXED' or obj.get('classificationStatus') == 'PENDING':
                continue
            if obj.get('visualPickupStatus') == 'PENDING' and obj.get('classificationStatus') == 'COMPLETED':
                continue
            if obj.get('objectId') == self.active_id:
                continue
            eligible, reason = decision(obj, self.binding, objects, self.clock())
            if not eligible:
                self._notice('alert.pickup_unavailable', obj, reason)
                continue
            try:
                with self.db:
                    if self.db.execute('SELECT 1 FROM jobs WHERE object_id=?', (obj['objectId'],)).fetchone():
                        continue
                    if self.db.execute("SELECT 1 FROM jobs WHERE status IN ('RUNNING', 'UNKNOWN')").fetchone():
                        self._notice('alert.pickup_unavailable', obj, 'ROVER_BUSY_OR_UNRECONCILED')
                        continue
                    if self.db.execute('SELECT consumed FROM scene_latch WHERE id=1').fetchone()[0]:
                        continue
                    directory = self.binding.state_dir / uuid.uuid4().hex
                    self.db.execute("INSERT INTO jobs VALUES (?, 'RUNNING', ?)", (obj['objectId'], str(directory)))
                    self.db.execute('UPDATE scene_latch SET consumed=1 WHERE id=1')
            except sqlite3.Error:
                self._notice('alert.pickup_unavailable', obj, 'RECOVERY_STORAGE_UNAVAILABLE')
                continue
            self.active_id = obj['objectId']
            self.pickup_started = False
            self.task = asyncio.create_task(self._run(dict(obj), directory))
            self._notice('alert.recovery_started', obj, 'GRABBABLE_FIXED_ROUTE')

    def observe_scene(self, received_at, has_can):
        """Re-arm only after five seconds of fresh empty-scene evidence.

        Merely changing camera/session IDs, losing a stream, restarting, or
        replaying an old classification cannot re-arm a consumed scene.
        """
        live_can = any(o.get('detectorLabel', '').lower() == 'can' and o.get('status') != 'LOST'
                       and _number(o.get('lastSeenAt')) and 0 <= self.clock() - o['lastSeenAt'] <= 1
                       for o in self.objects)
        if (self.db is None or self.active_id or has_can or live_can
                or not 0 <= self.clock() - received_at <= 1):
            self.clear_since = self.last_clear_frame = None
            return
        if self.db.execute("SELECT 1 FROM jobs WHERE status IN ('RUNNING', 'UNKNOWN')").fetchone():
            self.clear_since = self.last_clear_frame = None
            return
        if self.last_clear_frame is None or not 0 < received_at - self.last_clear_frame <= 1:
            self.clear_since = received_at
        self.last_clear_frame = received_at
        if received_at - self.clear_since >= 5:
            with self.db:
                self.db.execute('UPDATE scene_latch SET consumed=0 WHERE id=1')

    async def _run(self, obj, directory):
        status = 'UNKNOWN'
        try:
            current = next((o for o in self.objects if o.get('objectId') == obj['objectId']), None)
            if current is None or current.get('revision') != obj['revision'] or not decision(
                    current, self.binding, self.objects, self.clock())[0]:
                raise RuntimeError('Evidence changed before launch')
            directory.mkdir(mode=0o700)
            async with asyncio.timeout(300):
                result = await self.execute(obj, self.binding, directory, self.reference(obj['objectId']))
            if result.get('result') != 'RETURNED_AND_RELEASED':
                raise RuntimeError('Recovery did not complete')
            status = 'COMPLETED'
            self._notice('alert.recovery_completed', obj, 'RETURNED_AND_RELEASE_COMMAND_ACKNOWLEDGED')
        except (Exception, asyncio.CancelledError):
            self._notice('alert.recovery_failed', obj, 'RECOVERY_OUTCOME_REQUIRES_OPERATOR_CHECK')
        finally:
            with self.db:
                self.db.execute('UPDATE jobs SET status=? WHERE object_id=?', (status, obj['objectId']))
            self.active_id = None

    async def _execute(self, obj, config, directory, reference):
        if not reference:
            raise RuntimeError('Exact classification crop is unavailable')
        runner = Path(__file__).resolve().parents[1] / 'robot' / 'grok_tool_loop.py'
        reference_path = directory / 'target.jpg'
        reference_path.write_bytes(reference)
        report_path = directory / 'report.json'
        phase_path = directory / 'phase'
        progress_path = directory / 'progress.jsonl'
        args = [str(config.python), '-u', str(runner), '25', config.model, '--route-return',
                '--report-file', str(report_path), '--target-reference', str(reference_path),
                '--target-object-id', obj['objectId'], '--phase-file', str(phase_path),
                '--progress-file', str(progress_path), '--no-browser']
        with (directory / 'stdout.log').open('wb') as stdout, (directory / 'sdk.log').open('wb') as stderr:
            self.process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=stderr)
            async def pump():
                async for line in self.process.stdout:
                    stdout.write(line)
                    stdout.flush()
            reader = asyncio.create_task(pump())
            waiter = asyncio.create_task(self.process.wait())
            cancelled = False
            progress_read = 0
            def progress():
                nonlocal progress_read
                if not progress_path.exists(): return
                if progress_path.stat().st_size > 16384:
                    raise RuntimeError('Invalid progress report')
                lines = progress_path.read_text().splitlines(keepends=True)
                for line in lines[progress_read:]:
                    if not line.endswith('\n'): break
                    value = json.loads(line)
                    steps = ['FORWARD_30', 'TURN_RIGHT_90', 'FORWARD_154', 'PICKUP', 'RETURNED', 'LOWERED', 'RELEASED']
                    if (value.get('sequence') != progress_read + 1 or progress_read >= len(steps)
                            or value.get('step') != steps[progress_read]):
                        raise RuntimeError('Invalid progress report')
                    progress_read += 1
                    self._notice('alert.recovery_step', obj, 'ROUTE_STEP_COMPLETED', value['step'])
            try:
                while self.process.returncode is None:
                    progress()
                    if phase_path.exists() and phase_path.read_text() == 'PICKUP':
                        self.pickup_started = True
                    if not self.pickup_started:
                        current = next((o for o in self.objects if o.get('objectId') == obj['objectId']), None)
                        valid = (current is not None and current.get('revision') == obj['revision']
                                 and decision(current, config, self.objects, self.clock())[0])
                        if not valid and not cancelled:
                            self.process.send_signal(signal.SIGINT)
                            cancelled = True
                    try:
                        await asyncio.wait_for(asyncio.shield(waiter), .25)
                    except TimeoutError:
                        pass
                await reader
                progress()
                if cancelled or self.process.returncode != 0:
                    raise RuntimeError('Route outcome is uncertain')
                report = json.loads(report_path.read_text())
                if report.get('targetObjectId') != obj['objectId']:
                    raise RuntimeError('Route report target mismatch')
                return report
            finally:
                if self.process.returncode is None:
                    self.process.send_signal(signal.SIGINT)
                if not reader.done():
                    reader.cancel()
                self.process = None

    async def drain(self):
        if self.task is not None:
            await self.task

    async def close(self):
        self.closed = True
        if self.process is not None and self.process.returncode is None:
            self.process.send_signal(signal.SIGINT)
        if self.task is not None:
            await self.task
        if self.db is not None:
            self.db.close()


def main():
    """An explicit stopped-state reconciliation never resumes an old job."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('journal', type=Path)
    parser.add_argument('--confirmed-stopped', action='store_true', required=True)
    args = parser.parse_args()
    with sqlite3.connect(args.journal) as db:
        db.execute("UPDATE jobs SET status='RECONCILED' WHERE status IN ('RUNNING', 'UNKNOWN')")
    print('Uncertain runs reconciled as stopped; past objects remain consumed.')


if __name__ == '__main__':
    main()
