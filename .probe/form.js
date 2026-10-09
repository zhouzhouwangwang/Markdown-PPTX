
"use strict";
const $ = (id) => document.getElementById(id);
const md = $("md");
const STORE_KEY = "pptsvc.form.markdown";
let timer = null;

/* ---------- 示例稿件（与 examples/manuscript.md 一致） ---------- */
const EXAMPLE = `# 职业规划展示

> 从初心出发，聚焦目标岗位，规划下一步行动路径

---

## 初心起源

- 从一次跨部门协作中意识到：把复杂方案讲清楚，本身就是稀缺能力
- 过去两年独立完成 12 场方案汇报，逐步形成结构化表达的稳定方法
- 希望把这种能力沉淀为职业方向，而不只是临时任务

---

## 目标岗位

> 核心判断：表达与结构化能力，是可迁移的职业资产

- 岗位方向：解决方案 / 产品运营
- 核心要求：结构化表达、跨团队协作、行业理解
- 经验匹配：12 场方案汇报 + 3 个跨部门项目

---

## 能力盘点

- 结构化表达：可独立完成从提纲到成稿的全流程
- 方案可视化：熟练使用图表与版式传达信息
- 跨团队协作：与研发、市场、销售均有稳定协作经验
- 需求分析：能把模糊诉求拆解为可执行方案
- 数据分析：掌握基础统计与可视化工具
- 行业理解：对本行业头部公司的产品路线有持续跟踪
- 项目管理：主导过 3 个中型项目并按期交付
- 公开表达：累计对外分享 12 次，反馈良好

---

## 行动计划

1. 第 1-2 月：补齐数据分析短板，完成两个实战分析
2. 第 3-4 月：沉淀作品集，覆盖三类典型方案场景
3. 第 5-6 月：定向投递并完成岗位转型
`;

/* ---------- 粘贴内容实时自检：专抓"标记后缺空格/单独成行" ---------- */
function cut(s) { return s.length > 18 ? s.slice(0, 18) + "…" : s; }

function lint(text) {
  const warns = [];
  const lines = text.split(/\r?\n/);
  let hasH1 = false;
  lines.forEach((raw, i) => {
    const line = raw.trim();
    if (!line || /^(-{3,}|\*{3,}|_{3,})$/.test(line)) return;
    if (/^#{1,6}\s/.test(line)) { if (/^#\s/.test(line)) hasH1 = true; return; }
    const n = i + 1;
    if (/^#{1,6}\S/.test(line)) warns.push(`第 ${n} 行「${cut(line)}」：标题标记后缺一个空格（# 与文字之间要有空格）`);
    else if (/^[-*+]\S/.test(line)) warns.push(`第 ${n} 行「${cut(line)}」：列表标记后缺一个空格（- 与内容之间要有空格）`);
    else if (/^[-*+]$/.test(line)) warns.push(`第 ${n} 行："-" 单独成行：列表内容必须与 "- " 写在同一行`);
    else if (/^\d+[.)]\S/.test(line)) warns.push(`第 ${n} 行「${cut(line)}」：有序列表标记后缺一个空格（1. 与内容之间要有空格）`);
    else if (/^>$/.test(line)) warns.push(`第 ${n} 行：">" 单独成行：引用内容必须与 "> " 写在同一行`);
  });
  if (text.trim() && !hasH1) warns.push("全文缺少一级标题（需要一行「# 标题」作为封面）");
  return warns.slice(0, 8);
}

/* ---------- 分页预览：与服务端同公式（高度感知 + 条数上限） ---------- */
const NARROW = new Set(" \t.,:;'\"!?()[]{}/\\|·-_=+*<>@#$%^&`~");
const GEOM = { "16:9": [1100, 720], "4:3": [780, 720], A1: [2064, 3178] };
const METRICS = { classic: [236, 26, 1.45, 22], swiss: [220, 25, 1.5, 20], midnight: [230, 25, 1.5, 21], cards: [214, 24, 1.5, 14] };

function currentTheme() {
  const style = $("style").value;
  return style === "cards" ? "cards." + $("industry").value : style;
}

function units(s) {
  let t = 0;
  for (const ch of s) t += NARROW.has(ch) ? 0.40 : (ch.codePointAt(0) >= 0x2e80 ? 1.0 : 0.58);
  return t;
}

function outline(text, limit, aspect, theme) {
  const [w, h] = GEOM[aspect] || GEOM["16:9"];
  const isCards = theme && theme.indexOf("cards") === 0;
  const [top, font, line, margin] = isCards ? METRICS.cards : (METRICS[theme] || METRICS.classic);
  const budget = (h - 48 - top) / 1.05;
  const liHeight = isCards
    ? (item) => { const tw = Math.max(200, w - 2 * 16 - 76 - 18); return Math.max(1, Math.ceil(units(item) / (tw / font))) * font * line + 32 + 14; }
    : (item) => Math.max(1, Math.ceil(units(item) / (w / font))) * font * line + margin;
  const sections = [];
  let cur = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (/^##\s/.test(line)) { cur = { title: line.replace(/^##\s+/, ""), items: [] }; sections.push(cur); }
    else if ((/^[-*+]\s/.test(line) || /^\d+[.)]\s/.test(line)) && cur) cur.items.push(line.replace(/^([-*+]|\d+[.)])\s+/, ""));
  }
  const parts = sections.map((s, i) => {
    let used = 0, count = 0, pages = 1;
    for (const item of s.items) {
      const ih = liHeight(item);
      if (count > 0 && (count >= limit || used + ih > budget)) { pages++; used = ih; count = 1; }
      else { used += ih; count++; }
    }
    return { idx: i + 1, title: s.title, items: s.items.length, pages };
  });
  const total = 1 + parts.reduce((a, b) => a + b.pages, 0);
  return { parts, total };
}

