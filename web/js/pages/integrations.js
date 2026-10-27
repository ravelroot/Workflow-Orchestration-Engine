// Integrações: notificações para Slack, Discord, Teams, Google Chat e webhooks, e gatilho por URL.
import { api, can, get, post } from "../api.js";
import { clear, confirmDialog, copyButton, dateTime, drawer, empty, h, icon, modal, relative, toast } from "../ui.js";

export const KINDS = {
  slack: { label: "Slack", ic: "hash", hint: "Webhook de entrada de um canal", ph: "https://hooks.slack.com/services/…" },
  discord: { label: "Discord", ic: "chat", hint: "Webhook do canal (Integrações do servidor)", ph: "https://discord.com/api/webhooks/…" },
  teams: { label: "Microsoft Teams", ic: "team", hint: "Webhook de entrada ou fluxo do Workflows", ph: "https://….webhook.office.com/…" },
  google_chat: { label: "Google Chat", ic: "message", hint: "Webhook do espaço", ph: "https://chat.googleapis.com/v1/spaces/…" },
  webhook: { label: "Webhook genérico", ic: "braces", hint: "POST em JSON assinado com HMAC-SHA256", ph: "https://seu-sistema.com/delco/eventos" },
  trigger: { label: "Gatilho por URL", ic: "bolt", hint: "Uma URL secreta que dispara um workflow", ph: "" },
};
const EVENTS = {
  "run.started": "Começou",
  "run.succeeded": "Concluída",
  "run.failed": "Falhou",
  "run.cancelled": "Cancelada",
};
const EVENT_LONG = {
  "run.started": "Execução começou",
  "run.succeeded": "Execução concluída",
  "run.failed": "Execução falhou",
  "run.cancelled": "Execução cancelada",
  test: "Teste",
};

