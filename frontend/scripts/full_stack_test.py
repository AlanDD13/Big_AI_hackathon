"""Disposable PostgreSQL + current backend + frontend integration check.

Generated credentials stay in process memory. Never targets the shared DB.
Default runs automatically. --interactive enables commands through the printed command file.
Requires Docker, Node, frontend npm dependencies, and backend/requirements.txt.
This script only creates and contacts its own loopback PostgreSQL/backend stack.
"""
from pathlib import Path
from copy import deepcopy
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO
OUT = Path(os.environ.get('ROVR_TEST_OUTPUT_DIR', str(REPO / 'frontend/.test-artifacts'))).resolve()
WORK = OUT / 'run'
WORK.mkdir(parents=True, exist_ok=True)
INTERACTIVE = '--interactive' in sys.argv
SECRETS = [secrets.token_urlsafe(24) for _ in range(4)]
ADMIN, RUNTIME, OPERATOR, PEER = SECRETS
NAME = 'rovr-fulltest-' + uuid4().hex[:10]
ENV = dict(os.environ, POSTGRES_PASSWORD=ADMIN)
RESULTS = []
CHILDREN = []
FILES = []

def redact(value):
    for secret in SECRETS:
        value = value.replace(secret, '[redacted]')
    return value

def check(name, passed, detail=''):
    RESULTS.append(dict(name=name, passed=bool(passed), detail=detail))
    print(('PASS ' if passed else 'FAIL ') + name + (': ' + detail if detail else ''), flush=True)
    (OUT / 'full-stack-results.json').write_text(json.dumps(RESULTS, indent=2), encoding='utf-8')

def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(redact(result.stderr[-2000:] or result.stdout[-2000:]))
    return result.stdout.strip()

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

def database_url(port, database, user='rover_backend_runtime', password=None):
    return f'postgresql://{user}:{password or RUNTIME}@127.0.0.1:{port}/{database}?sslmode=disable'

def suite(name, args, env, cwd=BACKEND):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    log = redact(result.stdout + result.stderr)
    (OUT / f'{name}-test-log.txt').write_text(log, encoding='utf-8')
    check(name, result.returncode == 0, '\n'.join(log.strip().splitlines()[-4:]))

def start_process(args, name, env, cwd=BACKEND):
    log = (WORK / f'{name}.log').open('w', encoding='utf-8')
    FILES.append(log)
    process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    CHILDREN.append(process)
    return process

def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

