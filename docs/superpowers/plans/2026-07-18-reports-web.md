# 投研报告 Web 端浏览页面 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 本地起一个 Flask 服务浏览 reports/ 下 2000+ 篇投研报告：侧栏公司树导航 + 阅读区 + 标题搜索 + 仪表盘。

**Architecture:** 后端单文件 `web/server.py`（Flask，3 个 JSON API + 静态文件），实时扫描 reports/ 并从文件名/路径/首行 H1 推断元数据，不改动任何报告文件。前端单页应用（vanilla JS + 本地 vendor 的 marked.js），hash 路由，搜索在前端对索引过滤。

**Tech Stack:** Python 3.9 + Flask（`pip3 install --user flask`）、marked.js 12（本地 vendor）、无构建步骤。

**Spec:** `docs/superpowers/specs/2026-07-18-reports-web-design.md`

**注意：** 按用户要求本项目不写自动化测试，每个任务用命令/浏览器人工验证。commit message 用中文。**不要 push 到远程**（用户会自己决定）。

---

### Task 1: 环境准备与项目骨架

**Files:**
- Create: `web/static/`（目录）
- Create: `web/static/marked.min.js`（下载 vendor）

- [ ] **Step 1: 安装 Flask**

```bash
pip3 install --user flask
```

- [ ] **Step 2: 验证 Flask 可导入**

Run: `python3 -c "import flask; print(flask.__version__)"`
Expected: 输出版本号（如 `3.x.x`），无报错

- [ ] **Step 3: 创建目录并下载 marked.js**

```bash
mkdir -p web/static
curl -L -o web/static/marked.min.js https://cdn.jsdelivr.net/npm/marked@12.0.2/lib/marked.umd.min.js
```

- [ ] **Step 4: 验证 marked.js 下载成功**

Run: `head -c 100 web/static/marked.min.js && echo && wc -c web/static/marked.min.js`
Expected: 文件开头是 JS 代码（含 `marked` 字样），大小 > 30000 字节。若 CDN 不可达，改用 `https://unpkg.com/marked@12.0.2/lib/marked.umd.min.js`

- [ ] **Step 5: Commit**

```bash
git add web/static/marked.min.js
git commit -m "Web报告浏览：vendor marked.js"
```

---

### Task 2: 后端索引扫描与元数据推断（/api/index）

**Files:**
- Create: `web/server.py`

- [ ] **Step 1: 写 `web/server.py`（完整内容如下）**

