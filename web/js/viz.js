// Visualizações: grafo de tarefas (DAG), gráfico de vazão, linha do tempo e grade.
import { duration, h, icon, STATUS, tooltip, escapeHtml } from "./ui.js";

const W = 176;
const H = 48;
const GX = 56;
const GY = 14;

// ------------------------------------------------------------------ DAG
function computeLayers(tasks) {
  const layer = new Map();
  for (const t of tasks) {
    const ups = t.upstream.filter((u) => layer.has(u));
    layer.set(t.name, ups.length ? Math.max(...ups.map((u) => layer.get(u))) + 1 : 0);
  }
  const layers = [];
  for (const t of tasks) (layers[layer.get(t.name)] ??= []).push(t.name);
  const children = new Map(tasks.map((t) => [t.name, []]));
  tasks.forEach((t) => t.upstream.forEach((u) => children.get(u)?.push(t.name)));
  const ups = new Map(tasks.map((t) => [t.name, t.upstream]));
  const pos = new Map();
  const index = () => layers.forEach((L) => L.forEach((n, i) => pos.set(n, i)));
  index();
  const bary = (names) => (names.length ? names.reduce((a, n) => a + (pos.get(n) ?? 0), 0) / names.length : Infinity);
  for (let it = 0; it < 4; it++) {
    for (let l = 1; l < layers.length; l++) {
      layers[l].sort((a, b) => bary(ups.get(a)) - bary(ups.get(b)) || pos.get(a) - pos.get(b));
      index();
    }
    for (let l = layers.length - 2; l >= 0; l--) {
      layers[l].sort((a, b) => bary(children.get(a)) - bary(children.get(b)) || pos.get(a) - pos.get(b));
      index();
    }
  }
  return layers;
}

function layout(tasks) {
  const layers = computeLayers(tasks);
  const nodes = new Map();
  const isChain = layers.length > 6 && layers.every((L) => L.length === 1);
  if (isChain) {
    // cadeia longa (ex.: 50 passos): quebra em linhas em zigue-zague em vez de uma fila enorme
    const cols = 6;
    const gx = 40;
    const gy = 30;
    layers.forEach(([name], i) => {
      const row = Math.floor(i / cols);
      const col = row % 2 === 0 ? i % cols : cols - 1 - (i % cols);
      nodes.set(name, { x: col * (W + gx), y: row * (H + gy), row });
    });
    const rows = Math.ceil(layers.length / cols);
    return { nodes, width: Math.min(cols, layers.length) * (W + gx) - gx, height: rows * (H + gy) - gy, chain: true };
  }
  const tallest = Math.max(...layers.map((L) => L.length));
  layers.forEach((L, l) => {
    const offset = ((tallest - L.length) * (H + GY)) / 2;
    L.forEach((name, i) => nodes.set(name, { x: l * (W + GX), y: offset + i * (H + GY) }));
  });
  return { nodes, width: layers.length * (W + GX) - GX, height: tallest * (H + GY) - GY, chain: false };
}

function edgePath(a, b, chain) {
  if (chain && a.row !== b.row) {
    const x = a.x + W / 2;
    return `M${x},${a.y + H} L${x},${b.y - 2}`;
  }
  if (chain) {
    return a.x < b.x ? `M${a.x + W},${a.y + H / 2} L${b.x - 2},${b.y + H / 2}` : `M${a.x},${a.y + H / 2} L${b.x + W + 2},${b.y + H / 2}`;
  }
  const x1 = a.x + W;
  const y1 = a.y + H / 2;
  const x2 = b.x - 2;
  const y2 = b.y + H / 2;
  const c = Math.max(24, (x2 - x1) / 2);
  return `M${x1},${y1} C${x1 + c},${y1} ${x2 - c},${y2} ${x2},${y2}`;
}

const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