def main():
    import psycopg
    from psycopg import sql
    import httpx

    command(['docker', 'run', '--detach', '--rm', '--name', NAME,
             '--label', 'codex.task=rovr-full-stack-test', '--env', 'POSTGRES_PASSWORD',
             '--publish', '127.0.0.1::5432', '--tmpfs', '/var/lib/postgresql:rw', 'postgres:18'], env=ENV)
    port = int(command(['docker', 'port', NAME, '5432']).split(':')[-1])
    admin_url = database_url(port, 'postgres', 'postgres', ADMIN)
    deadline = time.monotonic() + 45
    while True:
        try:
            with psycopg.connect(admin_url, autocommit=True) as conn:
                conn.execute(sql.SQL('CREATE ROLE rover_backend_runtime LOGIN PASSWORD {}').format(sql.Literal(RUNTIME)))
                for db in ['rovr_unit', 'rovr_api']:
                    conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db)))
            break
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise RuntimeError('Disposable PostgreSQL did not become ready')
            time.sleep(.3)
    for db in ['rovr_unit', 'rovr_api']:
        with psycopg.connect(database_url(port, db, 'postgres', ADMIN), autocommit=True) as conn:
            for migration in sorted((BACKEND / 'backend/migrations').glob('*.sql')):
                conn.execute(migration.read_text(encoding='utf-8'))
    check('PostgreSQL 18 disposable migrations 001-007', True)
    unit_url = database_url(port, 'rovr_unit')
    api_url = database_url(port, 'rovr_api')
    test_env = dict(os.environ, BACKEND_TEST_DATABASE_URL=unit_url, BACKEND_DATABASE_URL='', OPERATOR_TOKEN=OPERATOR, PEER_TOKEN=PEER)
    suite('backend-postgres', [sys.executable, '-m', 'unittest', 'discover', '-s', 'backend/tests', '-v'], test_env)
    with psycopg.connect(unit_url, autocommit=True) as conn:
        for name in ['storage_smoke', 'enqueue_smoke', 'claim_dispatch_smoke', 'dispatch_outcome_smoke']:
            try:
                conn.execute((BACKEND / f'backend/tests/{name}.sql').read_text(encoding='utf-8'))
                check(f'SQL {name}', True)
            except Exception as exc:
                check(f'SQL {name}', False, redact(str(exc)))
                conn.execute('ROLLBACK')
    backend_port, vite_port = free_port(), free_port()
    backend_url = f'http://127.0.0.1:{backend_port}'
    frontend_url = f'http://127.0.0.1:{vite_port}'
    app_env = dict(os.environ, BACKEND_DATABASE_URL=api_url, BACKEND_TEST_DATABASE_URL='',
                   OPERATOR_TOKEN=OPERATOR, PEER_TOKEN=PEER, BACKEND_COOKIE_SECURE='0', BACKEND_MISSION_FIXTURE_MAPPING='1', BACKEND_URL=backend_url)

    def launch_backend():
        process = start_process([sys.executable, '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', str(backend_port)], 'backend', app_env)
        for _ in range(100):
            try:
                if httpx.get(backend_url + '/healthz', timeout=.5).status_code == 200:
                    return process
            except httpx.HTTPError:
                pass
            if process.poll() is not None:
                raise RuntimeError(redact((WORK / 'backend.log').read_text()[-2000:]))
            time.sleep(.1)
        raise RuntimeError('Backend startup timed out')

    backend = launch_backend()
    client = httpx.Client(base_url=backend_url, timeout=5)
    peer = {'Authorization': f'Bearer {PEER}'}
    check('snapshot requires operator cookie', client.get('/api/snapshot').status_code == 401)
    check('peer token does not replace operator login', client.post('/api/session', json={'token': PEER}).status_code == 401)
    check('operator session cookie', client.post('/api/session', json={'token': OPERATOR}).status_code == 204)
    initial = client.get('/api/snapshot').json()
    check('fresh database gives empty snapshot', initial['objects'] == [] and initial['missions'] == [])
    camera = 'e2e-mapping-' + uuid4().hex[:8]
    stamp = time.time()
    for frame in range(11):
        captured = stamp - 5 + frame * .5
        sample = dict(cameraId=camera, sessionId='session1', geometryVersion='e2e', frameId=frame, trackId='track1',
                      mapId='test-floor', mapVersion='v1', calibrationId='e2e-only', calibrationValid=True,
                      capturedAt=captured, receivedAt=captured, valid=True, invalidReason=None, floorPointM=[.2, .3], positionErrorM=.01)
        response = client.post('/api/observations', json=sample, headers=peer)
        assert response.status_code == 200, response.text
    mapping_id = f'{camera}:session1:track1'
    with psycopg.connect(api_url) as conn:
        db_object, = conn.execute("UPDATE rover_backend.objects SET state = state || '{\"pickupProfileId\":\"demo\"}'::jsonb WHERE camera_id=%s RETURNING id", (camera,)).fetchone()
        conn.execute("INSERT INTO rover_backend.classifications (object_id,status,data) VALUES (%s,'completed',%s::jsonb)",
                     (db_object, json.dumps(dict(category='TRASH', subject_kind='INANIMATE', carrying='NO', confidence='HIGH', provider='fixture'))))
    check('mapping observations stored in PostgreSQL', True)
    mission_body = dict(objectId=mapping_id, destinationZoneId='test-zone', source='manual')
    headers = {'Idempotency-Key': 'e2e-' + uuid4().hex}
    created = client.post('/api/missions', json=mission_body, headers=headers)
    check('mission queue creation', created.status_code == 202, str(created.status_code))
    mission = created.json()
    check('mission idempotent replay', client.post('/api/missions', json=mission_body, headers=headers).status_code == 200)
    check('mission changed-body conflict', client.post('/api/missions', json={**mission_body, 'destinationZoneId': 'other-zone'}, headers=headers).status_code == 409)
    with psycopg.connect(api_url) as conn:
        count, = conn.execute('SELECT count(*) FROM rover_backend.missions WHERE command_id IS NOT NULL').fetchone()
    check('queue does not dispatch robot', count == 0)
    fixture = json.loads((BACKEND / 'backend/tests/fixtures/vision-recovery.json').read_text())
    sample = fixture['sampleObject']
    run = uuid4().hex[:8]
    vision_id = f'e2e-vision-{run}:session1:track1'
    boot = uuid4().hex
    seq = 0
    last_event = None

    def publish(stage):
        nonlocal seq, last_event
        seq += 1
        body = deepcopy(sample)
        body.update(objectId=vision_id, cameraId=f'e2e-vision-{run}', trackId='track1', producerBootId=boot, entityVersion=seq,
                    revision=0, capturedAt=time.time(), lastSeenAt=time.time(), frameId=seq, mapping=None, potentialTarget=False,
                    status='OBSERVING', classificationStatus='PENDING', classification=None, classificationProvider=None,
                    metricStationarySeconds=0, eligibilityReason='NOT_ELIGIBLE', visualPickupStatus='PENDING', visualPickupAdvisory=None)
        if stage in {'classify', 'advisory'}:
            body.update(classificationStatus='COMPLETED', classification=deepcopy(sample['classification']), classificationProvider='fixture', status='STATIC_PENDING_REVIEW')
        if stage == 'advisory':
            body.update(visualPickupStatus='COMPLETED', visualPickupAdvisory=dict(object_form='OTHER', visual_suitability='POSSIBLE',
                reason='Local integration test only; no physical pickup.', advisory=True, pickupVerified=False, sourceFrameId=seq))
        kind = 'object.observed' if stage == 'observe' else 'object.classified' if stage == 'classify' else 'object.updated'
        route = '/api/detections' if kind == 'object.observed' else '/api/classifications' if kind == 'object.classified' else '/api/vision-events'
        event = dict(schemaVersion='vision.v1', producerBootId=boot, eventId=f'{boot}:{seq}', sequence=seq, type=kind, timestamp=time.time(), payload=body)
        result = client.post(route, json=event, headers=peer)
        check(f'vision {stage} HTTP acceptance', result.status_code == 200, str(result.status_code))
        last_event = (route, event)
        snapshot = client.get('/api/snapshot').json()
        current = next(o for o in snapshot['objects'] if o['objectId'] == vision_id)
        if stage in {'observe', 'pending'}:
            check(f'vision {stage} has no stale classification or advisory', current['classification'] is None and current['visualPickupAdvisory'] is None)
        elif stage == 'classify':
            check('classification confidence is separate from detector score', current['classification']['confidence'] == 'HIGH' and current['detectorScore'] == .87)
        else:
            check('visual advice never verifies pickup', current['visualPickupAdvisory']['pickupVerified'] is False and current['dispatchEligible'] is False)
        return event

    check('vision routes require peer authentication', client.post('/api/detections', json={}).status_code == 401)
    publish('observe')
    snap = client.get('/api/snapshot').json()
    row = next(o for o in snap['objects'] if o['objectId'] == vision_id)
    check('observed object appears before classification', row['status'] == 'OBSERVING' and row['classification'] is None)
    before = snap['cursor']
    publish('classify')
    with client.stream('GET', '/api/events', params={'after': before}) as stream:
        event = []
        for line in stream.iter_lines():
            event.append(line)
            if not line and len(event) > 1:
                break
    check('SSE carries classification update', any('"classification"' in line and 'HIGH' in line for line in event))
    publish('advisory')
    snap = client.get('/api/snapshot').json()
    row = next(o for o in snap['objects'] if o['objectId'] == vision_id)
    check('advisory remains unverified and non-dispatchable', row['visualPickupAdvisory']['pickupVerified'] is False and row['dispatchEligible'] is False)
    cursor = snap['cursor']
    replay = client.post(last_event[0], json=last_event[1], headers=peer)
    check('vision exact replay emits no extra event', replay.json().get('replayed') is True and client.get('/api/snapshot').json()['cursor'] == cursor)
    with psycopg.connect(api_url) as conn:
        stored, = conn.execute('SELECT count(*) FROM rover_backend.objects WHERE camera_id=%s', (f'e2e-vision-{run}',)).fetchone()
    check('vision ingest persists its object in PostgreSQL', stored == 1, f'{stored} matching rows')

    vite = start_process([shutil.which('node') or 'node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', str(vite_port), '--strictPort'], 'frontend', app_env, cwd=REPO / 'frontend')
    for _ in range(100):
        try:
            if httpx.get(frontend_url, timeout=.5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if vite.poll() is not None:
            raise RuntimeError('Frontend development server did not start')
        time.sleep(.1)
    frontend = httpx.Client(base_url=frontend_url, timeout=5)
    local_headers = {'Origin': frontend_url, 'X-Rovr-Local': '1'}
    session = frontend.post('/__local/session', headers=local_headers)
    check('frontend local session setup', session.status_code == 204 and 'httponly' in session.headers.get('set-cookie', '').lower())
    check('frontend proxy serves database-backed snapshot', frontend.get('/api/snapshot').status_code == 200)
    check('local session rejects cross-origin request', frontend.post('/__local/session', headers={**local_headers, 'Origin': 'https://example.invalid'}).status_code == 403)
    browser_env = frontend.get('/@vite/env').text
    check('server credentials absent from browser env module', all(secret not in browser_env for secret in SECRETS))
    print(f'BACKEND_URL {backend_url}\nFRONTEND_URL {frontend_url}\nVISION_OBJECT_ID {vision_id}', flush=True)
    print('READY_FOR_BROWSER: observe/classify/advisory/pending/notice/restart/quit', flush=True)
    control = WORK / 'command.txt'
    control.write_text('', encoding='utf-8')
    print(f'COMMAND_FILE {control}', flush=True)
    actions = iter(['observe', 'classify', 'advisory', 'pending', 'advisory', 'notice', 'restart', 'quit'])
    while True:
        action = control.read_text(encoding='utf-8').strip() if INTERACTIVE else next(actions)
        if not action:
            time.sleep(.15)
            continue
        if INTERACTIVE:
            control.write_text('', encoding='utf-8')
        if action == 'quit':
            break
        if action in {'observe', 'classify', 'advisory', 'pending'}:
            publish(action)
        elif action == 'notice':
            seq += 1
            response = client.post('/api/vision-events', json=dict(schemaVersion='vision.v1', producerBootId=boot,
                eventId=f'{boot}:{seq}', sequence=seq, type='alert.camera_unavailable', timestamp=time.time(),
                payload=dict(cameraId=f'e2e-vision-{run}', reason='LOCAL_TEST_CAMERA_OFFLINE')), headers=peer)
            check('camera-wide alert accepted', response.status_code == 200)
        elif action == 'restart':
            old = client.get('/api/snapshot').json()
            stop(backend)
            backend = launch_backend()
            check('restart invalidates previous session', client.get('/api/snapshot').status_code == 401)
            client.post('/api/session', json={'token': OPERATOR})
            current = client.get('/api/snapshot').json()
            check('restart changes backend boot', old['backendBootId'] != current['backendBootId'])
            check('mapping object survives restart', any(o['objectId'] == mapping_id for o in current['objects']))
            check('queued mission survives restart', any(m.get('missionId') == mission.get('missionId') for m in current['missions']))
            check('vision object survives restart', any(o['objectId'] == vision_id for o in current['objects']))
        print('COMMAND_COMPLETE ' + action, flush=True)
    frontend.close()
    client.close()

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        check('integration harness execution', False, redact(str(exc)))
    finally:
        for process in reversed(CHILDREN):
            stop(process)
        for file in FILES:
            file.close()
        if NAME.startswith('rovr-fulltest-'):
            subprocess.run(['docker', 'stop', '--time', '5', NAME], capture_output=True)
        print('CLEANUP_COMPLETE', flush=True)
    sys.exit(0 if RESULTS and all(row['passed'] for row in RESULTS) else 1)
