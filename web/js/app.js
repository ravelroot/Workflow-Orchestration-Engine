import { ApiError, can, get, getSession, live, login, logout, onAuthChange, onPrefsChange, post, prefs, refreshMe, savePrefs } from "./api.js";
import { brand, clear, dateTime, h, icon, initials, mark, modal, ROLE, setDisplayZone, toast } from "./ui.js";
import { insightsPage } from "./pages/insights.js";
import { integrationsPage } from "./pages/integrations.js";
import { openPalette, openShortcuts, installShortcuts } from "./palette.js";
import { overviewPage } from "./pages/overview.js";
import { workflowPage, workflowsPage } from "./pages/workflows.js";
import { runPage, runsPage } from "./pages/runs.js";
import { workersPage } from "./pages/workers.js";
import { accountPage, usersPage } from "./pages/account.js";
import { systemPage } from "./pages/system.js";

const app = document.getElementById("app");
let cleanup = [];

const THEME_KEY = "delco.theme";
const PALETTE_KEY = "delco.palette";
function savedPalette() {
  try {
    return localStorage.getItem(PALETTE_KEY) || "0";
  } catch {
    return "0";
  }
}
export function applyPalette(id) {
  const value = ["1", "2", "3", "4"].includes(String(id)) ? String(id) : "0";
  if (value === "0") delete document.documentElement.dataset.palette;
  else document.documentElement.dataset.palette = value;
  try {
    localStorage.setItem(PALETTE_KEY, value);
  } catch {
    return value;
  }
  return value;
}
function savedTheme() {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch {
    return null;
  }
}
function storeTheme(value) {
  try {
    if (value && value !== "system") localStorage.setItem(THEME_KEY, value);
    else localStorage.removeItem(THEME_KEY);
  } catch {
    return;
  }
}
function applyTheme(theme) {
  const choice = theme && theme !== "system" ? theme : null;
  const t = choice || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = t;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", t === "dark" ? "#191d20" : "#f7f3f1");
  return t;
}
function applyPrefs(p) {
  const theme = getSession() ? p.theme : savedTheme();
  storeTheme(theme);
  applyTheme(theme);
  applyPalette(getSession() ? p.palette : savedPalette());
  document.documentElement.dataset.density = p.density === "compact" ? "compact" : "comfortable";
  setDisplayZone(p.time_zone);
}
applyPrefs(getSession() ? prefs() : { theme: savedTheme(), palette: savedPalette() });
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => applyTheme(getSession() ? prefs().theme : savedTheme()));
onPrefsChange((p) => applyPrefs(p));

export async function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  storeTheme(next);
  applyTheme(next);
  if (getSession()) {
    try {
      await savePrefs({ theme: next });
    } catch {
      applyTheme(next);
    }
  }
  route();
}

const mine = new Set();
export function rememberRun(id) {
  mine.add(id);
}
function notifyRun(e) {
  if (e.type !== "run" || !["succeeded", "failed", "cancelled"].includes(e.status)) return;
  const p = prefs();
  const isMine = mine.has(e.id);
  const wanted = (e.status === "failed" && p.notify_failures) || (isMine && p.notify_mine);
  if (!wanted) return;
  if (isMine) mine.delete(e.id);
  const word = { succeeded: "terminou com sucesso", failed: "falhou", cancelled: "foi cancelada" }[e.status];
  const title = `Execução #${e.id} ${word}`;
  const body = e.workflow || "Delco";
  if (document.visibilityState === "visible" || !("Notification" in window) || Notification.permission !== "granted") {
    toast(`${title}${e.workflow ? ` · ${e.workflow}` : ""}`, e.status === "failed" ? "error" : "ok");
    return;
  }
  const n = new Notification(title, { body, icon: "img/favicon.svg", tag: `delco-run-${e.id}` });
  n.onclick = () => {
    window.focus();
    navigate(`/runs/${e.id}`);
    n.close();
  };
}
live.on(notifyRun);