function refresh() {
  const text = md.value;
  const lines = text.split(/\r?\n/);
  $("stats").textContent = `${lines.length} 行 · ${text.length} 字符`;

  const warns = lint(text);
  const lintEl = $("lint");
  lintEl.hidden = warns.length === 0;
  lintEl.innerHTML = "";
  if (warns.length) {
    const b = document.createElement("b"); b.textContent = "格式自检（发送前建议修正，服务端也会拦截）：";
    lintEl.appendChild(b);
    const ul = document.createElement("ul");
    warns.forEach((w) => { const li = document.createElement("li"); li.textContent = w; ul.appendChild(li); });
    lintEl.appendChild(ul);
  }

  const outEl = $("outline");
  outEl.innerHTML = "";
  if (text.trim() && !warns.length) {
    const limit = Number($("bullets").value) || 6;
    const o = outline(text, limit, $("aspect").value, currentTheme());
    const head = document.createElement("div");
    head.textContent = `解析预览：封面 + ${o.parts.length} 节 ≈ ${o.total} 页（按高度自动分页，每页上限 ${limit} 条）`;
    outEl.appendChild(head);
    const ul = document.createElement("ul");
    o.parts.forEach((p) => {
      const li = document.createElement("li");
      const split = p.pages > 1 ? ` → ${p.pages} 页（自动拆「（续）」）` : " → 1 页";
      li.textContent = `${String(p.idx).padStart(2, "0")} ${p.title} · ${p.items} 条${split}`;
      ul.appendChild(li);
    });
    outEl.appendChild(ul);
    outEl.hidden = false;
  } else {
    outEl.hidden = true;
  }

  try { localStorage.setItem(STORE_KEY, text); } catch (e) { /* 隐私模式等 */ }
  schedulePreview();
}

/* ---------- 请求 ---------- */
function payload(dryRun) {
  return {
    markdown: md.value,
    aspect_ratio: $("aspect").value,
    theme: currentTheme(),
    max_bullets: Number($("bullets").value) || 6,
    name: $("name").value.trim() || null,
    title: $("title").value.trim() || null,
    author: $("author").value.trim() || "pptsvc",
    dry_run: dryRun,
  };
}

function setLoading(on, label) {
  ["btnGen", "btnDry"].forEach((id) => { $(id).disabled = on; });
  $("btnGen").textContent = on ? (label || "转换中…") : "生成 PPTX";
}

