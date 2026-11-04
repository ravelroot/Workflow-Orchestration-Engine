import { download, get, getSession, api, post, prefs, savePrefs } from "../api.js";
import { clear, confirmDialog, copyButton, dateTime, debounce, empty, h, icon, initials, modal, relative, ROLE, toast } from "../ui.js";

function field(id, label, input, hint) {
  return h("div", { class: "field" }, h("label", { for: id }, label), input, hint ? h("span", { class: "hint" }, hint) : null, h("span", { class: "field-error", "data-for": id }));
}

function showErrors(form, ex) {
  form.querySelectorAll(".field-error").forEach((e) => (e.textContent = ""));
  form.querySelectorAll("[aria-invalid]").forEach((e) => e.removeAttribute("aria-invalid"));
  const fields = ex.fields || {};
  let shown = false;
  for (const [k, msg] of Object.entries(fields)) {
    const slot = form.querySelector(`.field-error[data-for="${k}"]`);
    if (slot) {
      slot.textContent = msg;
      form.querySelector(`#${k}`)?.setAttribute("aria-invalid", "true");
      shown = true;
    }
  }
  if (!shown) toast(ex.message, "error");
}

export function accountPage(main, ctx) {
  const user = getSession().user;
  const tokens = h("div");
  const prefsCard = preferencesCard();
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", { class: "stack" }, h("h1", {}, "Minha conta"), h("p", { class: "muted" }, "Preferências do painel, senha e tokens pessoais para a CLI e integrações."))),
    h("div", { class: "grid-2", style: { alignItems: "start" } },
      h("div", { class: "stack lg" },
        h("section", { class: "card pad" }, h("div", { class: "row" }, h("span", { class: "avatar", style: { width: "44px", height: "44px", fontSize: "15px" } }, initials(user.name)),
          h("div", { class: "stack sm" }, h("strong", {}, user.name), h("span", { class: "small muted" }, `${user.email} · ${ROLE[user.role]}`)))),
        prefsCard,
        passwordCard()),
      tokens)));
  if (ctx?.query?.get("tab") === "prefs") setTimeout(() => prefsCard.scrollIntoView({ block: "start" }), 50);

  async function loadTokens() {
    const list = await get("/tokens");
    const name = h("input", { class: "input", id: "name", placeholder: "Ex.: CLI do notebook", maxlength: "80" });
    const days = h("select", { class: "select", id: "expires_in_days" },
      [[30, "30 dias"], [90, "90 dias"], [365, "1 ano"], ["", "Sem validade"]].map(([v, l]) => h("option", { value: v, selected: v === 90 }, l)));
    const reveal = h("div");
    const form = h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      try {
        const t = await post("/tokens", { name: name.value, expires_in_days: days.value ? Number(days.value) : null });
        clear(reveal, h("div", { class: "notice", style: { flexDirection: "column", gap: "8px" } },
          h("strong", {}, "Copie o token agora: ele não aparece de novo."),
          h("div", { class: "copy-line" }, h("code", {}, t.token), copyButton(t.token)),
          h("span", { class: "small muted" }, "No terminal:"),
          h("div", { class: "copy-line" }, h("code", {}, `export DELCO_TOKEN=${t.token}`))));
        name.value = "";
        renderList(await get("/tokens"));
      } catch (ex) {
        showErrors(form, ex);
      }
    } },
    h("div", { class: "grid-2" }, field("name", "Nome do token", name), field("expires_in_days", "Validade", days)),
    h("div", {}, h("button", { class: "btn", type: "submit" }, icon("key", 15), "Criar token")));
    const listEl = h("div");
    function renderList(items) {
      clear(listEl, items.length ? h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Nome"), h("th", {}, "Último uso"), h("th", {}, "Validade"), h("th", {}))),
        h("tbody", {}, items.map((t) => h("tr", {},
          h("td", {}, h("div", { class: "strong small" }, t.name), h("div", { class: "tiny muted mono" }, `${t.prefix}…`)),
          h("td", { class: "small muted" }, t.last_used_at ? relative(t.last_used_at) : "nunca"),
          h("td", { class: "small muted" }, t.expires_at ? dateTime(t.expires_at) : "sem validade"),
          h("td", { class: "right" }, h("button", { class: "btn danger sm", onclick: async () => {
            if (!(await confirmDialog({ title: "Revogar token?", text: `Quem usa “${t.name}” perde o acesso na hora.`, confirm: "Revogar", danger: true }))) return;
            await api("DELETE", `/tokens/${t.id}`);
            toast("Token revogado");
            renderList(await get("/tokens"));
          } }, "Revogar")))))) : h("p", { class: "small muted", style: { padding: "0 20px 16px" } }, "Nenhum token pessoal ativo."));
    }
    renderList(list);
    clear(tokens, h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Tokens pessoais"), h("p", { class: "card-sub" }, "Use no cabeçalho Authorization: Bearer ou na variável DELCO_TOKEN da CLI."))),
      h("div", { class: "card-body stack" }, form, reveal), listEl));
  }
  loadTokens();
}

