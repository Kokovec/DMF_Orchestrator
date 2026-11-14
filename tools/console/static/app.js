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
    else if (k === 'onclick') e.addEventListener('click', v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) e.append(kid);
  return e;
};

// replaceChildren, skipping absent parts (replaceChildren itself would print "null").
const fill = (node, ...kids) => node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

const state = { ws: null, connected: false, running: null, commands: {}, status: null, retry: 500, flow: null, reviewed: null };

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
    if (m.type === 'hello') { state.commands = m.commands; state.running = m.running; setFlow(m.flow); refit(); render(); }
    else if (m.type === 'status') { state.status = m.status; render(); }
    else if (m.type === 'busy') { state.running = m; message(''); render(); }
    else if (m.type === 'flow') { setFlow(m.flow); render(); }
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

const flowOpen = () => state.flow && !['done', 'refused', 'cancelled'].includes(state.flow.step);

// What Start, Stop or Finalise does to an open deploy (flow.py, facility_changed).
function flowNote(action, workload) {
  const f = state.flow;
  if (!flowOpen()) return null;
  if (action === 'finalise' && workload === f.id) return `The open deploy of ${f.id} is cancelled: finalise deletes the secrets and generated files it uses.`;
  if (['review', 'provision'].includes(f.step)) return `The open deploy of ${f.id} goes back to its plan step: plan again once this has finished.`;
  return null;
}

// Resolves true when the user confirms. text: a string or nodes.
async function confirmDialog(title, text, command, ok = 'Run in the terminal') {
  $('confirm-title').textContent = title;
  fill($('confirm-text'), text);
  $('confirm-cmd').textContent = command;
  $('confirm-ok').textContent = ok;
  const d = $('confirm');
  d.returnValue = '';
  d.showModal();
  await new Promise((r) => d.addEventListener('close', r, { once: true }));
  return d.returnValue === 'ok';
}

function runningWorkloads() {
  const items = (state.status && state.status.workloads && state.status.workloads.items) || [];
  return items.filter((w) => w.deployed);
}

const CONFIRM = {
  mount: () => ({ title: 'Mount this distro for the toolkit',
    text: 'Bind-mounts this distro at /mnt/wsl/instances/<its name>, so DMF Toolkit in its own distro can read the facility profile. sudo asks for your password in the terminal. The mount lasts until WSL restarts.' }),
  stop: () => {
    const ws = runningWorkloads();
    return { title: 'Stop the facility',
      text: [ws.length ? `All of these stop with it: ${ws.map((w) => `${w.id} (${w.state})`).join(', ')}.`
        : 'No workload is deployed.',
      ' make stop stops k3s, every pod and the Flow Domains, and releases their memory. Volumes, secrets and allocations stay; Start brings the workloads back. sudo asks for your password in the terminal.',
      flowNote('stop') ? el('div', { class: 'warnbox' }, flowNote('stop')) : null] };
  },
};

async function act(action, workload) {
  const command = commandFor(action, workload);
  const body = { action, workload };
  if (action === 'finalise') {
    if (!(await finaliseDialog(workload, command))) return;
    body.confirm = workload;
  } else if (CONFIRM[action]) {
    const c = CONFIRM[action]();
    if (!(await confirmDialog(c.title, c.text, command))) return;
  }
  await post('/api/action', body);
}

async function post(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const text = await r.text();
  let out = {};
  try { out = JSON.parse(text); } catch (e) { out = { reason: text.trim() }; }
  if (!r.ok) message(out.reason || `Refused (${r.status})`);
  else term.focus();
  if (out.flow !== undefined) { setFlow(out.flow); render(); }
  return r.ok;
}

async function finaliseDialog(id, command) {
  const d = $('finalise');
  $('fin-id').textContent = id;
  $('fin-cmd').textContent = command;
  const note = flowNote('finalise', id);
  $('fin-flow').hidden = !note;
  $('fin-flow').textContent = note || '';
  const input = $('fin-input');
  input.value = '';
  $('fin-ok').disabled = true;
  input.oninput = () => { $('fin-ok').disabled = input.value !== id; };
  d.returnValue = '';
  d.showModal();
  input.focus();
  await new Promise((r) => d.addEventListener('close', r, { once: true }));
  return d.returnValue === 'ok' && input.value === id;
}

// ---- deploy flow ----
const deploy = (design) => post('/api/flow', { op: 'deploy', design });

async function redeploy(run, id) {
  const xml = commandFor('archive-xml').replace('<run>', run);
  const ok = await confirmDialog(`Redeploy ${id} from the archive`,
    [`Imports ${xml}, then plans and asks you to review the plan before anything is provisioned.`,
      el('br'), el('strong', {}, 'Its secrets must be set again: '),
      'finalise deleted them. If the design references any, the console asks for each one, and you type the value in the terminal.'],
    commandFor('import').replace('<source>', xml), 'Start');
  if (ok) await post('/api/flow', { op: 'redeploy', run });
}

