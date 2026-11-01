// Measure SwitchFrame's browser audio pipeline inside the user's own Chrome
// (DMF profile, started with DMF_CHROME_DEBUG_PORT). Run with Windows node.exe:
//   node.exe windows-audio-probe.mjs [port] [seconds]
// Reloads the SwitchFrame tab with instrumentation, clicks once to enable
// audio, then reports per-stream decoder in/out, ring-buffer write/consume,
// underruns and silence. Read-only apart from the reload and the click.
const port = +(process.argv[2] || 9333), secs = +(process.argv[3] || 15);
const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const tab = tabs.find(t => t.type === 'page' && /dmf\.test/.test(t.url));
if (!tab) { console.error('No SwitchFrame tab found in the DMF Chrome.'); process.exit(1); }
const ws = new WebSocket(tab.webSocketDebuggerUrl); await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await send('Page.enable');
const { result: { identifier } } = await send('Page.addScriptToEvaluateOnNewDocument', { source: `
  window.__R = []; window.__D = []; window.__ev = [];
  const pm = MessagePort.prototype.postMessage;
  MessagePort.prototype.postMessage = function (msg, ...r) { if (msg && msg.type === 'init' && msg.commBuffer) __R.push({ st: new Int32Array(msg.commBuffer), size: new Float32Array(msg.sab[0]).length }); return pm.call(this, msg, ...r); };
  const AD = window.AudioDecoder;
  window.AudioDecoder = class extends AD { constructor(i) { const s = { inN: 0, outN: 0, outS: 0, q: 0, gaps: [] }; __D.push(s);
      super({ output: f => { if (window.__on) { s.outN++; s.outS += f.numberOfFrames; } i.output(f); }, error: i.error }); this.__s = s; }
    decode(c) { const s = this.__s; if (window.__on) { s.inN++; const t = performance.now(); if (s.lt) s.gaps.push(t - s.lt); s.lt = t; s.q = Math.max(s.q, this.decodeQueueSize); } return super.decode(c); } };
  window.__go = () => { window.__on = true; window.__t0 = performance.now();
    __R.forEach(r => { r.w = 0; r.c = 0; r.ls = Atomics.load(r.st, 0); r.le = Atomics.load(r.st, 1); r.sil0 = Atomics.load(r.st, 2); r.under = 0; r.lastD = 1; });
    window.__iv = setInterval(() => __R.forEach(r => { const s = Atomics.load(r.st, 0), e = Atomics.load(r.st, 1), d = Atomics.load(r.st, 24);
      if (r.ls >= 0 && s >= 0) { r.c += (s - r.ls + r.size) % r.size; r.w += (e - r.le + r.size) % r.size; } r.ls = s; r.le = e; if (d === 0 && r.lastD > 0) r.under++; r.lastD = d; }), 5); };
  window.__report = () => { clearInterval(__iv); const T = (performance.now() - __t0) / 1000;
    return { seconds: +T.toFixed(1), audioContextRates: [...new Set(__R.map(r => r.size))].length,
      decoders: __D.filter(d => d.inN).map(d => { const g = d.gaps.sort((a,b)=>a-b); return { inPerSec: +(d.inN/T).toFixed(1), outPerSec: +(d.outN/T).toFixed(1), samplesOutPerSec: Math.round(d.outS/T), maxDecodeQueue: d.q, arrivalGapP99ms: Math.round(g[Math.floor(g.length*0.99)] || 0) }; }),
      players: __R.filter(r => r.w || r.c).map(r => ({ writtenPerSec: Math.round(r.w/T), consumedPerSec: Math.round(r.c/T), underruns: r.under, silencePct: Math.round(100 * (Atomics.load(r.st, 2) - r.sil0) / (T * 1000)) })) }; };` });
await send('Page.reload', { ignoreCache: false });
await new Promise(r => setTimeout(r, 9000));
for (const type of ['mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x: 5, y: 300, button: 'left', clickCount: 1 });
await new Promise(r => setTimeout(r, 3000));
await send('Runtime.evaluate', { expression: '__go()' });
await new Promise(r => setTimeout(r, secs * 1000));
const r = await send('Runtime.evaluate', { returnByValue: true, expression: 'JSON.stringify(__report())' });
console.log(JSON.stringify(JSON.parse(r.result.result.value), null, 1));
await send('Page.removeScriptToEvaluateOnNewDocument', { identifier });
ws.close(); process.exit(0);
