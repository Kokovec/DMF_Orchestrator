// Facility console page. Everything shown comes from the console server; data from the facility
// and the toolkit is only ever inserted as text (textContent), never as HTML.
'use strict';

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'dataset') Object.assign(e.dataset, v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) e.append(kid);
  return e;
};

// replaceChildren, skipping absent parts (replaceChildren itself would print "null").
const fill = (node, ...kids) => node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));

const state = { ws: null, connected: false, running: null, commands: {}, status: null, retry: 500 };

// ---- terminal ----
const term = new Terminal({
  convertEol: false, cursorBlink: true, scrollback: 5000, fontSize: 13,
  fontFamily: 'ui-monospace, "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace',
  theme: { background: '#111418', foreground: '#d7dde4' },
});
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open($('term'));
const send = (obj) => { if (state.connected) state.ws.send(JSON.stringify(obj)); };
term.onData((data) => send({ type: 'input', data }));
const refit = () => { try { fit.fit(); } catch (e) { /* hidden */ } send({ type: 'resize', rows: term.rows, cols: term.cols }); };
new ResizeObserver(refit).observe($('term'));

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  state.ws = ws;
  let first = true;
  ws.onopen = () => { state.connected = true; state.retry = 500; setConn('connected', 'ok'); };
  ws.onmessage = (ev) => {
    if (typeof ev.data !== 'string') {
      if (first) { term.reset(); first = false; }  // the first frame is the replay
      term.write(new Uint8Array(ev.data));
      return;
    }
    const m = JSON.parse(ev.data);
    if (m.type === 'hello') { state.commands = m.commands; state.running = m.running; refit(); render(); }
    else if (m.type === 'status') { state.status = m.status; render(); }
    else if (m.type === 'busy') { state.running = m; message(''); render(); }
    else if (m.type === 'done') {
      state.running = null;
      message(m.status === null ? `Ended without a status (interrupted?): ${m.command}`
        : m.status === 0 ? `Done: ${m.command}` : `Failed (exit ${m.status}): ${m.command}`);
      render();
    } else if (m.type === 'status-error') message(`Status poll failed: ${m.error}`);
  };
  ws.onclose = () => {
    state.connected = false;
    setConn('disconnected, retrying…', 'bad');
    render();
    setTimeout(connect, state.retry);
    state.retry = Math.min(state.retry * 2, 8000);
  };
}

function setConn(text, cls) { const c = $('conn'); c.textContent = text; c.className = `conn ${cls}`; }
function message(text) { $('term-msg').textContent = text; }

// ---- actions ----
function commandFor(action, workload) {
  const c = state.commands[action] || '';
  return workload ? c.replace('<id>', workload) : c;
}

const CONFIRM = {
  mount: { title: 'Mount this distro for the toolkit',
    text: 'Bind-mounts this distro at /mnt/wsl/instances/<its name>, so DMF Toolkit in its own distro can read the facility profile. sudo asks for your password in the terminal. The mount lasts until WSL restarts.' },
};

async function act(action, workload) {
  const command = commandFor(action, workload);
  const c = CONFIRM[action];
  if (c) {
    $('confirm-title').textContent = c.title;
    $('confirm-text').textContent = c.text;
    $('confirm-cmd').textContent = command;
    const d = $('confirm');
    d.returnValue = '';
    d.showModal();
    await new Promise((r) => d.addEventListener('close', r, { once: true }));
    if (d.returnValue !== 'ok') return;
  }
  const r = await fetch('/api/action', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, workload }),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) message(body.reason || `Refused (${r.status})`);
  else term.focus();
}

document.addEventListener('click', (ev) => {
  const b = ev.target.closest('button.act');
  if (b && !b.disabled) act(b.dataset.action, b.dataset.workload);
});

function actButton(label, action, workload, extraDisabled, why) {
  const cmd = commandFor(action, workload);
  const disabled = !state.connected || !!state.running || !!extraDisabled;
  return el('button', { class: 'act', dataset: { action, workload }, disabled,
    title: extraDisabled && why ? `${why}\n${cmd}` : cmd }, label);
}
const stage9 = (label, cmd) => el('button', { disabled: true, title: `stage 9: ${cmd}` }, label, ' ', el('small', {}, 'stage 9'));

// ---- rendering ----
const dot = (s) => el('span', { class: `dot ${s === 'ok' ? 'ok' : s === 'bad' ? 'bad' : ''}`, 'aria-hidden': 'true' });
function health(label, r, okText) {
  r = r || { state: 'unknown', error: 'not checked yet' };
  const text = r.state === 'unknown' ? 'unknown' : (r.text || okText);
  return el('span', { class: 'item', title: r.error || r.url || '' }, label, dot(r.state), text,
    r.state !== 'ok' && r.error ? el('span', { class: 'err' }, ` (${r.error})`) : null);
}

