// Ponto de entrada: rotas, estrutura da página, entrada e tema.
import { ApiError, can, get, getSession, live, login, logout, onAuthChange, post } from "./api.js";
import { brand, clear, h, icon, initials, mark, modal, ROLE, toast } from "./ui.js";
import { insightsPage } from "./pages/insights.js";
import { integrationsPage } from "./pages/integrations.js";
import { openPalette, openShortcuts, installShortcuts } from "./palette.js";
import { overviewPage } from "./pages/overview.js";
import { workflowPage, workflowsPage } from "./pages/workflows.js";
import { runPage, runsPage } from "./pages/runs.js";
import { workersPage } from "./pages/workers.js";
import { accountPage, usersPage } from "./pages/account.js";

const app = document.getElementById("app");
let cleanup = [];

// ------------------------------------------------------------------ tema
const THEME_KEY = "delco.theme";
function savedTheme() {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch {
    return null;
  }
}
function applyTheme(theme) {
  const t = theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = t;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", t === "dark" ? "#191d20" : "#f7f3f1");
  return t;
}
applyTheme(savedTheme());
export function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    /* sem storage */
  }
  applyTheme(next);
  route();
}

// ------------------------------------------------------------------ rotas
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
  live.followLogs(null);
  if (!getSession()) {
    renderLogin();
    return;
  }
  live.start();
  const { path, query } = parseHash();
  const match = ROUTES.map((r) => ({ r, m: path.match(r.re) })).find((x) => x.m);
  const main = h("main", { class: "main", id: "conteudo" });
  clear(app, h("div", { class: "shell" }, sidebar(match?.r.nav), main));
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

// ---------------------------------------------------------------- estrutura
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

// ------------------------------------------------------------------ entrada
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

// ------------------------------------------------------ disparo de execução
export function triggerDialog(workflowName, defaults = {}, { onDone } = {}) {
  modal(async (box, close) => {
    let workflows = [];
    const select = h("select", { class: "select", id: "wf" });
    const params = h("textarea", { class: "textarea", id: "params", spellcheck: "false" });
    const note = h("input", { class: "input", id: "note", maxlength: "500", placeholder: "Opcional" });
    const err = h("p", { class: "field-error" });
    const go = h("button", { class: "btn accent", type: "submit" }, icon("play", 15), "Executar");
    const fill = (name) => {
      const wf = workflows.find((w) => w.name === name);
      params.value = JSON.stringify(wf?.default_params ?? defaults, null, 2);
    };
    box.append(h("form", { onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = "";
      let parsed;
      try {
        parsed = params.value.trim() ? JSON.parse(params.value) : {};
        if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) throw new Error();
      } catch {
        params.setAttribute("aria-invalid", "true");
        err.textContent = "Os parâmetros precisam ser um objeto JSON válido, como {\"dias\": 2}.";
        return;
      }
      go.disabled = true;
      try {
        const run = await post(`/workflows/${select.value}/runs`, { params: parsed, note: note.value || null });
        toast(`Execução #${run.id} criada`);
        close();
        onDone ? onDone(run) : navigate(`/runs/${run.id}`);
      } catch (ex) {
        err.textContent = ex.message;
        go.disabled = false;
      }
    } },
    h("h2", {}, "Executar workflow"),
    h("p", { class: "muted small" }, "A execução entra na fila e começa assim que houver vaga."),
    h("div", { class: "stack", style: { marginTop: "18px" } },
      h("div", { class: "field" }, h("label", { for: "wf" }, "Workflow"), select),
      h("div", { class: "field" }, h("label", { for: "params" }, "Parâmetros (JSON)"), params,
        h("span", { class: "hint" }, "Chegam na tarefa como ctx.params. O que não for enviado usa o padrão do workflow.")),
      h("div", { class: "field" }, h("label", { for: "note" }, "Observação"), note),
      err),
    h("div", { class: "actions" }, h("button", { class: "btn ghost", type: "button", onclick: close }, "Cancelar"), go)));
    try {
      workflows = (await get("/workflows")).filter((w) => w.is_active);
      const details = await Promise.all(workflows.map((w) => get(`/workflows/${w.name}`).catch(() => null)));
      details.forEach((d, i) => d && (workflows[i].default_params = d.default_params));
      clear(select, workflows.map((w) => h("option", { value: w.name, selected: w.name === workflowName }, w.name)));
      if (workflowName) select.value = workflowName;
      fill(select.value);
      select.addEventListener("change", () => fill(select.value));
      if (workflowName) params.focus();
    } catch (ex) {
      err.textContent = ex.message;
    }
  });
}

onAuthChange((s) => {
  if (!s) route();
});
window.addEventListener("hashchange", route);
installShortcuts({ navigate, toggleTheme, isLogged: () => !!getSession() });
route();
