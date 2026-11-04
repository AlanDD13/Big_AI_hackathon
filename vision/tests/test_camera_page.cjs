const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function page({ultra = false, zoom = false, largeJpeg = false} = {}) {
  const elements = {};
  for (const id of ['camera-id', 'token', 'start', 'stop', 'status', 'preview', 'lens', 'camera-details', 'grok-toggle', 'grok-status']) {
    elements[id] = {value: '', disabled: false, textContent: '', listeners: {},
      addEventListener(type, handler) { this.listeners[type] = handler; },
      replaceChildren(...options) { this.options = options; }};
  }
  elements['camera-id'].value = 'phone-2';
  elements.token.value = 'scoped-camera-token';
  elements.preview.videoWidth = 3840;
  elements.preview.videoHeight = 2160;
  elements.preview.play = async () => {};
  const gridLabels = [];
  const grid = {width: 0, height: 0, getContext: () => ({beginPath() {}, moveTo() {}, lineTo() {}, stroke() {},
    strokeText() {}, fillText(label) { gridLabels.push(label); }})};
  elements['grid-overlay'] = grid;
  const calls = [], uploads = [], applied = [], qualities = [], tracks = [], controls = [];
  let grokEnabled = false;
  const devices = [{kind: 'videoinput', deviceId: 'rear', label: 'Back Camera'}];
  if (ultra) devices.push({kind: 'videoinput', deviceId: 'ultra', label: 'Back Ultra Wide Camera'});
  const canvas = {width: 0, height: 0, getContext: () => ({drawImage() {}}),
    toBlob(callback, type, quality) {
      qualities.push(quality);
      callback({size: largeJpeg && quality > 0.8 ? 5 * 1024 * 1024 : 1024});
    }};
  const context = {
    document: {querySelector: selector => elements[selector.slice(1)], createElement: () => canvas},
    location: {hash: '', pathname: '/camera', search: ''}, history: {},
    window: {isSecureContext: true}, screen: {orientation: {angle: 0}},
    navigator: {mediaDevices: {
      enumerateDevices: async () => devices,
      getUserMedia: async constraints => {
        calls.push(constraints);
        const deviceId = constraints.video.deviceId?.exact || 'rear';
        const settings = {deviceId, zoom: 1};
        const track = {label: devices.find(d => d.deviceId === deviceId).label, stopped: false,
          stop() { this.stopped = true; }, addEventListener() {},
          getSettings: () => settings,
          getCapabilities: () => ({width: {max: 3840}, height: {max: 2160},
            ...(zoom ? {zoom: {min: 0.5, max: 5}} : {})}),
          async applyConstraints(constraints) {
            applied.push(constraints);
            if (constraints.advanced?.[0]?.zoom !== undefined) settings.zoom = constraints.advanced[0].zoom;
          }};
        tracks.push(track);
        return {getTracks: () => [track], getVideoTracks: () => [track]};
      }}},
    crypto: {randomUUID: () => 'test-session'}, URLSearchParams, AbortController,
    Option: function(text, value) { this.text = text; this.value = value; },
    performance: {now: () => 0}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options) => {
      if (url.endsWith('/grok')) {
        controls.push({url, ...options});
        if (options.method === 'PUT') grokEnabled = JSON.parse(options.body).enabled;
        return {ok: true, json: async () => ({enabled: grokEnabled})};
      }
      uploads.push({url, ...options}); return {ok: true};
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/camera.js'), 'utf8'), context);
  return {elements, canvas, calls, uploads, applied, qualities, tracks, controls, grid, gridLabels,
    async start() {
      // Camera script initializes credential fields from the URL fragment.
      elements['camera-id'].value = 'phone-2'; elements.token.value = 'scoped-camera-token';
      await elements.start.listeners.click();
      for (let i = 0; i < 30; i++) await Promise.resolve();
    }};
}

test('prefers the ultra-wide lens and uploads native 4K dimensions', async () => {
  const p = page({ultra: true});
  await p.start();
  assert.equal(p.calls[1]?.video.deviceId.exact, 'ultra');
  assert.equal(p.tracks[0].stopped, true);
  assert.equal(p.canvas.width, 3840);
  assert.equal(p.canvas.height, 2160);
  assert.match(p.elements['camera-details'].textContent, /Ultra Wide/);
  assert.equal(p.uploads.length, 1);
  assert.equal(p.uploads[0].headers['Authorization'], 'Bearer scoped-camera-token');
});

test('requests 0.5 zoom only when supported and reports the actual setting', async () => {
  const p = page({zoom: true});
  await p.start();
  assert.ok(p.applied.some(c => c.advanced?.[0]?.zoom === 0.5));
  assert.match(p.elements['camera-details'].textContent, /0.5/);
});

test('unsupported 0.5 lens is reported without blocking full-resolution capture', async () => {
  const p = page();
  await p.start();
  assert.match(p.elements['camera-details'].textContent, /0.5.*not exposed/i);
  assert.equal(p.uploads.length, 1);
  assert.equal(p.canvas.width, 3840);
  p.elements.stop.listeners.click();
  assert.ok(p.tracks.every(track => track.stopped));
});

test('keeps JPEG under upload limit by adjusting quality, preserving dimensions', async () => {
  const p = page({largeJpeg: true});
  await p.start();
  assert.ok(p.uploads[0].body.size <= 4 * 1024 * 1024);
  assert.equal(p.canvas.width, 3840);
  assert.ok(p.qualities.length > 1);
});

test('honours an explicitly selected lens instead of switching to ultra-wide', async () => {
  const p = page({ultra: true});
  p.elements.lens.value = 'rear';
  await p.start();
  assert.equal(p.calls.length, 1);
  assert.equal(p.calls[0].video.deviceId.exact, 'rear');
});

test('does not upload frames exceeding the existing 16MP server limit', async () => {
  const p = page();
  p.elements.preview.videoWidth = 8000;
  p.elements.preview.videoHeight = 6000;
  await p.start();
  assert.equal(p.uploads.length, 0);
  assert.match(p.elements.status.textContent, /16MP/);
  assert.ok(p.tracks.every(track => track.stopped));
});

test('Grok switch shows server-confirmed OFF and sends explicit ON/OFF changes', async () => {
  const p = page();
  await p.start();
  assert.equal(p.elements['grok-toggle'].checked, false);
  assert.match(p.elements['grok-status'].textContent, /OFF/);
  p.elements['grok-toggle'].checked = true;
  await p.elements['grok-toggle'].listeners.change();
  assert.equal(p.elements['grok-toggle'].checked, true);
  assert.match(p.elements['grok-status'].textContent, /ON/);
  p.elements['grok-toggle'].checked = false;
  await p.elements['grok-toggle'].listeners.change();
  assert.match(p.elements['grok-status'].textContent, /OFF/);
  assert.deepEqual(p.controls.filter(c => c.method === 'PUT').map(c => JSON.parse(c.body).enabled), [true, false]);
});

test('preview draws all sixteen sectors separately from native camera uploads', async () => {
  const p = page();
  await p.start();
  assert.equal(p.gridLabels.length, 16);
  assert.equal(p.gridLabels[0], 'A1');
  assert.equal(p.gridLabels[15], 'D4');
  assert.equal(p.grid.width, 3840);
  assert.equal(p.grid.height, 2160);
  assert.notEqual(p.grid, p.canvas);
  assert.equal(p.uploads.length, 1);
});
