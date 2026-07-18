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
    r"^\|\s*\d+\s*\|\s*\**([^|*]+?)\**\s*\|\s*([A-Za-z0-9.\-]+)\s*\|\s*(\d+(?:\.\d+)?)%"
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8600)
