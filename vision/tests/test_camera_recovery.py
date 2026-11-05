"""Camera decision and exactly-once launch without a rover or provider call."""
import asyncio
import json
from pathlib import Path

import pytest

from vision.camera_recovery import CameraRecovery, RouteBinding, decision


def candidate(**updates):
    value = dict(objectId='phone-2:session1:track1', cameraId='phone-2',
                 sessionId='session1', geometryVersion='geometry1', cameraKind='FIXED',
                 revision=0, frameId=20, lastSeenAt=1000, bboxPx=[100, 100, 150, 200],
                 detectorLabel='can', motionState='STATIONARY', pixelStationarySeconds=6,
                 status='STATIC_PENDING_REVIEW', exclusionReason=None,
                 classificationStatus='COMPLETED', classificationProvider='grok',
                 classification=dict(category='TRASH', subject_kind='INANIMATE',
                                     carrying='NO', confidence='HIGH'),
                 visualPickupStatus='COMPLETED', mapping=None,
                 visualPickupAdvisory=dict(visual_suitability='POSSIBLE',
                                          robot_grasp_estimate='LIKELY',
                                          grabbability_score=.92, sourceFrameId=18))
    value.update(updates)
    return value


def binding(tmp_path):
    return RouteBinding(camera_id='phone-2', session_id='session1', geometry_version='geometry1',
                        pickup_area=(80, 80, 180, 230), python=Path('/usr/bin/python3'),
                        state_dir=tmp_path, setup_confirmed=True)


def test_positive_estimate_requires_live_classification_and_bound_setup(tmp_path):
    obj = candidate()
    assert decision(obj, binding(tmp_path), [obj], 1000) == (True, 'GRABBABLE')
    assert decision(obj, None, [obj], 1000) == (False, 'RECOVERY_NOT_ARMED')


@pytest.mark.parametrize('update,reason', [
    ({'classificationStatus': 'UNAVAILABLE'}, 'CLASSIFICATION_UNAVAILABLE'),
    ({'classificationProvider': 'fixture'}, 'LIVE_CLASSIFICATION_REQUIRED'),
    ({'classification': dict(category='TRASH', subject_kind='PERSON', carrying='NO', confidence='HIGH')}, 'CLASSIFICATION_EXCLUDED'),
    ({'classification': dict(category='TRASH', subject_kind='INANIMATE', carrying='YES', confidence='HIGH')}, 'CLASSIFICATION_EXCLUDED'),
    ({'classification': dict(category='TRASH', subject_kind='INANIMATE', carrying='NO', confidence='LOW')}, 'CLASSIFICATION_EXCLUDED'),
    ({'lastSeenAt': 998}, 'STALE_OBSERVATION'),
    ({'lastSeenAt': 1002}, 'STALE_OBSERVATION'),
    ({'motionState': 'MOVING'}, 'STATIONARITY_REQUIRED'),
    ({'pixelStationarySeconds': 4.9}, 'STATIONARITY_REQUIRED'),
    ({'geometryVersion': 'changed'}, 'ROUTE_SETUP_CHANGED'),
    ({'bboxPx': [200, 100, 250, 200]}, 'OUTSIDE_PICKUP_AREA'),
    ({'detectorLabel': 'paper'}, 'UNSUPPORTED_PICKUP_PROFILE'),
    ({'mapping': dict(valid=False)}, 'MAPPING_REJECTED'),
])
def test_rejections_never_admit_motion(tmp_path, update, reason):
    obj = candidate(**update)
    assert decision(obj, binding(tmp_path), [obj], 1000) == (False, reason)


@pytest.mark.parametrize('score', [None, True, float('nan'), float('inf'), -.1, .79, 1.1, '0.99'])
def test_missing_low_or_invalid_score_never_dispatches(tmp_path, score):
    obj = candidate()
    obj['visualPickupAdvisory']['grabbability_score'] = score
    assert decision(obj, binding(tmp_path), [obj], 1000) == (False, 'GRABBABILITY_LOW_OR_UNKNOWN')


def test_second_object_in_pickup_area_is_ambiguous(tmp_path):
    obj, other = candidate(), candidate(objectId='phone-2:session1:track2')
    assert decision(obj, binding(tmp_path), [obj, other], 1000) == (False, 'PICKUP_AREA_OCCUPIED')


@pytest.mark.asyncio
async def test_repeated_frames_and_restart_never_relaunch_object(tmp_path):
    calls, notices = [], []
    async def execute(obj, config, directory, reference):
        calls.append(obj['objectId'])
        await asyncio.sleep(0)
        return {'result': 'RETURNED_AND_RELEASED'}
    controller = CameraRecovery(binding(tmp_path), lambda *args: notices.append(args),
                                clock=lambda: 1000, execute=execute)
    obj = candidate()
    for _ in range(5): controller.update([obj])
    await controller.drain()
    controller.update([obj])
    await controller.drain()
    assert calls == [obj['objectId']]
    assert [n[0] for n in notices] == ['alert.recovery_started', 'alert.recovery_completed']
    await controller.close()
    restarted = CameraRecovery(binding(tmp_path), lambda *args: notices.append(args),
                               clock=lambda: 1000, execute=execute)
    restarted.update([obj])
    await restarted.drain()
    assert len(calls) == 1
    await restarted.close()


