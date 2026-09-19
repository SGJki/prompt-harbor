import { load, setConn } from './store.js';

const INVALIDATE_DEBOUNCE_MS = 300;
const POLL_INTERVAL_MS = 15000;

let es = null;
let esLive = false;
let invalidateTimer = 0;

function debouncedRefresh() {
  clearTimeout(invalidateTimer);
  invalidateTimer = setTimeout(() => load(), INVALIDATE_DEBOUNCE_MS);
}

function connect() {
  es = new EventSource('/api/events');
  es.addEventListener('ready', () => {
    esLive = true;
    setConn('live');
    load();
  });
  es.addEventListener('invalidate', debouncedRefresh);
  es.onerror = () => {
    esLive = false;
    setConn('poll');
  };
}

export function startRealtime() {
  connect();
  setInterval(() => {
    if (document.hidden || esLive) return;
    load();
  }, POLL_INTERVAL_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !esLive) load();
  });
}