export function integrationsPage(main) {
  const admin = can("admin");
  const body = h("div", {}, h("div", { class: "skeleton", style: { height: "240px" } }));
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", { class: "stack" }, h("h1", {}, "Integrações"),
        h("p", { class: "muted" }, "Avise o time no Slack, Discord, Teams ou Google Chat quando uma execução muda de estado, envie eventos para outro sistema por webhook ou dispare workflows de fora por uma URL secreta.")),
      admin ? h("button", { class: "btn accent", onclick: () => integrationDialog(null, load) }, icon("plus", 15), "Nova integração") : null),
    body));

  async function load() {
    let list;
    try {
      list = await get("/integrations");
    } catch (ex) {
      clear(body, h("div", { class: "notice error" }, ex.message));
      return;
    }
    if (!list.length) {
      clear(body, h("section", { class: "card" }, empty("Nenhuma integração ainda",
        "Conecte um canal do Slack, Discord, Teams ou Google Chat para receber avisos de falha, ou crie um gatilho por URL para disparar workflows a partir do GitHub Actions, GitLab CI ou de qualquer sistema.",
        admin ? h("button", { class: "btn", onclick: () => integrationDialog(null, load) }, icon("plus", 15), "Nova integração") : null)));
      return;
    }
    const outbound = list.filter((i) => i.kind !== "trigger");
    const inbound = list.filter((i) => i.kind === "trigger");
    clear(body, h("div", { class: "stack lg" },
      outbound.length ? h("div", { class: "stack" }, h("h2", {}, "Notificações"), h("div", { class: "integ-grid" }, outbound.map((i) => card(i)))) : null,
      inbound.length ? h("div", { class: "stack" }, h("h2", {}, "Gatilhos de entrada"), h("div", { class: "integ-grid" }, inbound.map((i) => card(i)))) : null,
      !admin ? h("p", { class: "small muted" }, "Só administradores criam, alteram e testam integrações.") : null));
  }

  function card(i) {
    const k = KINDS[i.kind];
    const sw = h("button", {
      class: "switch", role: "switch", "aria-checked": String(i.is_active), "aria-label": i.is_active ? "Desativar" : "Ativar",
      disabled: !admin,
      onclick: async () => {
        sw.disabled = true;
        try {
          await api("PATCH", `/integrations/${i.id}`, { is_active: !i.is_active });
          toast(i.is_active ? `${i.name} desativada` : `${i.name} ativada`);
          load();
        } catch (ex) { toast(ex.message, "error"); sw.disabled = false; }
      },
    });
    const destination = i.kind === "trigger"
      ? h("div", { class: "small" }, "Dispara ", h("a", { class: "mono strong", href: `#/workflows/${i.workflow}` }, i.workflow), h("div", { class: "tiny muted mono" }, `POST ${i.hook_path}`))
      : h("div", { class: "tiny muted mono ellipsis", title: "O restante da URL fica oculto" }, i.url);
    const lastWord = i.last_status === "sent" ? (i.kind === "trigger" ? "chamada recebida" : "entregue") : i.last_status === "error" ? "falhou" : null;
    return h("article", { class: `integ${i.is_active ? "" : " off"}` },
      h("div", { class: "integ-head" },
        h("span", { class: "integ-logo" }, icon(k.ic, 20)),
        h("div", { class: "grow" }, h("h3", { class: "ellipsis" }, i.name), h("span", { class: "tiny muted" }, k.label)),
        sw),
      destination,
      i.kind !== "trigger" ? h("div", { class: "events" },
        i.events.map((e) => h("span", { class: "tag" }, EVENTS[e] || e)),
        h("span", { class: "tag", title: i.workflows.join(", ") }, i.workflows.length ? `${i.workflows.length} workflow(s)` : "Todos os workflows")) : null,
      h("div", { class: "foot" },
        i.kind !== "trigger"
          ? h("span", { class: "num" }, `${i.stats.sent} entregue(s)`, i.stats.failed ? h("span", { style: { color: "var(--danger)" } }, ` · ${i.stats.failed} com falha`) : null, i.stats.pending ? ` · ${i.stats.pending} na fila` : "")
          : h("span", {}, i.last_delivery_at ? `Última chamada ${relative(i.last_delivery_at)}` : "Nunca chamado"),
        h("span", { class: "spacer" }),
        lastWord && i.kind !== "trigger" ? h("span", { class: `status-word ${i.last_status}`, title: dateTime(i.last_delivery_at, true) }, `${lastWord} ${relative(i.last_delivery_at)}`) : null),
      h("div", { class: "row tight wrap" },
        i.kind !== "trigger" ? h("button", { class: "btn ghost sm", onclick: () => deliveriesDrawer(i) }, icon("clock", 14), "Entregas") : null,
        admin && i.kind !== "trigger" ? h("button", { class: "btn ghost sm", onclick: async (e) => {
          const b = e.currentTarget;
          b.disabled = true;
          try {
            const r = await post(`/integrations/${i.id}/test`);
            r.status === "sent" ? toast(`Teste entregue (HTTP ${r.response_code})`) : toast(`Teste falhou: ${r.error}`, "error");
            load();
          } catch (ex) { toast(ex.message, "error"); } finally { b.disabled = false; }
        } }, icon("send", 14), "Enviar teste") : null,
        h("span", { class: "spacer" }),
        admin ? h("button", { class: "icon-btn", title: "Editar", "aria-label": `Editar ${i.name}`, onclick: () => integrationDialog(i, load) }, icon("edit", 16)) : null,
        admin ? h("button", { class: "icon-btn", title: "Excluir", "aria-label": `Excluir ${i.name}`, onclick: async () => {
          if (!(await confirmDialog({ title: "Excluir integração?", text: `“${i.name}” para de receber avisos${i.kind === "trigger" ? " e a URL deixa de funcionar na hora" : ""}. O histórico de entregas também é apagado.`, confirm: "Excluir", danger: true }))) return;
          try { await api("DELETE", `/integrations/${i.id}`); toast("Integração excluída"); load(); } catch (ex) { toast(ex.message, "error"); }
        } }, icon("trash", 16)) : null));
  }
  load();
}