const PALETTES = [
  ["#f7f3f1", "#4c6170", "#1f292f", "#56663f"],
  ["#e6ddce", "#5d1c16", "#7a6e46", "#f6e4a6"],
  ["#f5f4f4", "#000000", "#2b4c5a", "#e2c288"],
  ["#f3f1ec", "#a8235a", "#3e6f84", "#2e7d5b"],
  ["#e8e8e8", "#000000", "#ab9d9d", "#5c5454"],
];

function preferencesCard() {
  const p = prefs();
  const seg = (key, options) => {
    const el = h("div", { class: "segmented", role: "group" });
    const paint = (value) => clear(el, options.map(([v, l]) => h("button", { type: "button", "aria-pressed": String(value === v), onclick: () => save({ [key]: v }) }, l)));
    paint(p[key]);
    return { el, paint };
  };
  const theme = seg("theme", [["system", "Sistema"], ["light", "Claro"], ["dark", "Escuro"]]);
  const swatches = h("div", { class: "swatches", role: "group", "aria-label": "Paleta de cores" });
  const paintSwatches = (value) => clear(swatches, PALETTES.map((colors, i) => h("button", {
    type: "button", class: "swatch-btn", "aria-pressed": String(String(value) === String(i)), "aria-label": `Paleta ${i + 1}`,
    onclick: () => save({ palette: String(i) }),
  }, colors.map((c) => h("span", { style: { background: c } })))));
  paintSwatches(p.palette);
  const zone = seg("time_zone", [["local", "Horário local"], ["utc", "UTC"]]);
  const density = seg("density", [["comfortable", "Confortável"], ["compact", "Compacta"]]);
  const size = seg("page_size", [[25, "25"], [50, "50"], [100, "100"]]);
  const sw = (key, label, hint) => {
    const btn = h("button", { class: "switch", role: "switch", "aria-checked": String(!!p[key]), "aria-label": label, onclick: async () => {
      const next = !(btn.getAttribute("aria-checked") === "true");
      if (next && key.startsWith("notify") && "Notification" in window && Notification.permission === "default") {
        try { await Notification.requestPermission(); } catch { toast("O navegador não liberou notificações", "error"); }
      }
      await save({ [key]: next });
      btn.setAttribute("aria-checked", String(next));
    } });
    return h("div", { class: "pref-row" }, h("div", { class: "stack sm", style: { gap: "2px" } }, h("span", { class: "small strong" }, label), hint ? h("span", { class: "tiny muted" }, hint) : null), btn);
  };
  async function save(patch) {
    try {
      const merged = await savePrefs(patch);
      theme.paint(merged.theme);
      paintSwatches(merged.palette);
      zone.paint(merged.time_zone);
      density.paint(merged.density);
      size.paint(merged.page_size);
      toast("Preferência salva");
    } catch (ex) {
      toast(ex.message, "error");
    }
  }
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  const permText = { granted: "Notificações do navegador liberadas.", denied: "O navegador bloqueou as notificações; libere nas configurações do site.", default: "O navegador vai pedir permissão ao ligar.", unsupported: "Este navegador não tem notificações; os avisos aparecem só no painel." }[perm];
  return h("section", { class: "card", id: "preferencias" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Preferências"), h("p", { class: "card-sub" }, "Valem para a sua conta em qualquer navegador."))),
    h("div", { class: "card-body stack" },
      h("div", { class: "pref-row" }, h("span", { class: "small strong" }, "Tema"), theme.el),
      h("div", { class: "pref-row" }, h("span", { class: "small strong" }, "Paleta"), swatches),
      h("div", { class: "pref-row" }, h("div", { class: "stack sm", style: { gap: "2px" } }, h("span", { class: "small strong" }, "Datas e horas"), h("span", { class: "tiny muted" }, "A API sempre responde em UTC")), zone.el),
      h("div", { class: "pref-row" }, h("span", { class: "small strong" }, "Densidade das tabelas"), density.el),
      h("div", { class: "pref-row" }, h("span", { class: "small strong" }, "Execuções por página"), size.el),
      sw("notify_failures", "Avisar quando qualquer execução falhar", null),
      sw("notify_mine", "Avisar quando execuções que eu disparei terminarem", null),
      sw("log_wrap", "Quebrar linhas longas nos logs", null),
      h("p", { class: "tiny muted" }, `${permText} Com a aba aberta, os avisos aparecem no canto da tela.`)));
}

function passwordCard() {
  const current = h("input", { class: "input", id: "current_password", type: "password", autocomplete: "current-password" });
  const next = h("input", { class: "input", id: "new_password", type: "password", autocomplete: "new-password" });
  const form = h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    try {
      await api("PUT", "/auth/me/password", { current_password: current.value, new_password: next.value });
      current.value = next.value = "";
      showErrors(form, { fields: {} });
      toast("Senha alterada. Outras sessões foram encerradas.");
    } catch (ex) {
      showErrors(form, ex);
    }
  } },
  field("current_password", "Senha atual", current),
  field("new_password", "Nova senha", next, "Pelo menos 8 caracteres, com letras e números."),
  h("div", {}, h("button", { class: "btn", type: "submit" }, "Alterar senha")));
  return h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Senha")), h("div", { class: "card-body" }, form));
}

