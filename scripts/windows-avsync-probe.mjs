// Measure flash/beep alignment as SwitchFrame's browser player presents it,
// inside the user's own Chrome (DMF profile, started with DMF_CHROME_DEBUG_PORT).
// Put an mxl-syncsrc ("Sync test") on programme with its audio up, then run
// with Windows node.exe:
//   node.exe windows-avsync-probe.mjs [port] [seconds]
// Reloads the SwitchFrame tab with instrumentation and clicks once to enable
// audio. Read-only apart from the reload and the click.
//
// Video: every VideoFrame drawn to a large (monitor) canvas is sampled (4x4
//        downscale; small tiles are skipped: per-frame readback on all of
//        them slows the page enough to make the player drop frames);
//        a flash is the first bright draw after a dark one (time = draw call;
//        the screen shows it at the next vsync, +0..17 ms).
// Audio: every AudioWorkletNode the player connects is tapped by a detector
//        worklet; a beep is the first loud block after 200 ms of quiet, timed
//        with AudioContext.getOutputTimestamp(), i.e. when it leaves the
//        output device (includes the reported output latency; Bluetooth
//        devices may add latency the OS does not report).
const port = +(process.argv[2] || 9333), secs = +(process.argv[3] || 20);
const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const tab = tabs.find(t => t.type === 'page' && /dmf\.test/.test(t.url));
if (!tab) { console.error('No SwitchFrame tab found in the DMF Chrome.'); process.exit(1); }
const ws = new WebSocket(tab.webSocketDebuggerUrl); await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });

const inject = `(() => {
  const V = window.__V = new Map(), A = window.__A = [];
  const probe = new OffscreenCanvas(4, 4).getContext('2d', { willReadFrequently: true });
  const di = CanvasRenderingContext2D.prototype.drawImage;
  let nextCanvas = 0;
  CanvasRenderingContext2D.prototype.drawImage = function (img, ...rest) {
    if (window.__on && typeof VideoFrame !== 'undefined' && img instanceof VideoFrame && this.canvas.width >= 300) {
      const t = performance.now();
      const c = this.canvas; if (c.__id === undefined) c.__id = nextCanvas++;
      let s = V.get(c.__id); if (!s) { s = { w: c.width, h: c.height, draws: 0, bright: false, flashes: [] }; V.set(c.__id, s); }
      probe.drawImage(img, 0, 0, 4, 4); const d = probe.getImageData(0, 0, 4, 4).data; let y = 0; for (let i = 0; i < 64; i += 4) y += d[i + 1]; y /= 16;
      s.draws++; const b = y > 128; if (b && !s.bright) s.flashes.push({ t, pts: img.timestamp }); s.bright = b;
    }
    return di.call(this, img, ...rest);
  };
  const tapSrc = 'class T extends AudioWorkletProcessor { constructor() { super(); this.q = 1e9; } process(i) { const ch = i[0] && i[0][0]; if (ch) { for (let k = 0; k < ch.length; k++) { const a = Math.abs(ch[k]); if (a > 0.05 && this.q > sampleRate * 0.3) this.port.postMessage(currentFrame + k); if (a > 0.01) this.q = 0; else this.q++; } } return true; } } registerProcessor("dmf-av-tap", T);';
  const tapUrl = URL.createObjectURL(new Blob([tapSrc], { type: 'application/javascript' }));
  const ready = new WeakMap();
  const conn = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (dest, ...rest) {
    const r = conn.call(this, dest, ...rest);
    if (this instanceof AudioWorkletNode && !this.__tapped && !(this.__isTap)) {
      this.__tapped = true; const src = this, ctx = this.context;
      if (!ready.has(ctx)) ready.set(ctx, ctx.audioWorklet.addModule(tapUrl));
      ready.get(ctx).then(() => {
        const tap = new AudioWorkletNode(ctx, 'dmf-av-tap'); tap.__isTap = true;
        const mute = ctx.createGain(); mute.gain.value = 0;
        conn.call(src, tap); conn.call(tap, mute); conn.call(mute, ctx.destination);
        const s = { rate: ctx.sampleRate, outputLatencyMs: 0, beeps: [] }; A.push(s);
        tap.port.onmessage = (e) => { if (!window.__on) return; const o = ctx.getOutputTimestamp();
          s.outputLatencyMs = (ctx.outputLatency || 0) * 1000;
          s.beeps.push(o.performanceTime + (e.data / ctx.sampleRate - o.contextTime) * 1000); };
      });
    }
    return r;
  };
})();`;

await send('Page.enable');
const { result: { identifier } } = await send('Page.addScriptToEvaluateOnNewDocument', { source: inject });
await send('Page.reload', { ignoreCache: false });
await new Promise(r => setTimeout(r, 9000));
for (const type of ['mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x: 5, y: 300, button: 'left', clickCount: 1 });
await new Promise(r => setTimeout(r, 4000));
await send('Runtime.evaluate', { expression: 'window.__on = true' });
await new Promise(r => setTimeout(r, secs * 1000));
const r = await send('Runtime.evaluate', { returnByValue: true, expression: `JSON.stringify({ V: [...__V.entries()], A: __A })` });
await send('Page.removeScriptToEvaluateOnNewDocument', { identifier });
ws.close();

const { V, A } = JSON.parse(r.result.result.value);
const med = (x) => { const s = [...x].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : NaN; };
console.log(`canvases with video: ${V.length}, tapped audio players: ${A.length}`);
V.forEach(([cid, v]) => console.log(`  canvas ${cid} (${v.w}x${v.h}): ${v.draws} draws, ${v.flashes.length} flashes`));
A.forEach((a, i) => console.log(`  audio ${i}: ${a.beeps.length} beeps, reported output latency ${a.outputLatencyMs.toFixed(1)} ms`));
for (const [cid, v] of V) {
  if (v.flashes.length < 3) continue;
  A.forEach((a, i) => {
    if (a.beeps.length < 3) return;
    const d = a.beeps.map(b => { const f = v.flashes.reduce((p, q) => Math.abs(q.t - b) < Math.abs(p.t - b) ? q : p); return b - f.t; }).filter(x => Math.abs(x) < 500);
    if (d.length >= 3) console.log(`canvas ${cid} vs audio ${i}: beep minus flash median ${med(d).toFixed(0)} ms (range ${Math.min(...d).toFixed(0)} .. ${Math.max(...d).toFixed(0)}, n=${d.length})  [+ = sound after picture]`);
  });
}