function deliveriesDrawer(i) {
  drawer(async (panel, close) => {
    const list = h("div", {}, h("div", { class: "skeleton", style: { height: "240px" } }));
    panel.append(
      h("div", { class: "drawer-head" }, h("div", { class: "stack sm grow" }, h("span", { class: "tiny muted" }, KINDS[i.kind].label), h("h2", {}, i.name)),
        h("button", { class: "icon-btn", "aria-label": "Fechar", onclick: close }, icon("close", 18))),
      h("div", { class: "drawer-body stack" },
        h("p", { class: "small muted" }, "As 50 entregas mais recentes. Quando o destino recusa, o Delco tenta de novo até 5 vezes (10 s, 20 s, 40 s e 80 s depois)."),
        list));
    try {
      const rows = await get(`/integrations/${i.id}/deliveries`);
      clear(list, rows.length ? h("div", { class: "card" }, h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Evento"), h("th", {}, "Execução"), h("th", {}, "Resultado"), h("th", { class: "right" }, "Quando"))),
        h("tbody", {}, rows.map((d) => h("tr", {},
          h("td", { class: "small" }, EVENT_LONG[d.event] || d.event),
          h("td", { class: "small" }, d.run_id ? h("a", { href: `#/runs/${d.run_id}`, onclick: close, class: "strong" }, `#${d.run_id}`) : "—", d.workflow ? h("div", { class: "tiny muted mono" }, d.workflow) : null),
          h("td", {}, h("span", { class: `status-word ${d.status}` }, { sent: "Entregue", failed: "Falhou", pending: "Na fila" }[d.status]),
            h("div", { class: "tiny muted" }, [d.response_code ? `HTTP ${d.response_code}` : null, `${d.attempts} tentativa(s)`, d.next_attempt_at ? `próxima ${relative(d.next_attempt_at)}` : null].filter(Boolean).join(" · ")),
            d.error ? h("div", { class: "tiny", style: { color: "var(--danger)", maxWidth: "240px" }, title: d.error }, d.error.length > 80 ? `${d.error.slice(0, 79)}…` : d.error) : null),
          h("td", { class: "right small muted nowrap", title: dateTime(d.created_at, true) }, relative(d.created_at)))))))) : empty("Nenhuma entrega ainda", "As mensagens aparecem aqui quando um evento escolhido acontecer. Use Enviar teste para conferir agora."));
    } catch (ex) {
      clear(list, h("div", { class: "notice error" }, ex.message));
    }
  });
}

function field(id, label, input, hint) {
  return h("div", { class: "field" }, h("label", { for: id }, label), input, hint ? h("span", { class: "hint" }, hint) : null, h("span", { class: "field-error", "data-for": id }));
}