```python
#!/usr/bin/env python3
"""投研报告本地浏览服务。启动：python3 web/server.py，访问 http://localhost:8600"""
import os
import re
import time
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

TYPE_KEYWORDS = [
    ("earnings", "财报解读"),
    ("thesis", "论文追踪"),
    ("checklist", "买入清单"),
    ("research", "投资研究"),
    ("industry", "行业研究"),
    ("funnel", "漏斗筛选"),
    ("private", "未上市公司"),
    ("team", "四师团队"),
    ("management", "管理层研究"),
    ("deepseek", "AI分析"),
    ("公众号", "公众号文章"),
]

DATE_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})")
QUARTER_RE = re.compile(r"(20\d{2})Q([1-4])", re.I)
# 顶级目录名去掉类型/日期后缀，如 思格新能-team-20260409 → 思格新能
DIR_SUFFIX_RE = re.compile(r"(?:-(?:team|private|research))?-20\d{6}$|-deepseek分析$")
THEME_HINTS = ("对比", "轮动", "全景", "筛选", "候选池", "预测", "决策", "10年", "5年")

_title_cache = {}  # abspath -> (mtime, title)


def read_title(path: Path) -> str:
    """报告第一个 H1，回退到文件名。按 mtime 缓存避免每次扫描重读 2000+ 文件。"""
    try:
        st = path.stat()
    except OSError:
        return path.stem
    cached = _title_cache.get(str(path))
    if cached and cached[0] == st.st_mtime:
        return cached[1]
    title = path.stem
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for _ in range(50):
                line = f.readline()
                if not line:
                    break
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
    except OSError:
        pass
    _title_cache[str(path)] = (st.st_mtime, title)
    return title


def infer_type(name: str) -> str:
    lower = name.lower()
    for key, label in TYPE_KEYWORDS:
        if key in lower:
            return label
    return "其他"


def infer_date(name: str, mtime: float) -> str:
    m = DATE_RE.search(name)
    if m:
        return "{}-{}-{}".format(m.group(1), m.group(2), m.group(3))
    m = QUARTER_RE.search(name)
    if m:
        return "{}-Q{}".format(m.group(1), m.group(2))
    return time.strftime("%Y-%m-%d", time.localtime(mtime))


def classify(rel: Path, holding_names: set) -> dict:
    """返回 {company, group}。group ∈ 持仓公司/公司/行业研究/主题对比/公众号文章/其他"""
    stem = rel.stem
    lower = stem.lower()
    if len(rel.parts) > 1:
        company = DIR_SUFFIX_RE.sub("", rel.parts[0]) or rel.parts[0]
        group = "持仓公司" if company in holding_names else "公司"
        return {"company": company, "group": group}
    # 根目录散文件
    if "公众号" in stem:
        return {"company": None, "group": "公众号文章"}
    if "industry" in lower:
        return {"company": None, "group": "行业研究"}
    if "funnel" in lower or "vs" in lower or any(h in stem for h in THEME_HINTS):
        return {"company": None, "group": "主题对比"}
    first = re.split(r"[-–—]", stem)[0].strip()
    rest = stem[len(first):]
    if first and infer_type(rest) != "其他":
        group = "持仓公司" if first in holding_names else "公司"
        return {"company": first, "group": group}
    return {"company": None, "group": "其他"}


HOLDING_ROW_RE = re.compile(
    r"^\|\s*\d+\s*\|\s*\**([^|*]+?)\**\s*\|\s*([A-Za-z0-9.\-]+)\s*\|\s*([\d.]+)%"
)


def parse_holdings():
    """解析 portfolio-latest.md「组合概览」小节的持仓表。失败返回空列表，不报错。"""
    pf = REPORTS_DIR / "portfolio-latest.md"
    holdings = []
    if not pf.exists():
        return holdings
    try:
        text = pf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return holdings
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = "组合概览" in line
            continue
        if in_section:
            m = HOLDING_ROW_RE.match(line.strip())
            if m:
                holdings.append(
                    {"name": m.group(1).strip(), "code": m.group(2), "pct": float(m.group(3))}
                )
    return holdings


def scan_index():
    holdings = parse_holdings()
    holding_names = {h["name"] for h in holdings}
    reports = []
    for path in REPORTS_DIR.rglob("*.md"):
        rel = path.relative_to(REPORTS_DIR)
        if any(part.startswith(".") for part in rel.parts):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        meta = classify(rel, holding_names)
        reports.append(
            {
                "path": str(rel),
                "title": read_title(path),
                "type": infer_type(rel.name),
                "date": infer_date(rel.name, st.st_mtime),
                "company": meta["company"],
                "group": meta["group"],
            }
        )
    reports.sort(key=lambda r: r["date"], reverse=True)
    return {"reports": reports, "holdings": holdings}


@app.route("/")
def home():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/index")
def api_index():
    return jsonify(scan_index())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8600)
```

- [ ] **Step 2: 启动并验证索引数量**

```bash
python3 web/server.py > /tmp/report_web.log 2>&1 &
sleep 2
curl -s "http://localhost:8600/api/index" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('reports:', len(d['reports']))
print('holdings:', len(d['holdings']))
print('sample:', d['reports'][0])
"
find reports -name "*.md" -not -path "*/.*" | wc -l
```

Expected:
- `reports:` 数量与 `find` 输出一致（约 2128）
- `holdings:` > 0（portfolio-latest.md 有持仓表）
- `sample` 是最新日期的报告，含 path/title/type/date/company/group 六个字段

- [ ] **Step 3: 抽查分类正确性**

```bash
curl -s "http://localhost:8600/api/index" | python3 -c "
import sys, json
d = json.load(sys.stdin)
by = {}
for r in d['reports']:
    by.setdefault(r['group'], 0)
    by[r['group']] += 1
print(by)
for r in d['reports']:
    if r['path'].startswith('腾讯/'):
        print(r['company'], '|', r['group'], '|', r['title'][:30]); break
for r in d['reports']:
    if 'industry' in r['path']:
        print(r['group'], '|', r['path']); break
"
pkill -f "python3 web/server.py"
```

Expected: 腾讯目录下报告 company=腾讯；`核电-industry-*` 的 group=行业研究；各分组都有数量

- [ ] **Step 4: Commit**

```bash
git add web/server.py
git commit -m "Web报告浏览：索引扫描与元数据推断API"
```

---

### Task 3: 后端报告读取与仪表盘（/api/report、/raw、/api/dashboard）

**Files:**
- Modify: `web/server.py`（在 `@app.route("/api/index")` 之后、`if __name__` 之前追加）

- [ ] **Step 1: 追加以下代码到 `web/server.py`**

