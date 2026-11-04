'use strict';
const cameraId = document.querySelector('#camera-id');
const token = document.querySelector('#token');
const start = document.querySelector('#start');
const stop = document.querySelector('#stop');
const status = document.querySelector('#status');
const preview = document.querySelector('#preview');
const lens = document.querySelector('#lens');
const cameraDetails = document.querySelector('#camera-details');
const grokToggle = document.querySelector('#grok-toggle');
const grokStatus = document.querySelector('#grok-status');
let grokCamera = null, confirmedGrok = false;
async function syncGrok(enabled) {
  if (!grokCamera) return;
  grokToggle.disabled = true;
  try {
    const headers = {'Authorization': `Bearer ${grokCamera.secret}`};
    const options = {method: enabled === undefined ? 'GET' : 'PUT', credentials: 'omit', headers};
    if (enabled !== undefined) { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify({enabled}); }
    const response = await fetch(`/api/cameras/${encodeURIComponent(grokCamera.id)}/grok`, options);
    if (!response.ok) throw new Error(`Switch request failed (${response.status})`);
    const state = await response.json();
    confirmedGrok = state.enabled; grokToggle.checked = state.enabled;
    grokStatus.textContent = state.enabled ? 'ON — identification + pickup estimate after stable detection; two API calls per object episode.' :
      'OFF — no automatic Grok calls from this camera; local detection continues.';
  } catch (error) {
    grokToggle.checked = confirmedGrok;
    grokStatus.textContent = `${error.message}. Server state unconfirmed; retry the switch or restart capture.`;
  } finally { grokToggle.disabled = false; }
}
grokToggle.addEventListener('change', () => syncGrok(grokToggle.checked));
const isUltraWide = label => /ultra[\s-]*wide|0[.,]5\s*[x×]/i.test(label);
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
  stream = null; preview.srcObject = null; start.disabled = false; stop.disabled = true; lens.disabled = false;
  status.textContent = message;
}
stop.addEventListener('click', () => halt());
start.addEventListener('click', async () => {
  if (running || start.disabled) return;
  const id = cameraId.value.trim(), secret = token.value;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id) || !secret) { status.textContent = 'Enter a valid camera ID and token.'; return; }
  if (!window.isSecureContext) { status.textContent = 'HTTPS is required.'; return; }
  start.disabled = true; lens.disabled = true;
  const current = ++generation;
  try {
    grokCamera = {id, secret};
    await syncGrok();
    if (generation !== current) return;
    const selected = lens.value;
    const video = {width: {ideal: 99999}, height: {ideal: 99999}, frameRate: {ideal: 15, max: 30},
      ...(selected ? {deviceId: {exact: selected}} : {facingMode: {ideal: 'environment'}})};
    let acquired = await navigator.mediaDevices.getUserMedia({video, audio: false});
    if (generation !== current) { acquired.getTracks().forEach(track => track.stop()); return; }
    stream = acquired;
    // Labels become available after camera permission. Do not guess a lens from device order.
    let devices = [];
    try { devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput'); } catch (_) {}
    if (generation !== current) return;
    lens.replaceChildren(new Option('Auto — prefer 0.5× ultra-wide', ''),
      ...devices.map((d, i) => new Option(d.label || `Camera ${i + 1}`, d.deviceId)));
    lens.value = selected;
    const ultra = !selected && devices.find(d => isUltraWide(d.label) && !/front|facetime|user/i.test(d.label));
    if (ultra && ultra.deviceId !== stream.getVideoTracks()[0].getSettings().deviceId) {
      stream.getTracks().forEach(track => track.stop());
      acquired = await navigator.mediaDevices.getUserMedia({video: {...video, deviceId: {exact: ultra.deviceId}}, audio: false});
      if (generation !== current) { acquired.getTracks().forEach(track => track.stop()); return; }
      stream = acquired;
    }
    const track = stream.getVideoTracks()[0];
    const caps = track.getCapabilities?.() || {};
    if (caps.width?.max && caps.height?.max) {
      // Keep the existing server's 16MP limit, without shrinking frames on the canvas.
      const scale = Math.min(1, Math.sqrt(16000000 / (caps.width.max * caps.height.max)));
      try { await track.applyConstraints({width: {ideal: Math.floor(caps.width.max * scale)},
        height: {ideal: Math.floor(caps.height.max * scale)}, frameRate: video.frameRate}); } catch (_) {}
    }
    if (generation !== current) return;
    if (!isUltraWide(track.label) && caps.zoom?.min <= 0.5 && caps.zoom?.max >= 0.5) {
      try { await track.applyConstraints({...track.getConstraints?.(), advanced: [{zoom: 0.5}]}); } catch (_) {}
    }
    if (generation !== current) return;
    const settings = track.getSettings();
    const lensInfo = isUltraWide(track.label) ? 'Ultra-wide lens selected' : 'Standard / other lens selected';
    const zoomInfo = settings.zoom === 0.5 ? '0.5× zoom confirmed' :
      isUltraWide(track.label) ? 'Optical magnification not reported by browser' : '0.5× not exposed by this browser';
    cameraDetails.textContent = `${track.label || 'Camera'} — ${lensInfo}. ${zoomInfo}.`;
    preview.srcObject = stream;
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
        if (w * h > 16000000) throw new Error('Camera video exceeds the 16MP limit. Choose another lens.');
        const size = `${w}x${h}:${screen.orientation?.angle ?? window.orientation ?? 0}:${track.getSettings().zoom ?? ''}`;
        if (previous && previous !== size) geometry++;
        previous = size;
        canvas.width = w; canvas.height = h;
        canvas.getContext('2d').drawImage(preview, 0, 0, canvas.width, canvas.height);
        const captured = Date.now() / 1000;
        let blob;
        for (const quality of [0.95, 0.85, 0.75, 0.6, 0.45]) {
          blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
          if (!running || generation !== current) return;
          if (!blob || blob.size <= 4 * 1024 * 1024) break;
        }
        if (!running || generation !== current) return;
        if (!blob) throw new Error('Unable to encode camera frame.');
        if (blob.size > 4 * 1024 * 1024) throw new Error('Full-resolution JPEG exceeds 4 MiB. Choose another lens.');
        if (upload) { try { await upload; } catch (_) {} }
        if (!running || generation !== current) return;
        abort = new AbortController();
        const pending = fetch(`/api/cameras/${encodeURIComponent(id)}/frames`, {method: 'POST', credentials: 'omit', signal: abort.signal, headers: {'Content-Type': 'image/jpeg', 'Authorization': `Bearer ${secret}`, 'X-Session-Id': session, 'X-Geometry-Version': String(geometry), 'X-Frame-Id': String(sequence++), 'X-Captured-At': String(captured), 'X-Camera-Kind': 'FIXED'}, body: blob});
        upload = pending;
        let response;
        try { response = await pending; } finally { if (upload === pending) upload = null; }
        if (!response.ok) throw new Error(`Upload failed (${response.status})`);
        status.textContent = `Sending ${canvas.width} × ${canvas.height} — native resolution; phone clock`;
      } catch (error) { if (running && generation === current) { halt(error.message || 'Capture failed'); } return; }
      if (running && generation === current) timer = setTimeout(send, Math.max(0, 1000 / 3 - (performance.now() - begin)));
    };
    status.textContent = 'Starting camera…'; send();
  } catch (_) { if (generation === current) halt('Camera access failed. Check permissions and HTTPS.'); }
});
