import { state, subscribe, setTab, load, selectCall, loadDetail, setFilters, jumpToSession } from './store.js';
import { startRealtime } from './realtime.js';
import { overviewView, callsView, sessionsView, detailView, filterCalls, callTable } from './views.js';
import { esc, fmtDateTime } from './format.js';

const view = document.querySelector('#view');
const title = document.querySelector('#title');
const meta = document.querySelector('#meta');
const conn = document.querySelector('#conn');
const banner = document.querySelector('#banner');
const refreshBtn = document.querySelector('#refresh');

const TITLES = { overview: 'Overview', calls: 'Calls', sessions: 'Sessions' };
const CONN_LABEL = { live: 'live', poll: 'polling', off: 'offline' };

function renderView() {
  title.textContent = TITLES[state.tab];
  if (state.tab === 'calls') view.innerHTML = callsView(state);
  else if (state.tab === 'sessions') view.innerHTML = sessionsView(state);
  else view.innerHTML = overviewView(state);
  bindRowActions();
  updateDetail();
  updateStatus();
}

function bindRowActions() {
  view.querySelectorAll('.call-row').forEach(row => {
    const activate = () => selectCall(Number(row.dataset.callId));
    row.addEventListener('click', activate);
    row.querySelectorAll('td').forEach(cell => cell.addEventListener('click', event => {
      event.stopPropagation();
      activate();
    }));
  });
}

function updateRows() {
  const box = document.querySelector('#call-rows');
  if (!box) return;
  const shown = filterCalls(state);
  box.innerHTML = callTable(shown, state.selectedCallId);
  bindRowActions();
  const count = document.querySelector('#call-count');
  if (count) count.textContent = `${shown.length} of ${state.calls.length} calls`;
}

function updateDetail() {
  const panel = document.querySelector('#call-detail');
  if (!panel) return;
  panel.innerHTML = detailView(state.detail);
}

function updateStatus() {
  refreshBtn.disabled = state.loading;
  meta.textContent = state.lastUpdated ? `updated ${fmtDateTime(state.lastUpdated.toISOString())}` : '';
  conn.textContent = CONN_LABEL[state.conn] || '';
  conn.className = `conn ${state.conn}`;
  if (state.error) {
    banner.hidden = false;
    banner.innerHTML = `<span>Gateway unreachable: ${esc(state.error)}</span><button type="button" id="retry-load">Retry</button>`;
  } else {
    banner.hidden = true;
    banner.innerHTML = '';
  }
}

subscribe(type => {
  if (type === 'view') renderView();
  else if (type === 'data') {
    if (state.tab === 'calls') updateRows();
    else renderView();
  } else if (type === 'filters') updateRows();
  else if (type === 'detail' || type === 'selection') {
    updateRows();
    updateDetail();
  } else if (type === 'status') updateStatus();
});

document.querySelectorAll('.nav button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.nav button').forEach(x => x.classList.toggle('active', x === b));
  setTab(b.dataset.tab);
}));

refreshBtn.addEventListener('click', () => load());

banner.addEventListener('click', e => {
  if (e.target.id === 'retry-load') load();
});

view.addEventListener('click', e => {
  const sessionBtn = e.target.closest('[data-session-calls]');
  if (sessionBtn) {
    jumpToSession(sessionBtn.dataset.sessionCalls);
    return;
  }
  const retry = e.target.closest('[data-retry]');
  if (retry) loadDetail(Number(retry.dataset.retry));
});

view.addEventListener('change', e => {
  const el = e.target.closest('[data-filter]');
  if (el) setFilters({ [el.dataset.filter]: el.value });
});

let searchTimer = 0;
view.addEventListener('input', e => {
  const el = e.target.closest('[data-filter="q"]');
  if (!el) return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => setFilters({ q: el.value }), 200);
});

renderView();
load();
startRealtime();