```python
def safe_path(rel: str) -> Path:
    full = (REPORTS_DIR / rel).resolve()
    if not str(full).startswith(str(REPORTS_DIR.resolve()) + os.sep):
        abort(403)
    if not full.is_file():
        abort(404)
    return full


@app.route("/api/report")
def api_report():
    rel = request.args.get("path", "")
    full = safe_path(rel)
    if full.suffix != ".md":
        abort(403)
    return jsonify({"path": rel, "markdown": full.read_text(encoding="utf-8", errors="ignore")})


@app.route("/raw/<path:rel>")
def raw_file(rel):
    """报告内引用的图片等静态资源"""
    return send_file(safe_path(rel))


@app.route("/api/dashboard")
def api_dashboard():
    data = scan_index()
    reports = data["reports"]
    by_company, by_type, by_month = {}, {}, {}
    for r in reports:
        if r["company"]:
            by_company[r["company"]] = by_company.get(r["company"], 0) + 1
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        month = r["date"][:7]
        by_month[month] = by_month.get(month, 0) + 1
    return jsonify(
        {
            "recent": reports[:30],
            "holdings": data["holdings"],
            "top_companies": sorted(by_company.items(), key=lambda kv: -kv[1])[:20],
            "by_type": sorted(by_type.items(), key=lambda kv: -kv[1]),
            "by_month": sorted(by_month.items(), reverse=True)[:12],
        }
    )
```

- [ ] **Step 2: 重启并验证三个接口**

```bash
python3 web/server.py > /tmp/report_web.log 2>&1 &
sleep 2
# 正常读取
curl -s "http://localhost:8600/api/report?path=portfolio-latest.md" | head -c 200; echo
# 目录穿越必须被拒
curl -s -o /dev/null -w "traversal: %{http_code}\n" "http://localhost:8600/api/report?path=../CLAUDE.md"
# 仪表盘
curl -s "http://localhost:8600/api/dashboard" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('recent:', len(d['recent']), 'holdings:', len(d['holdings']))
print('top1:', d['top_companies'][0], 'types:', len(d['by_type']), 'months:', len(d['by_month']))
"
pkill -f "python3 web/server.py"
```

Expected: report 返回 JSON 且含 markdown 字段；`traversal: 403`；dashboard 各字段非空（recent=30）

- [ ] **Step 3: Commit**

```bash
git add web/server.py
git commit -m "Web报告浏览：报告读取、静态资源与仪表盘API"
```

---

### Task 4: 前端页面骨架（index.html + style.css）

**Files:**
- Create: `web/static/index.html`
- Create: `web/static/style.css`

- [ ] **Step 1: 写 `web/static/index.html`**