let health = { scheduler: true };
async function checkHealth() {
  if (!getSession()) return;
  try {
    const res = await fetch("/api/health");
    const next = await res.json();
    const changed = next.scheduler !== health.scheduler;
    health = next;
    if (changed) renderBanner();
  } catch {
    return;
  }
}
setInterval(checkHealth, 30000);
function renderBanner() {
  const slot = document.querySelector(".global-banner");
  if (!slot) return;
  clear(slot, health.scheduler === false
    ? h("div", { class: "notice warn banner" }, icon("alert", 16), h("div", { class: "small" },
      h("strong", {}, "Nenhum agendador ativo. "), "Execuções novas não começam e horários agendados não disparam. Suba com ",
      h("code", {}, "delco scheduler"), " ou ", h("code", {}, "docker compose up -d scheduler"), "."),
      can("admin") ? h("a", { class: "btn ghost sm", href: "#/system" }, "Ver sistema") : null)
    : null);
}

const ROUTES = [
  { re: /^\/$/, page: overviewPage, nav: "overview" },
  { re: /^\/workflows$/, page: workflowsPage, nav: "workflows" },
  { re: /^\/workflows\/([a-z0-9_]+)$/, page: workflowPage, nav: "workflows" },
  { re: /^\/runs$/, page: runsPage, nav: "runs" },
  { re: /^\/runs\/(\d+)$/, page: runPage, nav: "runs" },
  { re: /^\/workers$/, page: workersPage, nav: "workers" },
  { re: /^\/insights$/, page: insightsPage, nav: "insights" },
  { re: /^\/integrations$/, page: integrationsPage, nav: "integrations", role: "operator" },
  { re: /^\/account$/, page: accountPage, nav: "account" },
  { re: /^\/users$/, page: usersPage, nav: "users", role: "admin" },
  { re: /^\/system$/, page: systemPage, nav: "system", role: "admin" },
];

export function navigate(path) {
  location.hash = `#${path}`;
}

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  return { path, query: new URLSearchParams(query) };
}

function route() {
  cleanup.forEach((fn) => fn());
  cleanup = [];
  document.querySelectorAll(".overlay, .menu, .drawer, .drawer-overlay").forEach((el) => el.remove());
  document.querySelectorAll(".tooltip").forEach((el) => (el.hidden = true));
  live.followLogs(null);
  if (!getSession()) {
    renderLogin();
    return;
  }
  live.start();
  const { path, query } = parseHash();
  const match = ROUTES.map((r) => ({ r, m: path.match(r.re) })).find((x) => x.m);
  const main = h("main", { class: "main", id: "conteudo", tabindex: "-1" });
  const wrap = h("div", { class: "main-wrap" }, h("div", { class: "global-banner" }), main);
  clear(app, h("div", { class: "shell" }, sidebar(match?.r.nav), wrap));
  renderBanner();
  if (!match || (match.r.role && !can(match.r.role))) {
    main.append(h("div", { class: "page" }, h("div", { class: "empty" }, h("strong", {}, "Página não encontrada"),
      h("a", { class: "btn ghost", href: "#/" }, "Voltar para a visão geral"))));
    return;
  }
  const ctx = {
    params: match.m.slice(1),
    query,
    onCleanup: (fn) => cleanup.push(fn),
    live: (fn) => cleanup.push(live.on(fn)),
  };
  match.r.page(main, ctx);
  main.focus?.();
}

