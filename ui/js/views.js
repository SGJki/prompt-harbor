import { esc, fmtDateTime, fmtDuration, fmtBytes, tryPrettyJSON } from './format.js';

export function statusPill(c) {
  const code = c.status_code;
  let cls = '';
  if (code !== null && code !== undefined) cls = code >= 200 && code < 400 ? 'ok' : 'err';
  return `<span class="pill ${cls}">${esc(c.status || code || '—')}</span>`;
}

function sessionLabel(s) {
  return `#${s.id} ${s.agent || ''}${s.project_name ? ' · ' + s.project_name : ''}`.trim();
}

export function filterCalls(state) {
  const { status, session, q } = state.filters;
  const needle = q.trim().toLowerCase();
  return state.calls.filter(c => {
    if (status !== 'all' && c.status !== status) return false;
    if (session !== 'all' && String(c.session_id) !== session) return false;
    if (needle && !`${c.model ?? ''} ${c.endpoint ?? ''}`.toLowerCase().includes(needle)) return false;
    return true;
  });
}

export function callTable(calls, selectedId) {
  if (!calls.length) return '<div class="empty">No calls recorded yet.</div>';
  const rows = calls.map(c => `<tr class="call-row ${c.id === selectedId ? 'selected' : ''}" data-call-id="${esc(c.id)}" tabindex="0" role="button" aria-label="Open call ${esc(c.id)}">
<td>${esc(c.id)}</td>
<td title="${esc(c.created_at)}">${esc(fmtDateTime(c.created_at))}</td>
<td>${esc(c.session_id)}</td>
<td>${esc(c.endpoint)}</td>
<td>${esc(c.model)}</td>
<td>${statusPill(c)}</td>
<td>${esc(fmtDuration(c.duration_ms))}</td>
</tr>`);
  return `<table><tr><th>ID</th><th>Time</th><th>Session</th><th>Endpoint</th><th>Model</th><th>Status</th><th>Duration</th></tr>${rows.join('')}</table>`;
}

export function overviewView(state) {
  const calls = state.calls;
  const sessions = state.sessions;
  const ok = calls.filter(c => c.status_code >= 200 && c.status_code < 400).length;
  const rate = calls.length ? Math.round((ok / calls.length) * 100) : 0;
  const warnings = (state.securityWarnings || []).map(warning => `<div class="warn">${esc(warning)}</div>`).join('');
  return `${warnings ? `<div class="panel security-warning"><h3>Security warning</h3>${warnings}</div>` : ''}
<div class="cards">
<div class="card">Total calls<b>${calls.length}</b></div>
<div class="card">Success rate<b>${rate}%</b></div>
<div class="card">Sessions<b>${sessions.length}</b></div>
</div>
<div class="panel"><h3>Recent calls</h3>${callTable(calls.slice(0, 20), state.selectedCallId)}</div>`;
}

export function callsView(state) {
  const f = state.filters;
  const sessionOpts = [`<option value="all" ${f.session === 'all' ? 'selected' : ''}>All sessions</option>`]
    .concat(state.sessions.map(s => `<option value="${esc(s.id)}" ${String(s.id) === f.session ? 'selected' : ''}>${esc(sessionLabel(s))}</option>`))
    .join('');
  const statusOpts = ['all', 'succeeded', 'failed', 'running']
    .map(s => `<option value="${s}" ${f.status === s ? 'selected' : ''}>${s[0].toUpperCase() + s.slice(1)}</option>`)
    .join('');
  const shown = filterCalls(state);
  return `<div class="panel">
<div class="filters">
<select data-filter="status">${statusOpts}</select>
<select data-filter="session">${sessionOpts}</select>
<input data-filter="q" type="search" placeholder="Filter by model or endpoint…" value="${esc(f.q)}">
</div>
<div id="call-rows">${callTable(shown, state.selectedCallId)}</div>
<div class="call-count" id="call-count">${shown.length} of ${state.calls.length} calls</div>
</div>
<div class="panel" id="call-detail"><p class="muted">Select a call to inspect its request and response.</p></div>`;
}

