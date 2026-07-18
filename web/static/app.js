/* global marked */
let INDEX = null;
let CURRENT_PATH = null;

const $ = (s) => document.querySelector(s);
const GROUP_ORDER = ["持仓公司", "观察中", "公司", "行业研究", "主题对比", "公众号文章", "其他"];

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function toggleObserve(company) {
  const idx = (INDEX.observe || []).indexOf(company);
  const url = idx >= 0 ? "/api/observe/remove" : "/api/observe/add";
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: company }) });
  INDEX.observe = await res.json();
  renderSidebar($("#search").value.trim());
  if (location.hash === "#dash") renderDashboard();
}

async function boot() {
  INDEX = await (await fetch("/api/index")).json();
  renderSidebar();
  $("#search").addEventListener("input", (e) => renderSidebar(e.target.value.trim()));
  $("#dash-btn").addEventListener("click", () => { location.hash = "#dash"; });
  window.addEventListener("hashchange", route);
  route();
}

function route() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h === "dash") { renderDashboard(); return; }
  if (h.startsWith("r=")) { openReport(h.slice(2)); }
}

function reportLink(r) {
  const cls = r.path === CURRENT_PATH ? "report-link active" : "report-link";
  return `<a class="${cls}" href="#r=${encodeURIComponent(r.path)}" title="${esc(r.path)}">` +
    `<span class="rl-title">${esc(r.title)}</span><span class="rl-date">${r.date}</span></a>`;
}

function renderSidebar(query = "") {
  const box = $("#sidebar");
  if (query) {
    const q = query.toLowerCase();
    const hits = INDEX.reports.filter((r) =>
      r.title.toLowerCase().includes(q) ||
      r.path.toLowerCase().includes(q) ||
      (r.company || "").toLowerCase().includes(q)
    ).slice(0, 200);
    box.innerHTML = `<div class="group-title">搜索结果（${hits.length}）</div>` +
      hits.map(reportLink).join("");
    return;
  }
  const groups = new Map(GROUP_ORDER.map((g) => [g, new Map()]));
  for (const r of INDEX.reports) {
    const g = groups.has(r.group) ? r.group : "其他";
    const key = r.company || "_";
    const m = groups.get(g);
    if (!m.has(key)) m.set(key, []);
    m.get(key).push(r);
  }
  let html = "";
  for (const [gname, companies] of groups) {
    let total = 0;
    companies.forEach((list) => { total += list.length; });
    if (!total) continue;
    html += `<div class="group-title">${gname}（${total}）</div>`;
    if (companies.size === 1 && companies.has("_")) {
      html += companies.get("_").map(reportLink).join("");
    } else {
      const sorted = [...companies.entries()].sort((a, b) => b[1].length - a[1].length);
      for (const [comp, reps] of sorted) {
        const open = reps.some((r) => r.path === CURRENT_PATH) ? " open" : "";
        const isObserved = INDEX.observe && INDEX.observe.includes(comp);
        html += `<details${open}><summary>` +
          `<span class="observe-star" data-company="${esc(comp)}" title="${isObserved ? '取消观察' : '加入观察'}">${isObserved ? '★' : '☆'}</span>` +
          `${esc(comp)} <span class="count">${reps.length}</span></summary>` +
          reps.map(reportLink).join("") + `</details>`;
      }
    }
  }
  box.innerHTML = html;
  box.querySelectorAll(".observe-star").forEach((star) => {
    star.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleObserve(star.dataset.company);
    });
  });
  const active = box.querySelector(".report-link.active");
  if (active) active.scrollIntoView({ block: "center" });
}

async function openReport(path) {
  const res = await fetch("/api/report?path=" + encodeURIComponent(path));
  if (!res.ok) {
    $("#content").innerHTML = `<div class="empty">加载失败（${res.status}）：${esc(path)}</div>`;
    return;
  }
  const data = await res.json();
  CURRENT_PATH = path;
  const dir = path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : "";
  const el = $("#content");
  el.innerHTML = `<article class="report">${marked.parse(data.markdown)}</article>`;
  el.scrollTop = 0;
  // 相对图片走 /raw/
  el.querySelectorAll("img").forEach((img) => {
    const src = img.getAttribute("src") || "";
    if (!/^(https?:|data:|\/)/.test(src)) img.src = "/raw/" + dir + src;
  });
  // 相对 .md 链接站内跳转；外链新窗口
  el.querySelectorAll("a").forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (/^(https?:)/.test(href)) {
      a.target = "_blank";
    } else if (href.endsWith(".md") && !href.startsWith("#")) {
      a.href = "#r=" + encodeURIComponent(normalize(dir + decodeURIComponent(href)));
    }
  });
  buildToc(el);
  renderSidebar($("#search").value.trim());
}