function sidebar(active) {
  const user = getSession().user;
  const link = (key, href, label, ic, keys) =>
    h("a", { href, class: active === key ? "active" : "", "aria-current": active === key ? "page" : null, title: label },
      icon(ic), h("span", { class: "txt" }, label), keys ? h("span", { class: "kbd-hint", "aria-hidden": "true" }, keys) : null);
  const liveEl = h("span", { class: "live" }, h("span", { class: "dot" }), "Conectando…");
  cleanup.push(live.onStatus((on) => {
    liveEl.classList.toggle("on", on);
    liveEl.lastChild.textContent = on ? "Ao vivo" : "Reconectando…";
  }));
  const chip = h("button", { class: "user-chip", "aria-haspopup": "menu", onclick: (e) => openUserMenu(e.currentTarget) },
    h("span", { class: "avatar" }, initials(user.name)),
    h("span", { class: "who" }, h("span", { class: "small strong ellipsis" }, user.name), h("span", { class: "tiny muted" }, ROLE[user.role])),
    icon("chevron", 16));
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  return h("aside", { class: "sidebar" },
    brand(),
    h("div", { class: "sidebar-tag" }, "Orquestração de workflows"),
    h("button", { class: "palette-trigger", onclick: () => openPalette() }, icon("search", 15), "Buscar ou ir para…", h("kbd", {}, mac ? "⌘K" : "Ctrl K")),
    h("nav", { class: "nav", "aria-label": "Principal" },
      link("overview", "#/", "Visão geral", "overview", "g v"),
      link("workflows", "#/workflows", "Workflows", "workflows", "g w"),
      link("runs", "#/runs", "Execuções", "runs", "g e"),
      link("workers", "#/workers", "Workers", "workers", "g k"),
      link("insights", "#/insights", "Desempenho", "insights", "g d")),
    h("div", { class: "nav-label" }, "Conexões"),
    h("nav", { class: "nav", "aria-label": "Conexões" },
      can("operator") ? link("integrations", "#/integrations", "Integrações", "plug", "g i") : null,
      can("admin") ? link("system", "#/system", "Sistema", "system", "g s") : null,
      h("a", { href: "/api/docs", target: "_blank", rel: "noopener" }, icon("api"), h("span", { class: "txt" }, "Documentação da API"), icon("external", 13))),
    h("div", { class: "sidebar-foot" },
      liveEl,
      h("button", { class: "btn ghost sm", onclick: toggleTheme }, icon(document.documentElement.dataset.theme === "dark" ? "sun" : "moon", 15),
        document.documentElement.dataset.theme === "dark" ? "Tema claro" : "Tema escuro"),
      chip));
}

function openUserMenu(anchor) {
  document.querySelector(".menu")?.remove();
  const r = anchor.getBoundingClientRect();
  const menu = h("div", { class: "menu", role: "menu", style: { left: `${r.left}px`, bottom: `${innerHeight - r.top + 6}px`, width: `${Math.max(220, r.width)}px` } },
    h("a", { href: "#/account", role: "menuitem" }, icon("key", 16), "Minha conta e tokens"),
    h("a", { href: "#/account?tab=prefs", role: "menuitem" }, icon("sliders", 16), "Preferências"),
    h("button", { role: "menuitem", onclick: () => openShortcuts() }, icon("keyboard", 16), "Atalhos de teclado"),
    can("admin") ? h("a", { href: "#/users", role: "menuitem" }, icon("users", 16), "Usuários e auditoria") : null,
    h("hr"),
    h("button", { role: "menuitem", onclick: async () => { await logout(); toast("Sessão encerrada"); } }, icon("logout", 16), "Sair"));
  if (innerWidth <= 900) Object.assign(menu.style, { top: `${r.bottom + 6}px`, bottom: "auto", left: "auto", right: "10px" });
  document.body.append(menu);
  const close = (e) => {
    if (!menu.contains(e.target) && e.target !== anchor) {
      menu.remove();
      document.removeEventListener("mousedown", close);
    }
  };
  setTimeout(() => document.addEventListener("mousedown", close));
  menu.addEventListener("click", () => menu.remove());
}

const DEMO = [
  ["admin@delco.dev", "Helena Prado", "Administrador"],
  ["operador@delco.dev", "Tiago Moura", "Operador"],
  ["leitura@delco.dev", "Bruna Sales", "Leitura"],
];

