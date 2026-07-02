"""漏斗编排: 股票池过滤 -> 批量因子 -> 明细因子 -> 行业中性Z -> 复合排名.

分工提醒: 本文件只做"能量化的部分"(可复现). 十五五主题映射/催化剂/护城河
由 AI 在候选榜上二次处理, 不写进这里.
"""

from __future__ import annotations

import os
import time
import datetime as dt
from typing import Optional

import numpy as np
import pandas as pd

from . import config, factors
from .provider import DataProvider, get_provider


# ---------------------------------------------------------------------------
# 第0层: 主板股票池机械过滤
# ---------------------------------------------------------------------------

def filter_universe(spot: pd.DataFrame) -> pd.DataFrame:
    df = spot.copy()
    df = df[df["code"].str.startswith(config.MAINBOARD_PREFIXES)]
    if config.EXCLUDE_ST:
        name = df["name"].fillna("")
        keep = ~name.str.upper().str.contains("ST", regex=False) & ~name.str.contains("退", regex=False)
        df = df[keep.values]
    cap_yi = pd.to_numeric(df["total_mktcap"], errors="coerce") / 1e8
    df = df[cap_yi >= config.MIN_MKTCAP_YI]
    if config.MAX_MKTCAP_YI:
        df = df[cap_yi <= config.MAX_MKTCAP_YI]
    df["mktcap_yi"] = cap_yi
    return df.reset_index(drop=True)


def attach_industry(df: pd.DataFrame, industry_map: pd.DataFrame) -> pd.DataFrame:
    if industry_map is None or industry_map.empty:
        df["industry"] = "全市场"
        return df
    return df.merge(industry_map, on="code", how="left").fillna({"industry": "其他"})


# ---------------------------------------------------------------------------
# 批量层因子(来自快照): 价值收益率 + 拥挤(换手)
# ---------------------------------------------------------------------------

