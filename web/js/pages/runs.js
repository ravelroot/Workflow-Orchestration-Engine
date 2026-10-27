// Execuções: lista com filtros e página da execução com grafo ao vivo, linha do tempo e logs.
import { can, download, get, live, post } from "../api.js";
import { navigate, triggerDialog } from "../app.js";
import {
  ATTEMPT, clear, confirmDialog, dateTime, debounce, downloadText, drawer, duration, empty, h, icon, pill,
  progressBar, relative, STATUS, time, toast, TRIGGER,
} from "../ui.js";
import { dagView, gantt } from "../viz.js";
import { runsTable } from "./overview.js";

const FILTERS = [["", "Todas"], ["running", "Rodando"], ["queued", "Na fila"], ["succeeded", "Sucesso"], ["failed", "Falhou"], ["cancelled", "Cancelada"]];

export function runsPage(main, ctx) {
  let status = ctx.query.get("status") || "";
  let workflow = ctx.query.get("workflow") || "";
  let trigger = ctx.query.get("trigger") || "";
  let since = ctx.query.get("since") || "";
  let page = 1;
  const chips = h("div", { class: "chips" });
  const select = h("select", { class: "select auto", "aria-label": "Workflow" }, h("option", { value: "" }, "Todos os workflows"));
  const origin = h("select", { class: "select auto", "aria-label": "Origem" }, h("option", { value: "" }, "Qualquer origem"),
    Object.entries(TRIGGER).map(([v, l]) => h("option", { value: v, selected: v === trigger }, l)));
  const period = h("select", { class: "select auto", "aria-label": "Período" },
    [["", "Todo o histórico"], ["1", "Últimas 24 h"], ["7", "Últimos 7 dias"], ["30", "Últimos 30 dias"]].map(([v, l]) => h("option", { value: v, selected: v === since }, l)));
  const search = h("input", { class: "input", type: "search", placeholder: "Número, workflow ou observação", style: { width: "290px" }, "aria-label": "Buscar execução" });
  const exportBtn = h("button", { class: "btn ghost", onclick: async () => {
    exportBtn.disabled = true;
    try {
      await download(`/runs/export?${filters()}`, `execucoes-delco-${new Date().toISOString().slice(0, 10)}.csv`);
      toast("CSV exportado");
    } catch (ex) {
      toast(ex.message, "error");
    } finally {
      exportBtn.disabled = false;
    }
  } }, icon("download", 15), "Exportar CSV");
  const body = h("section", { class: "card" }, h("div", { class: "skeleton", style: { height: "360px" } }));
  const pager = h("div", { class: "row", style: { justifyContent: "space-between" } });
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", { class: "stack" }, h("h1", {}, "Execuções"), h("p", { class: "muted" }, "Todas as execuções, da mais recente para a mais antiga. Filtre, busque pelo número e exporte o resultado.")),
      h("div", { class: "row" }, exportBtn, can("operator") ? h("button", { class: "btn accent", onclick: () => triggerDialog() }, icon("play", 15), "Executar workflow") : null)),
    h("div", { class: "toolbar" }, chips),
    h("div", { class: "toolbar" }, h("div", { class: "search" }, icon("search", 15), search), select, origin, period),
    h("div", { class: "stack" }, body, pager)));

  function filters() {
    const q = new URLSearchParams();
    if (status) q.set("status", status);
    if (workflow) q.set("workflow", workflow);
    if (trigger) q.set("trigger", trigger);
    if (since) q.set("since", since);
    if (search.value.trim()) q.set("q", search.value.trim());
    return q;
  }

  function renderChips() {
    clear(chips, FILTERS.map(([value, label]) => h("button", {
      class: "chip", "aria-pressed": String(status === value),
      onclick: () => { status = value; page = 1; renderChips(); load(); },
    }, value ? h("span", { class: `sq ${value}` }) : null, label)));
  }

  async function load() {
    const q = filters();
    q.set("page", page);
    q.set("size", 25);
    try {
      const data = await get(`/runs?${q}`);
      const filtered = status || workflow || trigger || since || search.value.trim();
      clear(body, data.items.length ? h("div", {}, runsTable(data.items)) : empty(
        filtered ? "Nenhuma execução com esses filtros" : "Nenhuma execução ainda",
        filtered ? "Tire algum filtro ou amplie o período." : "Dispare um workflow pelo botão Executar ou aguarde o próximo horário agendado.",
        filtered ? h("button", { class: "btn ghost sm", onclick: () => {
          status = workflow = trigger = since = ""; search.value = ""; select.value = origin.value = period.value = ""; page = 1; renderChips(); load();
        } }, "Limpar filtros") : null));
      const pages = Math.max(1, Math.ceil(data.total / data.size));
      clear(pager,
        h("span", { class: "small muted" }, `${data.total} execuções · página ${data.page} de ${pages}`),
        h("div", { class: "row tight" },
          h("button", { class: "btn ghost sm", disabled: page <= 1, onclick: () => { page--; load(); } }, "Anterior"),
          h("button", { class: "btn ghost sm", disabled: page >= pages, onclick: () => { page++; load(); } }, "Próxima")));
    } catch (ex) {
      clear(body, h("div", { class: "notice error" }, ex.message));
    }
  }
  get("/workflows").then((list) => {
    list.forEach((w) => select.append(h("option", { value: w.name, selected: w.name === workflow }, w.name)));
  });
  select.addEventListener("change", () => { workflow = select.value; page = 1; load(); });
  origin.addEventListener("change", () => { trigger = origin.value; page = 1; load(); });
  period.addEventListener("change", () => { since = period.value; page = 1; load(); });
  search.addEventListener("input", debounce(() => { page = 1; load(); }, 300));
  renderChips();
  load();
  const refresh = debounce(() => page === 1 && load(), 1000);
  ctx.live((e) => (e.type === "run" || (e.type === "task" && e.status !== "running")) && refresh());
}

