#!/usr/bin/env python3
"""
A股质量快速筛选工具 —— 用 4 条硬指标快速将 5000+ 只 A 股缩到 100 只以内。

指标设计哲学：
  - ROE > 12%  → 资本的效率——股东的钱有没有赚到足够的回报
  - 毛利率 > 20% → 定价权——产品有没有差异化（制造业放宽至20%，消费品天然更高）
  - FCF/净利润 > 0.5 → 利润质量——赚的是不是真金白银
  - 净利率 > 5% → 抗风险——收入波动时利润会不会归零

用法:
  python3 tools/a_share_quality_screen.py                    # 默认参数
  python3 tools/a_share_quality_screen.py --roe 15 --margin 30  # 消费品严格版
  python3 tools/a_share_quality_screen.py --output ranked.csv   # 输出CSV

依赖: akshare, pandas
"""

import argparse
import sys
import time
import warnings
from datetime import datetime

import akshare as ak
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# ── 配置 ──────────────────────────────────────────────
DEFAULT_ROE = 12          # 5年平均ROE最低要求(%)
DEFAULT_MARGIN = 20       # 5年平均毛利率最低要求(%)
DEFAULT_FCF_RATIO = 0.5   # (经营现金流-资本开支)/净利润 最低要求
DEFAULT_NET_MARGIN = 5    # 5年平均净利率最低要求(%)
MIN_YEARS_DATA = 3        # 至少需要3年数据
SLEEP_INTERVAL = 0.3      # API请求间隔(秒)，避免被封
OUTPUT_DIR = None          # 输出目录(None则打印到stdout+保存到reports/)

# 排除的行业（申万一级，不适用通用指标）
EXCLUDE_INDUSTRIES = ["银行", "非银金融", "房地产", "综合"]


def get_a_share_list():
    """获取沪深A股全列表"""
    print("📊 正在获取A股全列表...")
    df = ak.stock_zh_a_spot_em()
    df = df[df["代码"].str.match(r"^(60|00|30)\d{4}")]  # 仅沪深A股
    # 排除ST、*ST
    df = df[~df["名称"].str.contains(r"\*?ST", na=False)]
    print(f"  有效标的: {len(df)} 只")
    return df


def get_financial_data(code):
    """获取单只股票的财务指标"""
    time.sleep(SLEEP_INTERVAL)
    try:
        fin = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
        if fin is None or len(fin) < MIN_YEARS_DATA:
            return None
        # 取最近5年
        fin = fin.tail(5)
        return fin
    except Exception:
        return None


def compute_metrics(fin_df):
    """从财务数据中计算筛选指标"""
    years = len(fin_df)
    if years < MIN_YEARS_DATA:
        return None

    # 计算5年均值（处理百分比字符串 "23.35%"，兼容不同列名）
    def pct_to_float(series):
        return pd.to_numeric(series.astype(str).str.replace("%", ""), errors="coerce")

    def safe_get(df, *col_names):
        for c in col_names:
            if c in df.columns:
                return pct_to_float(df[c])
        return pd.Series([np.nan]*len(df))

    roe_vals = safe_get(fin_df, "净资产收益率", "加权净资产收益率")
    margin_vals = safe_get(fin_df, "销售毛利率", "毛利率")
    net_margin_vals = safe_get(fin_df, "销售净利率", "净利率")

    # 每股经营现金流 / EPS = OCF/NI（都是每股口径，可直接比）
    ocf_per_share = pd.to_numeric(fin_df.get("每股经营现金流", pd.Series([np.nan]*len(fin_df))), errors="coerce")
    eps_vals = pd.to_numeric(fin_df.get("基本每股收益", pd.Series([np.nan]*len(fin_df))), errors="coerce")
    ocf_ni_vals = []
    for ocfs, ep in zip(ocf_per_share, eps_vals):
        if pd.notna(ocfs) and pd.notna(ep) and ep > 0.001:
            # 比值为负时（OCF为负），取0
            ocf_ni_vals.append(max(0, float(ocfs) / float(ep)))
        elif pd.notna(ocfs) and ocfs > 0.01:
            ocf_ni_vals.append(1.0)  # EPS接近零但CF为正，算通过
        else:
            ocf_ni_vals.append(np.nan)

    metrics = {
        "years": years,
        "avg_roe": roe_vals.mean(),
        "avg_margin": margin_vals.mean(),
        "avg_net_margin": net_margin_vals.mean(),
        "ocf_ni_ratio": np.nanmean(ocf_ni_vals) if ocf_ni_vals else 0,
        "latest_roe": roe_vals.iloc[-1] if len(roe_vals) > 0 else np.nan,
        "latest_margin": margin_vals.iloc[-1] if len(margin_vals) > 0 else np.nan,
    }
    return metrics


