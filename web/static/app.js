/* global marked */
let INDEX = null;
let CURRENT_PATH = null;

const $ = (s) => document.querySelector(s);
const GROUP_ORDER = ["持仓公司", "公司", "行业研究", "主题对比", "公众号文章", "其他"];

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
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
        html += `<details${open}><summary>${esc(comp)} <span class="count">${reps.length}</span></summary>` +
          reps.map(reportLink).join("") + `</details>`;
      }
    }
  }
  box.innerHTML = html;
  const active = box.querySelector(".report-link.active");
  if (active) active.scrollIntoView({ block: "center" });
}

async function openReport(path) {
  // Task 6 实现
  $("#content").innerHTML = `<div class="empty">阅读页开发中：${esc(path)}</div>`;
}

async function renderDashboard() {
  // Task 7 实现
  $("#content").innerHTML = `<div class="empty">仪表盘开发中</div>`;
}

boot();