function fmtBytes(n) {
  if (n == null) return "—";
  return n >= 1048576 ? (n / 1048576).toFixed(2) + " MB" : (n / 1024).toFixed(1) + " KB";
}

function renderChecks(checks) {
  const ul = $("checks");
  ul.innerHTML = "";
  Object.entries(checks).forEach(([name, ok]) => {
    const li = document.createElement("li");
    const mark = document.createElement("span");
    mark.className = ok ? "yes" : "no";
    mark.textContent = ok ? "✓" : "✗";
    const label = document.createElement("span");
    label.textContent = name;
    li.append(mark, label);
    ul.appendChild(li);
  });
}

function renderOk(status, ms, body) {
  $("empty").hidden = true; $("error").hidden = true; $("result").hidden = false;
  const badge = $("badge");
  badge.className = "badge ok";
  badge.textContent = `${status} OK`;
  const t = body.timings_ms || {};
  const scaleNote = body.height_scale && body.height_scale < 1
    ? ` · 已自动降密重排（scale ${body.height_scale}）` : "";
  const verified = body.dry_run ? " · 版式已验证，可直接生成" : "";
  $("meta").textContent = `${ms} ms（渲染 ${t.render ?? "—"} · 转换 ${t.convert ?? "—"} · 校验 ${t.verify ?? "—"}）${scaleNote}${verified}`;
  $("taskId").textContent = body.task_id || "—";
  $("slides").textContent = body.slides ?? "—";
  $("bytes").textContent = body.dry_run ? "dry_run" : fmtBytes(body.artifact_bytes);
  $("timings").textContent = body.dry_run ? "—" : `${ms} ms`;
  const dl = $("download");
  if (body.download_url) { dl.href = body.download_url; dl.style.display = ""; }
  else dl.style.display = "none";
  if (body.checks && body.checks.checks) renderChecks(body.checks.checks);
  $("raw").textContent = JSON.stringify(body, null, 2);
}

function renderErr(status, ms, body, rawText) {
  $("empty").hidden = true; $("result").hidden = true; $("error").hidden = false;
  const badge = $("errBadge");
  badge.className = "badge bad";
  badge.textContent = status === 0 ? "网络错误" : `${status}`;
  $("errMeta").textContent = `${ms} ms`;
  const el = $("errBody");
  el.innerHTML = "";
  const detail = body && body.detail;
  const add = (text) => { const d = document.createElement("div"); d.textContent = text; el.appendChild(d); };
  if (typeof detail === "string") add(detail);
  else if (Array.isArray(detail)) detail.forEach((d) => add(`${(d.loc || []).join(".")}：${d.msg}`));
  else if (detail && typeof detail === "object") {
    if (detail.message) add(String(detail.message));
    (detail.errors || []).forEach((e) => add(`• ${e}`));
  }
  else add(rawText ? rawText.slice(0, 500) : "无响应体");
  $("errRaw").textContent = rawText || "";
  /* 422 会点名 slideNN.html：预览自动跳到出错页并标红 */
  const hit = JSON.stringify(body && body.detail ? body.detail : "").match(/slide(\d+)\.html/i);
  if (hit) pvFlag(Number(hit[1]) - 1);
}