async function renderDashboard() {
  CURRENT_PATH = null;
  const d = await (await fetch("/api/dashboard")).json();
  const maxC = d.top_companies.length ? d.top_companies[0][1] : 1;
  const maxT = d.by_type.length ? d.by_type[0][1] : 1;
  const maxM = Math.max(...d.by_month.map((x) => x[1]), 1);
  $("#toc").innerHTML = "";
  $("#content").innerHTML = `
  <div class="dash">
    <h2>📌 组合持仓</h2>
    <div class="cards">${d.holdings.map((h) => `
      <div class="hcard" data-name="${esc(h.name)}">
        <div class="hname">${esc(h.name)}</div>
        <div class="hcode">${esc(h.code)}</div>
        <div class="hpct">${h.pct}%</div>
      </div>`).join("") || "<div class='empty'>未能从 portfolio-latest.md 解析到持仓</div>"}
    </div>
    <h2>👁 观察中</h2>
    <div class="cards">${(d.observe || []).length ? d.observe.map((name) => `
      <div class="hcard observe-card" data-name="${esc(name)}">
        <div class="hname">${esc(name)}</div>
      </div>`).join("") : "<div class='empty'>暂无观察对象。在侧栏公司名旁点击 ☆ 添加</div>"}
    </div>
    <h2>🕐 最近报告</h2>
    <div class="recent">${d.recent.map(reportLink).join("")}</div>
    <h2>📊 研究覆盖 Top 20（按公司）</h2>
    ${d.top_companies.map(([c, n]) => bar(c, n, maxC)).join("")}
    <h2>📁 按类型</h2>
    ${d.by_type.map(([t, n]) => bar(t, n, maxT)).join("")}
    <h2>📅 按月份（近12个月）</h2>
    ${d.by_month.map(([m, n]) => bar(m, n, maxM)).join("")}
  </div>`;
  $("#content").scrollTop = 0;
  $("#content").querySelectorAll(".hcard,.observe-card").forEach((c) => {
    c.addEventListener("click", () => {
      $("#search").value = c.dataset.name;
      renderSidebar(c.dataset.name);
    });
  });
}

function bar(label, n, max) {
  return `<div class="bar-row"><span class="bar-label">${esc(label)}</span>` +
    `<span class="bar" style="width:${Math.round((n / max) * 60)}%"></span>` +
    `<span class="bar-n">${n}</span></div>`;
}

function normalize(p) {
  const parts = [];
  for (const seg of p.split("/")) {
    if (seg === "" || seg === ".") continue;
    if (seg === "..") parts.pop();
    else parts.push(seg);
  }
  return parts.join("/");
}

function buildToc(contentEl) {
  const heads = contentEl.querySelectorAll("h2, h3");
  let html = "<div class='toc-title'>目录</div>";
  heads.forEach((h, i) => {
    h.id = "h-" + i;
    html += `<a class="toc-${h.tagName.toLowerCase()}" href="#" data-target="h-${i}">${esc(h.textContent)}</a>`;
  });
  const cur = INDEX.reports.find((r) => r.path === CURRENT_PATH);
  if (cur && cur.company) {
    const others = INDEX.reports.filter((r) => r.company === cur.company && r.path !== cur.path);
    if (others.length) {
      html += `<div class="toc-title">「${esc(cur.company)}」其他报告（${others.length}）</div>` +
        others.map(reportLink).join("");
    }
  }
  $("#toc").innerHTML = html;
  $("#toc").querySelectorAll("a[data-target]").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const t = document.getElementById(a.dataset.target);
      if (t) t.scrollIntoView({ behavior: "smooth" });
    });
  });
}

boot();