```html
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Berkshire 报告库</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header id="topbar">
  <span id="brand">📚 AI Berkshire</span>
  <input id="search" type="search" placeholder="搜索报告标题、公司名、文件名…">
  <button id="dash-btn">仪表盘</button>
</header>
<div id="layout">
  <nav id="sidebar"></nav>
  <main id="content"><div class="empty">从左侧选择报告，或点击右上角「仪表盘」</div></main>
  <aside id="toc"></aside>
</div>
<script src="/static/marked.min.js"></script>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: 写 `web/static/style.css`**

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; color: #24292f; background: #fff; }

/* 顶栏 */
#topbar { display: flex; align-items: center; gap: 16px; padding: 10px 20px; border-bottom: 1px solid #e1e4e8; position: sticky; top: 0; background: #fff; z-index: 10; }
#brand { font-weight: 700; font-size: 16px; white-space: nowrap; }
#search { flex: 1; max-width: 480px; padding: 7px 12px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 14px; outline: none; }
#search:focus { border-color: #0969da; }
#dash-btn { padding: 7px 14px; border: 1px solid #d0d7de; border-radius: 6px; background: #f6f8fa; cursor: pointer; font-size: 14px; }
#dash-btn:hover { background: #eef1f4; }

/* 三栏布局 */
#layout { display: flex; height: calc(100vh - 53px); }
#sidebar { width: 300px; overflow-y: auto; border-right: 1px solid #e1e4e8; padding: 12px 8px; flex-shrink: 0; }
#content { flex: 1; overflow-y: auto; padding: 24px 40px 60px; }
#toc { width: 260px; overflow-y: auto; border-left: 1px solid #e1e4e8; padding: 12px; flex-shrink: 0; font-size: 13px; }
.empty { color: #8b949e; padding: 40px; text-align: center; }

/* 侧栏 */
.group-title { font-size: 12px; font-weight: 700; color: #8b949e; text-transform: uppercase; margin: 14px 8px 6px; }
#sidebar details { margin: 0; }
#sidebar summary { cursor: pointer; padding: 5px 8px; border-radius: 6px; font-size: 14px; list-style-position: inside; }
#sidebar summary:hover { background: #f6f8fa; }
.count { color: #8b949e; font-size: 12px; }
.report-link { display: flex; justify-content: space-between; gap: 8px; padding: 5px 8px 5px 22px; border-radius: 6px; text-decoration: none; color: #24292f; font-size: 13px; }
.report-link:hover { background: #f6f8fa; }
.report-link.active { background: #ddf4ff; color: #0969da; }
.rl-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rl-date { color: #8b949e; font-size: 12px; white-space: nowrap; }

/* 报告正文 */
.report { max-width: 860px; margin: 0 auto; line-height: 1.75; font-size: 15px; }
.report h1 { font-size: 26px; border-bottom: 1px solid #e1e4e8; padding-bottom: 10px; }
.report h2 { font-size: 21px; margin-top: 32px; border-bottom: 1px solid #eaecef; padding-bottom: 6px; }
.report h3 { font-size: 17px; margin-top: 24px; }
.report table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 13.5px; display: block; overflow-x: auto; }
.report th, .report td { border: 1px solid #d0d7de; padding: 6px 10px; }
.report th { background: #f6f8fa; white-space: nowrap; }
.report tr:nth-child(even) { background: #fafbfc; }
.report blockquote { margin: 14px 0; padding: 8px 16px; border-left: 4px solid #d4a72c; background: #fff8e6; color: #57534e; border-radius: 0 6px 6px 0; }
.report code { background: #f6f8fa; padding: 2px 5px; border-radius: 4px; font-size: 13px; }
.report pre { background: #f6f8fa; padding: 12px; border-radius: 6px; overflow-x: auto; }
.report pre code { background: none; padding: 0; }
.report img { max-width: 100%; }
.report a { color: #0969da; }
.report hr { border: none; border-top: 1px solid #e1e4e8; margin: 24px 0; }

/* TOC */
.toc-title { font-size: 12px; font-weight: 700; color: #8b949e; margin: 14px 0 6px; }
#toc a { display: block; padding: 3px 6px; color: #57606a; text-decoration: none; border-radius: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#toc a:hover { background: #f6f8fa; color: #0969da; }
.toc-h3 { padding-left: 18px !important; font-size: 12px; }
#toc .report-link { padding-left: 6px; }

/* 仪表盘 */
.dash { max-width: 960px; margin: 0 auto; }
.dash h2 { font-size: 18px; margin-top: 32px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; }
.hcard { border: 1px solid #d0d7de; border-radius: 8px; padding: 12px; cursor: pointer; }
.hcard:hover { border-color: #0969da; box-shadow: 0 2px 8px rgba(9,105,218,.12); }
.hname { font-weight: 600; font-size: 14px; }
.hcode { color: #8b949e; font-size: 12px; margin: 2px 0; }
.hpct { font-size: 20px; font-weight: 700; color: #0969da; }
.recent .report-link { padding-left: 8px; font-size: 14px; }
.bar-row { display: flex; align-items: center; gap: 10px; margin: 4px 0; font-size: 13px; }
.bar-label { width: 140px; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 0; }
.bar { height: 14px; background: #54aeff; border-radius: 3px; min-width: 2px; }
.bar-n { color: #8b949e; }
```

- [ ] **Step 3: 验证骨架**

```bash
python3 web/server.py > /tmp/report_web.log 2>&1 &
sleep 2
curl -s -o /dev/null -w "home: %{http_code}\n" http://localhost:8600/
curl -s -o /dev/null -w "css: %{http_code}\n" http://localhost:8600/static/style.css
pkill -f "python3 web/server.py"
```

Expected: 两个都是 200（此时 app.js 还不存在，浏览器控制台会 404，属预期）

- [ ] **Step 4: Commit**

```bash
git add web/static/index.html web/static/style.css
git commit -m "Web报告浏览：前端页面骨架与样式"
```

---

### Task 5: 前端索引加载、侧栏树与搜索（app.js 第一部分）

**Files:**
- Create: `web/static/app.js`

- [ ] **Step 1: 写 `web/static/app.js`（完整文件，openReport/renderDashboard 先占位，Task 6/7 替换）**

```javascript
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
```

- [ ] **Step 2: 浏览器验证**

```bash
python3 web/server.py > /tmp/report_web.log 2>&1 &
sleep 1
open http://localhost:8600/
```