// -------------------------------------------------------------------- execução
export function runPage(main, ctx) {
  const id = Number(ctx.params[0]);
  const head = h("div", { class: "skeleton", style: { height: "90px" } });
  const facts = h("div");
  const graphBody = h("div", { class: "card-body" });
  const ganttBody = h("div", { class: "card-body" });
  const tasksBody = h("div");
  const paramsBody = h("div", { class: "card-body" });
  const summary = h("span", { class: "card-sub" });
  main.append(h("div", { class: "page" },
    h("a", { class: "back", href: "#/runs" }, icon("back", 15), "Execuções"),
    head,
    h("div", { class: "stack lg mt" },
      facts,
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Grafo"), summary),
        h("span", { class: "tiny muted" }, "Clique numa tarefa para ver log, resultado e tentativas")), graphBody),
      h("div", { class: "grid-main" },
        h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Tarefas")), h("div", { style: { paddingTop: "6px" } }, tasksBody)),
        h("div", { class: "stack" },
          h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Linha do tempo")), ganttBody),
          h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Parâmetros")), paramsBody))))));

  let run;
  let view;
  let selected = ctx.query.get("task");
  let openTask = null;
  let leaving = false;

  const stateMap = () => Object.fromEntries(run.tasks.map((t) => [t.name, { status: t.status, progress: t.progress, attempts: t.attempts, selected: t.name === selected }]));
  const info = (t, s) => {
    const task = run.tasks.find((x) => x.name === t.name);
    if (!task) return "";
    const parts = [STATUS[s.status]];
    if (s.status === "running" && s.progress !== null && s.progress !== undefined) parts.push(`${s.progress}%`);
    else if (task.duration_ms !== null && ["succeeded", "failed"].includes(s.status)) parts.push(duration(task.duration_ms));
    if (task.attempts > 1) parts.push(`${task.attempts}ª tentativa`);
    return parts.join(" · ");
  };

  function renderHead() {
    const active = ["queued", "running"].includes(run.status);
    const finished = !active;
    head.className = "";
    head.removeAttribute("style");
    clear(head, h("div", { class: "page-head", style: { marginBottom: 0 } },
      h("div", { class: "stack" },
        h("div", { class: "row tight" }, h("h1", {}, `Execução #${run.id}`), pill(run.status)),
        h("p", { class: "muted" }, h("a", { href: `#/workflows/${run.workflow}`, class: "strong", style: { color: "var(--ink)" } }, run.workflow), ` · versão ${run.version}`, run.note ? ` · “${run.note}”` : "")),
      can("operator") ? h("div", { class: "row" },
        active ? h("button", { class: "btn danger", onclick: async () => {
          if (!(await confirmDialog({ title: "Cancelar execução?", text: "Tarefas na fila são canceladas e as que estão rodando recebem o pedido de parada no próximo heartbeat.", confirm: "Cancelar execução", danger: true }))) return;
          try { await post(`/runs/${id}/cancel`); toast("Cancelamento enviado"); load(); } catch (ex) { toast(ex.message, "error"); }
        } }, icon("stop", 14), "Cancelar") : null,
        finished && run.tasks.some((t) => ["failed", "skipped", "cancelled"].includes(t.status)) ? h("button", { class: "btn", onclick: async () => {
          try { const r = await post(`/runs/${id}/retry`, {}); toast(`Retomando ${r.reset.length} tarefa(s)`); load(); } catch (ex) { toast(ex.message, "error"); }
        } }, icon("retry", 15), "Retomar da falha") : null,
        finished ? h("button", { class: "btn ghost", onclick: () => triggerDialog(run.workflow, run.params, { onDone: (r) => navigate(`/runs/${r.id}`) }) }, icon("play", 14), "Executar de novo") : null) : null));
    const fact = (label, value, title) => h("div", { class: "fact" }, h("span", { class: "label" }, label), h("span", { class: "value", title: title || "" }, value));
    clear(facts, h("div", { class: "facts" },
      fact("Origem", `${TRIGGER[run.trigger] || run.trigger}${run.triggered_by ? ` · ${run.triggered_by}` : ""}`),
      fact(run.logical_date ? "Data lógica" : "Criada", dateTime(run.logical_date || run.created_at, true)),
      fact("Início", run.started_at ? dateTime(run.started_at, true) : "aguardando vaga"),
      fact("Fim", run.finished_at ? dateTime(run.finished_at, true) : "—"),
      fact("Duração", duration(run.duration_ms)),
      fact("Tarefas", `${run.tasks_succeeded} de ${run.tasks_total} concluídas`)));
    const counts = {};
    run.tasks.forEach((t) => (counts[t.status] = (counts[t.status] || 0) + 1));
    clear(summary, Object.entries(counts).map(([s, n]) => `${n} ${STATUS[s].toLowerCase()}`).join(" · "));
  }

  function renderTasks() {
    clear(tasksBody, h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Tarefa"), h("th", {}, "Estado"), h("th", {}, "Tentativas"), h("th", {}, "Início"), h("th", { class: "right" }, "Duração"))),
      h("tbody", {}, run.tasks.map((t) => h("tr", { class: "link", onclick: () => openDrawer(t.name) },
        h("td", {}, h("div", { class: "mono strong", style: { fontSize: "12.5px" } }, t.name),
          t.error && t.status !== "skipped" ? h("div", { class: "tiny", style: { color: "var(--danger)", maxWidth: "360px" }, title: t.error }, t.error.length > 90 ? `${t.error.slice(0, 89)}…` : t.error) : null),
        h("td", {}, t.status === "running" && t.progress !== null ? h("div", { class: "stack sm" }, pill("running", t.cancel_requested ? "Parando…" : `Rodando · ${t.progress}%`), progressBar([{ cls: "run", value: t.progress }, { cls: "", value: 100 - t.progress }])) : pill(t.status)),
        h("td", { class: "num small" }, `${t.attempts}/${t.max_attempts}`),
        h("td", { class: "small muted nowrap" }, t.started_at ? time(t.started_at) : "—"),
        h("td", { class: "right num small nowrap" }, duration(t.duration_ms))))))));
  }

  function render() {
    renderHead();
    if (!view) {
      view = dagView(run.graph.tasks, { onSelect: openDrawer, info });
      clear(graphBody, view.el);
    }
    view.update(stateMap());
    clear(ganttBody, gantt(run, openDrawer));
    renderTasks();
    clear(paramsBody, h("pre", { class: "json" }, JSON.stringify(run.params, null, 2)));
  }

  async function load() {
    try {
      run = await get(`/runs/${id}`);
    } catch (ex) {
      clear(head, h("div", { class: "notice error" }, ex.message));
      return;
    }
    render();
    openTask?.refresh();
  }

  function openDrawer(name) {
    selected = name;
    view?.update(stateMap());
    history.replaceState(null, "", `#/runs/${id}?task=${encodeURIComponent(name)}`);
    openTask?.close();
    openTask = taskDrawer(id, name, () => {
      selected = null;
      openTask = null;
      if (leaving) return;
      view?.update(stateMap());
      history.replaceState(null, "", `#/runs/${id}`);
    }, load);
  }

  load().then(() => selected && run?.tasks.some((t) => t.name === selected) && openDrawer(selected));
  const refresh = debounce(load, 350);
  ctx.live((e) => {
    if (e.type === "task" && e.run_id === id) {
      const t = run?.tasks.find((x) => x.id === e.id);
      if (t && e.status === "running" && t.status === "running" && e.progress !== undefined) {
        t.progress = e.progress; // só progresso: atualiza sem ir ao servidor
        view?.update(stateMap());
        return;
      }
      refresh();
    } else if (e.type === "run" && e.id === id) refresh();
  });
  const ticker = setInterval(() => run && ["running", "queued"].includes(run.status) && renderHead(), 1000);
  ctx.onCleanup(() => { leaving = true; clearInterval(ticker); openTask?.close(); });
}

// ---------------------------------------------------------------- gaveta da tarefa
function taskDrawer(runId, name, onClosed, reloadRun) {
  let task;
  let attemptId = null;
  let followLatest = true;
  let tab = "log";
  let lastLogId = 0;
  const title = h("div", { class: "stack sm grow" }, h("span", { class: "tiny muted" }, `Execução #${runId}`), h("h2", { class: "mono", style: { fontSize: "16px" } }, name));
  const status = h("div");
  const body = h("div", { class: "stack lg" }, h("div", { class: "skeleton", style: { height: "200px" } }));
  const logBox = h("div", { class: "logbox", role: "log", "aria-live": "polite" });
  const allLines = [];
  const levels = new Set(["debug", "info", "warning", "error"]);
  const logSearch = h("input", { class: "input", type: "search", placeholder: "Buscar no log", "aria-label": "Buscar no log" });
  const lvToggle = h("div", { class: "lv-toggle", role: "group", "aria-label": "Níveis" }, ["debug", "info", "warning", "error"].map((lv) =>
    h("button", { "aria-pressed": "true", onclick: (e) => {
      levels.has(lv) ? levels.delete(lv) : levels.add(lv);
      e.currentTarget.setAttribute("aria-pressed", String(levels.has(lv)));
      repaintLogs();
    } }, lv)));
  const logText = () => allLines.map((l) => `${l.at} ${l.level.toUpperCase().padEnd(7)} ${l.message}`).join("\n");
  const logTools = h("div", { class: "log-tools" },
    h("div", { class: "search" }, icon("search", 14), logSearch), lvToggle,
    h("button", { class: "icon-btn bordered", title: "Copiar log", "aria-label": "Copiar log", onclick: async () => {
      try { await navigator.clipboard.writeText(logText()); toast("Log copiado"); } catch { toast("Não foi possível copiar", "error"); }
    } }, icon("copy", 15)),
    h("button", { class: "icon-btn bordered", title: "Baixar log (.txt)", "aria-label": "Baixar log", onclick: () =>
      downloadText(`delco-run${runId}-${name}${attemptId ? `-tentativa${task?.attempts_list.find((a) => a.id === attemptId)?.number ?? ""}` : ""}.txt`, logText()) }, icon("download", 15)));
  logSearch.addEventListener("input", debounce(repaintLogs, 150));
  function lineEl(l) {
    const q = logSearch.value.trim();
    let msg = l.message;
    if (q) {
      const i = msg.toLowerCase().indexOf(q.toLowerCase());
      if (i >= 0) msg = [msg.slice(0, i), h("mark", {}, msg.slice(i, i + q.length)), msg.slice(i + q.length)];
    }
    return h("div", { class: `logline ${l.level}` }, h("span", { class: "t" }, time(l.at)), h("span", { class: "lv" }, l.level), h("span", { class: "m" }, msg));
  }
  const visible = (l) => levels.has(l.level) && (!logSearch.value.trim() || l.message.toLowerCase().includes(logSearch.value.trim().toLowerCase()));
  function repaintLogs() {
    clear(logBox, allLines.filter(visible).map(lineEl));
    if (allLines.length && !logBox.childElementCount) logBox.append(h("div", { class: "logline empty" }, h("span"), h("span"), h("span", { class: "m muted" }, "Nenhuma linha com esse filtro.")));
  }
  let closeFn;
  const unsub = live.on((e) => {
    if (e.type === "log" && e.attempt_id === attemptId) appendLogs(e.lines);
  });

  closeFn = drawer((panel, close, onClose) => {
    onClose(() => { unsub(); live.followLogs(null); onClosed(); });
    panel.append(
      h("div", { class: "drawer-head" }, title, status, h("button", { class: "icon-btn", "aria-label": "Fechar", onclick: close }, icon("close", 18))),
      h("div", { class: "drawer-body" }, body));
  });

  function appendLogs(lines) {
    const atBottom = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 20;
    if (lines.length) logBox.querySelectorAll(".logline.empty").forEach((el) => el.remove());
    for (const l of lines) {
      if (l.id <= lastLogId) continue;
      lastLogId = l.id;
      allLines.push(l);
      if (visible(l)) logBox.append(lineEl(l));
    }
    if (atBottom) logBox.scrollTop = logBox.scrollHeight;
  }

  async function loadLogs() {
    clear(logBox);
    allLines.length = 0;
    lastLogId = 0;
    if (!attemptId) {
      logBox.append(h("div", { class: "logline empty" }, h("span"), h("span"), h("span", { class: "m muted" }, "A tarefa ainda não rodou.")));
      return;
    }
    live.followLogs(attemptId);
    let after = 0;
    for (;;) {
      const page = await get(`/attempts/${attemptId}/logs?after=${after}&limit=2000`);
      appendLogs(page.lines);
      after = lastLogId;
      if (!page.has_more) break;
    }
    if (!lastLogId) logBox.append(h("div", { class: "logline empty" }, h("span"), h("span"), h("span", { class: "m muted" }, "Sem linhas de log nesta tentativa.")));
    logBox.scrollTop = logBox.scrollHeight;
  }

  function render() {
    clear(status, pill(task.status, task.cancel_requested && task.status === "running" ? "Parando…" : undefined));
    const kv = (k, v, t) => h("div", {}, h("span", { class: "k" }, k), h("span", { class: "v", title: t || (typeof v === "string" ? v : "") }, v ?? "—"));
    const attemptSelect = task.attempts_list.length > 1
      ? h("select", { class: "select auto", style: { height: "30px" }, "aria-label": "Tentativa", onchange: (e) => { attemptId = Number(e.target.value); followLatest = attemptId === task.attempts_list[0].id; loadLogs(); } },
        task.attempts_list.map((a) => h("option", { value: a.id, selected: a.id === attemptId }, `Tentativa ${a.number} · ${ATTEMPT[a.status]}`)))
      : null;
    const finished = !["pending", "queued", "running", "retry_wait"].includes(task.status);
    const tabsEl = h("div", { class: "tabs", role: "tablist", style: { marginBottom: "12px" } }, [["log", "Log"], ["output", "Resultado"], ["attempts", `Tentativas (${task.attempts_list.length})`]].map(([k, label]) =>
      h("button", { class: "tab", role: "tab", "aria-selected": String(tab === k), onclick: () => { tab = k; render(); } }, label)));
    let content;
    if (tab === "log") content = h("div", { class: "stack sm" }, h("div", { class: "row tight wrap" }, attemptSelect, h("div", { class: "grow" }, logTools)), logBox);
    else if (tab === "output") {
      content = task.status === "succeeded"
        ? h("pre", { class: "json" }, JSON.stringify(task.output, null, 2))
        : h("p", { class: "muted small" }, "O resultado aparece aqui quando a tarefa termina com sucesso. Ele fica disponível para as tarefas seguintes em ctx.upstream.");
    } else {
      content = h("div", { class: "stack sm" }, task.attempts_list.length ? task.attempts_list.map((a) => h("div", { class: "attempt" },
        h("div", { class: "row" }, h("strong", { class: "small" }, `Tentativa ${a.number}`), pill(a.status === "timed_out" || a.status === "lost" ? "failed" : a.status, ATTEMPT[a.status]),
          h("span", { class: "spacer" }), h("span", { class: "tiny muted mono" }, a.worker_id || "")),
        h("div", { class: "tiny muted" }, `${dateTime(a.started_at, true)} · ${duration(a.duration_ms)} · ${a.log_lines} linha(s) de log`),
        a.error ? h("div", { class: "small", style: { color: "var(--danger)" } }, a.error) : null,
        a.traceback ? h("details", {}, h("summary", { class: "small muted", style: { cursor: "pointer" } }, "Traceback"), h("pre", { class: "trace", style: { marginTop: "6px" } }, a.traceback)) : null))
        : h("p", { class: "muted small" }, "Nenhuma tentativa ainda."));
    }
    clear(body,
      h("dl", { class: "kv", style: { margin: 0 } },
        kv("Tentativas", `${task.attempts} de ${task.max_attempts}`),
        kv("Fila", task.queue),
        kv("Worker", task.worker_id || task.attempts_list[0]?.worker_id || "—"),
        kv("Início", task.started_at ? dateTime(task.started_at, true) : "—"),
        kv("Duração", duration(task.duration_ms)),
        kv("Tempo limite", task.timeout_seconds ? duration(task.timeout_seconds * 1000) : "sem limite"),
        kv("Depende de", task.upstream.length ? task.upstream.join(", ") : "—"),
        kv("Regra", task.trigger_rule),
        kv(task.status === "retry_wait" ? "Nova tentativa" : "Retentativas", task.status === "retry_wait" && task.ready_at ? relative(task.ready_at) : `${task.retries} · espera ${task.retry_delay}s × ${task.retry_backoff}`)),
      task.status === "running" && task.progress !== null ? h("div", { class: "progress-line" }, progressBar([{ cls: "run", value: task.progress }, { cls: "", value: 100 - task.progress }]), h("span", { class: "small num" }, `${task.progress}%`)) : null,
      task.error && task.status !== "succeeded" ? h("div", { class: `notice ${task.status === "skipped" ? "" : "error"}` }, icon("alert", 16), h("div", { class: "small" }, task.error)) : null,
      can("operator") && finished ? h("div", { class: "row" }, h("button", { class: "btn ghost sm", onclick: async () => {
        try {
          const r = await post(`/runs/${runId}/retry`, { from_task: name });
          toast(`Reexecutando ${r.reset.length} tarefa(s) a partir de ${name}`);
          reloadRun();
        } catch (ex) { toast(ex.message, "error"); }
      } }, icon("retry", 14), "Reexecutar a partir desta tarefa")) : null,
      h("div", {}, tabsEl, content));
  }

  async function refresh() {
    try {
      task = await get(`/runs/${runId}/tasks/${encodeURIComponent(name)}`);
    } catch (ex) {
      clear(body, h("div", { class: "notice error" }, ex.message));
      return;
    }
    const latest = task.attempts_list[0]?.id ?? null;
    if (followLatest && latest !== attemptId) {
      attemptId = latest;
      render();
      loadLogs();
    } else {
      render();
    }
  }
  refresh();
  return { close: () => closeFn(), refresh };
}
