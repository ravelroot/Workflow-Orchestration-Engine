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
  const body = h("div", { class: "stack lg" }, h("div", { class: "skeleton", style: { height: "96px" } }), h("div", { class: "skeleton", style: { height: "320px" } }));
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", { class: "stack" }, h("h1", {}, "Visão geral"), h("p", { class: "muted" }, "Estado do motor agora e nas últimas 24 horas.")),
      can("operator") ? h("button", { class: "btn accent", onclick: () => triggerDialog() }, icon("play", 15), "Executar workflow") : null),
    body));

  async function load() {
    let data, workers;
    try {
      [data, workers] = await Promise.all([get("/overview"), get("/workers")]);
    } catch (ex) {
      clear(body, h("div", { class: "notice error" }, ex.message));
      return;
    }
    const k = data.kpis;
    const kpi = (label, value, sub, cls = "") => h("div", { class: `kpi ${cls}` }, h("span", { class: "label" }, label), h("strong", {}, value), h("span", { class: "sub" }, sub));
    const online = workers.filter((w) => w.status === "online");
    clear(body,
      h("div", { class: "kpis" },
        kpi("Execuções ativas", int(k.active_runs), `${int(k.queued_runs)} aguardando vaga`),
        kpi("Tarefas rodando", h("span", {}, int(k.running_tasks), h("small", {}, `/ ${int(k.slots_total)}`)), `${int(k.workers_online)} worker(s) online`),
        kpi("Tarefas na fila", int(k.queued_tasks), `${int(k.retry_tasks)} esperando nova tentativa`),
        kpi("Sucesso em 24 h", int(k.runs_succeeded_24h), "execuções concluídas"),
        kpi("Falhas em 24 h", int(k.runs_failed_24h), `${int(k.failed_attempts_24h)} tentativas com erro`, k.runs_failed_24h ? "bad" : ""),
        kpi("Duração média", duration(k.avg_task_ms_24h), "por tarefa, em 24 h")),
      h("div", { class: "grid-main" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Tarefas por hora"), h("p", { class: "card-sub" }, "Tentativas concluídas nas últimas 24 horas")),
            h("div", { class: "legend" }, h("span", {}, h("span", { class: "sq succeeded" }), "Sucesso"), h("span", {}, h("span", { class: "sq failed" }), "Falha"))),
          h("div", { class: "card-body" }, throughputChart(data.throughput))),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Workers"), h("p", { class: "card-sub" }, `${online.length} online · ${online.reduce((a, w) => a + w.concurrency, 0)} vagas`)),
            h("a", { class: "btn ghost sm", href: "#/workers" }, "Ver todos")),
          h("div", { class: "card-body stack" },
            online.length ? online.map((w) => h("div", { class: "stack sm" },
              h("div", { class: "row" }, h("span", { class: "dot", style: { color: "var(--ok)" } }), h("span", { class: "mono strong" }, w.id), h("span", { class: "spacer" }),
                h("span", { class: "tiny muted num" }, `${w.running.length}/${w.concurrency} ocupadas`)),
              progressBar([{ cls: "run", value: w.running.length }, { cls: "", value: w.concurrency - w.running.length }]),
              h("span", { class: "tiny muted" }, w.running.length ? w.running.map((r) => `${r.workflow}.${r.task_name}`).join(" · ") : "Livre"),
            )) : h("p", { class: "muted small" }, "Nenhum worker online. Suba um com `delco worker` ou `docker compose up -d worker`.")))),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "Execuções recentes"), h("a", { class: "btn ghost sm", href: "#/runs" }, "Ver todas")),
        h("div", { class: "card-body", style: { padding: "8px 0 0" } }, runsTable(data.recent_runs))));
  }
  load();
  const refresh = debounce(load, 1200);
  ctx.live((e) => ["run", "task", "worker"].includes(e.type) && refresh());
  const timer = setInterval(load, 30000);
  ctx.onCleanup(() => clearInterval(timer));
}
