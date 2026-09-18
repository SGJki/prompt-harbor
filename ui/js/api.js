export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function getJSON(path, { signal, timeout = 10000 } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  if (signal) {
    if (signal.aborted) ctl.abort();
    else signal.addEventListener('abort', () => ctl.abort(), { once: true });
  }
  try {
    const res = await fetch(path, { signal: ctl.signal });
    if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
    return await res.json();
  } catch (err) {
    if (err.name === 'AbortError' || err instanceof ApiError) throw err;
    throw new ApiError(err.message || 'network error');
  } finally {
    clearTimeout(timer);
  }
}

export async function putJSON(path, body, { signal, timeout = 10000 } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  if (signal) {
    if (signal.aborted) ctl.abort();
    else signal.addEventListener('abort', () => ctl.abort(), { once: true });
  }
  try {
    const res = await fetch(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-Prompt-Harbor-Request': '1' },
      body: JSON.stringify(body),
      signal: ctl.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(data?.error?.message || `HTTP ${res.status}`, res.status);
    return data;
  } catch (err) {
    if (err.name === 'AbortError' || err instanceof ApiError) throw err;
    throw new ApiError(err.message || 'network error');
  } finally {
    clearTimeout(timer);
  }
}