const ACTIONS = {
  "auth.login": "entrou", "auth.failed": "errou a senha", "auth.password": "trocou a senha", "run.trigger": "disparou execução",
  "run.cancel": "cancelou execução", "run.retry": "retomou execução", "run.backfill": "reprocessou período", "run.mark": "marcou tarefa",
  "run.note": "editou observação", "run.delete": "excluiu execução", "run.timeout": "execução passou do tempo limite",
  "workflow.pause": "pausou workflow", "workflow.resume": "retomou workflow", "worker.pause": "pausou worker", "worker.resume": "retomou worker",
  "token.create": "criou token", "token.revoke": "revogou token", "user.create": "criou usuário", "user.update": "alterou usuário",
  "integration.create": "criou integração", "integration.update": "alterou integração", "integration.delete": "excluiu integração",
  "integration.test": "testou integração", "integration.redeliver": "reenviou entrega", "system.housekeeping": "rodou a limpeza",
};

export function usersPage(main) {
  const list = h("section", { class: "card" }, h("div", { class: "skeleton", style: { height: "200px" } }));
  const audit = h("section", { class: "card" });
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", { class: "stack" }, h("h1", {}, "Usuários"), h("p", { class: "muted" }, "Quem acessa o painel e a API, e o que cada um fez.")),
      h("button", { class: "btn", onclick: () => userDialog(null, load) }, icon("plus", 15), "Novo usuário")),
    h("div", { class: "stack lg" }, list, audit)));
  let users = [];
  const filters = { action: "", user: "", q: "", since: "", page: 1 };
  const actionSel = h("select", { class: "select auto", "aria-label": "Ação" });
  const userSel = h("select", { class: "select auto", "aria-label": "Usuário" });
  const periodSel = h("select", { class: "select auto", "aria-label": "Período" }, [["", "Todo o período"], ["1", "Últimas 24 h"], ["7", "Últimos 7 dias"], ["30", "Últimos 30 dias"]].map(([v, l]) => h("option", { value: v }, l)));
  const search = h("input", { class: "input", type: "search", placeholder: "Alvo ou pessoa", style: { width: "200px" }, "aria-label": "Buscar na auditoria" });
  const auditBody = h("div");
  const auditPager = h("div", { class: "row", style: { justifyContent: "space-between", padding: "12px 20px 16px" } });
  const query = () => {
    const q = new URLSearchParams();
    for (const k of ["action", "user", "q", "since"]) if (filters[k]) q.set(k, filters[k]);
    return q;
  };
  clear(audit,
    h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Auditoria"), h("p", { class: "card-sub" }, "Toda ação que muda algo fica registrada por 180 dias")),
      h("button", { class: "btn ghost sm", onclick: async () => {
        try { await download(`/audit/export?${query()}`, `auditoria-delco-${new Date().toISOString().slice(0, 10)}.csv`); toast("CSV exportado"); } catch (ex) { toast(ex.message, "error"); }
      } }, icon("download", 14), "Exportar CSV")),
    h("div", { class: "toolbar", style: { padding: "14px 20px 0", marginBottom: "6px" } }, h("div", { class: "search" }, icon("search", 15), search), actionSel, userSel, periodSel),
    auditBody, auditPager);

  async function loadAudit() {
    const q = query();
    q.set("page", filters.page);
    q.set("size", 30);
    const log = await get(`/audit?${q}`);
    if (actionSel.options.length !== log.actions.length + 1) {
      clear(actionSel, h("option", { value: "" }, "Todas as ações"), log.actions.map((a) => h("option", { value: a, selected: a === filters.action }, `${ACTIONS[a] || a}`)));
    }
    clear(auditBody, log.items.length ? h("div", { class: "table-wrap", style: { paddingTop: "6px" } }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Quando"), h("th", {}, "Quem"), h("th", {}, "Ação"), h("th", {}, "Alvo"), h("th", {}, "Detalhes"))),
      h("tbody", {}, log.items.map((a) => h("tr", {},
        h("td", { class: "small muted nowrap" }, dateTime(a.created_at, true)),
        h("td", { class: "small" }, a.user_name || "sistema"),
        h("td", { class: "small" }, ACTIONS[a.action] || a.action),
        h("td", { class: "small mono" }, a.target?.startsWith("run:") ? h("a", { href: `#/runs/${a.target.slice(4)}`, style: { textDecoration: "underline" } }, a.target) : a.target?.startsWith("workflow:") ? h("a", { href: `#/workflows/${a.target.slice(9)}`, style: { textDecoration: "underline" } }, a.target) : a.target || "—"),
        h("td", { class: "tiny muted mono ellipsis", style: { maxWidth: "280px" }, title: JSON.stringify(a.details) }, Object.keys(a.details || {}).length ? JSON.stringify(a.details) : "—")))))) : empty("Nada encontrado", "Tire algum filtro ou amplie o período."));
    const pages = Math.max(1, Math.ceil(log.total / log.size));
    clear(auditPager,
      h("span", { class: "small muted" }, `${log.total} registro(s) · página ${log.page} de ${pages}`),
      h("div", { class: "row tight" },
        h("button", { class: "btn ghost sm", disabled: filters.page <= 1, onclick: () => { filters.page--; loadAudit(); } }, "Anterior"),
        h("button", { class: "btn ghost sm", disabled: filters.page >= pages, onclick: () => { filters.page++; loadAudit(); } }, "Próxima")));
  }
  const reAudit = () => { filters.page = 1; loadAudit().catch((ex) => clear(auditBody, h("div", { class: "notice error" }, ex.message))); };
  actionSel.addEventListener("change", () => { filters.action = actionSel.value; reAudit(); });
  userSel.addEventListener("change", () => { filters.user = userSel.value; reAudit(); });
  periodSel.addEventListener("change", () => { filters.since = periodSel.value; reAudit(); });
  search.addEventListener("input", debounce(() => { filters.q = search.value.trim(); reAudit(); }, 300));

  async function load() {
    users = await get("/users");
    const me = getSession().user.id;
    clear(userSel, h("option", { value: "" }, "Qualquer pessoa"), users.map((u) => h("option", { value: u.id, selected: String(u.id) === filters.user }, u.name)));
    clear(list, h("div", { class: "table-wrap", style: { paddingTop: "6px" } }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Usuário"), h("th", {}, "Papel"), h("th", {}, "Situação"), h("th", {}, "Último acesso"), h("th", {}))),
      h("tbody", {}, users.map((u) => h("tr", {},
        h("td", {}, h("div", { class: "row" }, h("span", { class: "avatar" }, initials(u.name)), h("div", {}, h("div", { class: "strong small" }, u.name, u.id === me ? h("span", { class: "muted" }, " (você)") : null), h("div", { class: "tiny muted" }, u.email)))),
        h("td", { class: "small" }, ROLE[u.role]),
        h("td", {}, h("span", { class: `pill ${u.is_active ? (u.locked ? "retry_wait" : "succeeded") : "cancelled"}` }, h("span", { class: "dot" }), !u.is_active ? "Desativado" : u.locked ? "Bloqueado" : "Ativo")),
        h("td", { class: "small muted" }, u.last_login_at ? relative(u.last_login_at) : "nunca"),
        h("td", { class: "right" }, h("button", { class: "btn ghost sm", onclick: () => userDialog(u, load) }, "Editar"))))))));
    await loadAudit();
  }
  load().catch((ex) => clear(list, h("div", { class: "notice error" }, ex.message)));
}