const flowOp = (op) => post('/api/flow', { op });

function setFlow(f) {
  state.flow = f || null;
  // Another page (or this one) moved the flow on: a review left open here is stale.
  const d = $('review');
  if (d.open && !(f && f.step === 'review')) { d.returnValue = ''; d.close(); }
  if (f && f.step === 'review' && f.summary && state.reviewed !== f.summary.time) {
    state.reviewed = f.summary.time;
    setTimeout(openReview, 0);
  }
}

const STEPS = [['import', 'Import'], ['secrets', 'Secrets'], ['plan', 'Plan'], ['review', 'Review'], ['provision', 'Provision']];

function renderFlow() {
  const f = state.flow;
  $('flow').hidden = !f;
  if (!f) return;
  const title = { deploy: 'Deploy', update: 'Update', redeploy: 'Redeploy' }[f.kind] || f.kind;
  $('h-flow').textContent = `${title} ${f.id || ''}`.trim();
  const at = STEPS.findIndex(([k]) => k === f.step);
  const ended = f.step === 'done' ? STEPS.length : at;
  const steps = el('ol', { class: 'steps' }, STEPS.map(([k, label], i) => {
    let cls = i < ended ? 'done' : i === at ? 'now' : '';
    if (i === at && f.error) cls = 'bad';
    return el('li', { class: cls }, label);
  }), f.step === 'refused' ? el('li', { class: 'bad' }, 'Refused') : null,
  f.step === 'cancelled' ? el('li', { class: 'bad' }, 'Cancelled') : null,
  f.step === 'done' ? el('li', { class: 'done' }, 'Done') : null);

  const parts = [steps, el('div', { class: 'muted' }, `from ${f.source}`)];
  if (f.note) parts.push(el('div', { class: 'warnbox' }, f.note));
  if (f.running) parts.push(el('div', { class: 'okbox' }, `Running in the terminal: ${f.command}`,
    f.step === 'provision' ? ' (sudo asks for your password there)' : ''));
  else if (f.pending) parts.push(el('div', { class: 'okbox' }, 'Starting the next step…'));
  if (f.message) parts.push(el('div', { class: f.step === 'refused' ? 'badbox' : 'okbox' }, f.message));
  if (f.error) parts.push(el('div', { class: 'badbox' }, f.error));

  const imp = f.import;
  if (imp && imp.problems && imp.problems.length && (f.step === 'refused' || f.step === 'secrets')) {
    parts.push(el('ul', { class: 'reasons' }, imp.problems.map((p) =>
      el('li', {}, `${p.where}: ${p.message} [rule ${p.rule}]`))));
  }
  if (f.step === 'secrets' && f.secrets.length) {
    parts.push(el('div', {}, `Still to set: ${f.secrets.join(', ')}. The value is typed at the terminal's prompt, twice, and isn't shown.`));
  }

  const busy = f.running || f.pending || !state.connected;
  const buttons = [];
  if (!busy) {
    if (f.step === 'secrets' && f.secrets.length) {
      const cmd = `make secret-set WORKLOAD=${f.id} NAME=${f.secrets[0]}`;
      buttons.push(el('button', { class: 'primary', title: cmd, onclick: () => flowOp('run') }, `Set ${f.secrets[0]} in the terminal`));
    } else if (['import', 'plan', 'provision'].includes(f.step)) {
      buttons.push(el('button', { title: f.command || '', onclick: () => flowOp('run') }, `Run ${f.step} again`));
    } else if (f.step === 'review') {
      buttons.push(el('button', { class: 'primary', onclick: openReview }, 'Review the plan'));
      buttons.push(el('button', { title: `make plan WORKLOAD=${f.id}`, onclick: () => flowOp('plan') }, 'Plan again'));
    }
    if (flowOpen()) buttons.push(el('button', { onclick: () => flowOp('cancel') }, 'Cancel'));
    else buttons.push(el('button', { onclick: () => flowOp('close') }, 'Close'));
  }
  parts.push(el('div', { class: 'buttons' }, buttons));
  fill($('flow-body'), parts);
}

function list(title, items, empty) {
  if (!items || !items.length) return el('div', {}, `${title}: `, el('span', { class: 'muted' }, empty));
  return el('details', {}, el('summary', {}, `${title}: ${items.length}`), el('ul', {}, items.map((x) => el('li', {}, x))));
}