@pytest.mark.asyncio
async def test_negative_decision_notifies_once_and_does_not_launch(tmp_path):
    calls, notices = [], []
    async def execute(*args): calls.append(args)
    controller = CameraRecovery(binding(tmp_path), lambda *args: notices.append(args),
                                clock=lambda: 1000, execute=execute)
    obj = candidate()
    obj['visualPickupAdvisory']['grabbability_score'] = .4
    for _ in range(5): controller.update([obj])
    await controller.drain()
    assert not calls
    assert len(notices) == 1
    assert notices[0][0] == 'alert.pickup_unavailable'
    assert notices[0][2] == 'GRABBABILITY_LOW_OR_UNKNOWN'
    await controller.close()


@pytest.mark.asyncio
async def test_uncertain_run_blocks_other_objects_even_after_restart(tmp_path):
    calls = []
    async def execute(obj, *args):
        calls.append(obj['objectId'])
        raise TimeoutError('unknown physical outcome')
    controller = CameraRecovery(binding(tmp_path), lambda *args: None,
                                clock=lambda: 1000, execute=execute)
    controller.update([candidate()])
    await controller.drain()
    await controller.close()
    restarted = CameraRecovery(binding(tmp_path), lambda *args: None,
                               clock=lambda: 1000, execute=execute)
    restarted.update([candidate(objectId='phone-2:session1:track2')])
    await restarted.drain()
    assert len(calls) == 1
    await restarted.close()


@pytest.mark.asyncio
async def test_any_camera_and_no_cross_camera_duplicate_until_fresh_scene_clear(tmp_path):
    now = [1000.]
    config = RouteBinding('*', '*', '*', None, Path('/usr/bin/python3'), tmp_path, True)
    calls = []
    async def execute(obj, *args):
        calls.append(obj['objectId'])
        return {'result': 'RETURNED_AND_RELEASED'}
    controller = CameraRecovery(config, lambda *args: None, clock=lambda: now[0], execute=execute)
    controller.update([candidate()])
    await controller.drain()
    await controller.close()
    controller = CameraRecovery(config, lambda *args: None, clock=lambda: now[0], execute=execute)
    other = candidate(objectId='mac-1:new_session:new_track', cameraId='mac-1',
                      sessionId='new_session', geometryVersion='new_geometry')
    controller.update([other])
    await controller.drain()
    assert len(calls) == 1  # camera swap/restart is not a new recovery
    controller.update([])
    for _ in range(11):
        controller.observe_scene(now[0], False)
        now[0] += .5
    other['lastSeenAt'] = now[0]
    controller.update([other])
    await controller.drain()
    assert len(calls) == 2
    await controller.close()


@pytest.mark.asyncio
async def test_other_worker_cannot_clear_latch_during_an_active_run(tmp_path):
    now, launches, finish = [1000.], [], asyncio.Event()
    config = RouteBinding('*', '*', '*', None, Path('/usr/bin/python3'), tmp_path, True)
    async def execute(obj, *args):
        launches.append(obj['objectId'])
        await finish.wait()
        return {'result': 'RETURNED_AND_RELEASED'}
    first = CameraRecovery(config, lambda *args: None, clock=lambda: now[0], execute=execute)
    second = CameraRecovery(config, lambda *args: None, clock=lambda: now[0], execute=execute)
    first.update([candidate()])
    await asyncio.sleep(0)
    for _ in range(12):
        second.update([])
        second.observe_scene(now[0], False)
        now[0] += .5
    finish.set()
    await first.drain()
    second.update([candidate(objectId='mac-1:s2:t2', cameraId='mac-1', sessionId='s2', lastSeenAt=now[0])])
    await second.drain()
    assert len(launches) == 1
    await first.close()
    await second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('step,valid', [('FORWARD_30', True), ('RELEASED', False)])
async def test_worker_only_forwards_ordered_structured_progress(tmp_path, monkeypatch, step, valid):
    notices = []
    controller = CameraRecovery(binding(tmp_path), lambda *args: notices.append(args), clock=lambda: 1000)
    obj = candidate()
    class Process:
        returncode = 0
        def __init__(self):
            self.stdout = asyncio.StreamReader()
            # Model/SDK stdout must never be treated as completed-step evidence.
            self.stdout.feed_data(b'PICKUP COMPLETE RELEASED\n')
            self.stdout.feed_eof()
        async def wait(self): return 0
    async def launch(*args, **kwargs):
        def path(flag): return Path(args[args.index(flag) + 1])
        assert path('--target-reference').read_bytes() == b'exact-crop'
        assert args[args.index('--target-object-id') + 1] == obj['objectId']
        path('--progress-file').write_text(json.dumps({'sequence':1,'step':step})+'\n')
        path('--report-file').write_text(json.dumps({'result':'RETURNED_AND_RELEASED','targetObjectId':obj['objectId']}))
        return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    if valid:
        await controller._execute(obj, binding(tmp_path), tmp_path, b'exact-crop')
        assert [n[-1] for n in notices] == ['FORWARD_30']
    else:
        with pytest.raises(RuntimeError, match='Invalid progress report'):
            await controller._execute(obj, binding(tmp_path), tmp_path, b'exact-crop')
        assert not notices
    await controller.close()
