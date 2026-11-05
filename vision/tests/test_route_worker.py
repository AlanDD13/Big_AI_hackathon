import asyncio
import json
import signal
from unittest.mock import patch

import pytest

from vision.camera_recovery import CameraRecovery
from vision.tests.test_camera_recovery import binding, candidate


class Stream:
    def __aiter__(self): return self
    async def __anext__(self): raise StopAsyncIteration


class Process:
    def __init__(self, blocked=False):
        self.stdout = Stream()
        self.returncode = None
        self.signals = []
        self.done = asyncio.Event()
        if not blocked: self.done.set()
    async def wait(self):
        await self.done.wait()
        self.returncode = 130 if self.signals else 0
        return self.returncode
    def send_signal(self, value):
        self.signals.append(value)
        self.done.set()


@pytest.mark.asyncio
async def test_worker_uses_fixed_argv_reference_and_exact_target_report(tmp_path):
    proc, commands, notices = Process(), [], []
    async def spawn(*args, **kwargs):
        commands.append(args)
        report = args[args.index('--report-file') + 1]
        from pathlib import Path
        Path(report).write_text(json.dumps({'result': 'RETURNED_AND_RELEASED',
                                          'targetObjectId': candidate()['objectId']}))
        return proc
    controller = CameraRecovery(binding(tmp_path), lambda *args: notices.append(args),
                                clock=lambda: 1000, reference=lambda _: b'jpeg')
    with patch('vision.camera_recovery.asyncio.create_subprocess_exec', side_effect=spawn):
        controller.update([candidate()])
        await controller.drain()
    assert len(commands) == 1
    assert '--route-return' in commands[0] and '--target-reference' in commands[0]
    assert '--no-browser' in commands[0] and '--phase-file' in commands[0]
    assert not proc.signals
    assert notices[-1][0] == 'alert.recovery_completed'
    await controller.close()


@pytest.mark.asyncio
async def test_evidence_change_cancels_before_grasp_and_blocks_retry(tmp_path):
    now = [1000]
    proc = Process(blocked=True)
    async def spawn(*args, **kwargs):
        now[0] = 1002  # Camera evidence expires while starting the child.
        return proc
    controller = CameraRecovery(binding(tmp_path), lambda *args: None,
                                clock=lambda: now[0], reference=lambda _: b'jpeg')
    with patch('vision.camera_recovery.asyncio.create_subprocess_exec', side_effect=spawn) as create:
        controller.update([candidate()])
        await controller.drain()
        obj = candidate(objectId='phone-2:session1:track2', lastSeenAt=1002)
        controller.update([obj])
        await controller.drain()
        assert create.call_count == 1
    assert proc.signals == [signal.SIGINT]
    assert controller.db.execute('SELECT status FROM jobs').fetchone()[0] == 'UNKNOWN'
    await controller.close()
