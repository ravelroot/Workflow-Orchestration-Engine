// Minha conta (senha e tokens pessoais) e administração de usuários.
import { get, getSession, api, post } from "../api.js";
import { clear, confirmDialog, copyButton, dateTime, empty, h, icon, initials, modal, relative, ROLE, toast } from "../ui.js";

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

export function accountPage(main) {
  const user = getSession().user;
  const tokens = h("div");
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", { class: "stack" }, h("h1", {}, "Minha conta"), h("p", { class: "muted" }, "Senha e tokens pessoais para a CLI e integrações."))),
    h("div", { class: "grid-2", style: { alignItems: "start" } },
      h("div", { class: "stack lg" },
        h("section", { class: "card pad" }, h("div", { class: "row" }, h("span", { class: "avatar", style: { width: "44px", height: "44px", fontSize: "15px" } }, initials(user.name)),
          h("div", { class: "stack sm" }, h("strong", {}, user.name), h("span", { class: "small muted" }, `${user.email} · ${ROLE[user.role]}`)))),
        passwordCard()),
      tokens)));

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

// ------------------------------------------------------------------ usuários
export function usersPage(main) {
  const list = h("section", { class: "card" }, h("div", { class: "skeleton", style: { height: "200px" } }));
  const audit = h("section", { class: "card" });
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", { class: "stack" }, h("h1", {}, "Usuários"), h("p", { class: "muted" }, "Quem acessa o painel e a API, e o que cada um fez.")),
      h("button", { class: "btn", onclick: () => userDialog(null, load) }, icon("plus", 15), "Novo usuário")),
    h("div", { class: "stack lg" }, list, audit)));

  async function load() {
    const [users, log] = await Promise.all([get("/users"), get("/audit?size=30")]);
    const me = getSession().user.id;
    clear(list, h("div", { class: "table-wrap", style: { paddingTop: "6px" } }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Usuário"), h("th", {}, "Papel"), h("th", {}, "Situação"), h("th", {}, "Último acesso"), h("th", {}))),
      h("tbody", {}, users.map((u) => h("tr", {},
        h("td", {}, h("div", { class: "row" }, h("span", { class: "avatar" }, initials(u.name)), h("div", {}, h("div", { class: "strong small" }, u.name, u.id === me ? h("span", { class: "muted" }, " (você)") : null), h("div", { class: "tiny muted" }, u.email)))),
        h("td", { class: "small" }, ROLE[u.role]),
        h("td", {}, h("span", { class: `pill ${u.is_active ? (u.locked ? "retry_wait" : "succeeded") : "cancelled"}` }, h("span", { class: "dot" }), !u.is_active ? "Desativado" : u.locked ? "Bloqueado" : "Ativo")),
        h("td", { class: "small muted" }, u.last_login_at ? relative(u.last_login_at) : "nunca"),
        h("td", { class: "right" }, h("button", { class: "btn ghost sm", onclick: () => userDialog(u, load) }, "Editar"))))))));
    const ACTIONS = {
      "auth.login": "entrou", "auth.failed": "errou a senha", "auth.password": "trocou a senha", "run.trigger": "disparou execução",
      "run.cancel": "cancelou execução", "run.retry": "retomou execução", "workflow.pause": "pausou workflow", "workflow.resume": "retomou workflow",
      "token.create": "criou token", "token.revoke": "revogou token", "user.create": "criou usuário", "user.update": "alterou usuário",
    };
    clear(audit, h("div", { class: "card-head" }, h("h2", {}, "Auditoria"), h("span", { class: "card-sub" }, "Últimas 30 ações")),
      log.items.length ? h("div", { class: "table-wrap", style: { paddingTop: "6px" } }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Quando"), h("th", {}, "Quem"), h("th", {}, "Ação"), h("th", {}, "Alvo"))),
        h("tbody", {}, log.items.map((a) => h("tr", {},
          h("td", { class: "small muted nowrap" }, dateTime(a.created_at, true)),
          h("td", { class: "small" }, a.user_name || "sistema"),
          h("td", { class: "small" }, ACTIONS[a.action] || a.action),
          h("td", { class: "small mono" }, a.target?.startsWith("run:") ? h("a", { href: `#/runs/${a.target.slice(4)}`, style: { textDecoration: "underline" } }, a.target) : a.target || "—")))))) : empty("Nada registrado ainda"));
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
