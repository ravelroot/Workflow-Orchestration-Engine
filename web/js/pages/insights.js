// Desempenho: saúde de cada workflow no período, tarefas que mais falham e as mais lentas.
import { get } from "../api.js";
import { clear, debounce, duration, empty, h, int, rateClass } from "../ui.js";
import { dailySpark } from "../viz.js";

export function insightsPage(main, ctx) {
  let days = Number(ctx.query.get("days")) === 30 ? 30 : 7;
  const seg = h("div", { class: "segmented", role: "group", "aria-label": "Período" });
  const body = h("div", { class: "stack lg" }, h("div", { class: "skeleton", style: { height: "96px" } }), h("div", { class: "skeleton", style: { height: "360px" } }));
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", { class: "stack" }, h("h1", {}, "Desempenho"),
        h("p", { class: "muted" }, "Como cada workflow se comportou no período: volume, taxa de sucesso, duração típica (p50) e nos piores casos (p95).")),
      seg),
    body));

  function renderSeg() {
    clear(seg, [[7, "7 dias"], [30, "30 dias"]].map(([d, label]) => h("button", {
      "aria-pressed": String(days === d),
      onclick: () => { days = d; history.replaceState(null, "", `#/insights?days=${d}`); renderSeg(); load(); },
    }, label)));
  }

  async function load() {
    let data;
    try {
      data = await get(`/insights?days=${days}`);
    } catch (ex) {
      clear(body, h("div", { class: "notice error" }, ex.message));
      return;
    }
    const t = data.totals;
    const kpi = (label, value, sub, cls = "") => h("div", { class: `kpi ${cls}` }, h("span", { class: "label" }, label), h("strong", {}, value), h("span", { class: "sub" }, sub));
    const rate = (r) => h("span", { class: `rate ${rateClass(r)}` }, r === null ? "—" : `${String(r).replace(".", ",")}%`);
    const maxRuns = Math.max(1, ...data.workflows.map((w) => w.runs));
    clear(body,
      h("div", { class: "kpis" },
        kpi("Execuções terminadas", int(t.runs), `${int(t.succeeded)} com sucesso · ${int(t.failed)} com falha`),
        kpi("Taxa de sucesso", t.success_rate === null ? "—" : `${String(t.success_rate).replace(".", ",")}%`, "sucesso ÷ (sucesso + falha)", rateClass(t.success_rate) === "good" ? "good" : rateClass(t.success_rate) === "bad" ? "bad" : ""),
        kpi("Tentativas com erro", int(t.failed_attempts), "inclui as que deram certo depois", t.failed_attempts ? "" : "good"),
        kpi("Tempo de processamento", duration(t.processing_ms), "soma da duração das tentativas")),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Por workflow"), h("p", { class: "card-sub" }, `Últimos ${days} dias · as barras mostram as execuções de cada dia (verde: sucesso, vermelho: falha)`))),
        data.workflows.length ? h("div", { class: "table-wrap", style: { paddingTop: "12px" } }, h("table", { class: "table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Workflow"), h("th", {}, "Volume"), h("th", { class: "right" }, "Sucesso"),
            h("th", { class: "right" }, "p50"), h("th", { class: "right" }, "p95"), h("th", { style: { width: "170px" } }, "Por dia"))),
          h("tbody", {}, data.workflows.map((w) => h("tr", { class: "link", onclick: () => (location.hash = `#/workflows/${w.workflow}`) },
            h("td", {}, h("span", { class: "wf-name" }, w.workflow)),
            h("td", {}, h("div", { class: "meter" }, h("div", { class: "track" }, h("div", { class: "fill", style: { width: `${(100 * w.runs) / maxRuns}%` } })),
              h("span", { class: "small num nowrap" }, `${int(w.runs)}${w.failed ? ` · ${w.failed} falha${w.failed > 1 ? "s" : ""}` : ""}`))),
            h("td", { class: "right" }, rate(w.success_rate)),
            h("td", { class: "right num small nowrap" }, duration(w.p50_ms)),
            h("td", { class: "right num small nowrap" }, duration(w.p95_ms)),
            h("td", {}, dailySpark(w.daily))))))) : empty("Nada para medir ainda", "Os números aparecem quando as primeiras execuções terminarem.")),
      h("div", { class: "grid-2", style: { alignItems: "start" } },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Tarefas que mais falham"), h("p", { class: "card-sub" }, "Tentativas com erro, tempo esgotado ou worker perdido"))),
          h("div", { style: { paddingTop: "10px" } }, data.failing_tasks.length ? h("div", { class: "rank" }, data.failing_tasks.map((f) => h("a", { class: "rank-item", href: f.last_run_id ? `#/runs/${f.last_run_id}?task=${encodeURIComponent(f.task)}` : `#/workflows/${f.workflow}` },
            h("div", { class: "stack sm", style: { gap: "1px", minWidth: 0 } }, h("span", { class: "mono strong ellipsis" }, f.task), h("span", { class: "tiny muted" }, f.workflow)),
            h("span", { class: "small num strong", style: { color: "var(--danger)" } }, `${f.failures} falha${f.failures > 1 ? "s" : ""}`),
            f.last_error ? h("span", { class: "err", title: f.last_error }, f.last_error) : null))) : empty("Nenhuma falha no período", "Todas as tentativas terminaram bem."))),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Tarefas mais lentas"), h("p", { class: "card-sub" }, "Pelo p95 das tentativas com sucesso"))),
          h("div", { style: { paddingTop: "10px" } }, data.slowest_tasks.length ? h("div", { class: "rank" }, data.slowest_tasks.map((s) => h("a", { class: "rank-item", href: `#/workflows/${s.workflow}?tab=tasks` },
            h("div", { class: "stack sm", style: { gap: "1px", minWidth: 0 } }, h("span", { class: "mono strong ellipsis" }, s.task), h("span", { class: "tiny muted" }, `${s.workflow} · ${int(s.count)} tentativas · média ${duration(s.avg_ms)}`)),
            h("span", { class: "small num strong" }, duration(s.p95_ms))))) : empty("Sem dados de duração", "Rode algum workflow para ver as tarefas mais demoradas.")))));
  }
  renderSeg();
  load();
  const refresh = debounce(load, 3000);
  ctx.live((e) => e.type === "run" && ["succeeded", "failed", "cancelled"].includes(e.status) && refresh());
}
