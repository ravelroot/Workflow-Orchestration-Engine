// Cliente HTTP + WebSocket. O token de sessão fica no sessionStorage: some ao fechar a aba.

const KEY = "delco.session";
let session = null;
try {
  session = JSON.parse(sessionStorage.getItem(KEY) || "null");
} catch {
  session = null;
}

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `Erro ${status}`);
    this.status = status;
    this.code = body?.code;
    this.fields = body?.fields || {};
  }
}

const listeners = new Set();
export const onAuthChange = (fn) => listeners.add(fn);

export function getSession() {
  return session;
}

function setSession(value) {
  session = value;
  try {
    if (value) sessionStorage.setItem(KEY, JSON.stringify(value));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* navegação privada sem storage: segue só em memória */
  }
  listeners.forEach((fn) => fn(value));
}

export async function api(method, path, body) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (session?.token) headers.Authorization = `Bearer ${session.token}`;
  let res;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, { message: "Sem conexão com o servidor." });
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (res.status === 401 && session) {
    setSession(null);
    live.stop();
  }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

export const get = (p) => api("GET", p);
export const post = (p, b) => api("POST", p, b ?? {});

export async function login(email, password) {
  const data = await api("POST", "/auth/login", { email, password });
  setSession({ token: data.token, user: data.user, expires_at: data.expires_at });
  return data.user;
}

export async function logout() {
  try {
    await api("POST", "/auth/logout");
  } catch {
    /* sessão já inválida */
  }
  live.stop();
  setSession(null);
}

export const can = (role) => {
  const level = { viewer: 0, operator: 1, admin: 2 };
  return session && level[session.user.role] >= level[role];
};

// --------------------------------------------------------------- tempo real
export const live = {
  ws: null,
  handlers: new Set(),
  statusHandlers: new Set(),
  connected: false,
  retry: 0,
  wanted: false,
  logAttempt: null,

  start() {
    this.wanted = true;
    if (this.ws || !session) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/ws?token=${encodeURIComponent(session.token)}`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.setConnected(true);
      if (this.logAttempt) ws.send(JSON.stringify({ action: "logs", attempt_id: this.logAttempt }));
    };
    ws.onmessage = (e) => {
      let event;
      try {
        event = JSON.parse(e.data);
      } catch {
        return;
      }
      this.handlers.forEach((fn) => fn(event));
    };
    ws.onclose = () => {
      this.ws = null;
      this.setConnected(false);
      if (!this.wanted || !session) return;
      const wait = Math.min(15000, 800 * 2 ** this.retry++);
      setTimeout(() => this.start(), wait);
    };
  },
  stop() {
    this.wanted = false;
    this.ws?.close();
    this.ws = null;
  },
  setConnected(v) {
    this.connected = v;
    this.statusHandlers.forEach((fn) => fn(v));
  },
  on(fn) {
    this.handlers.add(fn);
    return () => this.handlers.delete(fn);
  },
  onStatus(fn) {
    this.statusHandlers.add(fn);
    fn(this.connected);
    return () => this.statusHandlers.delete(fn);
  },
  followLogs(attemptId) {
    this.logAttempt = attemptId;
    if (this.ws?.readyState === 1) {
      this.ws.send(JSON.stringify(attemptId ? { action: "logs", attempt_id: attemptId } : { action: "unlogs" }));
    }
  },
};

setInterval(() => live.ws?.readyState === 1 && live.ws.send('{"action":"ping"}'), 25000);