def batch_factors(df: pd.DataFrame) -> pd.DataFrame:
    ys = df.apply(lambda r: factors.value_yields(r["pe_ttm"], r["pb"], r["ps_ttm"]), axis=1)
    df["ep"] = [y["ep"] for y in ys]
    df["bp"] = [y["bp"] for y in ys]
    df["sp"] = [y["sp"] for y in ys]
    df["turnover_f"] = pd.to_numeric(df["turnover"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# 明细层因子(逐票取数): 质量 + 动量 + 研报覆盖
# ---------------------------------------------------------------------------

def detail_factors(provider: DataProvider, df: pd.DataFrame,
                   limit: int, verbose: bool = True) -> pd.DataFrame:
    sub = df.head(limit).copy()
    q_fscore, q_gp, q_acc, q_roe, q_roic, q_nig = [], [], [], [], [], []
    mom, rep = [], []
    n = len(sub)
    for i, (_, row) in enumerate(sub.iterrows(), 1):
        code = row["code"]
        try:
            fin = provider.get_financials(code)
            q_fscore.append(factors.piotroski_fscore(fin)["fscore"])
            q_gp.append(factors.gross_profitability(fin))
            q_acc.append(factors.accrual_quality(fin))
            q_roe.append(factors.latest(fin, "roe"))
            q_roic.append(factors.latest(fin, "roic"))
            q_nig.append(factors.latest(fin, "ni_growth"))
        except Exception:
            q_fscore.append(None); q_gp.append(None); q_acc.append(None)
            q_roe.append(None); q_roic.append(None); q_nig.append(None)
        try:
            price = provider.get_price_history(code, config.MOMENTUM_LOOKBACK_DAYS)
            mom.append(factors.momentum_12_1(price))
        except Exception:
            mom.append(None)
        try:
            rep.append(provider.get_report_count(code, config.REPORT_COUNT_MONTHS))
        except Exception:
            rep.append(-1)
        if verbose and (i % 20 == 0 or i == n):
            print(f"  明细取数 {i}/{n} ...", flush=True)
        time.sleep(config.REQUEST_SLEEP)

    sub["fscore"] = q_fscore
    sub["gross_prof"] = q_gp
    sub["accrual_q"] = q_acc
    sub["roe"] = q_roe
    sub["roic"] = q_roic
    sub["ni_growth"] = q_nig
    sub["momentum"] = mom
    rep_s = pd.Series(rep, index=sub.index)
    sub["report_count"] = rep_s.where(rep_s >= 0)  # -1 视为缺失
    return sub


# ---------------------------------------------------------------------------
# 行业中性 Z 分 + 复合打分
# ---------------------------------------------------------------------------

def _winsor_z(s: pd.Series, q: float) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if s.notna().sum() < 3:
        return pd.Series(np.nan, index=s.index)
    lo, hi = s.quantile(q), s.quantile(1 - q)
    s = s.clip(lo, hi)
    mu, sd = s.mean(), s.std(ddof=0)
    if not sd or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sd


def industry_neutral_z(df: pd.DataFrame, col: str, higher_better: bool = True) -> pd.Series:
    grp = df.groupby("industry", group_keys=False) if config.INDUSTRY_NEUTRAL else [("all", df)]
    if config.INDUSTRY_NEUTRAL:
        z = df.groupby("industry", group_keys=False)[col].apply(
            lambda s: _winsor_z(s, config.WINSOR_QUANTILE))
    else:
        z = _winsor_z(df[col], config.WINSOR_QUANTILE)
    z = z if higher_better else -z
    return z


def composite_score(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # 各原始因子 -> 行业中性 z (标注方向)
    specs = {
        "quality": [("fscore", True), ("gross_prof", True), ("accrual_q", True),
                    ("roe", True), ("roic", True), ("ni_growth", True)],
        "value": [("ep", True), ("bp", True), ("sp", True)],
        "momentum": [("momentum", True)],
        "crowding": [("turnover_f", False), ("report_count", False)],  # 越低越冷门=越好
    }
    cat_scores = {}
    for cat, members in specs.items():
        zcols = []
        for col, hb in members:
            if col not in df.columns:
                continue
            zc = f"z_{col}"
            df[zc] = industry_neutral_z(df, col, hb)
            zcols.append(zc)
        cat_scores[cat] = df[zcols].mean(axis=1, skipna=True) if zcols else 0.0
        df[f"score_{cat}"] = cat_scores[cat]

    w = config.FACTOR_WEIGHTS
    df["score_total"] = sum(w[c] * df[f"score_{c}"].fillna(0) for c in w)

    # 价值陷阱护栏: 质量/动量分位过低者剔除
    df["pct_quality"] = df["score_quality"].rank(pct=True)
    df["pct_momentum"] = df["score_momentum"].rank(pct=True)
    g = config.VALUE_TRAP_GUARD
    df["passed_guard"] = (
        (df["pct_quality"] >= g["min_quality_pct"]) &
        (df["pct_momentum"] >= g["min_momentum_pct"])
    )
    return df.sort_values("score_total", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------

def run_screen(provider: Optional[DataProvider] = None,
               limit: int = config.DETAIL_LIMIT_DEFAULT,
               theme_codes: Optional[set] = None,
               fetch_industry: bool = True,
               verbose: bool = True) -> pd.DataFrame:
    provider = provider or get_provider()
    if verbose:
        print(f"[1/5] 数据源={provider.name} 拉取全市场快照 ...", flush=True)
    spot = provider.get_spot()

    if verbose:
        print("[2/5] 第0层主板池过滤 ...", flush=True)
    uni = filter_universe(spot)
    if theme_codes:
        uni = uni[uni["code"].isin(theme_codes)].reset_index(drop=True)
    if verbose:
        print(f"      通过池: {len(uni)} 只", flush=True)

    industry_map = None
    if fetch_industry:
        if verbose:
            print("[3/5] 拉取行业分类(用于中性化) ...", flush=True)
        try:
            industry_map = provider.get_industry_map()
        except Exception as e:  # noqa: BLE001
            print(f"      行业分类获取失败, 退化为全市场中性: {e}")
    uni = attach_industry(uni, industry_map)
    uni = batch_factors(uni)

    # 批量层先按"价值收益率+低换手"粗排, 决定明细取数优先级(控制耗时)
    uni["_prelim"] = (
        uni["ep"].rank(pct=True).fillna(0)
        + uni["bp"].rank(pct=True).fillna(0)
        - uni["turnover_f"].rank(pct=True).fillna(0.5)
    )
    uni = uni.sort_values("_prelim", ascending=False).reset_index(drop=True)

    if verbose:
        print(f"[4/5] 明细取数(质量/动量/研报), 上限 {limit} 只 ...", flush=True)
    detailed = detail_factors(provider, uni, limit=limit, verbose=verbose)

    if verbose:
        print("[5/5] 行业中性Z + 复合打分排名 ...", flush=True)
    ranked = composite_score(detailed)
    return ranked


def save_report(ranked: pd.DataFrame, outdir: Optional[str] = None) -> str:
    date = dt.date.today().strftime("%Y%m%d")
    outdir = outdir or os.path.join(config.OUTPUT_DIR, f"十五五主板筛选-{date}")
    os.makedirs(outdir, exist_ok=True)
    csv_path = os.path.join(outdir, "screen_full.csv")
    ranked.to_csv(csv_path, index=False, encoding="utf-8-sig")

    cols = ["code", "name", "industry", "mktcap_yi", "score_total",
            "score_quality", "score_value", "score_crowding", "score_momentum",
            "fscore", "roe", "ni_growth", "pe_ttm", "pb", "passed_guard"]
    top = ranked[ranked.get("passed_guard", True)].head(30)
    md = [f"# 十五五主板冷门潜力池 — {date}", "",
          f"通过价值陷阱护栏候选 Top 30 (全量见 `screen_full.csv`, 共 {len(ranked)} 只)", ""]
    show = [c for c in cols if c in top.columns]
    md.append("| " + " | ".join(show) + " |")
    md.append("|" + "|".join(["---"] * len(show)) + "|")
    for _, r in top.iterrows():
        md.append("| " + " | ".join(
            f"{r[c]:.2f}" if isinstance(r[c], (int, float, np.floating)) and c not in ("code",)
            else str(r[c]) for c in show) + " |")
    md += ["", "> 量化初筛结果, 不构成投资建议. 下一步交 AI 做十五五主题映射/催化剂/护城河终审."]
    md_path = os.path.join(outdir, "README.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return outdir


# ---------------------------------------------------------------------------
# 离线自测: 用合成数据验证因子与打分逻辑(无网络)
# ---------------------------------------------------------------------------

def selftest() -> bool:
    print("=" * 60)
    print("离线自测: 因子 + 复合打分(合成数据, 无网络)")
    print("=" * 60)
    ok = True

    # 构造一只"好票": 连续两年 ROA 上升/现金流>净利/去杠杆/毛利升/周转升/不摊薄
    good = pd.DataFrame({
        "roa": [12, 9], "ocf": [120, 90], "net_income_parent": [100, 80],
        "eps": [1.0, 0.8], "debt_ratio": [40, 45], "current_ratio": [2.0, 1.6],
        "gross_margin": [55, 50], "asset_turnover": [0.9, 0.8], "ocf_to_ni": [1.2, 1.1],
        "revenue": [500, 450], "cogs": [225, 225], "equity": [800, 700],
        "roic": [15, 13], "ni_growth": [25, 20],
    }, index=["20241231", "20231231"])
    fs = factors.piotroski_fscore(good)
    print(f"  好票 F-Score = {fs['fscore']}/9  detail={fs['detail']}")
    assert fs["fscore"] >= 8, "好票F-Score应接近满分"
    gp = factors.gross_profitability(good)
    print(f"  好票 毛利资产比 = {gp:.4f}")
    assert gp and gp > 0

    # 差票: ROA下降/现金流<净利/加杠杆/毛利降
    bad = good.copy()
    bad.loc["20241231", ["roa", "gross_margin", "asset_turnover", "current_ratio"]] = [5, 45, 0.7, 1.4]
    bad.loc["20241231", ["debt_ratio", "ocf_to_ni"]] = [60, 0.5]
    fb = factors.piotroski_fscore(bad)
    print(f"  差票 F-Score = {fb['fscore']}/9")
    assert fb["fscore"] < fs["fscore"], "差票应低于好票"

    # 动量
    up = pd.DataFrame({"close": list(np.linspace(10, 20, 300))})
    down = pd.DataFrame({"close": list(np.linspace(20, 10, 300))})
    mu, md_ = factors.momentum_12_1(up), factors.momentum_12_1(down)
    print(f"  上涨票动量={mu:.3f}  下跌票动量={md_:.3f}")
    assert mu > 0 > md_, "动量方向应正确"

    # 复合打分: 3只票行业内 z + 排名
    df = pd.DataFrame({
        "code": ["600001", "600002", "600003", "600004"],
        "name": ["好", "中", "差", "冷门好"],
        "industry": ["A", "A", "A", "A"],
        "fscore": [8, 5, 2, 8], "gross_prof": [0.4, 0.25, 0.1, 0.42],
        "accrual_q": [1.3, 1.0, 0.6, 1.25], "roe": [22, 12, 4, 20],
        "roic": [18, 10, 3, 17], "ni_growth": [25, 8, -10, 22],
        "ep": [0.08, 0.05, 0.03, 0.09], "bp": [0.5, 0.3, 0.2, 0.55],
        "sp": [0.4, 0.3, 0.2, 0.45], "momentum": [0.2, 0.05, -0.3, 0.15],
        "turnover_f": [3.0, 5.0, 8.0, 1.2], "report_count": [15, 10, 3, 2],
    })
    ranked = composite_score(df)
    print("\n  复合打分结果:")
    print(ranked[["name", "score_total", "score_quality", "score_crowding",
                  "passed_guard"]].to_string(index=False))
    # "冷门好"(低换手低研报+高质量)应排在"差"之前, 且差票过不了护栏
    assert ranked.iloc[-1]["name"] == "差", "最差票应垫底"
    assert not ranked[ranked["name"] == "差"]["passed_guard"].iloc[0], "差票应被护栏拦下"
    print("\n  ✅ 全部断言通过")
    return ok