export function sessionsView(state) {
  if (!state.sessions.length) return '<div class="panel"><h3>Sessions</h3><div class="empty">No sessions recorded yet.</div></div>';
  const rows = state.sessions.map(s => `<tr>
<td>${esc(s.id)}</td>
<td>${esc(s.agent)}</td>
<td>${esc(s.project_name)}</td>
<td>${esc(s.cwd)}</td>
<td title="${esc(s.started_at)}">${esc(fmtDateTime(s.started_at))}</td>
<td title="${esc(s.last_seen_at)}">${esc(fmtDateTime(s.last_seen_at))}</td>
<td>${esc(s.call_count ?? '—')}</td>
<td><button class="link" data-session-calls="${esc(s.id)}">calls</button></td>
</tr>`);
  return `<div class="panel"><h3>Sessions</h3><table><tr><th>ID</th><th>Agent</th><th>Project</th><th>Working directory</th><th>Started</th><th>Last seen</th><th>Calls</th><th></th></tr>${rows.join('')}</table></div>`;
}

function prettySSE(text) {
  if (!/^data:/m.test(text)) return null;
  return text.split('\n').map(line => {
    if (!line.startsWith('data:')) return line;
    const payload = line.slice(5).trim();
    if (!payload || payload === '[DONE]') return line;
    const pretty = tryPrettyJSON(payload);
    return pretty === null ? line : 'data: ' + pretty;
  }).join('\n');
}

function bodyBlock(text) {
  if (!text) return '<pre class="muted">—</pre>';
  const sse = prettySSE(text);
  if (sse !== null) return `<pre>${esc(sse)}</pre>`;
  return `<pre>${esc(tryPrettyJSON(text) ?? text)}</pre>`;
}

export function detailView(detail) {
  if (detail.loading) return '<p class="muted">Loading…</p>';
  if (detail.error) return `<p class="muted">Unable to load call details (${esc(detail.error)}). <button class="link" data-retry="${esc(detail.callId)}">Retry</button></p>`;
  if (!detail.data) return '<p class="muted">Select a call to inspect its request and response.</p>';
  const c = detail.data;
  const usage = c.input_tokens !== null || c.output_tokens !== null || c.total_tokens !== null
    ? `<h4>Usage</h4><pre>${esc(JSON.stringify({ input_tokens: c.input_tokens, output_tokens: c.output_tokens, total_tokens: c.total_tokens }, null, 2))}</pre>`
    : '';
  const error = c.error_message ? `<h4>Error</h4><pre>${esc(c.error_type)}: ${esc(c.error_message)}</pre>` : '';
  const requestTruncated = c.request_truncated ? 'yes' : 'no';
  const responseTruncated = c.response_truncated ? 'yes' : 'no';
  const truncated = c.request_truncated || c.response_truncated
    ? `<p class="warn">Body truncated (request=${requestTruncated}, response=${responseTruncated})</p>`
    : '';
  return `<h3>Call ${esc(c.id)}</h3>
<p><b>${esc(c.endpoint)}</b> · ${statusPill(c)} (${esc(c.status_code ?? '—')}) · ${esc(fmtDuration(c.duration_ms))} · in ${esc(fmtBytes(c.input_bytes))} / out ${esc(fmtBytes(c.output_bytes))}${c.stream ? ' · stream' : ''}</p>
${truncated}
<h4>Request headers</h4><pre>${esc(JSON.stringify(c.request_headers_json || {}, null, 2))}</pre>
<h4>Request</h4>${bodyBlock(c.request_body)}
<h4>Response headers</h4><pre>${esc(JSON.stringify(c.response_headers_json || {}, null, 2))}</pre>
<h4>Response</h4>${bodyBlock(c.response_body)}
${usage}${error}`;
}
