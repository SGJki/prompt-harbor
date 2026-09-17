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