function openReview() {
  const f = state.flow;
  if (!f || f.step !== 'review' || !f.summary) return;
  const s = f.summary;
  const c = s.counts || {};
  const cap = s.capacity || {};
  $('rv-title').textContent = `Plan for ${f.id}${s.revision != null ? `, revision ${s.revision}` : ''}`;
  const restarts = s.restarts || [];
  const capText = cap.requested && cap.free
    ? `needs ${cap.requested.cpu} CPU and ${cap.requested.memory_gib} GiB; ${cap.free.cpu} CPU and ${cap.free.memory_gib} GiB free`
    : '';
  const warnings = (f.import && f.import.warnings) || [];
  const alloc = f.import && f.import.allocation;
  fill($('rv-body'),
    el('dl', { class: 'kv' },
      el('dt', {}, 'Changes'), el('dd', { class: 'big' }, `${c.add} to add, ${c.change} to change, ${c.destroy} to destroy`),
      el('dt', {}, 'Restarts'), el('dd', { class: restarts.length ? 'big' : '' },
        restarts.length ? restarts.map((r) => `${r.resource || r.address}: ${r.why}`).join('; ') : 'none: no running Media Function restarts'),
      el('dt', {}, 'Replaced'), el('dd', {}, (s.replaced || []).length ? s.replaced.join(', ') : 'none'),
      el('dt', {}, 'Capacity'), el('dd', {}, cap.fits ? `fits: ${capText}` : `doesn't fit: ${capText}`),
      alloc ? el('dt', {}, 'Allocation') : null,
      alloc ? el('dd', {}, `uid ${alloc.uid}, QUIC ${alloc.quicPort}, SRT ${alloc.srtPort}${alloc.new ? ' (new)' : ''}`) : null),
    list('Created', s.created, 'none'), list('Changed in place', s.changed, 'none'), list('Destroyed', s.destroyed, 'none'),
    warnings.length ? el('div', { class: 'warnbox' }, el('strong', {}, 'Warnings from the import'),
      el('ul', {}, warnings.map((w) => el('li', {}, w)))) : null,
    el('p', { class: 'muted' }, 'Provision applies this saved plan. sudo asks for your password in the terminal.'));
  $('rv-cmd').textContent = `make provision WORKLOAD=${f.id}`;
  const d = $('review');
  if (d.open) return;
  d.returnValue = '';
  d.showModal();
  d.addEventListener('close', () => {
    if (d.returnValue === 'ok') flowOp('provision');
    else if (d.returnValue === 'cancel-flow') flowOp('cancel');
  }, { once: true });
}

document.addEventListener('click', (ev) => {
  const b = ev.target.closest('button.act');
  if (b && !b.disabled) act(b.dataset.action, b.dataset.workload);
});

function actButton(label, action, workload, extraDisabled, why) {
  const cmd = commandFor(action, workload);
  const disabled = !state.connected || !!state.running || !!extraDisabled;
  const reason = extraDisabled ? why : '';
  return el('button', { class: 'act', dataset: { action, workload }, disabled,
    title: reason ? `${reason}\n${cmd}` : cmd }, label);
}

function flowButton(label, onclick, disabled, why, cmd) {
  const locked = flowOpen();
  const off = !state.connected || !!state.running || locked || !!disabled;
  const reason = locked ? 'A deploy is open below: finish or cancel it first.' : disabled ? why : '';
  return el('button', { disabled: off, title: reason ? `${reason}\n${cmd}` : cmd, onclick: off ? null : onclick }, label);
}

// ---- rendering ----
const dot = (s) => el('span', { class: `dot ${s === 'ok' ? 'ok' : s === 'bad' ? 'bad' : ''}`, 'aria-hidden': 'true' });
function health(label, r, okText) {
  r = r || { state: 'unknown', error: 'not checked yet' };
  const text = r.state === 'unknown' ? 'unknown' : (r.text || okText);
  return el('span', { class: 'item', title: r.error || r.url || r.fix || '' }, label, dot(r.state), text,
    r.state !== 'ok' && r.error ? el('span', { class: 'err' }, ` (${r.error})`) : null);
}

