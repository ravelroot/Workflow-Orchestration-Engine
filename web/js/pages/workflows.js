// Lista de workflows e página de detalhe (grade, grafo, tarefas e execuções).
import { can, get, post } from "../api.js";
import { triggerDialog } from "../app.js";
import { clear, dateTime, debounce, duration, empty, h, icon, pct, pill, relative, sq, toast } from "../ui.js";
import { dagView, runGrid } from "../viz.js";
import { runsTable } from "./overview.js";

function pauseSwitch(wf, onChange) {
  const sw = h("button", {
    class: "switch", role: "switch", "aria-checked": String(!wf.is_paused),
    "aria-label": wf.is_paused ? "Retomar agendamento" : "Pausar agendamento",
    title: wf.is_paused ? "Pausado: clique para retomar" : "Ativo: clique para pausar",
    disabled: !can("operator") || !wf.is_active,
    onclick: async (e) => {
      e.stopPropagation();
      sw.disabled = true;
      try {
        await post(`/workflows/${wf.name}/${wf.is_paused ? "resume" : "pause"}`);
        toast(wf.is_paused ? `${wf.name} retomado` : `${wf.name} pausado`);
        onChange?.();
      } catch (ex) {
        toast(ex.message, "error");
        sw.disabled = false;
      }
    },
  });
  return sw;
}

export function workflowsPage(main, ctx) {
  const search = h("input", { class: "input", type: "search", placeholder: "Buscar workflow ou tag", style: { width: "240px" } });
  const errors = h("div");
  const body = h("section", { class: "card" }, h("div", { class: "skeleton", style: { height: "320px" } }));
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", { class: "stack" }, h("h1", {}, "Workflows"), h("p", { class: "muted" }, "Definidos em Python na pasta de workflows. O Delco relê a pasta a cada 30 segundos.")),
      h("div", { class: "row" }, search, can("operator") ? h("button", { class: "btn accent", onclick: () => triggerDialog() }, icon("play", 15), "Executar") : null)),
    h("div", { class: "stack" }, errors, body)));
  let rows = [];

  function render() {
    const q = search.value.trim().toLowerCase();
    const list = rows.filter((w) => !q || [w.name, w.description, ...(w.tags || [])].join(" ").toLowerCase().includes(q));
    if (!list.length) {
      clear(body, empty(rows.length ? "Nenhum workflow encontrado" : "Nenhum workflow carregado", "Coloque arquivos .py na pasta de workflows (veja o exemplo ola_delco.py)."));
      return;
    }
    clear(body, h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Workflow"), h("th", {}, "Agenda"), h("th", {}, "Últimas execuções"),
        h("th", { class: "right" }, "Sucesso 7 d"), h("th", {}, "Agendamento"), h("th", {}))),
      h("tbody", {}, list.map((w) => h("tr", { class: "link", onclick: () => (location.hash = `#/workflows/${w.name}`) },
        h("td", {}, h("div", { class: "row tight" }, h("span", { class: "wf-name" }, w.name),
          !w.is_active ? h("span", { class: "tag" }, "fora da pasta") : null, (w.tags || []).map((t) => h("span", { class: "tag" }, t))),
          h("div", { class: "wf-desc", title: w.description }, w.description || "—")),
        h("td", { class: "small nowrap" }, h("div", {}, w.schedule_text),
          h("div", { class: "tiny muted" }, w.schedule && !w.is_paused && w.next_run_at ? `próxima ${relative(w.next_run_at)}` : `${w.task_count} tarefas`)),
        h("td", {}, h("div", { class: "recent" }, w.recent.map((r) =>
          h("a", { href: `#/runs/${r.id}`, onclick: (e) => e.stopPropagation(), title: `#${r.id}` }, sq(r.status))))),
        h("td", { class: "right num small" }, pct(w.success_rate)),
        h("td", {}, w.schedule ? h("div", { class: "row tight" }, pauseSwitch(w, load), h("span", { class: "small muted" }, w.is_paused ? "Pausado" : "Ativo")) : h("span", { class: "small muted" }, "Manual")),
        h("td", { class: "right" }, can("operator") && w.is_active ? h("button", { class: "btn ghost sm", onclick: (e) => { e.stopPropagation(); triggerDialog(w.name); } }, icon("play", 13), "Executar") : null)))))));
  }

  async function load() {
    try {
      const [list, errs] = await Promise.all([get("/workflows"), get("/load-errors")]);
      rows = list;
      clear(errors, Object.keys(errs).length ? h("div", { class: "notice error" }, icon("alert", 18), h("div", {},
        h("strong", {}, "Arquivos com erro na pasta de workflows"),
        ...Object.entries(errs).map(([file, msg]) => h("div", {}, h("div", { class: "mono" }, file), h("pre", {}, msg.trim().split("\n").slice(-3).join("\n")))))) : null);
      render();
    } catch (ex) {
      clear(body, h("div", { class: "notice error" }, ex.message));
    }
  }
  search.addEventListener("input", render);
  load();
  const refresh = debounce(load, 1000);
  ctx.live((e) => (e.type === "run" || e.type === "workflows") && refresh());
}

