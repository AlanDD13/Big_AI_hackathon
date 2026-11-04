'use strict';
const cameraId = document.querySelector('#camera-id');
const token = document.querySelector('#token');
const start = document.querySelector('#start');
const stop = document.querySelector('#stop');
const status = document.querySelector('#status');
const preview = document.querySelector('#preview');
const canvas = document.createElement('canvas');
let stream = null, running = false, generation = 0, timer = null, abort = null, upload = null;
const fragment = new URLSearchParams(location.hash.slice(1));
cameraId.value = fragment.get('cameraId') || '';
token.value = fragment.get('token') || '';
if (location.hash) history.replaceState(null, '', location.pathname + location.search);
function halt(message = 'Stopped') {
  running = false; generation++; clearTimeout(timer);
  if (abort) abort.abort();
  if (stream) stream.getTracks().forEach(track => track.stop());
  stream = null; preview.srcObject = null; start.disabled = false; stop.disabled = true;
  status.textContent = message;
}
stop.addEventListener('click', () => halt());
start.addEventListener('click', async () => {
  if (running || start.disabled) return;
  const id = cameraId.value.trim(), secret = token.value;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id) || !secret) { status.textContent = 'Enter a valid camera ID and token.'; return; }
  if (!window.isSecureContext) { status.textContent = 'HTTPS is required.'; return; }
  start.disabled = true;
  const current = ++generation;
  try {
    const acquired = await navigator.mediaDevices.getUserMedia({video: {
      facingMode: {ideal: 'environment'}, width: {ideal: 1920}, height: {ideal: 1080},
      frameRate: {ideal: 15, max: 30}
    }, audio: false});
    if (generation !== current) { acquired.getTracks().forEach(track => track.stop()); return; }
    stream = acquired; preview.srcObject = stream;
    stream.getVideoTracks()[0].addEventListener('ended', () => { if (generation === current) halt('Camera stopped.'); });
    if (!preview.videoWidth) await new Promise((resolve, reject) => { preview.onloadedmetadata = resolve; setTimeout(() => reject(new Error('Camera not ready')), 5000); });
    await preview.play();
    if (generation !== current) return;
    running = true; stop.disabled = false;
    const session = crypto.randomUUID(); let sequence = 0, geometry = 0, previous = '';
    const send = async () => {
      if (!running || generation !== current) return;
      const begin = performance.now();
      try {
        const w = preview.videoWidth, h = preview.videoHeight;
        if (!w || !h) throw new Error('Camera is not ready');
        if (w < h) throw new Error('Turn the phone to landscape and restart capture.');
        const size = `${w}x${h}:${screen.orientation?.angle ?? window.orientation ?? 0}`;
        if (previous && previous !== size) geometry++;
        previous = size;
        canvas.width = Math.min(1920, w); canvas.height = Math.max(1, Math.round(h * canvas.width / w));
        canvas.getContext('2d').drawImage(preview, 0, 0, canvas.width, canvas.height);
        const captured = Date.now() / 1000;
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));
        if (!running || generation !== current) return;
        if (!blob) throw new Error('Unable to encode camera frame.');
        if (upload) { try { await upload; } catch (_) {} }
        if (!running || generation !== current) return;
        abort = new AbortController();
        const pending = fetch(`/api/cameras/${encodeURIComponent(id)}/frames`, {method: 'POST', credentials: 'omit', signal: abort.signal, headers: {'Content-Type': 'image/jpeg', 'Authorization': `Bearer ${secret}`, 'X-Session-Id': session, 'X-Geometry-Version': String(geometry), 'X-Frame-Id': String(sequence++), 'X-Captured-At': String(captured), 'X-Camera-Kind': 'FIXED'}, body: blob});
        upload = pending;
        let response;
        try { response = await pending; } finally { if (upload === pending) upload = null; }
        if (!response.ok) throw new Error(`Upload failed (${response.status})`);
        status.textContent = `Sending ${canvas.width} × ${canvas.height}; phone clock`;
      } catch (error) { if (running && generation === current) { halt(error.message || 'Capture failed'); } return; }
      if (running && generation === current) timer = setTimeout(send, Math.max(0, 1000 / 3 - (performance.now() - begin)));
    };
    status.textContent = 'Starting camera…'; send();
  } catch (_) { if (generation === current) halt('Camera access failed. Check permissions and HTTPS.'); }
});