function renderLogin() {
  live.stop();
  const email = h("input", { class: "input", id: "email", type: "email", autocomplete: "username", required: true });
  const password = h("input", { class: "input", id: "senha", type: "password", autocomplete: "current-password", required: true });
  const err = h("p", { class: "field-error", role: "alert" });
  const submit = h("button", { class: "btn block", type: "submit", style: { height: "40px" } }, "Entrar");
  const form = h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = "";
    submit.disabled = true;
    try {
      await login(email.value, password.value);
      applyPrefs(prefs());
      checkHealth();
      route();
    } catch (ex) {
      err.textContent = ex instanceof ApiError ? ex.message : "Não foi possível entrar.";
    } finally {
      submit.disabled = false;
    }
  } },
  h("div", { class: "field" }, h("label", { for: "email" }, "E-mail"), email),
  h("div", { class: "field" }, h("label", { for: "senha" }, "Senha"), password),
  err, submit);
  const stops = ["ok", "ok", "retry", "run", "", "", "", ""];
  const track = h("div", { class: "signal-track", "aria-hidden": "true" },
    h("div", { class: "track-row" }, stops.map((st, i) => [
      i ? h("span", { class: `rail${stops[i - 1] === "ok" || stops[i - 1] === "retry" ? " done" : ""}` }) : null,
      h("span", { class: `stop ${st}` })])),
    h("div", { class: "track-caption" }, h("span", {}, "passo_20"), h("span", {}, "passo_23 · retomando"), h("span", {}, "passo_27")));
  clear(app, h("div", { class: "login" },
    h("section", { class: "login-art" },
      mark("big-mark"),
      h("div", { class: "stack lg" },
        h("h1", {}, "Workflows que retomam de onde pararam."),
        h("p", {}, "Agende pipelines escritos em Python, distribua as tarefas entre workers e acompanhe cada passo em tempo real. Se um servidor cair no passo 23 de 50, o Delco continua do passo 23."),
        track),
      h("div", { class: "foot" }, h("span", {}, "MySQL"), h("span", {}, "Redis Streams"), h("span", {}, "WebSockets"), h("span", {}, "Slack, Discord, Teams e webhooks"))),
    h("section", { class: "login-panel" },
      h("div", { class: "login-box" },
        h("div", { class: "brand" }, h("span", { class: "brand-mark" }, mark()), h("span", { class: "brand-name" }, "Delco")),
        h("div", { class: "stack sm" }, h("h2", {}, "Entrar"), h("p", { class: "muted small" }, "Use sua conta ou uma das contas de demonstração.")),
        form,
        h("div", { class: "demo" },
          h("span", { class: "label muted" }, "Contas de demonstração (senha Delco@2026)"),
          DEMO.map(([e, n, r]) => h("button", { type: "button", onclick: () => { email.value = e; password.value = "Delco@2026"; submit.focus(); } },
            h("span", { class: "avatar" }, initials(n)),
            h("span", { class: "who" }, h("strong", {}, n), h("span", { class: "muted tiny" }, `${r} · ${e}`)))))))));
  email.focus();
}