人工检查：
- 侧栏出现分组：持仓公司（置顶）→ 公司 → 行业研究 → 主题对比 → 公众号文章 → 其他，公司节点可展开且显示报告数
- 搜索框输入「腾讯」，侧栏变成过滤后的列表；清空恢复树形
- 点击任一报告，内容区显示「阅读页开发中」占位（预期）

验证完 `pkill -f "python3 web/server.py"`。

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js
git commit -m "Web报告浏览：侧栏公司树与标题搜索"
```

---

### Task 6: 阅读页（Markdown 渲染、TOC、链接图片、公司时间线）

**Files:**
- Modify: `web/static/app.js`（替换 `openReport` 占位函数，并在文件末尾 `boot();` 之前新增 `normalize`/`buildToc`）

- [ ] **Step 1: 用以下代码替换 `openReport` 占位函数**

```javascript
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
```

- [ ] **Step 2: 在 `boot();` 之前新增两个函数**

```javascript
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
```

- [ ] **Step 3: 浏览器验证**

```bash
python3 web/server.py > /tmp/report_web.log 2>&1 &
sleep 1
open http://localhost:8600/
```

人工检查（覆盖 spec 验证清单的三类报告）：
- 打开 `腾讯/腾讯-research-20260408.md`：表格有边框斑马纹、引用块黄色样式、★ 正常显示；右侧 TOC 列出各章节且点击平滑跳转；TOC 下方出现「腾讯」其他报告时间线
- 打开系列子目录 `智元机器人/《看懂智元机器人》/00-系列说明.md`：若文中有指向 01/02 的相对链接，点击能站内跳转
- 打开任一根目录散文件（如 `核电-industry-20260409.md`）正常渲染
- 浏览器前进/后退按钮可在报告间切换（hash 路由）

验证完 `pkill -f "python3 web/server.py"`。

- [ ] **Step 4: Commit**

```bash
git add web/static/app.js
git commit -m "Web报告浏览：阅读页渲染、TOC与公司时间线"
```

---

### Task 7: 仪表盘页

**Files:**
- Modify: `web/static/app.js`（替换 `renderDashboard` 占位函数，新增 `bar` 辅助函数）

- [ ] **Step 1: 用以下代码替换 `renderDashboard` 占位函数**

```javascript
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
  $("#content").querySelectorAll(".hcard").forEach((c) => {
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
```

- [ ] **Step 2: 浏览器验证**

```bash
python3 web/server.py > /tmp/report_web.log 2>&1 &
sleep 1
open http://localhost:8600/#dash
```

人工检查：
- 持仓卡片与 `reports/portfolio-latest.md` 组合概览表一致（名称、代码、占比）
- 点击持仓卡片 → 侧栏过滤出该公司报告
- 最近报告流按日期倒序、点击可打开
- 三组统计条形图正常显示、比例合理

验证完 `pkill -f "python3 web/server.py"`。

- [ ] **Step 3: Commit**

```bash
git add web/static/app.js
git commit -m "Web报告浏览：仪表盘（持仓卡片、最近报告、覆盖统计）"
```

---

### Task 8: 综合验证（spec 验证清单）

**Files:** 无新文件

- [ ] **Step 1: 索引完整性核对**

```bash
python3 web/server.py > /tmp/report_web.log 2>&1 &
sleep 2
echo "API: $(curl -s http://localhost:8600/api/index | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["reports"]))')"
echo "文件系统: $(find reports -name '*.md' -not -path '*/.*' | wc -l | tr -d ' ')"
```

Expected: 两个数字一致

- [ ] **Step 2: 新报告即时可见验证**

```bash
echo "# 测试报告-删除我" > "reports/测试报告-research-20260718.md"
curl -s "http://localhost:8600/api/index" | python3 -c "
import sys, json
d = json.load(sys.stdin)
hit = [r for r in d['reports'] if '测试报告' in r['path']]
print('found:', hit)
"
rm "reports/测试报告-research-20260718.md"
pkill -f "python3 web/server.py"
```

Expected: `found:` 非空且 company=测试报告、type=投资研究（无需重启服务）

- [ ] **Step 3: 浏览器过一遍完整流程**

启动服务，依次操作并确认无 console 报错：
1. 首页 → 侧栏树完整、持仓公司置顶
2. 搜索「earnings」→ 出现财报类报告
3. 打开一篇长报告 → 渲染、TOC、时间线正常
4. 仪表盘 → 三块内容正常
5. 后退按钮回到上一篇报告

- [ ] **Step 4: 完成**

告知用户全部完成，启动命令为 `python3 web/server.py`。按 CLAUDE.md 惯例询问是否推送到 GitHub（不要主动 push）。
