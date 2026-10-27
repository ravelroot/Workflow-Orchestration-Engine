// Visão geral: indicadores, vazão das últimas 24 h, workers e execuções recentes.
import { can, get } from "../api.js";
import { triggerDialog } from "../app.js";
import { clear, debounce, duration, empty, h, icon, int, pill, progressBar, relative, TRIGGER } from "../ui.js";
import { throughputChart } from "../viz.js";

export function runsTable(runs, { showWorkflow = true } = {}) {
  if (!runs.length) return empty("Nenhuma execução ainda", "Dispare um workflow pelo botão Executar ou aguarde o próximo horário agendado.");
  return h("div", { class: "table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {},
      h("th", {}, "#"), showWorkflow ? h("th", {}, "Workflow") : null, h("th", {}, "Estado"), h("th", {}, "Progresso"),
      h("th", {}, "Origem"), h("th", {}, "Início"), h("th", { class: "right" }, "Duração"))),
    h("tbody", {}, runs.map((r) => h("tr", { class: "link", onclick: () => (location.hash = `#/runs/${r.id}`) },
      h("td", { class: "num muted" }, r.id),
      showWorkflow ? h("td", { class: "strong" }, r.workflow) : null,
      h("td", {}, pill(r.status)),
      h("td", {}, h("div", { class: "progress-line" },
        progressBar([
          { cls: "ok", value: r.tasks_succeeded },
          { cls: "bad", value: r.tasks_failed },
          { cls: "neutral", value: r.tasks_finished - r.tasks_succeeded - r.tasks_failed },
          { cls: "run", value: r.tasks_running },
          { cls: "", value: r.tasks_total - r.tasks_finished - r.tasks_running },
        ]),
        h("span", { class: "tiny muted num nowrap", title: "Tarefas terminadas / total" }, `${r.tasks_finished}/${r.tasks_total}`))),
      h("td", { class: "small" }, TRIGGER[r.trigger] || r.trigger, r.triggered_by ? h("span", { class: "muted" }, ` · ${r.triggered_by.split(" ")[0]}`) : null),
      h("td", { class: "small muted nowrap", title: new Date(r.started_at || r.created_at).toLocaleString("pt-BR") }, relative(r.started_at || r.created_at)),
      h("td", { class: "right num small nowrap" }, duration(r.duration_ms)))))));
}

