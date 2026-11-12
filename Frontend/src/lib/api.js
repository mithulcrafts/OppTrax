const SESSION_KEY = 'opptrax_session_token';

export function getApiBaseUrl() {
  if (typeof window !== 'undefined') {
    const urlParam = new URLSearchParams(window.location.search).get('api');
    if (urlParam) {
      try {
        localStorage.setItem('opptrax_custom_api_url', urlParam);
      } catch {
        // ignore storage errors
      }
      return urlParam.replace(/\/+$/, '');
    }
    try {
      const saved = localStorage.getItem('opptrax_custom_api_url');
      if (saved) return saved.replace(/\/+$/, '');
    } catch {
      // ignore
    }
  }

  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL.replace(/\/+$/, '');
  }
  if (typeof window !== 'undefined') {
    const port = window.location.port;
    if (port === '5173' || port === '4173') {
      return 'http://localhost:8000';
    }
    if (window.location.origin.includes('github.io')) {
      return 'https://surely-unused-submarine.ngrok-free.dev';
    }
    return '';
  }
  return 'http://localhost:8000';
}

export function getStoredSessionToken() {
  try {
    return localStorage.getItem(SESSION_KEY) || '';
  } catch {
    return '';
  }
}

export function setStoredSessionToken(token) {
  try {
    if (token) {
      localStorage.setItem(SESSION_KEY, token);
    } else {
      localStorage.removeItem(SESSION_KEY);
    }
  } catch {
    // Ignore storage errors in restricted environments
  }
}

export async function apiRequest(path, options = {}) {
  const base = getApiBaseUrl();
  const token = getStoredSessionToken();
  const headers = { ...(options.headers || {}) };

  if (token && !headers['X-Session-Token']) {
    headers['X-Session-Token'] = token;
  }

  if (base.includes('ngrok') && !headers['ngrok-skip-browser-warning']) {
    headers['ngrok-skip-browser-warning'] = 'true';
  }

  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(`${base}${path}`, {
    ...options,
    headers,
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.message || `Request failed (${response.status})`);
  }
  return data;
}
