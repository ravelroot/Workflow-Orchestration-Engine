// Paleta de comandos (Ctrl+K / ⌘K) e atalhos de teclado no estilo "g e" (ir para Execuções).
import { can, get, logout } from "./api.js";
import { clear, h, icon, modal, STATUS, toast } from "./ui.js";

const PAGES = [
  ["Visão geral", "#/", "overview", "g v"],
  ["Workflows", "#/workflows", "workflows", "g w"],
  ["Execuções", "#/runs", "runs", "g e"],
  ["Workers", "#/workers", "workers", "g k"],
  ["Desempenho", "#/insights", "insights", "g d"],
  ["Integrações", "#/integrations", "plug", "g i", "operator"],
  ["Minha conta e tokens", "#/account", "key", "g c"],
  ["Usuários e auditoria", "#/users", "users", "g u", "admin"],
];

const norm = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

export async function openPalette() {
  if (document.querySelector(".palette")) return;
  const app = await import("./app.js");
  let workflows = [];
  let runs = [];
  let items = [];
  let index = 0;
  const input = h("input", { type: "text", placeholder: "Buscar tela, workflow ou execução (#118)…", "aria-label": "Comando", autocomplete: "off", spellcheck: "false" });
  const list = h("div", { class: "palette-list", role: "listbox" });
  modal((box, close) => {
    const run = (item) => { close(); item.run(); };
    function build() {
      const q = norm(input.value.trim());
      const groups = [];
      const num = q.replace(/^#/, "");
      if (/^\d+$/.test(num)) {
        groups.push(["Ir para", [{ label: `Abrir execução #${num}`, ic: "runs", run: () => app.navigate(`/runs/${num}`) }]]);
      }
      const match = (text) => !q || norm(text).includes(q.replace(/^#/, ""));
      const pages = PAGES.filter((p) => !p[4] || can(p[4])).filter((p) => match(p[0]))
        .map(([label, href, ic, keys]) => ({ label, ic, hint: keys, run: () => (location.hash = href) }));
      if (pages.length) groups.push(["Telas", pages]);
      const actions = [
        can("operator") ? { label: "Executar workflow…", ic: "play", run: () => app.triggerDialog() } : null,
        { label: "Alternar tema claro/escuro", ic: "moon", run: () => app.toggleTheme() },
        { label: "Atalhos de teclado", ic: "keyboard", hint: "?", run: () => openShortcuts() },
        { label: "Documentação da API", ic: "api", run: () => window.open("/api/docs", "_blank", "noopener") },
        { label: "Sair", ic: "logout", run: async () => { await logout(); toast("Sessão encerrada"); } },
      ].filter(Boolean).filter((a) => match(a.label));
      const wfs = workflows.filter((w) => match(`${w.name} ${w.description || ""} ${(w.tags || []).join(" ")}`)).slice(0, 8)
        .map((w) => ({ label: w.name, mono: true, ic: "workflows", hint: w.schedule_text, run: () => app.navigate(`/workflows/${w.name}`) }));
      if (wfs.length) groups.push(["Workflows", wfs]);
      const rs = runs.filter((r) => match(`#${r.id} ${r.workflow} ${STATUS[r.status]}`)).slice(0, 6)
        .map((r) => ({ label: `#${r.id} · ${r.workflow}`, ic: "runs", hint: STATUS[r.status], run: () => app.navigate(`/runs/${r.id}`) }));
      if (rs.length) groups.push(["Execuções recentes", rs]);
      if (actions.length) groups.push(["Ações", actions]);
      items = groups.flatMap(([, arr]) => arr);
      index = Math.min(index, Math.max(0, items.length - 1));
      let i = 0;
      clear(list, items.length ? groups.map(([title, arr]) => [
        h("div", { class: "palette-group" }, title),
        arr.map((item) => {
          const my = i++;
          return h("button", {
            class: "palette-item", role: "option", "aria-selected": String(my === index),
            onmousemove: () => { if (index !== my) { index = my; paint(); } },
            onclick: () => run(item),
          }, icon(item.ic, 16), h("span", { class: item.mono ? "mono" : "" }, item.label), item.hint ? h("span", { class: "hint-r" }, item.hint) : null);
        }),
      ]) : h("div", { class: "empty", style: { padding: "28px" } }, h("strong", {}, "Nada encontrado"), h("p", {}, "Tente o nome de um workflow ou o número de uma execução, como #118.")));
    }
    function paint() {
      list.querySelectorAll(".palette-item").forEach((el, i) => el.setAttribute("aria-selected", String(i === index)));
      list.querySelectorAll(".palette-item")[index]?.scrollIntoView({ block: "nearest" });
    }
    input.addEventListener("input", () => { index = 0; build(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); index = Math.min(items.length - 1, index + 1); paint(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); index = Math.max(0, index - 1); paint(); }
      else if (e.key === "Enter" && items[index]) { e.preventDefault(); run(items[index]); }
    });
    box.className = "palette";
    box.append(
      h("div", { class: "palette-input" }, icon("search", 18), input, h("kbd", {}, "Esc")),
      list,
      h("div", { class: "palette-foot" },
        h("span", {}, h("kbd", {}, "↑"), h("kbd", {}, "↓"), "navegar"),
        h("span", {}, h("kbd", {}, "Enter"), "abrir"),
        h("span", {}, h("kbd", {}, "?"), "atalhos")));
    build();
    Promise.all([get("/workflows").catch(() => []), get("/runs?size=20").catch(() => ({ items: [] }))]).then(([w, r]) => {
      workflows = w;
      runs = r.items;
      build();
    });
  }, { bare: true });
  input.focus();
}

export function openShortcuts() {
  document.querySelector(".overlay")?.remove();
  const keys = (...k) => h("span", { class: "keys" }, k.map((x) => h("kbd", {}, x)));
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  modal((box, close) => {
    box.append(
      h("h2", {}, "Atalhos de teclado"),
      h("p", { class: "muted small" }, "Funcionam em qualquer tela, fora de campos de texto."),
      h("div", { class: "shortcuts" },
        h("span", {}, "Abrir a paleta de comandos"), keys(mac ? "⌘" : "Ctrl", "K"),
        h("span", {}, "Focar a busca da página"), keys("/"),
        h("span", {}, "Executar workflow"), keys("x"),
        ...PAGES.filter((p) => !p[4] || can(p[4])).flatMap(([label, , , k]) => [h("span", {}, `Ir para ${label}`), keys(...k.split(" "))]),
        h("span", {}, "Mostrar esta lista"), keys("?")),
      h("div", { class: "actions" }, h("button", { class: "btn", onclick: close }, "Fechar")));
  });
}

export function installShortcuts({ navigate, isLogged }) {
  let pendingG = 0;
  document.addEventListener("keydown", async (e) => {
    if (!isLogged()) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openPalette();
      return;
    }
    const t = e.target;
    if (e.ctrlKey || e.metaKey || e.altKey || t.closest?.("input, textarea, select, [contenteditable]") || document.querySelector(".overlay")) return;
    if (pendingG && Date.now() - pendingG < 1200) {
      pendingG = 0;
      const page = PAGES.find((p) => p[3] === `g ${e.key.toLowerCase()}` && (!p[4] || can(p[4])));
      if (page) {
        e.preventDefault();
        navigate(page[1].slice(1));
      }
      return;
    }
    if (e.key === "g") pendingG = Date.now();
    else if (e.key === "?") { e.preventDefault(); openShortcuts(); }
    else if (e.key === "/") {
      const search = document.querySelector('.main input[type="search"]');
      if (search) { e.preventDefault(); search.focus(); }
    } else if (e.key === "x" && can("operator")) {
      e.preventDefault();
      (await import("./app.js")).triggerDialog();
    }
  });
}