export function overviewPage(main, ctx) {
  const body = h("div", { class: "stack lg" }, h("div", { class: "skeleton", style: { height: "190px" } }), h("div", { class: "skeleton", style: { height: "320px" } }));
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", { class: "stack" }, h("h1", {}, "Visão geral"), h("p", { class: "muted" }, "O que o motor está fazendo agora, o que vem a seguir e como foram as últimas 24 horas.")),
      can("operator") ? h("button", { class: "btn accent", onclick: () => triggerDialog() }, icon("play", 15), "Executar workflow") : null),
    body));

  async function load() {
    let data, workers, upcoming;
    try {
      [data, workers, upcoming] = await Promise.all([get("/overview"), get("/workers"), get("/upcoming?hours=24").catch(() => [])]);
    } catch (ex) {
      clear(body, h("div", { class: "notice error" }, ex.message));
      return;
    }
    const k = data.kpis;
    const online = workers.filter((w) => w.status === "online");
    const stat = (value, label, cls = "", extra = null) => h("div", { class: `console-stat ${cls}` }, h("strong", {}, value, extra), h("span", {}, label));
    const done24 = k.runs_succeeded_24h + k.runs_failed_24h;
    const rate = done24 ? (100 * k.runs_succeeded_24h) / done24 : null;
    const kpi = (label, value, sub, cls = "") => h("div", { class: `kpi ${cls}` }, h("span", { class: "label" }, label), h("strong", {}, value), h("span", { class: "sub" }, sub));
    clear(body,
      h("section", { class: "console", "aria-label": "Agora" },
        h("div", {}, h("h2", {}, "Agora"), h("p", { class: "card-sub" }, online.length
          ? `${online.length} worker(s) online · cada lâmpada é uma vaga; acesa quando uma tarefa está rodando`
          : "Nenhum worker online. Suba um com delco worker ou docker compose up -d worker.")),
        h("div", { class: "console-stats" },
          stat(int(k.active_runs), "execuções rodando", k.active_runs ? "lit" : ""),
          stat(int(k.running_tasks), "vagas ocupadas", k.running_tasks ? "lit" : "", h("small", {}, `/ ${int(k.slots_total)}`)),
          stat(int(k.queued_tasks), "tarefas na fila"),
          stat(int(k.retry_tasks), "esperando nova tentativa", k.retry_tasks ? "alarm" : "")),
        h("div", { class: "lamp-board" }, online.length ? online.map((w) => h("div", { class: "lamp-row" },
          h("span", { class: "who", title: `${w.id} · filas ${w.queues.join(", ")}` }, h("span", { class: "dot" }), w.id),
          h("div", { class: "lamps" },
            w.running.map((r) => h("a", { class: "lamp on", href: `#/runs/${r.run_id}?task=${encodeURIComponent(r.task_name)}`, title: `${r.workflow} #${r.run_id} · ${r.task_name}` }, h("span", {}, r.task_name))),
            Array.from({ length: Math.max(0, w.concurrency - w.running.length) }, () => h("span", { class: "lamp free" }, h("span", {}, "livre")))),
          h("span", { class: "busy" }, `${w.running.length}/${w.concurrency}`))) : h("p", { class: "empty-line" }, "As vagas aparecem aqui assim que um worker se conectar."))),
      h("div", { class: "kpis" },
        kpi("Concluídas em 24 h", int(k.runs_succeeded_24h), rate === null ? "nenhuma execução terminou" : `${Math.round(rate)}% de sucesso`, "good"),
        kpi("Falhas em 24 h", int(k.runs_failed_24h), `${int(k.failed_attempts_24h)} tentativas com erro`, k.runs_failed_24h ? "bad" : ""),
        kpi("Aguardando vaga", int(k.queued_runs), "execuções na fila do workflow"),
        kpi("Duração média", duration(k.avg_task_ms_24h), "por tarefa, em 24 h")),
      h("div", { class: "grid-main" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Tarefas por hora"), h("p", { class: "card-sub" }, "Tentativas concluídas nas últimas 24 horas")),
            h("div", { class: "legend" }, h("span", {}, h("span", { class: "sq succeeded" }), "Sucesso"), h("span", {}, h("span", { class: "sq failed" }), "Falha"))),
          h("div", { class: "card-body" }, throughputChart(data.throughput))),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Próximas execuções"), h("p", { class: "card-sub" }, "Agendadas para as próximas 24 horas")),
            h("a", { class: "btn ghost sm", href: "#/workflows" }, icon("calendar", 14), "Agendas")),
          h("div", { style: { paddingTop: "8px" } }, upcomingList(upcoming)))),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Execuções recentes"), h("a", { class: "btn ghost sm", href: "#/runs" }, "Ver todas")),
        h("div", { class: "card-body", style: { padding: "12px 0 0" } }, runsTable(data.recent_runs))));
  }
  load();
  const refresh = debounce(load, 1200);
  ctx.live((e) => ["run", "task", "worker"].includes(e.type) && refresh());
  const timer = setInterval(load, 30000);
  ctx.onCleanup(() => clearInterval(timer));
}

function upcomingList(items) {
  if (!items.length) return h("p", { class: "muted small", style: { padding: "4px 20px 20px" } }, "Nenhuma execução agendada nas próximas 24 horas. Workflows pausados ou sem agenda não aparecem aqui.");
  // agrupa horários repetidos do mesmo workflow para a lista não virar um relógio de 15 em 15 minutos
  const seen = new Map();
  const rows = [];
  for (const it of items) {
    const n = seen.get(it.workflow) || 0;
    seen.set(it.workflow, n + 1);
    if (n < 2) rows.push(it);
  }
  const extra = [...seen.entries()].filter(([, n]) => n > 2);
  return h("div", { class: "upcoming" },
    rows.slice(0, 7).map((it) => h("a", { class: "up-item", href: `#/workflows/${it.workflow}` },
      h("time", { datetime: it.at }, new Date(it.at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })),
      h("div", { class: "stack sm", style: { gap: "0" } }, h("span", { class: "wf-name" }, it.workflow), h("span", { class: "tiny muted" }, it.schedule_text)),
      h("span", { class: "rel" }, relative(it.at)))),
    extra.length ? h("p", { class: "tiny muted", style: { padding: "8px 20px 16px" } }, extra.map(([wf, n]) => `${wf}: ${n} horários`).join(" · ")) : null);
}