function paramField(name, spec, value) {
  const id = `param-${name}`;
  const label = spec.label || name;
  let input;
  if (spec.choices) {
    input = h("select", { class: "select", id }, spec.choices.map((c) => h("option", { value: JSON.stringify(c), selected: JSON.stringify(c) === JSON.stringify(value) }, String(c))));
  } else if (spec.type === "boolean") {
    input = h("input", { type: "checkbox", id, checked: !!value });
  } else if (spec.type === "integer" || spec.type === "number") {
    input = h("input", { class: "input", id, type: "number", step: spec.type === "integer" ? "1" : "any", value: value ?? "",
      min: spec.min ?? null, max: spec.max ?? null });
  } else if (spec.type === "date") {
    input = h("input", { class: "input", id, type: "date", value: value ?? "" });
  } else if (spec.type === "array" || spec.type === "object") {
    input = h("textarea", { class: "textarea", id, rows: "3", spellcheck: "false", style: { minHeight: "70px" } }, value === undefined || value === null ? "" : JSON.stringify(value, null, 2));
  } else {
    input = h("input", { class: "input", id, type: spec.secret ? "password" : "text", value: value ?? "", autocomplete: "off" });
  }
  const limits = [];
  if (spec.min !== undefined && spec.min !== null) limits.push(`mín. ${spec.min}`);
  if (spec.max !== undefined && spec.max !== null) limits.push(`máx. ${spec.max}`);
  const hint = [spec.description, limits.join(" · ")].filter(Boolean).join(" · ");
  const read = () => {
    if (spec.choices) return JSON.parse(input.value);
    if (spec.type === "boolean") return input.checked;
    const raw = input.value.trim();
    if (raw === "") return spec.required ? null : undefined;
    if (spec.type === "integer" || spec.type === "number") return Number(raw);
    if (spec.type === "array" || spec.type === "object") return JSON.parse(raw);
    return raw;
  };
  const el = spec.type === "boolean"
    ? h("div", { class: "field" }, h("label", { class: "check" }, input, h("span", { class: "strong small" }, label)), hint ? h("span", { class: "hint" }, hint) : null, h("span", { class: "field-error", "data-for": `params.${name}` }))
    : h("div", { class: "field" }, h("label", { for: id }, label, spec.required ? h("span", { class: "req", title: "Obrigatório" }, " *") : null, h("span", { class: "tag mono type-tag" }, spec.type)), input, hint ? h("span", { class: "hint" }, hint) : null, h("span", { class: "field-error", "data-for": `params.${name}` }));
  return { el, read, input };
}

function paramsEditor(schema, values) {
  const names = Object.keys(schema || {});
  let mode = names.length ? "form" : "json";
  const fields = new Map();
  const host = h("div", { class: "stack" });
  const json = h("textarea", { class: "textarea", id: "params", spellcheck: "false" });
  const toggle = h("div", { class: "segmented", role: "group", "aria-label": "Modo" });
  function collectForm() {
    const out = {};
    for (const [name, f] of fields) {
      const v = f.read();
      if (v !== undefined) out[name] = v;
    }
    const extra = Object.keys(values || {}).filter((k) => !fields.has(k));
    extra.forEach((k) => (out[k] = values[k]));
    return out;
  }
  function render() {
    clear(toggle, [["form", "Formulário"], ["json", "JSON"]].map(([k, l]) => h("button", { type: "button", "aria-pressed": String(mode === k), disabled: k === "form" && !names.length,
      onclick: () => {
        if (k === mode) return;
        try {
          if (k === "json") values = collectForm();
          else values = json.value.trim() ? JSON.parse(json.value) : {};
        } catch {
          toast("Corrija o JSON antes de trocar de modo", "error");
          return;
        }
        mode = k;
        render();
      } }, l)));
    fields.clear();
    if (mode === "form") {
      clear(host, h("div", { class: "param-grid" }, names.map((n) => {
        const f = paramField(n, schema[n], values?.[n] ?? schema[n].default);
        fields.set(n, f);
        return f.el;
      })));
    } else {
      json.value = JSON.stringify(values || {}, null, 2);
      clear(host, h("div", { class: "field" }, json, h("span", { class: "hint" }, "Chegam na tarefa como ctx.params. O que não for enviado usa o padrão do workflow."), h("span", { class: "field-error", "data-for": "params" })));
    }
  }
  render();
  return {
    toggle,
    el: host,
    read() {
      if (mode === "form") return collectForm();
      const parsed = json.value.trim() ? JSON.parse(json.value) : {};
      if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) throw new Error("Os parâmetros precisam ser um objeto JSON, como {\"dias\": 2}.");
      return parsed;
    },
    focus() {
      (host.querySelector("input, select, textarea"))?.focus();
    },
  };
}