export function dagView(tasks, { onSelect, info } = {}) {
  const { nodes, width, height, chain } = layout(tasks);
  const pad = 24;
  const root = h("g");
  const edgesG = h("g");
  const nodesG = h("g");
  root.append(edgesG, nodesG);
  const svg = h("svg", { class: "dag-svg", role: "img", "aria-label": "Grafo de dependências das tarefas" },
    h("defs", {}, h("marker", { id: "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
      h("path", { d: "M0,1 L9,5 L0,9 z", fill: "var(--line-strong)" }))),
    root);
  const wrap = h("div", { class: "dag-wrap" }, svg);
  const els = new Map();
  const edgeEls = [];

  for (const t of tasks) {
    const b = nodes.get(t.name);
    for (const u of t.upstream) {
      const a = nodes.get(u);
      if (!a) continue;
      const p = h("path", { class: "dag-edge", d: edgePath(a, b, chain), "marker-end": "url(#arrow)" });
      edgesG.append(p);
      edgeEls.push({ from: u, el: p });
    }
    const title = h("text", { class: "title", x: 14, y: 20 }, clip(t.name, 22));
    const meta = h("text", { class: "meta", x: 14, y: 36 }, "");
    const pbg = h("rect", { class: "pbar-bg", x: 14, y: H - 7, width: W - 26, height: 3, rx: 1.5, visibility: "hidden" });
    const pbar = h("rect", { class: "pbar", x: 14, y: H - 7, width: 0, height: 3, rx: 1.5, visibility: "hidden" });
    const g = h("g", { class: "dag-node", transform: `translate(${b.x},${b.y})`, tabindex: "0", role: "button", "aria-label": t.name },
      h("title", {}, t.name),
      h("rect", { class: "box", width: W, height: H, rx: 6 }),
      h("rect", { class: "accent-bar", x: 4, y: 6, width: 3, height: H - 12, rx: 1.5 }),
      title, meta, pbg, pbar);
    g.addEventListener("click", (e) => { e.stopPropagation(); onSelect?.(t.name); });
    g.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && onSelect?.(t.name));
    nodesG.append(g);
    els.set(t.name, { g, meta, pbg, pbar });
  }

  // ---- zoom e arraste
  const view = { x: pad, y: pad, k: 1 };
  const apply = () => root.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`);
  const fit = () => {
    const cw = wrap.clientWidth || 900;
    const ch = wrap.clientHeight || 360;
    view.k = Math.max(0.3, Math.min(1, (cw - pad * 2) / width, (ch - pad * 2) / height));
    view.x = Math.max(pad, (cw - width * view.k) / 2);
    view.y = Math.max(pad, (ch - height * view.k) / 2);
    apply();
  };
  const zoom = (factor, cx, cy) => {
    const k = Math.min(2, Math.max(0.2, view.k * factor));
    cx ??= wrap.clientWidth / 2;
    cy ??= wrap.clientHeight / 2;
    view.x = cx - ((cx - view.x) * k) / view.k;
    view.y = cy - ((cy - view.y) * k) / view.k;
    view.k = k;
    apply();
  };
  wrap.addEventListener("wheel", (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    const r = wrap.getBoundingClientRect();
    zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  let drag = null;
  wrap.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".dag-node, .dag-tools")) return;
    drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    wrap.classList.add("dragging");
    wrap.setPointerCapture(e.pointerId);
  });
  wrap.addEventListener("pointermove", (e) => {
    if (!drag) return;
    view.x = drag.vx + e.clientX - drag.x;
    view.y = drag.vy + e.clientY - drag.y;
    apply();
  });
  const end = () => { drag = null; wrap.classList.remove("dragging"); };
  wrap.addEventListener("pointerup", end);
  wrap.addEventListener("pointercancel", end);
  wrap.prepend(h("div", { class: "dag-tools" },
    h("button", { class: "icon-btn", title: "Aproximar", onclick: () => zoom(1.2) }, icon("plus", 15)),
    h("button", { class: "icon-btn", title: "Afastar", onclick: () => zoom(1 / 1.2) }, icon("minus", 15)),
    h("button", { class: "icon-btn", title: "Encaixar", onclick: fit }, icon("fit", 15))));

  const fitHeight = Math.max(200, Math.min(540, height + pad * 2));
  wrap.style.height = `${fitHeight}px`;
  requestAnimationFrame(fit);
  new ResizeObserver(() => fit()).observe(wrap);

  function update(states = {}) {
    for (const t of tasks) {
      const s = states[t.name] || { status: "pending" };
      const e = els.get(t.name);
      e.g.setAttribute("class", `dag-node ${s.status}${s.selected ? " selected" : ""}`);
      e.meta.textContent = info ? info(t, s) : STATUS[s.status] || "";
      const running = s.status === "running" && s.progress !== null && s.progress !== undefined;
      e.pbg.setAttribute("visibility", running ? "visible" : "hidden");
      e.pbar.setAttribute("visibility", running ? "visible" : "hidden");
      if (running) e.pbar.setAttribute("width", ((W - 26) * s.progress) / 100);
    }
    for (const edge of edgeEls) {
      edge.el.setAttribute("class", `dag-edge${states[edge.from]?.status === "succeeded" ? " done" : ""}`);
    }
  }
  update();
  return { el: wrap, update, fit };
}

// ------------------------------------------------------------- vazão (24 h)
export function throughputChart(rows) {
  const w = 720;
  const hgt = 210;
  const left = 34;
  const bottom = 24;
  const top = 10;
  const max = Math.max(4, ...rows.map((r) => r.succeeded + r.failed));
  const nice = Math.ceil(max / 4) * 4;
  const bw = (w - left) / rows.length;
  const y = (v) => top + (hgt - top - bottom) * (1 - v / nice);
  const svg = h("svg", { class: "chart-svg", viewBox: `0 0 ${w} ${hgt}`, role: "img", "aria-label": "Tarefas concluídas por hora nas últimas 24 horas" });
  for (let i = 0; i <= 4; i++) {
    const v = (nice / 4) * i;
    svg.append(h("line", { class: "grid", x1: left, x2: w, y1: y(v), y2: y(v) }), h("text", { class: "axis", x: left - 8, y: y(v) + 4, "text-anchor": "end" }, v));
  }
  rows.forEach((r, i) => {
    const x = left + i * bw + bw * 0.18;
    const bwi = bw * 0.64;
    const okH = y(0) - y(r.succeeded);
    const badH = y(0) - y(r.failed);
    if (r.succeeded) svg.append(h("rect", { class: "ok", x, y: y(r.succeeded), width: bwi, height: okH, rx: 2 }));
    if (r.failed) svg.append(h("rect", { class: "bad", x, y: y(r.succeeded) - badH - (r.succeeded ? 1.5 : 0), width: bwi, height: badH, rx: 2 }));
    const d = new Date(r.hour);
    if (i % 3 === 0 || i === rows.length - 1) {
      svg.append(h("text", { class: "axis", x: x + bwi / 2, y: hgt - 6, "text-anchor": "middle" }, `${String(d.getHours()).padStart(2, "0")}h`));
    }
    const hit = h("rect", { class: "hit", x: left + i * bw, y: top, width: bw, height: hgt - top - bottom });
    const to = new Date(d.getTime() + 3600e3);
    tooltip(hit, () => `${String(d.getHours()).padStart(2, "0")}:00–${String(to.getHours()).padStart(2, "0")}:00<br>${r.succeeded} com sucesso · ${r.failed} com falha`);
    svg.append(hit);
  });
  return svg;
}

// --------------------------------------------------------- linha do tempo
export function gantt(run, onSelect) {
  const started = run.tasks.filter((t) => t.started_at);
  if (!started.length || !run.started_at) return h("p", { class: "muted small" }, "Nenhuma tarefa começou ainda.");
  const t0 = new Date(run.started_at).getTime();
  const end = (t) => (t.finished_at ? new Date(t.finished_at).getTime() : Date.now());
  const total = Math.max(1000, ...started.map((t) => end(t) - t0));
  const rows = [...started].sort((a, b) => new Date(a.started_at) - new Date(b.started_at));
  const grid = h("div", { class: "gantt" });
  for (const t of rows) {
    const s = new Date(t.started_at).getTime() - t0;
    const bar = h("div", {
      class: `g-bar ${t.status}`,
      style: { left: `${(100 * s) / total}%`, width: `${Math.max(0.4, (100 * (end(t) - t0 - s)) / total)}%` },
      onclick: () => onSelect?.(t.name),
    });
    tooltip(bar, () => `<strong>${escapeHtml(t.name)}</strong><br>${STATUS[t.status]} · ${duration(t.duration_ms)}<br>tentativas: ${t.attempts}`);
    grid.append(h("div", { class: "g-name", title: t.name, onclick: () => onSelect?.(t.name) }, t.name), h("div", { class: "g-track" }, bar));
  }
  grid.append(h("div", { class: "g-axis" }, ...[0, 0.25, 0.5, 0.75, 1].map((f) => h("span", {}, duration(total * f)))));
  return grid;
}

// ------------------------------------------------- grade (tarefas x execuções)
export function runGrid(data, workflow) {
  const runs = data.runs;
  const maxDur = Math.max(1, ...runs.map((r) => r.duration_ms || 0));
  const g = h("div", { class: "grid-view", style: { gridTemplateColumns: `minmax(140px, 220px) repeat(${runs.length}, 20px)` } });
  g.append(h("div", { class: "gv-name" }, ""));
  for (const r of runs) {
    const bar = h("a", { href: `#/runs/${r.id}`, class: r.status, style: { height: `${6 + (50 * (r.duration_ms || 0)) / maxDur}px` } });
    tooltip(bar, () => `<strong>Execução #${r.id}</strong><br>${STATUS[r.status]} · ${duration(r.duration_ms)}<br>${new Date(r.created_at).toLocaleString("pt-BR")}`);
    g.append(h("div", { class: "gv-head" }, bar));
  }
  for (const name of data.tasks) {
    g.append(h("div", { class: "gv-name", title: name }, name));
    for (const r of runs) {
      const c = r.cells[name];
      if (!c) {
        g.append(h("div", { class: "gv-cell" }));
        continue;
      }
      const a = h("a", { href: `#/runs/${r.id}?task=${encodeURIComponent(name)}` }, h("span", { class: `sq ${c.status}` }));
      tooltip(a, () => `<strong>${escapeHtml(name)}</strong> · execução #${r.id}<br>${STATUS[c.status]}${c.attempts > 1 ? ` · ${c.attempts} tentativas` : ""}${c.duration_ms ? ` · ${duration(c.duration_ms)}` : ""}`);
      g.append(h("div", { class: "gv-cell" }, a));
    }
  }
  void workflow;
  return g;
}
