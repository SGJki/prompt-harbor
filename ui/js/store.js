import { getJSON } from './api.js';

const emptyDetail = () => ({ callId: null, loading: false, error: null, data: null });

export const state = {
  tab: 'overview',
  calls: [],
  sessions: [],
  loading: false,
  error: null,
  lastUpdated: null,
  selectedCallId: null,
  detail: emptyDetail(),
  filters: { status: 'all', session: 'all', q: '' },
  conn: 'poll',
  securityWarnings: [],
};

const listeners = new Set();
export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
function emit(type) {
  for (const fn of listeners) fn(type, state);
}
function set(patch) {
  Object.assign(state, patch);
}

let loadCtl = null;
let loadSeq = 0;
let pendingReload = false;

export function setTab(tab) {
  if (tab === state.tab) return;
  loadCtl?.abort();
  detailCtl?.abort();
  set({ tab, selectedCallId: null, detail: emptyDetail(), error: null });
  emit('view');
  load();
}

export async function load() {
  if (state.loading) {
    pendingReload = true;
    return;
  }
  loadCtl?.abort();
  const ctl = new AbortController();
  loadCtl = ctl;
  const seq = ++loadSeq;
  const tab = state.tab;
  const endpoint = tab === 'sessions' ? '/api/sessions' : '/api/overview';
  set({ loading: true, error: null });
  emit('status');
  try {
    const data = await getJSON(endpoint, { signal: ctl.signal });
    if (seq !== loadSeq) return;
    if (tab === 'sessions') set({ sessions: data.sessions || [] });
    else set({ calls: data.calls || [], sessions: data.sessions || [], securityWarnings: data.security_warnings || [] });
    set({ lastUpdated: new Date() });
    emit('data');
  } catch (err) {
    if (err.name === 'AbortError' || seq !== loadSeq) return;
    set({ error: err.message || 'request failed' });
    emit('status');
  } finally {
    if (seq !== loadSeq) return;
    set({ loading: false });
    emit('status');
    if (pendingReload) {
      pendingReload = false;
      load();
    }
  }
}

let detailCtl = null;
let detailSeq = 0;

export function selectCall(id) {
  detailCtl?.abort();
  set({ selectedCallId: id });
  emit('selection');
  loadDetail(id);
}

export async function loadDetail(id) {
  detailCtl?.abort();
  const ctl = new AbortController();
  detailCtl = ctl;
  const seq = ++detailSeq;
  set({ detail: { callId: id, loading: true, error: null, data: null } });
  emit('detail');
  try {
    const data = await getJSON('/api/calls/' + encodeURIComponent(id), { signal: ctl.signal });
    if (seq !== detailSeq) return;
    set({ detail: { callId: id, loading: false, error: null, data } });
    emit('detail');
  } catch (err) {
    if (err.name === 'AbortError' || seq !== detailSeq) return;
    set({ detail: { callId: id, loading: false, error: err.message || 'request failed', data: null } });
    emit('detail');
  }
}

export function setFilters(patch) {
  set({ filters: { ...state.filters, ...patch } });
  emit('filters');
}

export function jumpToSession(sessionId) {
  const target = String(sessionId);
  if (state.tab === 'calls') {
    setFilters({ session: target });
    return;
  }
  loadCtl?.abort();
  detailCtl?.abort();
  set({
    tab: 'calls',
    filters: { ...state.filters, session: target },
    selectedCallId: null,
    detail: emptyDetail(),
    error: null,
  });
  emit('view');
  load();
}

export function setConn(conn) {
  if (conn === state.conn) return;
  set({ conn });
  emit('status');
}