function userDialog(user, onSaved) {
  modal((box, close) => {
    const email = h("input", { class: "input", id: "email", type: "email", value: user?.email || "", disabled: !!user });
    const name = h("input", { class: "input", id: "name", value: user?.name || "" });
    const role = h("select", { class: "select", id: "role" }, Object.entries(ROLE).map(([v, l]) => h("option", { value: v, selected: (user?.role || "operator") === v }, l)));
    const password = h("input", { class: "input", id: "password", type: "password", autocomplete: "new-password" });
    const active = h("input", { type: "checkbox", id: "is_active", checked: user ? user.is_active : true });
    const form = h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      try {
        if (user) {
          const body = { name: name.value, role: role.value, is_active: active.checked };
          if (password.value) body.password = password.value;
          if (user.locked) body.unlock = true;
          await api("PATCH", `/users/${user.id}`, body);
          toast("Usuário atualizado");
        } else {
          await post("/users", { email: email.value, name: name.value, role: role.value, password: password.value });
          toast("Usuário criado");
        }
        close();
        onSaved();
      } catch (ex) {
        showErrors(form, ex);
      }
    } },
    h("h2", {}, user ? "Editar usuário" : "Novo usuário"),
    field("email", "E-mail", email),
    field("name", "Nome", name),
    field("role", "Papel", role, "Leitura só consulta · Operador dispara, cancela e pausa · Administrador também gerencia usuários."),
    field("password", user ? "Nova senha (opcional)" : "Senha", password, "Trocar a senha encerra as sessões e tokens do usuário."),
    user ? h("label", { class: "row tight small" }, active, "Conta ativa") : null,
    h("span", { class: "field-error", "data-for": "_" }),
    h("div", { class: "actions" }, h("button", { class: "btn ghost", type: "button", onclick: close }, "Cancelar"), h("button", { class: "btn", type: "submit" }, "Salvar")));
    box.append(form);
  });
}