async function send(dryRun) {
  if (!md.value.trim()) { md.focus(); return; }
  const data = payload(dryRun);
  setLoading(true, dryRun ? "预检中…" : "转换中…");
  const started = performance.now();
  try {
    const res = await fetch("/v1/decks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const ms = Math.round(performance.now() - started);
    const text = await res.text();
    let body = null;
    try { body = JSON.parse(text); } catch (e) { /* 非 JSON 错误体 */ }
    if (res.ok) renderOk(res.status, ms, body);
    else renderErr(res.status, ms, body, text);
  } catch (err) {
    renderErr(0, Math.round(performance.now() - started), null, String(err));
  } finally {
    setLoading(false);
  }
}

/* ---------- 样式预览：POST /v1/preview，真实渲染 HTML ---------- */
let pvTimer = null;
let pvSeq = 0;
let pvState = { pages: [], idx: 0, canvas: [1280, 720] };

function pvClear() {
  pvState.pages = [];
  $("pvTabs").innerHTML = "";
  $("pvNote").textContent = "";
  $("pvFrameWrap").hidden = true;
  $("pvEmpty").hidden = false;
}

function pvShow(idx) {
  const page = pvState.pages[idx];
  const wrap = $("pvFrameWrap");
  if (!page || wrap.hidden) return;
  pvState.idx = idx;
  const [w, h] = pvState.canvas;
  const frame = $("pvFrame");
  frame.srcdoc = page.html;
  const scale = wrap.clientWidth / w;
  frame.style.width = w + "px";
  frame.style.height = h + "px";
  frame.style.transform = "scale(" + scale + ")";
  wrap.style.height = Math.round((wrap.clientWidth * h) / w) + "px";
  document.querySelectorAll("#pvTabs button").forEach((b, i) => {
    b.classList.toggle("on", i === idx);
  });
}

/* 校验失败时跳到出错页并标红该标签 */
function pvFlag(idx) {
  const page = pvState.pages[idx];
  if (!page) return;
  const tabs = document.querySelectorAll("#pvTabs button");
  tabs.forEach((b) => b.classList.remove("bad"));
  if (tabs[idx]) tabs[idx].classList.add("bad");
  if (!$("pvFrameWrap").hidden) pvShow(idx);
}

function schedulePreview() {
  clearTimeout(pvTimer);
  pvTimer = setTimeout(fetchPreview, 300);
}

async function fetchPreview() {
  const text = md.value;
  if (!text.trim() || lint(text).length) { pvClear(); return; }
  const seq = ++pvSeq;
  try {
    const res = await fetch("/v1/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        markdown: text,
        aspect_ratio: $("aspect").value,
        theme: currentTheme(),
        max_bullets: Number($("bullets").value) || 6,
      }),
    });
    if (seq !== pvSeq) return;
    if (!res.ok) { pvClear(); return; }
    const body = await res.json();
    if (seq !== pvSeq) return;
    pvState = {
      pages: body.pages,
      idx: Math.min(pvState.idx, body.pages.length - 1),
      canvas: body.canvas,
    };
    const tabs = $("pvTabs");
    tabs.innerHTML = "";
    body.pages.forEach((p, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = p.kind === "cover" ? "封面" : "内容 " + i;
      b.addEventListener("click", () => pvShow(i));
      tabs.appendChild(b);
    });
    $("pvNote").textContent = `共 ${body.slides} 页`;
    $("pvEmpty").hidden = true;
    $("pvFrameWrap").hidden = false;
    pvShow(pvState.idx);
  } catch (e) { if (seq === pvSeq) pvClear(); }
}

/* ---------- 健康检查 ---------- */
async function checkHealth() {
  const el = $("health");
  try {
    const res = await fetch("/healthz");
    const h = await res.json();
    if (h.ok) { el.className = "badge ok"; el.textContent = `● 正常 · node ${h.node} · 并发 ${h.max_concurrency}`; }
    else { el.className = "badge bad"; el.textContent = `● 异常 ${JSON.stringify(h.checks)}`; }
  } catch (e) { el.className = "badge bad"; el.textContent = "● 服务不可达"; }
}

/* ---------- 绑定 ---------- */
md.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(refresh, 200); });
md.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); send(false); } });
$("bullets").addEventListener("input", refresh);
$("aspect").addEventListener("change", refresh);
$("style").addEventListener("change", () => {
  $("industry").disabled = $("style").value !== "cards";
  refresh();
});
$("industry").addEventListener("change", refresh);
window.addEventListener("resize", () => pvShow(pvState.idx));
$("btnGen").addEventListener("click", () => send(false));
$("btnDry").addEventListener("click", () => send(true));
$("btnExample").addEventListener("click", () => { md.value = EXAMPLE; refresh(); });
$("btnClear").addEventListener("click", () => { md.value = ""; refresh(); md.focus(); });

try { md.value = localStorage.getItem(STORE_KEY) || ""; } catch (e) { /* ignore */ }
refresh();
checkHealth();