function render() {
  const s = state.status;
  $('running').textContent = state.running ? `running: ${state.running.command}` : '';
  const f = (s && s.facility) || {};
  fill($('fac-health'), 
    health('k3s', f.k3s), health('Kubernetes', f.kubernetes), health('Keycloak', f.keycloak), health('NMOS registry', f.nmos));
  fill($('fac-capacity'), f.capacity && f.capacity.state === 'ok'
    ? el('span', { title: 'ansible/scripts/capacity.py --facility: the numbers make plan uses' }, f.capacity.text)
    : health('Capacity', f.capacity));
  const m = f.mount;
  fill($('fac-mount'), health('Toolkit mount', m),
    m && m.target ? el('span', { class: 'err' }, ` ${m.target}`) : null,
    m && m.stacked > 1 ? el('span', { class: 'err' }, ` (mounted ${m.stacked} times)`) : null);
  fill($('fac-profile'), health('Profile', f.profile));
  for (const b of document.querySelectorAll('.facility button.act')) {
    const a = b.dataset.action;
    const mounted = a === 'mount' && m && m.state === 'ok';
    b.disabled = !state.connected || !!state.running || mounted;
    b.title = mounted ? `Already mounted.\n${commandFor(a)}` : commandFor(a);
  }
  if (!s) return;
  $('updated').textContent = `status ${new Date(s.time).toLocaleTimeString()}`;

  // Workloads
  const w = s.workloads || {};
  $('wl-note').textContent = w.state === 'unknown' ? `unknown: ${w.error}` : (w.error || '');
  fill($('wl-list'), ...(w.items || []).map((x) => {
    const cls = x.state === 'on air' ? 'onair' : x.state === 'running' ? 'ok' : /failed/.test(x.state) ? 'warn' : '';
    return el('li', {},
      el('div', { class: 'head' },
        el('span', { class: 'id' }, x.id),
        el('span', { class: `state ${cls}` }, x.state === 'on air' ? 'ON AIR' : x.state),
        el('span', { class: 'muted' }, x.kind),
        x.revision != null ? el('span', {}, `rev ${x.revision}`) : null,
        x.pods ? el('span', { class: 'muted' }, `pods ${x.pods.ready}/${x.pods.total}`) : null,
        x.cpu != null ? el('span', {}, `${x.cpu} CPU`) : null,
        x.deployed_from ? el('span', { class: 'muted' }, `(deployed: ${x.deployed_from})`) : null),
      x.errors && x.errors.length ? el('div', { class: 'note' }, x.errors.join('; ')) : null,
      el('div', { class: 'buttons' },
        actButton('Open UI', 'open-ui', x.id, !x.deployed || x.kind === 'no file', 'Not deployed, or no workload file.'),
        actButton('Status', 'status', x.id, x.kind === 'no file', 'No workload file.'),
        stage9('Update', `make import, plan, provision WORKLOAD=${x.id}`),
        stage9('Finalise', `make finalise WORKLOAD=${x.id}`)));
  }));

  // Toolkit designs
  const d = s.designs || {};
  $('ds-note').textContent = d.state === 'ok' ? '' : (d.text || 'toolkit API: unknown') + (d.error ? ` (${d.error})` : '');
  fill($('ds-list'), ...(d.items || []).map((x) => el('li', {},
    el('div', { class: 'head' },
      el('span', { class: 'id' }, x.id),
      x.name && x.name !== x.id ? el('span', { class: 'muted' }, x.name) : null,
      el('span', {}, `rev ${x.revision}`),
      x.publishable === true ? el('span', { class: 'state ok' }, 'publishable')
        : x.publishable === false ? el('span', { class: 'state warn' }, `${x.reasons.length} problem${x.reasons.length === 1 ? '' : 's'}`)
          : el('span', { class: 'state' }, x.error || 'unknown'),
      x.deployed ? el('span', { class: 'muted' }, x.newer ? `deployed rev ${x.deployed_revision}: newer revision` : 'deployed') : null,
      stage9(x.deployed ? 'Update' : 'Deploy', `make import XML=http://localhost:3001/api/workloads/${x.id}/published.xml …`)),
    x.reasons && x.reasons.length ? el('ul', { class: 'reasons' }, ...x.reasons.map((r) => el('li', {}, r))) : null)));

  // Archive
  const a = s.archive || {};
  fill($('ar-list'), ...(a.state === 'unknown' ? [el('li', { class: 'note' }, `unknown: ${a.error}`)] : []),
    ...(a.items || []).map((x) => el('li', {}, el('div', { class: 'head' },
      el('span', { class: 'id' }, x.id), el('span', { class: 'muted' }, x.time),
      x.source_xml ? stage9('Redeploy', `make import XML=.state/archive/${x.run}/source.xml …`)
        : el('span', { class: 'muted' }, 'no source.xml')))));
}

render();
connect();