function showFieldErrors(form, ex) {
  form.querySelectorAll(".field-error").forEach((el) => (el.textContent = ""));
  let shown = false;
  for (const [k, msg] of Object.entries(ex.fields || {})) {
    const slot = form.querySelector(`.field-error[data-for="${CSS.escape(k)}"]`) || (k.startsWith("params.") ? form.querySelector('.field-error[data-for="params"]') : null);
    if (slot) {
      slot.textContent = slot.dataset.for === "params" ? `${k.slice(7)}: ${msg}` : msg;
      shown = true;
    }
  }
  return shown;
}

export function triggerDialog(workflowName, defaults = null, { onDone } = {}) {
  modal(async (box, close) => {
    let workflows = [];
    const select = h("select", { class: "select", id: "wf" });
    const note = h("input", { class: "input", id: "note", maxlength: "500", placeholder: "Opcional" });
    const err = h("p", { class: "field-error" });
    const go = h("button", { class: "btn accent", type: "submit" }, icon("play", 15), "Executar");
    const toggleSlot = h("div");
    const editorSlot = h("div");
    let editor = null;
    const fill = async (name) => {
      clear(editorSlot, h("div", { class: "skeleton", style: { height: "90px" } }));
      let detail = null;
      try { detail = await get(`/workflows/${name}`); } catch { detail = null; }
      const schema = detail?.params_schema || {};
      const values = name === workflowName && defaults ? defaults : detail?.default_params || {};
      editor = paramsEditor(schema, values);
      clear(toggleSlot, editor.toggle);
      clear(editorSlot, editor.el);
    };
    const form = h("form", { onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = "";
      let parsed;
      try {
        parsed = editor.read();
      } catch (ex) {
        err.textContent = ex instanceof SyntaxError ? "JSON inválido: confira vírgulas e aspas." : ex.message;
        return;
      }
      go.disabled = true;
      try {
        const run = await post(`/workflows/${select.value}/runs`, { params: parsed, note: note.value || null });
        rememberRun(run.id);
        toast(`Execução #${run.id} criada`);
        close();
        onDone ? onDone(run) : navigate(`/runs/${run.id}`);
      } catch (ex) {
        if (!showFieldErrors(form, ex)) err.textContent = ex.message;
        go.disabled = false;
      }
    } },
    h("h2", {}, "Executar workflow"),
    h("p", { class: "muted small" }, "A execução entra na fila e começa assim que houver vaga."),
    h("div", { class: "stack", style: { marginTop: "18px" } },
      h("div", { class: "field" }, h("label", { for: "wf" }, "Workflow"), select),
      h("div", { class: "row" }, h("span", { class: "label" }, "Parâmetros"), h("span", { class: "spacer" }), toggleSlot),
      editorSlot,
      h("div", { class: "field" }, h("label", { for: "note" }, "Observação"), note),
      err),
    h("div", { class: "actions" }, h("button", { class: "btn ghost", type: "button", onclick: close }, "Cancelar"), go));
    box.append(form);
    try {
      workflows = (await get("/workflows")).filter((w) => w.is_active);
      clear(select, workflows.map((w) => h("option", { value: w.name, selected: w.name === workflowName }, w.name)));
      if (workflowName) select.value = workflowName;
      await fill(select.value);
      select.addEventListener("change", () => fill(select.value));
      if (workflowName) editor?.focus();
    } catch (ex) {
      err.textContent = ex.message;
    }
  }, { wide: true });
}

