import { get, post } from "../api.js";
import { clear, confirmDialog, copyButton, dateTime, h, icon, int, relative, toast } from "../ui.js";

const STATUS_WORD = { ok: "Tudo certo", warn: "Funciona, com avisos", error: "Há problemas a corrigir" };
const GROUPS = { "configuração": "Configuração", componentes: "Componentes", processos: "Processos", workflows: "Workflows", "integrações": "Integrações", "segurança": "Segurança" };

export function systemPage(main) {
  const body = h("div", { class: "stack lg" }, h("div", { class: "skeleton", style: { height: "120px" } }), h("div", { class: "skeleton", style: { height: "320px" } }));
  const refresh = h("button", { class: "btn ghost", onclick: () => load() }, icon("retry", 15), "Conferir de novo");
  main.append(h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", { class: "stack" }, h("h1", {}, "Sistema"), h("p", { class: "muted" }, "Saúde de cada peça do Delco, verificações de configuração e segurança, e a configuração em uso. O mesmo diagnóstico sai no terminal com delco doctor.")),
      refresh),
    body));

  async function load() {
    refresh.disabled = true;
    let data;
    try {
      data = await get("/system");
    } catch (ex) {
      clear(body, h("div", { class: "notice error" }, ex.message));
      refresh.disabled = false;
      return;
    }
    refresh.disabled = false;
    const groups = new Map();
    data.checks.forEach((c) => (groups.get(c.group) || groups.set(c.group, []).get(c.group)).push(c));
    const counts = { ok: 0, warn: 0, error: 0 };
    data.checks.forEach((c) => counts[c.status]++);
    const v = data.versions;
    const comp = (label, value, sub, status) => h("div", { class: `sys-comp ${status}` }, h("span", { class: "label" }, label), h("strong", {}, value), h("span", { class: "sub" }, sub));
    const dbCheck = data.checks.find((c) => c.id === "database");
    const sched = data.schedulers;
    const workersCheck = data.checks.find((c) => c.id === "workers");
    clear(body,
      h("section", { class: `sys-summary ${data.status}` },
        h("span", { class: "sys-lamp" }),
        h("div", { class: "stack sm" }, h("h2", {}, STATUS_WORD[data.status]),
          h("span", { class: "small" }, `${counts.ok} verificação(ões) ok · ${counts.warn} aviso(s) · ${counts.error} problema(s)`)),
        h("span", { class: "spacer" }),
        h("div", { class: "tiny mono sys-versions" }, `Delco ${v.delco} · Python ${v.python} · Django ${v.django}`)),
      h("div", { class: "sys-comps" },
        comp("MySQL", dbCheck?.status === "ok" ? dbCheck.message.replace("Versão ", "") : "fora do ar", "fonte da verdade", dbCheck?.status || "error"),
        comp("Redis", data.redis.version || "fora do ar", data.redis.version ? `${data.redis.used_memory} · ${data.redis.aof_enabled ? "AOF ligado" : "AOF desligado"}` : "filas e eventos", data.redis.version ? "ok" : "error"),
        comp("Agendadores", int(sched.length), sched.length ? `sinal ${relative(sched[0].last_beat_at)}` : "nenhum ativo", sched.length ? "ok" : "error"),
        comp("Workers", workersCheck?.message?.split(" ")[0] || "0", "consumindo as filas", workersCheck?.status || "error")),
      h("div", { class: "grid-main" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Verificações"), h("p", { class: "card-sub" }, "Do mais grave para o mais simples, em cada grupo"))),
          h("div", { class: "checks-list" }, [...groups.entries()].map(([g, list]) => [
            h("div", { class: "check-group" }, GROUPS[g] || g),
            list.sort((a, b) => ["error", "warn", "ok"].indexOf(a.status) - ["error", "warn", "ok"].indexOf(b.status)).map((c) => h("div", { class: `check-item ${c.status}` },
              h("span", { class: "check-icon", "aria-label": c.status }, icon(c.status === "ok" ? "check" : "alert", 15)),
              h("div", { class: "stack sm", style: { gap: "2px" } }, h("strong", { class: "small" }, c.label), h("span", { class: "small muted" }, c.message),
                c.hint && c.status !== "ok" ? h("span", { class: "tiny hint-line" }, c.hint) : null))),
          ]))),
        h("div", { class: "stack" },
          h("section", { class: "card" },
            h("div", { class: "card-head" }, h("h2", {}, "Agendadores")),
            h("div", { class: "card-body stack sm" }, sched.length ? sched.map((s) => h("div", { class: "sched" },
              h("div", { class: "row tight" }, h("span", { class: "dot", style: { color: s.last_error ? "var(--warn)" : "var(--ok)" } }), h("span", { class: "mono strong small" }, s.id)),
              h("span", { class: "tiny muted" }, `desde ${dateTime(s.started_at)} · ${int(s.cycles)} ciclos · ${s.last_sync?.workflows ?? "?"} workflow(s)`),
              s.last_error ? h("span", { class: "tiny", style: { color: "var(--danger)" } }, s.last_error) : null))
              : h("p", { class: "small muted" }, "Nenhum agendador mandou sinal nos últimos 20 segundos."))),
          h("section", { class: "card" },
            h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Manutenção"), h("p", { class: "card-sub" }, `Execuções terminadas há mais de ${data.config.retention_days} dias são apagadas a cada 10 minutos.`))),
            h("div", { class: "card-body stack" },
              h("button", { class: "btn ghost", onclick: async (e) => {
                if (!(await confirmDialog({ title: "Rodar a limpeza agora?", text: `Apaga execuções terminadas há mais de ${data.config.retention_days} dias, tokens vencidos, workers desligados há uma semana e auditoria com mais de 180 dias.`, confirm: "Limpar agora" }))) return;
                e.target.disabled = true;
                try {
                  const r = await post("/system/housekeeping");
                  toast(`Limpeza feita: ${r.removed.runs} execução(ões), ${r.removed.tokens} token(s), ${r.removed.workers} worker(s)`);
                } catch (ex) { toast(ex.message, "error"); } finally { e.target.disabled = false; }
              } }, icon("trash", 15), "Rodar limpeza agora"),
              h("div", { class: "stack sm" }, h("span", { class: "label" }, "Métricas para o Prometheus"),
                h("span", { class: "small muted" }, "GET /api/metrics com Authorization: Bearer e o valor de DELCO_METRICS_TOKEN (ou um token pessoal)."),
                h("pre", { class: "json" }, `scrape_configs:\n  - job_name: delco\n    metrics_path: /api/metrics\n    authorization:\n      credentials: <DELCO_METRICS_TOKEN>\n    static_configs:\n      - targets: ["${location.host}"]`),
                h("div", {}, copyButton(`scrape_configs:\n  - job_name: delco\n    metrics_path: /api/metrics\n    authorization:\n      credentials: <DELCO_METRICS_TOKEN>\n    static_configs:\n      - targets: ["${location.host}"]`, "Copiar exemplo"))))))),
      Object.keys(data.load_errors || {}).length ? h("div", { class: "notice error" }, icon("alert", 18), h("div", {}, h("strong", {}, "Arquivos de workflow com erro"),
        ...Object.entries(data.load_errors).map(([file, msg]) => h("div", {}, h("div", { class: "mono" }, file), h("pre", {}, msg.trim().split("\n").slice(-3).join("\n")))))) : null,
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Configuração em uso"), h("p", { class: "card-sub" }, "Lida das variáveis DELCO_* deste processo da API. Senhas e chaves aparecem mascaradas."))),
        h("div", { class: "table-wrap", style: { paddingTop: "8px" } }, h("table", { class: "table config-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Variável"), h("th", {}, "Valor"))),
          h("tbody", {}, Object.entries(data.config).filter(([k]) => k !== "secret_key_from_env").map(([k, val]) => h("tr", {},
            h("td", { class: "mono small" }, `DELCO_${k.toUpperCase()}`),
            h("td", { class: "small mono" }, Array.isArray(val) ? val.join(", ") : val === "" ? h("span", { class: "muted" }, "—") : String(val)))))))));
  }
  load();
}