async function integrationDialog(current, onSaved) {
  let workflows = [];
  try { workflows = (await get("/workflows")).filter((w) => w.is_active); } catch { /* segue sem a lista */ }
  modal((box, close) => {
    let kind = current?.kind || "slack";
    const name = h("input", { class: "input", id: "name", maxlength: "120", value: current?.name || "", placeholder: "Ex.: Alertas do time de dados" });
    const url = h("input", { class: "input", id: "url", type: "url", value: "", placeholder: "" });
    const evs = Object.entries(EVENT_LONG).filter(([e]) => e !== "test").map(([e, label]) => {
      const cb = h("input", { type: "checkbox", value: e, checked: current ? current.events.includes(e) : ["run.failed"].includes(e) });
      return { e, cb, el: h("label", { class: "check" }, cb, label) };
    });
    const wfChecks = workflows.map((w) => {
      const cb = h("input", { type: "checkbox", value: w.name, checked: current?.workflows?.includes(w.name) });
      return { cb, el: h("label", { class: "check mono", style: { fontSize: "12.5px" } }, cb, w.name) };
    });
    const target = h("select", { class: "select", id: "workflow" }, workflows.map((w) => h("option", { value: w.name, selected: current?.workflow === w.name }, w.name)));
    const kindPick = h("div", { class: "kind-pick", role: "group", "aria-label": "Tipo" });
    const dynamic = h("div", { class: "stack" });
    const errSlot = h("span", { class: "field-error", "data-for": "kind" });
    function renderKinds() {
      clear(kindPick, Object.entries(KINDS).map(([key, k]) => h("button", { type: "button", "aria-pressed": String(kind === key), disabled: !!current && key !== kind,
        onclick: () => { kind = key; renderKinds(); renderDynamic(); } }, icon(k.ic, 18), k.label, h("span", {}, k.hint))));
    }
    function renderDynamic() {
      const k = KINDS[kind];
      url.placeholder = k.ph;
      if (kind === "trigger") {
        clear(dynamic, field("workflow", "Workflow que a URL dispara", target, "A chamada aceita um JSON opcional: {\"params\": {...}, \"note\": \"...\"}. A URL aparece uma única vez, logo depois de criar."));
      } else {
        clear(dynamic,
          field("url", current ? "Nova URL do webhook (deixe em branco para manter)" : "URL do webhook", url,
            kind === "webhook" ? "Cada envio leva o cabeçalho X-Delco-Signature (HMAC-SHA256 do corpo com o segredo da integração)." : "Copie a URL nas configurações do canal. Ela funciona como uma senha: quem tiver a URL consegue postar no canal."),
          h("div", { class: "field" }, h("span", { class: "label" }, "Avisar quando"), h("div", { class: "checks" }, evs.map((x) => x.el)), h("span", { class: "field-error", "data-for": "events" })),
          h("details", { open: !!current?.workflows?.length }, h("summary", { class: "small strong", style: { cursor: "pointer" } }, "Só alguns workflows (opcional)"),
            h("div", { class: "stack sm", style: { marginTop: "10px" } }, h("span", { class: "hint" }, "Sem nenhum marcado, vale para todos."), h("div", { class: "checks" }, wfChecks.map((x) => x.el)))));
      }
    }
    const save = h("button", { class: "btn", type: "submit" }, current ? "Salvar" : "Criar integração");
    const form = h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      form.querySelectorAll(".field-error").forEach((el) => (el.textContent = ""));
      form.querySelectorAll("[aria-invalid]").forEach((el) => el.removeAttribute("aria-invalid"));
      const payload = { name: name.value };
      if (!current) payload.kind = kind;
      if (kind === "trigger") payload.workflow = target.value;
      else {
        if (!current || url.value.trim()) payload.url = url.value.trim();
        payload.events = evs.filter((x) => x.cb.checked).map((x) => x.e);
        payload.workflows = wfChecks.filter((x) => x.cb.checked).map((x) => x.cb.value);
      }
      save.disabled = true;
      try {
        const r = current ? await api("PATCH", `/integrations/${current.id}`, payload) : await post("/integrations", payload);
        toast(current ? "Integração salva" : "Integração criada");
        close();
        onSaved();
        if (r.hook_url) showHookUrl(r);
      } catch (ex) {
        let shown = false;
        for (const [k, msg] of Object.entries(ex.fields || {})) {
          const slot = form.querySelector(`.field-error[data-for="${k}"]`);
          if (slot) { slot.textContent = msg; form.querySelector(`#${k}`)?.setAttribute("aria-invalid", "true"); shown = true; }
        }
        if (!shown) toast(ex.message, "error");
      } finally {
        save.disabled = false;
      }
    } },
    h("div", {}, h("h2", {}, current ? "Editar integração" : "Nova integração"),
      h("p", { class: "muted small" }, current ? KINDS[current.kind].label : "Escolha para onde o Delco manda os avisos, ou crie uma URL para disparar um workflow.")),
    current ? null : kindPick, errSlot,
    field("name", "Nome", name),
    dynamic,
    h("div", { class: "actions" }, h("button", { class: "btn ghost", type: "button", onclick: close }, "Cancelar"), save));
    renderKinds();
    renderDynamic();
    box.append(form);
  }, { wide: true });
}

function showHookUrl(r) {
  const curl = `curl -X POST ${r.hook_url} \\\n  -H 'Content-Type: application/json' \\\n  -d '{"params": {}, "note": "Disparado pelo CI"}'`;
  modal((box, close) => {
    box.append(
      h("h2", {}, "Copie a URL agora"),
      h("p", { class: "muted small" }, `Ela dispara ${r.workflow} e não aparece de novo. Guarde num segredo do CI (por exemplo, DELCO_HOOK_URL).`),
      h("div", { class: "stack", style: { marginTop: "16px" } },
        h("div", { class: "copy-line" }, h("code", {}, r.hook_url), copyButton(r.hook_url)),
        h("span", { class: "small muted" }, "Exemplo:"),
        h("pre", { class: "json" }, curl)),
      h("div", { class: "actions" }, h("button", { class: "btn", onclick: close }, "Pronto")));
  }, { wide: true });
}