export function backfillDialog(wf, { onDone } = {}) {
  modal((box, close) => {
    const today = new Date();
    const iso = (d) => d.toISOString().slice(0, 10);
    const start = h("input", { class: "input", id: "start", type: "date", value: iso(new Date(today.getTime() - 6 * 86400e3)), max: iso(today) });
    const end = h("input", { class: "input", id: "end", type: "date", value: iso(today), max: iso(today) });
    const note = h("input", { class: "input", id: "bf-note", maxlength: "500", placeholder: "Opcional" });
    const redo = h("input", { type: "checkbox", id: "redo" });
    const preview = h("div", { class: "bf-preview" });
    const go = h("button", { class: "btn accent", type: "submit" }, icon("history", 15), "Criar execuções");
    const editor = paramsEditor(wf.params_schema || {}, wf.default_params || {});
    let timer;
    const body = (dry) => ({ start: start.value, end: end.value, params: editor.read(), note: note.value || null, dry_run: dry, skip_existing: !redo.checked });
    async function refresh() {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        form.querySelectorAll(".field-error").forEach((el) => (el.textContent = ""));
        if (!start.value || !end.value) return;
        try {
          const r = await post(`/workflows/${wf.name}/backfill`, body(true));
          const shown = r.slots.slice(0, 6).map((s) => dateTime(s));
          clear(preview, h("strong", {}, `${r.slots.length} execução(ões)`), r.slots.length ? ` · ${shown.join(", ")}${r.slots.length > 6 ? "…" : ""}` : "",
            r.skipped.length ? h("div", { class: "tiny muted" }, `${r.skipped.length} horário(s) já têm execução e serão pulados.`) : null);
          go.disabled = !r.slots.length;
        } catch (ex) {
          go.disabled = true;
          if (!showFieldErrors(form, ex)) clear(preview, h("span", { class: "field-error" }, ex.message));
          else clear(preview);
        }
      }, 250);
    }
    const form = h("form", { onsubmit: async (e) => {
      e.preventDefault();
      go.disabled = true;
      try {
        const r = await post(`/workflows/${wf.name}/backfill`, body(false));
        r.created.forEach(rememberRun);
        toast(`${r.created.length} execução(ões) criadas`);
        close();
        onDone ? onDone(r) : navigate(`/runs?workflow=${wf.name}&trigger=backfill`);
      } catch (ex) {
        if (!showFieldErrors(form, ex)) toast(ex.message, "error");
        go.disabled = false;
      }
    } },
    h("h2", {}, "Reprocessar período"),
    h("p", { class: "muted small" }, `Cria uma execução para cada horário da agenda de ${wf.name} (${wf.schedule_text.toLowerCase()}) entre as datas, com a data lógica de cada horário em ctx.logical_date. Elas respeitam o limite de execuções simultâneas.`),
    h("div", { class: "stack", style: { marginTop: "18px" } },
      h("div", { class: "grid-2" },
        h("div", { class: "field" }, h("label", { for: "start" }, "De"), start, h("span", { class: "field-error", "data-for": "start" })),
        h("div", { class: "field" }, h("label", { for: "end" }, "Até"), end, h("span", { class: "field-error", "data-for": "end" }))),
      h("label", { class: "check small" }, redo, "Refazer também horários que já têm execução"),
      Object.keys(wf.params_schema || {}).length ? h("div", { class: "stack sm" }, h("div", { class: "row" }, h("span", { class: "label" }, "Parâmetros"), h("span", { class: "spacer" }), editor.toggle), editor.el) : null,
      h("div", { class: "field" }, h("label", { for: "bf-note" }, "Observação"), note),
      preview),
    h("div", { class: "actions" }, h("button", { class: "btn ghost", type: "button", onclick: close }, "Cancelar"), go));
    [start, end, redo].forEach((el) => el.addEventListener("change", refresh));
    box.append(form);
    refresh();
  }, { wide: true });
}

onAuthChange((s) => {
  if (!s) route();
});
if (getSession()) {
  const before = JSON.stringify([getSession().user, prefs()]);
  refreshMe().then(() => {
    if (JSON.stringify([getSession().user, prefs()]) !== before) route();
  }).catch(() => null);
  checkHealth();
}
window.addEventListener("hashchange", route);
installShortcuts({ navigate, toggleTheme, isLogged: () => !!getSession() });
route();
