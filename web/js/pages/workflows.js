import { can, get, post } from "../api.js";
import { backfillDialog, triggerDialog } from "../app.js";
import { bulkBar, clear, dateTime, debounce, duration, empty, h, highlightPython, icon, pct, relative, selection, sq, toast } from "../ui.js";
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
  const bulk = h("div");
  const body = h("section", { class: "card" }, h("div", { class: "skeleton", style: { height: "320px" } }));
  const operator = can("operator");
  const sel = selection(() => render());
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", { class: "stack" }, h("h1", {}, "Workflows"), h("p", { class: "muted" }, "Definidos em Python na pasta de workflows. O Delco relê a pasta a cada 30 segundos. Crie um novo com delco new e teste com delco dev run, sem servidor.")),
      h("div", { class: "row" }, h("div", { class: "search" }, icon("search", 15), search), operator ? h("button", { class: "btn accent", onclick: () => triggerDialog() }, icon("play", 15), "Executar") : null)),
    h("div", { class: "stack" }, errors, bulk, body)));
  let rows = [];

  async function bulkAction(action) {
    const names = [...sel.ids];
    try {
      const r = await post("/workflows/bulk", { action, names });
      toast(r.changed.length ? `${r.changed.length} agenda(s) ${action === "pause" ? "pausada(s)" : "retomada(s)"}` : "Nada para mudar: as selecionadas já estavam assim ou não têm agenda");
      sel.clear();
      load();
    } catch (ex) {
      toast(ex.message, "error");
    }
  }

  function render() {
    const q = search.value.trim().toLowerCase();
    const list = rows.filter((w) => !q || [w.name, w.description, ...(w.tags || [])].join(" ").toLowerCase().includes(q));
    clear(bulk, operator ? bulkBar(sel.ids.size, [
      h("button", { class: "btn ghost sm", onclick: () => bulkAction("pause") }, icon("pause", 13), "Pausar agendas"),
      h("button", { class: "btn ghost sm", onclick: () => bulkAction("resume") }, icon("play", 13), "Retomar agendas"),
    ], () => sel.clear()) : null);
    if (!list.length) {
      clear(body, empty(rows.length ? "Nenhum workflow encontrado" : "Nenhum workflow carregado", "Coloque arquivos .py na pasta de workflows (veja o exemplo ola_delco.py) ou crie um com delco new nome_do_workflow."));
      return;
    }
    clear(body, h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, operator ? h("th", { class: "check-cell" }, sel.headBox(list.map((w) => w.name))) : null, h("th", {}, "Workflow"), h("th", {}, "Agenda"), h("th", {}, "Últimas execuções"),
        h("th", { class: "right" }, "Sucesso 7 d"), h("th", {}, "Agendamento"), h("th", {}))),
      h("tbody", {}, list.map((w) => h("tr", { class: `link${sel.has(w.name) ? " selected" : ""}`, onclick: () => (location.hash = `#/workflows/${w.name}`) },
        operator ? h("td", { class: "check-cell" }, sel.box(w.name, `Selecionar ${w.name}`)) : null,
        h("td", {}, h("div", { class: "row tight" }, h("span", { class: "wf-name" }, w.name),
          !w.is_active ? h("span", { class: "tag" }, "fora da pasta") : null, (w.tags || []).map((t) => h("span", { class: "tag" }, t)),
          w.run_timeout ? h("span", { class: "tag", title: "Tem tempo limite de execução" }, icon("clock", 11)) : null),
          h("div", { class: "wf-desc", title: w.description }, w.description || "—")),
        h("td", { class: "small nowrap" }, h("div", {}, w.schedule_text),
          h("div", { class: "tiny muted" }, w.schedule && !w.is_paused && w.next_run_at ? `próxima ${relative(w.next_run_at)}` : `${w.task_count} tarefas`)),
        h("td", {}, h("div", { class: "recent" }, w.recent.slice(-10).map((r) =>
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

  const TABS = [["grid", "Grade"], ["graph", "Grafo"], ["tasks", "Tarefas e parâmetros"], ["runs", "Execuções"], ["versions", "Versões"], ["code", "Código"]];
  let codeLine = null;
  function renderTabs() {
    clear(tabs, TABS.map(([key, label]) => h("button", {
      class: "tab", role: "tab", "aria-selected": String(current === key),
      onclick: () => { current = key; history.replaceState(null, "", `#/workflows/${name}${key === "grid" ? "" : `?tab=${key}`}`); renderTabs(); renderPanel(); },
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
        onSelect: (task) => { current = "code"; codeLine = wf.graph.tasks.find((t) => t.name === task)?.line ?? null; renderTabs(); renderPanel(); },
      });
      clear(panel, h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Grafo de dependências"),
        h("p", { class: "card-sub" }, `Versão ${wf.version} · clique numa tarefa para ver o código · arraste para mover · Ctrl + roda do mouse para zoom`))), h("div", { class: "card-body" }, view.el)));
    } else if (current === "tasks") {
      const params = Object.entries(wf.params_schema || {});
      clear(panel, h("div", { class: "stack lg" },
        params.length ? h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Parâmetros"), h("p", { class: "card-sub" }, "Validados a cada disparo; o formulário de Executar é montado a partir daqui"))),
          h("div", { class: "table-wrap", style: { paddingTop: "8px" } }, h("table", { class: "table" },
            h("thead", {}, h("tr", {}, ["Parâmetro", "Tipo", "Padrão", "Regras", "Descrição"].map((c) => h("th", {}, c)))),
            h("tbody", {}, params.map(([name, spec]) => h("tr", {},
              h("td", { class: "mono strong small" }, name, spec.required ? h("span", { class: "req", title: "Obrigatório" }, " *") : null),
              h("td", {}, h("span", { class: "tag mono" }, spec.type)),
              h("td", { class: "small mono" }, "default" in spec ? (spec.secret ? "••••" : JSON.stringify(spec.default)) : h("span", { class: "muted" }, "—")),
              h("td", { class: "small" }, [spec.choices ? `opções: ${spec.choices.join(", ")}` : null, spec.min !== undefined ? `mín. ${spec.min}` : null, spec.max !== undefined ? `máx. ${spec.max}` : null, spec.inferred ? "livre" : null].filter(Boolean).join(" · ") || "—"),
              h("td", { class: "small muted" }, spec.description || "—"))))))) : null,
        h("section", { class: "card" }, h("div", { class: "table-wrap" }, h("table", { class: "table" },
          h("thead", {}, h("tr", {}, ["Tarefa", "Depende de", "Retentativas", "Espera", "Tempo limite", "Fila", "Regra"].map((c) => h("th", {}, c)))),
          h("tbody", {}, wf.graph.tasks.map((t) => h("tr", { id: `t-${t.name}` },
            h("td", {}, h("div", { class: "mono strong" }, t.name), t.description ? h("div", { class: "tiny muted", style: { maxWidth: "360px" } }, t.description) : null),
            h("td", { class: "small mono" }, t.upstream.length ? t.upstream.join(", ") : h("span", { class: "muted" }, "—")),
            h("td", { class: "small" }, h("span", { class: "num" }, t.retries), t.retry_on ? h("div", { class: "tiny muted mono", title: "Só repete estes erros" }, `só ${t.retry_on.join(", ")}`) : null),
            h("td", { class: "small nowrap" }, `${t.retry_delay}s × ${t.retry_backoff}`),
            h("td", { class: "small nowrap" }, t.timeout ? duration(t.timeout * 1000) : "sem limite"),
            h("td", {}, h("span", { class: "tag" }, t.queue)),
            h("td", { class: "small mono" }, t.trigger_rule)))))))));
    } else if (current === "versions") {
      const rows = await get(`/workflows/${name}/versions`);
      const changeLine = (c) => {
        if (!c) return h("span", { class: "muted small" }, "Primeira versão registrada");
        const items = [
          ...c.added.map((t) => h("span", { class: "diff add" }, `+ ${t}`)),
          ...c.removed.map((t) => h("span", { class: "diff del" }, `− ${t}`)),
          ...c.changed.map((x) => h("span", { class: "diff mod", title: x.fields.join(", ") }, `~ ${x.task}`, h("small", {}, ` ${x.fields.join(", ")}`))),
          c.run_timeout ? h("span", { class: "diff mod" }, `~ tempo limite da execução`) : null,
        ].filter(Boolean);
        return items.length ? h("div", { class: "diffs" }, items) : h("span", { class: "muted small" }, "Só opções do workflow");
      };
      clear(panel, h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Histórico de versões"),
        h("p", { class: "card-sub" }, "Uma versão nova nasce quando o grafo muda (tarefas, dependências ou opções). Cada execução guarda a versão com que começou."))),
      h("div", { class: "versions" }, rows.map((v) => h("div", { class: `version${v.current ? " current" : ""}` },
        h("div", { class: "v-num" }, `v${v.version}`),
        h("div", { class: "stack sm", style: { gap: "6px" } },
          h("div", { class: "row tight wrap small" }, h("strong", {}, dateTime(v.created_at)), h("span", { class: "muted" }, `· ${v.tasks} tarefa(s) · ${v.runs} execução(ões) · `), h("span", { class: "mono tiny muted" }, v.fingerprint.slice(0, 12)),
            v.current ? h("span", { class: "pill succeeded" }, h("span", { class: "dot" }), "Atual") : null),
          changeLine(v.changes)))))));
    } else if (current === "code") {
      const src = await get(`/workflows/${name}/source`);
      if (!src.code) {
        clear(panel, h("section", { class: "card" }, empty("Código ainda não sincronizado", "O agendador guarda o arquivo na próxima leitura da pasta (até 30 segundos).")));
        return;
      }
      const marks = new Map(Object.entries(src.tasks).filter(([, l]) => l).map(([t, l]) => [l, t]));
      const lines = src.code.replace(/\n$/, "").split("\n");
      const jump = h("select", { class: "select auto", "aria-label": "Ir para a tarefa", style: { height: "32px" }, onchange: (e) => {
        const el = codeBox.querySelector(`[data-line="${e.target.value}"]`);
        codeBox.querySelectorAll(".hl").forEach((x) => x.classList.remove("hl"));
        if (el) { el.classList.add("hl"); el.scrollIntoView({ block: "center" }); }
      } }, h("option", { value: "" }, "Ir para a tarefa…"), [...marks.entries()].sort((a, b) => a[0] - b[0]).map(([l, t]) => h("option", { value: l }, t)));
      const codeBox = h("div", { class: "code-view", role: "region", "aria-label": `Código de ${src.file}` },
        lines.map((line, i) => h("div", { class: `cl${marks.has(i + 1) ? " task-start" : ""}`, "data-line": i + 1 },
          h("span", { class: "ln" }, i + 1), h("span", { class: "lc" }, highlightPython(line)))));
      clear(panel, h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", { class: "mono", style: { fontSize: "16px" } }, src.file),
        h("p", { class: "card-sub" }, `Somente leitura · lido pelo agendador ${relative(src.updated_at)} · ${lines.length} linhas`)),
        h("div", { class: "row tight" }, jump, copyCode(src.code))), h("div", { class: "card-body" }, codeBox)));
      if (codeLine) {
        const el = codeBox.querySelector(`[data-line="${codeLine}"]`);
        if (el) { el.classList.add("hl"); setTimeout(() => el.scrollIntoView({ block: "center" }), 30); }
        codeLine = null;
      }
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
        wf.schedule && can("operator") && wf.is_active ? h("button", { class: "btn ghost", onclick: () => backfillDialog(wf) }, icon("history", 15), "Reprocessar período") : null,
        can("operator") && wf.is_active ? h("button", { class: "btn accent", onclick: () => triggerDialog(name) }, icon("play", 15), "Executar") : null)));
    const fact = (label, value, title) => h("div", { class: "fact" }, h("span", { class: "label" }, label), h("span", { class: "value", title: title || "" }, value));
    clear(facts, h("div", { class: "facts" },
      fact("Agenda", wf.schedule_text, wf.schedule || ""),
      fact("Próxima execução", wf.schedule && !wf.is_paused && wf.next_run_at ? relative(wf.next_run_at) : "—", wf.next_run_at ? dateTime(wf.next_run_at) : ""),
      fact("Versão", `v${wf.version} · ${wf.fingerprint.slice(0, 8)}`, `${wf.versions} versão(ões) registradas`),
      fact("Arquivo", wf.source_file || "—"),
      fact("Execuções simultâneas", `até ${wf.max_active_runs}`),
      fact("Tempo limite da execução", wf.run_timeout ? duration(wf.run_timeout * 1000) : "sem limite"),
      fact("30 dias", `${s.succeeded} ok · ${s.failed} falhas`, `média ${duration(s.avg_ms)}`),
      fact("Duração média", duration(s.avg_ms))));
    renderTabs();
    renderPanel();
  }
  load();
  const refresh = debounce(() => (current === "grid" || current === "runs") && renderPanel(), 1200);
  ctx.live((e) => (e.type === "run" || (e.type === "task" && e.status !== "running")) && refresh());
}

function copyCode(code) {
  return h("button", { class: "btn ghost sm", onclick: async () => {
    try { await navigator.clipboard.writeText(code); toast("Código copiado"); } catch { toast("Não foi possível copiar", "error"); }
  } }, icon("copy", 13), "Copiar");
}
