"""Loopback browser acceptance fixture: actual backend + P4 fake tuple output.

No database, SDK, robot, camera or model is accessed. Generated credentials stay
in process memory. Stop by writing quit to the printed control-file path.
Requires backend requirements, Pillow is not needed; frontend npm dependencies.
"""
from pathlib import Path
import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time

REPO = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get('ROVR_MANUAL_QA_DIR', str(REPO / 'frontend/.test-artifacts/manual'))).resolve()
OUT.mkdir(parents=True, exist_ok=True)
CONTROL = OUT / 'control.txt'
CONTROL.write_text('run', encoding='utf-8')
sys.path.insert(0, str(REPO))
from backend.app import create_app
from backend.manual_proxy import ManualProxy
import uvicorn

spec = importlib.util.spec_from_file_location('offline_bridge_fixture', REPO / 'robot/tests/test_bridge.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fixture = module.BridgeTests()
bridge, clock, writes = fixture._make_bridge()
assert bridge.physical is False and isinstance(writes, list)
fixture._ready(bridge, clock)
clock.wall = time.time()
peer, operator = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
p4 = module.BridgeHTTPServer(bridge, host='127.0.0.1', port=0, peer_token=peer)
p4_url = p4.start()
proxy = ManualProxy(url=p4_url, peer_token=peer, now=time.time, autonomous_active=lambda _: False)
app = create_app(operator_token=operator, peer_token=peer, cookie_secure=False, manual_proxy=proxy)
def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]
bp, fp = free_port(), free_port()
backend = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=bp, log_level='warning'))
threading.Thread(target=backend.run, daemon=True).start()
alive = threading.Event()
alive.set()
def telemetry():
    last = time.monotonic()
    sample = 1000
    while alive.is_set():
        current = time.monotonic()
        clock.advance(current - last)
        clock.wall = time.time()
        last = current
        sample += 1
        mode = CONTROL.read_text(encoding='utf-8-sig').strip()
        if mode != 'stale':
            bridge.controller.heartbeat()
            velocity = bridge.controller.status()['requestedVelocity']
            bridge.controller.observe(*velocity, sample_id=sample)
        bridge.controller.tick()
        (OUT / 'evidence.json').write_text(json.dumps({'synthetic': True, 'physical': bridge.physical, 'outputCount': len(writes), 'nonzeroOutputs': sum(any(v) for v in writes), 'lastOutput': writes[-1] if writes else None, 'stage': bridge.manual_status('rover-1')[1].get('stage')}), encoding='utf-8')
        time.sleep(.05)
threading.Thread(target=telemetry, daemon=True).start()
env = dict(os.environ, BACKEND_URL=f'http://127.0.0.1:{bp}', OPERATOR_TOKEN=operator)
log = (OUT / 'vite.log').open('w', encoding='utf-8')
child = subprocess.Popen([shutil.which('node'), 'node_modules/vite/bin/vite.js', '--port', str(fp)], cwd=REPO / 'frontend', env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
(OUT / 'state.json').write_text(json.dumps({'frontend': f'http://127.0.0.1:{fp}/', 'control': str(CONTROL), 'synthetic': True}), encoding='utf-8')
print(f'Simulator-only browser fixture http://127.0.0.1:{fp}/\nControl: {CONTROL}', flush=True)
try:
    while CONTROL.read_text(encoding='utf-8-sig').strip() != 'quit':
        if child.poll() is not None:
            raise RuntimeError('Vite exited; inspect local fixture log')
        time.sleep(.25)
finally:
    child.terminate()
    try:
        child.wait(timeout=4)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    alive.clear()
    p4.close()
    bridge.close()
    backend.should_exit = True
    log.close()