export function workflowPage(main, ctx) {
  const name = ctx.params[0];
  const head = h("div", { class: "skeleton", style: { height: "96px" } });
  const facts = h("div");
  const tabs = h("div", { class: "tabs", role: "tablist" });
  const panel = h("div");
  main.append(h("div", { class: "page" }, h("a", { class: "back", href: "#/workflows" }, icon("back", 15), "Workflows"), head, h("div", { class: "stack lg mt" }, facts, h("div", {}, tabs, panel))));
  let wf;
  let current = ctx.query.get("tab") || "grid";

  const TABS = [["grid", "Grade"], ["graph", "Grafo"], ["tasks", "Tarefas"], ["runs", "Execuções"]];
  function renderTabs() {
    clear(tabs, TABS.map(([key, label]) => h("button", {
      class: "tab", role: "tab", "aria-selected": String(current === key),
      onclick: () => { current = key; renderTabs(); renderPanel(); },
    }, label)));
  }

  async function renderPanel() {
    if (!wf) return;
    if (current === "grid") {
      const data = await get(`/workflows/${name}/grid?limit=30`);
      clear(panel, h("section", { class: "card" }, h("div", { class: "card-head" },
        h("div", {}, h("h2", {}, "Grade de execuções"), h("p", { class: "card-sub" }, "Cada coluna é uma execução (a mais recente à direita); cada linha, uma tarefa. A altura da barra no topo é a duração.")),
        h("div", { class: "legend" }, ["succeeded", "failed", "running", "retry_wait", "skipped"].map((s) => h("span", {}, sq(s), { succeeded: "Sucesso", failed: "Falha", running: "Rodando", retry_wait: "Nova tentativa", skipped: "Pulada" }[s])))),
      h("div", { class: "card-body" }, data.runs.length ? runGrid(data, name) : empty("Sem execuções", "Clique em Executar para rodar a primeira."))));
    } else if (current === "graph") {
      const view = dagView(wf.graph.tasks, {
        info: (t) => [t.queue !== "default" ? `fila ${t.queue}` : null, t.retries ? `${t.retries} retentativa(s)` : "sem retentativa", t.trigger_rule !== "all_success" ? t.trigger_rule : null].filter(Boolean).join(" · "),
        onSelect: (task) => { current = "tasks"; renderTabs(); renderPanel().then(() => document.getElementById(`t-${task}`)?.scrollIntoView({ block: "center" })); },
      });
      clear(panel, h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Grafo de dependências"),
        h("p", { class: "card-sub" }, `Versão ${wf.version} · arraste para mover · Ctrl + roda do mouse para zoom`))), h("div", { class: "card-body" }, view.el)));
    } else if (current === "tasks") {
      clear(panel, h("section", { class: "card" }, h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["Tarefa", "Depende de", "Retentativas", "Espera", "Tempo limite", "Fila", "Regra"].map((c) => h("th", {}, c)))),
        h("tbody", {}, wf.graph.tasks.map((t) => h("tr", { id: `t-${t.name}` },
          h("td", {}, h("div", { class: "mono strong" }, t.name), t.description ? h("div", { class: "tiny muted", style: { maxWidth: "360px" } }, t.description) : null),
          h("td", { class: "small mono" }, t.upstream.length ? t.upstream.join(", ") : h("span", { class: "muted" }, "—")),
          h("td", { class: "num small" }, t.retries),
          h("td", { class: "small nowrap" }, `${t.retry_delay}s × ${t.retry_backoff}`),
          h("td", { class: "small nowrap" }, t.timeout ? duration(t.timeout * 1000) : "sem limite"),
          h("td", {}, h("span", { class: "tag" }, t.queue)),
          h("td", { class: "small mono" }, t.trigger_rule))))))));
    } else {
      const page = await get(`/runs?workflow=${encodeURIComponent(name)}&size=50`);
      clear(panel, h("section", { class: "card" }, h("div", { style: { paddingTop: "6px" } }, runsTable(page.items, { showWorkflow: false }))));
    }
  }

  async function load() {
    try {
      wf = await get(`/workflows/${name}`);
    } catch (ex) {
      clear(head, h("div", { class: "notice error" }, ex.message));
      return;
    }
    const s = wf.stats_30d;
    head.className = "";
    head.removeAttribute("style");
    clear(head, h("div", { class: "page-head", style: { marginBottom: 0 } },
      h("div", { class: "stack" },
        h("div", { class: "row tight" }, h("h1", {}, wf.name), ...(wf.tags || []).map((t) => h("span", { class: "tag" }, t)),
          !wf.is_active ? h("span", { class: "pill failed" }, "Arquivo removido") : wf.is_paused ? h("span", { class: "pill retry_wait" }, "Pausado") : null),
        h("p", { class: "muted", style: { maxWidth: "760px" } }, wf.description || "Sem descrição.")),
      h("div", { class: "row" },
        wf.schedule && can("operator") ? h("button", { class: "btn ghost", onclick: async () => {
          await post(`/workflows/${name}/${wf.is_paused ? "resume" : "pause"}`);
          toast(wf.is_paused ? "Agendamento retomado" : "Agendamento pausado");
          load();
        } }, icon(wf.is_paused ? "play" : "pause", 15), wf.is_paused ? "Retomar agenda" : "Pausar agenda") : null,
        can("operator") && wf.is_active ? h("button", { class: "btn accent", onclick: () => triggerDialog(name, wf.default_params) }, icon("play", 15), "Executar") : null)));
    const fact = (label, value, title) => h("div", { class: "fact" }, h("span", { class: "label" }, label), h("span", { class: "value", title: title || "" }, value));
    clear(facts, h("div", { class: "facts" },
      fact("Agenda", wf.schedule_text, wf.schedule || ""),
      fact("Próxima execução", wf.schedule && !wf.is_paused && wf.next_run_at ? relative(wf.next_run_at) : "—", wf.next_run_at ? dateTime(wf.next_run_at) : ""),
      fact("Versão", `v${wf.version} · ${wf.fingerprint.slice(0, 8)}`, `${wf.versions} versão(ões) registradas`),
      fact("Arquivo", wf.source_file || "—"),
      fact("Execuções simultâneas", `até ${wf.max_active_runs}`),
      fact("30 dias", `${s.succeeded} ok · ${s.failed} falhas`, `média ${duration(s.avg_ms)}`),
      fact("Duração média", duration(s.avg_ms))));
    renderTabs();
    renderPanel();
  }
  load();
  const refresh = debounce(() => (current === "grid" || current === "runs") && renderPanel(), 1200);
  ctx.live((e) => (e.type === "run" || (e.type === "task" && e.status !== "running")) && refresh());
}