function render() {
  const s = state.status;
  $('running').textContent = state.running ? `running: ${state.running.command}` : '';
  renderFlow();
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
  const k3sUp = f.k3s && f.k3s.state === 'ok';
  const kubeUp = f.kubernetes && f.kubernetes.state === 'ok';
  for (const b of document.querySelectorAll('.facility button.act')) {
    const a = b.dataset.action;
    const why = a === 'mount' && m && m.state === 'ok' ? 'Already mounted.'
      : a === 'start' && k3sUp && kubeUp ? 'The facility is running.'
        : a === 'stop' && f.k3s && f.k3s.state === 'bad' ? 'k3s is already stopped.' : '';
    b.disabled = !state.connected || !!state.running || !!why;
    b.title = why ? `${why}\n${commandFor(a)}` : commandFor(a);
  }
  if (!s) return;
  $('updated').textContent = `status ${new Date(s.time).toLocaleTimeString()}`;

  const d = s.designs || {};
  const designs = Object.fromEntries((d.items || []).map((x) => [x.id, x]));

  // Workloads
  const w = s.workloads || {};
  $('wl-note').textContent = w.state === 'unknown' ? `unknown: ${w.error}` : (w.error || '');
  fill($('wl-list'), ...(w.items || []).map((x) => {
    const cls = x.state === 'on air' ? 'onair' : x.state === 'running' ? 'ok' : /failed/.test(x.state) ? 'warn' : '';
    const design = designs[x.id];
    const rev = x.deployed && x.deployed_revision != null && x.deployed_revision !== x.revision
      ? `rev ${x.revision} compiled, rev ${x.deployed_revision} deployed`
      : x.revision != null ? `rev ${x.revision}` : null;
    const updateWhy = x.kind !== 'compiled' ? 'Only a compiled workload is updated from the toolkit.'
      : !design ? 'Not among the toolkit\'s designs.' : design.publishable !== true ? 'The toolkit\'s design doesn\'t publish.' : '';
    return el('li', {},
      el('div', { class: 'head' },
        el('span', { class: 'id' }, x.id),
        el('span', { class: `state ${cls}` }, x.state === 'on air' ? 'ON AIR' : x.state),
        el('span', { class: 'muted' }, x.kind),
        rev ? el('span', {}, rev) : null,
        x.pods ? el('span', { class: 'muted' }, `pods ${x.pods.ready}/${x.pods.total}`) : null,
        x.cpu != null ? el('span', {}, `${x.cpu} CPU`) : null,
        x.deployed_from ? el('span', { class: 'muted' }, `(deployed: ${x.deployed_from})`) : null),
      x.errors && x.errors.length ? el('div', { class: 'note' }, x.errors.join('; ')) : null,
      el('div', { class: 'buttons' },
        actButton('Open UI', 'open-ui', x.id, !x.deployed || x.kind === 'no file', 'Not deployed, or no workload file.'),
        actButton('Status', 'status', x.id, x.kind === 'no file', 'No workload file.'),
        flowButton(x.deployed ? 'Update' : 'Deploy', () => deploy(x.id), updateWhy, updateWhy,
          `make import XML=${commandFor('design-xml').replace('<id>', x.id)}, then plan, review, provision`),
        actButton('Finalise', 'finalise', x.id, !x.deployed || !kubeUp,
          !x.deployed ? 'Not deployed.' : 'Kubernetes doesn\'t answer: finalise needs the facility running.')));
  }));

  // Toolkit designs
  const p = d.profile;
  fill($('ds-profile'), p ? health('Toolkit profile', p) : null,
    p && p.fix ? el('span', { class: 'err' }, p.fix) : null);
  $('ds-note').textContent = d.state === 'ok' ? '' : (d.text || 'toolkit API: unknown') + (d.error ? ` (${d.error})` : '');
  fill($('ds-list'), ...(d.items || []).map((x) => el('li', {},
    el('div', { class: 'head' },
      el('span', { class: 'id' }, x.id),
      x.name && x.name !== x.id ? el('span', { class: 'muted' }, x.name) : null,
      el('span', {}, `rev ${x.revision}`),
      x.publishable === true ? el('span', { class: 'state ok' }, 'publishable')
        : x.publishable === false ? el('span', { class: 'state warn' }, plural(x.reasons.length, 'problem'))
          : el('span', { class: 'state' }, x.error || 'unknown'),
      x.deployed ? el('span', { class: 'muted' }, x.deployed_revision == null ? 'deployed (revision not labelled)'
        : x.newer ? `deployed rev ${x.deployed_revision}: newer revision` : `deployed rev ${x.deployed_revision}`) : null,
      flowButton(x.deployed ? 'Update' : 'Deploy', () => deploy(x.id), x.publishable !== true, 'The design doesn\'t publish.',
        `make import XML=${commandFor('design-xml').replace('<id>', x.id)}, then plan, review, provision`)),
    x.reasons && x.reasons.length ? el('ul', { class: 'reasons' }, ...x.reasons.map((r) => el('li', {}, r))) : null)));

  // Archive
  const a = s.archive || {};
  const deployedIds = new Set(runningWorkloads().map((x) => x.id));
  fill($('ar-list'), ...(a.state === 'unknown' ? [el('li', { class: 'note' }, `unknown: ${a.error}`)] : []),
    ...(a.items || []).map((x) => el('li', {}, el('div', { class: 'head' },
      el('span', { class: 'id' }, x.id), el('span', { class: 'muted' }, x.time),
      x.source_xml ? flowButton('Redeploy', () => redeploy(x.run, x.id), deployedIds.has(x.id),
        `${x.id} is deployed: finalise it first, or update it from the toolkit.`,
        `make import XML=${commandFor('archive-xml').replace('<run>', x.run)}, then plan, review, provision`)
        : el('span', { class: 'muted' }, 'no source.xml')))));
}

render();
connect();