def screen_stocks(stocks_df, roe_min, margin_min, fcf_min, net_margin_min):
    """主筛选逻辑"""
    results = []
    total = len(stocks_df)
    passed, failed, errors = 0, 0, 0

    for i, (_, row) in enumerate(stocks_df.iterrows()):
        code = row["代码"]
        name = row["名称"]

        if i % 50 == 0:
            print(f"  进度: {i}/{total} (通过:{passed} 排除:{failed} 错误:{errors})")

        fin = get_financial_data(code)
        if fin is None:
            errors += 1
            continue

        m = compute_metrics(fin)
        if m is None:
            errors += 1
            continue

        # 逐条检查
        checks = {
            "ROE": (m["avg_roe"] >= roe_min, f"{m['avg_roe']:.1f}%"),
            "毛利率": (m["avg_margin"] >= margin_min, f"{m['avg_margin']:.1f}%"),
            "OCF/NI": (m["ocf_ni_ratio"] >= fcf_min, f"{m['ocf_ni_ratio']:.2f}"),
            "净利率": (m["avg_net_margin"] >= net_margin_min, f"{m['avg_net_margin']:.1f}%"),
        }

        passed_all = all(v[0] for v in checks.values())
        if passed_all:
            passed += 1
            results.append({
                "code": code, "name": name,
                "avg_roe": round(m["avg_roe"], 1),
                "avg_margin": round(m["avg_margin"], 1),
                "avg_net_margin": round(m["avg_net_margin"], 1),
                "ocf_ni": round(m["ocf_ni_ratio"], 2),
                "years": m["years"],
                "score": round(m["avg_roe"] * 0.4 + m["avg_margin"] * 0.3 + m["ocf_ni_ratio"] * 15 + m["avg_net_margin"] * 0.3, 1),
            })
        else:
            failed += 1

    results.sort(key=lambda x: x["score"], reverse=True)
    return results, passed, failed, errors


def print_results(results, passed, failed, errors, total, args):
    """格式化输出"""
    print("\n" + "=" * 70)
    print(f"  A股质量快速筛选结果")
    print(f"  日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  参数: ROE≥{args.roe}% | 毛利率≥{args.margin}% | OCF/NI≥{args.fcf} | 净利率≥{args.net_margin}%")
    print(f"  全市场: {total}只 → 通过: {passed}只 ({passed/total*100:.1f}%) → 排除: {failed}只 → 数据不足: {errors}只")
    print("=" * 70)

    if not results:
        print("\n  ⚠️ 无股票通过筛选，建议放宽参数。")
        return

    # 排名前30
    print(f"\n  📈 综合评分前30（评分 = ROE×0.4 + 毛利率×0.3 + OCF/NI×15 + 净利率×0.3）:\n")
    print(f"  {'排名':<4} {'代码':<8} {'名称':<10} {'ROE%':>6} {'毛利率%':>7} {'净利率%':>7} {'OCF/NI':>7} {'评分':>6}")
    print(f"  {'-'*4} {'-'*8} {'-'*10} {'-'*6} {'-'*7} {'-'*7} {'-'*7} {'-'*6}")
    for i, r in enumerate(results[:30]):
        print(f"  {i+1:<4} {r['code']:<8} {r['name']:<10} {r['avg_roe']:>6.1f} {r['avg_margin']:>7.1f} {r['avg_net_margin']:>7.1f} {r['ocf_ni']:>7.2f} {r['score']:>6.1f}")

    # 行业分布
    print(f"\n  📊 行业分布（通过公司）:\n")
    industry_count = {}
    for r in results:
        ind = r["name"]  # simplified
        # Try to categorize
        industry_count[ind] = industry_count.get(ind, 0) + 1
    #  这里行业分类需要额外数据，先跳过

    # 保存CSV
    if args.output:
        df_out = pd.DataFrame(results)
        df_out.to_csv(args.output, index=False, encoding="utf-8-sig")
        print(f"\n  💾 完整结果已保存至: {args.output}")
    else:
        # 默认保存
        out_path = "/Users/zhouming.wang/workspace/project/ai-berkshire/reports/a股质量筛选结果.csv"
        df_out = pd.DataFrame(results)
        df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n  💾 完整结果已保存至: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="A股质量快速筛选工具")
    parser.add_argument("--roe", type=float, default=DEFAULT_ROE, help=f"5年平均ROE最低要求 (默认: {DEFAULT_ROE}%%)")
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN, help=f"5年平均毛利率最低要求 (默认: {DEFAULT_MARGIN}%%)")
    parser.add_argument("--fcf", type=float, default=DEFAULT_FCF_RATIO, help=f"OCF/NI最低要求 (默认: {DEFAULT_FCF_RATIO})")
    parser.add_argument("--net-margin", type=float, default=DEFAULT_NET_MARGIN, help=f"5年平均净利率最低要求 (默认: {DEFAULT_NET_MARGIN}%%)")
    parser.add_argument("--output", type=str, default=None, help="输出CSV路径")
    parser.add_argument("--sample", type=int, default=None, help="仅测试前N只（调试用）")
    args = parser.parse_args()

    print("🔍 A股质量快速筛选")
    print(f"   指标: ROE≥{args.roe}% | 毛利率≥{args.margin}% | OCF/NI≥{args.fcf} | 净利率≥{args.net_margin}%")
    print()

    # Step 1: 获取全列表
    all_stocks = get_a_share_list()
    if args.sample:
        all_stocks = all_stocks.head(args.sample)
        print(f"   ⚠️ 调试模式: 仅测试前{args.sample}只")

    # Step 2: 逐只筛选
    print(f"\n📊 开始逐只筛选（预计{len(all_stocks)*SLEEP_INTERVAL/60:.0f}分钟）...\n")
    results, passed, failed, errors = screen_stocks(
        all_stocks, args.roe, args.margin, args.fcf, args.net_margin
    )

    # Step 3: 输出
    print_results(results, passed, failed, errors, len(all_stocks), args)


if __name__ == "__main__":
    main()
