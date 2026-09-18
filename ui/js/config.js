import { esc } from './format.js';

const CONFIG_LABELS = {
  database: 'Database path', listen: 'Listen address', upstream: 'Upstream URL', max_body: 'Body capture limit (bytes)',
  retention_days: 'Retention (days)', db_timeout: 'Database timeout (seconds)', upstream_timeout: 'Upstream timeout (seconds)',
  sidecar_timeout: 'Sidecar request timeout (seconds)', sidecar_start_timeout: 'Sidecar start timeout (seconds)',
  sidecar_stop_timeout: 'Sidecar stop timeout (seconds)', api_call_limit: 'API call display limit', cli_call_limit: 'CLI call display limit',
  sse_keepalive: 'SSE keepalive (seconds)', purge_interval: 'Purge interval (seconds)', ui_path: 'Custom UI entry path',
  sidecar_url: 'Sidecar URL', sidecar_command: 'Sidecar command', sidecar_token: 'Sidecar token',
};

function configField(name, field) {
  const label = CONFIG_LABELS[name] || name;
  const value = field.secret ? '' : field.value ?? '';
  const integer = ['max_body', 'retention_days', 'api_call_limit', 'cli_call_limit'].includes(name);
  const decimal = ['db_timeout', 'upstream_timeout', 'sidecar_timeout', 'sidecar_start_timeout', 'sidecar_stop_timeout', 'sse_keepalive', 'purge_interval'].includes(name);
  const type = field.secret ? 'password' : integer || decimal ? 'number' : 'text';
  const step = decimal ? ' step="any"' : '';
  const locked = field.source === 'cli' || field.source === 'env';
  const source = `<span class="config-badge source-${esc(field.source || 'default')}">${esc(field.source || 'default')}</span>`;
  const restart = field.restart_required ? '<span class="config-badge">restart</span>' : '<span class="config-badge live">live</span>';
  const secretHelp = field.secret && field.configured ? '<small class="muted">configured; leave blank to keep</small>' : '';
  const clear = field.secret && field.configured && !locked ? `<label class="config-clear"><input type="checkbox" data-config-clear="${esc(name)}"> clear</label>` : '';
  return `<label class="config-field ${locked ? 'is-locked' : ''}"><span>${esc(label)} ${source}${restart}</span><input data-config-field="${esc(name)}" type="${type}"${step} value="${esc(value)}" placeholder="${field.secret ? 'optional' : ''}"${locked ? ' disabled aria-disabled="true"' : ''}>${locked ? '<small class="muted">locked by startup override</small>' : secretHelp}${clear}</label>`;
}

export function configView(state) {
  const fields = state.config.fields || {};
  const names = Object.keys(fields);
  if (!names.length) return `<div class="panel"><p class="muted">${esc(state.configError || 'Loading configuration…')}</p></div>`;
  const gateway = names.filter(name => fields[name].section === 'gateway').map(name => configField(name, fields[name])).join('');
  const sidecar = names.filter(name => fields[name].section === 'sidecar').map(name => configField(name, fields[name])).join('');
  const restart = (state.configNotice || []).length ? `<div class="notice">Saved. Restart required for: ${state.configNotice.map(esc).join(', ')}</div>` : '';
  const error = state.configError ? `<div class="form-error">${esc(state.configError)}</div>` : '';
  return `<form class="config-form" id="config-form">
${restart}${error}<div class="panel"><div class="panel-heading"><div><h3>Gateway</h3><p class="muted">${esc(state.config.path || 'prompt-harbor.ini')}</p></div></div><div class="config-grid">${gateway}</div></div>
<div class="panel"><div class="panel-heading"><h3>Sidecar</h3></div><div class="config-grid">${sidecar}</div></div>
<div class="config-actions"><button type="submit"${state.configSaving ? ' disabled' : ''}>${state.configSaving ? 'Saving…' : 'Save changes'}</button></div>
</form>`;
}
