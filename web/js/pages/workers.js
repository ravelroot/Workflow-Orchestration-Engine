// Workers: vagas de cada processo, o que está rodando agora e o estado das filas.
import { get } from "../api.js";
import { clear, dateTime, debounce, duration, empty, h, int, progressBar, relative } from "../ui.js";

export function workersPage(main, ctx) {
  const board = h("section", { class: "card" }, h("div", { class: "skeleton", style: { height: "260px" } }));
  const queues = h("section", { class: "card" });
  const sub = h("p", { class: "muted" }, "Cada linha é um processo worker; cada caixa, uma vaga de execução.");
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", { class: "stack" }, h("h1", {}, "Workers"), sub)),
    h("div", { class: "stack lg" }, board, queues)));

  async function load() {
    let workers, qs;
    try {
      [workers, qs] = await Promise.all([get("/workers"), get("/queues")]);
    } catch (ex) {
      clear(board, h("div", { class: "notice error" }, ex.message));
      return;
    }
    const online = workers.filter((w) => w.status !== "offline");
    const recentOff = workers.filter((w) => w.status === "offline" && Date.now() - new Date(w.stopped_at || w.last_heartbeat_at).getTime() < 3600e3);
    const slots = online.reduce((a, w) => a + w.concurrency, 0);
    const busy = online.reduce((a, w) => a + w.running.length, 0);
    clear(sub, `${online.length} worker(s) online · ${busy} de ${slots} vagas ocupadas. Cada linha é um processo; cada caixa, uma vaga.`);
    clear(board,
      h("div", { class: "card-head" }, h("h2", {}, "Quadro de vagas"), h("span", { class: "card-sub" }, "Aumente com DELCO_WORKER_CONCURRENCY ou subindo mais workers")),
      online.length ? h("div", { style: { paddingTop: "10px" } }, online.map((w) => {
        const free = Math.max(0, w.concurrency - w.running.length);
        const color = w.status === "online" ? "var(--ok)" : w.status === "stopping" ? "var(--warn)" : "var(--mauve)";
        return h("div", { class: `worker-row ${w.status}` },
          h("div", { class: "stack sm" },
            h("div", { class: "row tight" }, h("span", { class: "dot", style: { color } }), h("span", { class: "mono strong" }, w.id)),
            h("span", { class: "small muted" }, `${{ online: "Online", stopping: "Desligando", offline: "Offline" }[w.status]} · filas ${w.queues.join(", ")}`),
            h("span", { class: "tiny muted" }, w.status === "offline" ? `parou ${relative(w.stopped_at)}` : `sinal ${relative(w.last_heartbeat_at)} · desde ${dateTime(w.started_at)}`),
            h("span", { class: "tiny muted" }, `${int(w.executed_24h)} tentativas em 24 h · ${int(w.failed_24h)} com erro`)),
          w.status === "offline" ? h("p", { class: "small muted" }, "Sem vagas: o processo não está mais enviando sinal.") :
            h("div", { class: "slots" },
              w.running.map((r) => h("a", { class: "slot", href: `#/runs/${r.run_id}?task=${encodeURIComponent(r.task_name)}` },
                h("strong", { title: r.task_name }, r.task_name),
                h("span", { class: "muted ellipsis" }, `${r.workflow} · #${r.run_id}`),
                h("span", { class: "muted" }, `${duration(Date.now() - new Date(r.started_at).getTime())}${r.attempts > 1 ? ` · ${r.attempts}ª tentativa` : ""}`),
                r.progress !== null ? progressBar([{ cls: "run", value: r.progress }, { cls: "", value: 100 - r.progress }]) : null)),
              Array.from({ length: free }, () => h("div", { class: "slot free" }, "livre"))));
      })) : empty("Nenhum worker online", "Suba um com `delco worker` ou `docker compose up -d --scale worker=3`."),
      recentOff.length ? h("div", { class: "offline-list" }, h("span", { class: "strong" }, "Desligados na última hora:"),
        recentOff.map((w) => h("span", { class: "row tight" }, h("span", { class: "dot", style: { color: "var(--mauve)" } }), h("span", { class: "mono" }, w.id), `parou ${relative(w.stopped_at || w.last_heartbeat_at)}`))) : null);
    clear(queues,
      h("div", { class: "card-head" }, h("h2", {}, "Filas"), h("span", { class: "card-sub" }, "Contagem no MySQL e no Redis Stream de cada fila")),
      qs.length ? h("div", { class: "table-wrap", style: { paddingTop: "6px" } }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["Fila", "Aguardando", "Rodando", "Nova tentativa", "Mensagens no stream", "Entregues sem confirmação"].map((c, i) => h("th", { class: i ? "right" : "" }, c)))),
        h("tbody", {}, qs.map((q) => h("tr", {},
          h("td", {}, h("span", { class: "tag" }, q.queue)),
          h("td", { class: "right num" }, int(q.waiting)),
          h("td", { class: "right num" }, int(q.running)),
          h("td", { class: "right num" }, int(q.retry_wait)),
          h("td", { class: "right num" }, int(q.stream_length)),
          h("td", { class: "right num" }, int(q.stream_pending))))))) : h("div", { class: "card-body muted small" }, "Nenhuma fila em uso ainda."));
  }
  load();
  const refresh = debounce(load, 800);
  ctx.live((e) => (e.type === "worker" || e.type === "task") && refresh());
  const timer = setInterval(load, 5000);
  ctx.onCleanup(() => clearInterval(timer));
}
