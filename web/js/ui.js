// Utilitários de interface: criação de elementos, ícones, formatação e camadas.

export function h(tag, attrs = {}, ...children) {
  const svgTags = new Set(["svg", "g", "path", "rect", "line", "text", "circle", "polyline", "defs", "marker", "title", "clipPath"]);
  const el = svgTags.has(tag) ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

const PATHS = {
  overview: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  workflows: '<rect x="3" y="4" width="6" height="5" rx="1"/><rect x="15" y="4" width="6" height="5" rx="1"/><rect x="9" y="15" width="6" height="5" rx="1"/><path d="M6 9v2a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V9M12 13v2"/>',
  runs: '<path d="M5 4l14 8-14 8z"/>',
  workers: '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  api: '<path d="M8 6l-6 6 6 6M16 6l6 6-6 6M14 4l-4 16"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5M16 4.8a3.5 3.5 0 0 1 0 6.4M18 14.8c1.8.8 3 2.5 3.5 5.2"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M15 8l2 2"/>',
  logout: '<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10"/>',
  play: '<path d="M7 5l12 7-12 7z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
  retry: '<path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v5h5"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  chevron: '<path d="M8 10l4 4 4-4"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  file: '<path d="M7 3h7l5 5v13H7zM14 3v5h5"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  alert: '<path d="M12 4l9 16H3zM12 10v4M12 17h.01"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4.4 4.2-6.5 8-6.5s7 2.1 8 6.5"/>',
};

export function icon(name, size = 18) {
  const span = document.createElement("span");
  span.style.display = "inline-flex";
  span.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name] || ""}</svg>`;
  return span.firstChild;
}

// Marca do Delco: a grade de execuções (seis casas cheias, três por fazer). viewBox 64 x 64.
export function markSvg(color = "#fff") {
  return (
    `<g fill="${color}"><rect x="2" y="2" width="16" height="16" rx="4"/><rect x="24" y="2" width="16" height="16" rx="4"/>` +
    `<rect x="46" y="2" width="16" height="16" rx="4"/><rect x="2" y="24" width="16" height="16" rx="4"/>` +
    `<rect x="24" y="24" width="16" height="16" rx="4"/><rect x="2" y="46" width="16" height="16" rx="4"/></g>` +
    `<g fill="none" stroke="${color}" stroke-width="3.5"><rect x="47.75" y="25.75" width="12.5" height="12.5" rx="3"/>` +
    `<rect x="25.75" y="47.75" width="12.5" height="12.5" rx="3"/><rect x="47.75" y="47.75" width="12.5" height="12.5" rx="3"/></g>`
  );
}

export function mark(cls = "", color = "#fff") {
  const wrap = document.createElement("span");
  wrap.innerHTML = `<svg class="${cls}" viewBox="0 0 64 64" aria-hidden="true">${markSvg(color)}</svg>`;
  return wrap.firstChild;
}

export function brand() {
  return h("a", { class: "brand", href: "#/" }, h("span", { class: "brand-mark" }, mark()), h("span", { class: "brand-name" }, "Delco"));
}

// ---------------------------------------------------------------- formatação
const INT = new Intl.NumberFormat("pt-BR");
export const int = (n) => INT.format(n || 0);
export const pct = (n) => (n === null || n === undefined ? "—" : `${Math.round(n)}%`);

export function dateTime(iso, withSeconds = false) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", ...(withSeconds ? { second: "2-digit" } : {}),
  });
}
export const time = (iso) => (iso ? new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");

export function duration(ms) {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0).replace(".", ",")} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(Math.floor(s % 60)).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

export function relative(iso) {
  if (!iso) return "—";
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  const fmt = (n, u) => (diff < 0 ? `há ${n} ${u}` : `em ${n} ${u}`);
  if (abs < 45) return diff < 0 ? "agora" : "em instantes";
  if (abs < 3600) return fmt(Math.round(abs / 60), "min");
  if (abs < 86400) return fmt(Math.round(abs / 3600), "h");
  const d = Math.round(abs / 86400);
  return fmt(d, d === 1 ? "dia" : "dias");
}

export const initials = (name) =>
  (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");

export const STATUS = {
  pending: "Pendente",
  queued: "Na fila",
  running: "Rodando",
  retry_wait: "Nova tentativa",
  succeeded: "Sucesso",
  failed: "Falhou",
  skipped: "Pulada",
  cancelled: "Cancelada",
};
export const TRIGGER = { schedule: "Agendada", manual: "Painel", api: "API", cli: "CLI" };
export const ROLE = { admin: "Administrador", operator: "Operador", viewer: "Leitura" };
export const ATTEMPT = {
  running: "Rodando", succeeded: "Sucesso", failed: "Falhou", timed_out: "Tempo esgotado", lost: "Worker caiu", cancelled: "Cancelada",
};

export function pill(status, label) {
  return h("span", { class: `pill ${status}` }, h("span", { class: "dot" }), label || STATUS[status] || status);
}
export const sq = (status, title) => h("span", { class: `sq ${status}`, title: title || STATUS[status] || status });

export function progressBar(parts) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  return h("div", { class: "bar" }, parts.filter((p) => p.value > 0).map((p) => h("span", { class: p.cls, style: { width: `${(100 * p.value) / total}%` } })));
}

// ---------------------------------------------------------------- camadas
let toastHost;
export function toast(message, kind = "ok") {
  toastHost ??= document.body.appendChild(h("div", { class: "toasts", role: "status", "aria-live": "polite" }));
  const el = h("div", { class: `toast ${kind}` }, h("span", { class: "dot" }), message);
  toastHost.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 3500);
}

export function modal(build) {
  const prev = document.activeElement;
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    prev?.focus?.();
  };
  const onKey = (e) => e.key === "Escape" && close();
  const box = h("div", { class: "modal", role: "dialog", "aria-modal": "true" });
  const overlay = h("div", { class: "overlay", onmousedown: (e) => e.target === overlay && close() }, box);
  build(box, close);
  document.body.append(overlay);
  document.addEventListener("keydown", onKey);
  box.querySelector("input, textarea, select, button")?.focus();
  return close;
}

export function confirmDialog({ title, text, confirm = "Confirmar", danger = false }) {
  return new Promise((resolve) => {
    let done = false;
    const closeFn = modal((box, close) => {
      const finish = (v) => { if (!done) { done = true; resolve(v); close(); } };
      box.append(
        h("h2", {}, title),
        h("p", { class: "muted" }, text),
        h("div", { class: "actions" },
          h("button", { class: "btn ghost", onclick: () => finish(false) }, "Voltar"),
          h("button", { class: `btn ${danger ? "danger" : ""}`, onclick: () => finish(true) }, confirm)),
      );
    });
    void closeFn;
  });
}

export function drawer(build) {
  const prev = document.activeElement;
  let onClose = null;
  const close = () => {
    overlay.remove();
    panel.remove();
    document.removeEventListener("keydown", onKey);
    onClose?.();
    prev?.focus?.();
  };
  const onKey = (e) => e.key === "Escape" && close();
  const overlay = h("div", { class: "drawer-overlay", onclick: close });
  const panel = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true" });
  document.body.append(overlay, panel);
  document.addEventListener("keydown", onKey);
  build(panel, close, (fn) => (onClose = fn));
  return close;
}

let tip;
export function tooltip(target, html) {
  target.addEventListener("mouseenter", () => {
    tip ??= document.body.appendChild(h("div", { class: "tooltip", hidden: true }));
    tip.innerHTML = typeof html === "function" ? html() : html;
    tip.hidden = false;
  });
  target.addEventListener("mousemove", (e) => {
    if (!tip) return;
    const w = tip.offsetWidth;
    tip.style.left = `${Math.min(e.clientX + 12, innerWidth - w - 8)}px`;
    tip.style.top = `${e.clientY + 14}px`;
  });
  target.addEventListener("mouseleave", () => tip && (tip.hidden = true));
}

export function copyButton(text, label = "Copiar") {
  return h("button", {
    class: "btn ghost sm",
    onclick: async () => {
      try {
        await navigator.clipboard.writeText(text);
        toast("Copiado");
      } catch {
        toast("Não foi possível copiar", "error");
      }
    },
  }, icon("copy", 14), label);
}

export function debounce(fn, ms = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export function empty(title, text) {
  return h("div", { class: "empty" }, h("strong", {}, title), text ? h("p", {}, text) : null);
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
