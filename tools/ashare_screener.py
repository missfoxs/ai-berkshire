#!/usr/bin/env python3
"""A股主板"十五五"冷门潜力股筛选器 — CLI 入口.

数据源可插拔(默认 AKShare, 保留 Tushare 快速替换):
    export ASHARE_PROVIDER=akshare          # 默认
    export ASHARE_PROVIDER=tushare; export TUSHARE_TOKEN=xxxx

用法:
    python3 tools/ashare_screener.py selftest                # 离线自测(无网络, 验证因子)
    python3 tools/ashare_screener.py universe                # 只跑第0层主板池(快)
    python3 tools/ashare_screener.py screen --limit 200      # 完整漏斗 -> 生成报告
    python3 tools/ashare_screener.py screen --theme theme.csv --limit 300
                                                             # theme.csv: 含 code 列, 限定主题池

依赖: akshare(或 tushare) + pandas + numpy. 建议在 .venv 内运行.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ashare import config  # noqa: E402


def _load_theme_codes(path: str):
    import pandas as pd
    df = pd.read_csv(path, dtype=str)
    col = "code" if "code" in df.columns else df.columns[0]
    return set(df[col].str.strip().str.zfill(6))


def cmd_selftest(_args):
    from ashare.screener import selftest
    return 0 if selftest() else 1


def _provider_from_args(args):
    from ashare.provider import get_provider
    return get_provider(
        args.provider,
        use_cache=not getattr(args, "no_cache", False),
        refresh=getattr(args, "refresh", False),
    )


def cmd_universe(args):
    from ashare.screener import filter_universe
    provider = _provider_from_args(args)
    print(f"数据源={provider.name} 拉取快照 ...", flush=True)
    spot = provider.get_spot()
    print(spot)
    return 0
    uni = filter_universe(spot)
    print(f"主板池(剔ST/市值>={config.MIN_MKTCAP_YI}亿): {len(uni)} 只")
    print(uni[["code", "name", "mktcap_yi", "pe_ttm", "pb"]].head(20).to_string(index=False))
    if args.out:
        uni.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"已保存: {args.out}")
    return 0


def cmd_screen(args):
    from ashare.screener import run_screen, save_report
    provider = _provider_from_args(args)
    theme = _load_theme_codes(args.theme) if args.theme else None
    ranked = run_screen(
        provider=provider, limit=args.limit, theme_codes=theme,
        fetch_industry=not args.no_industry,
    )
    outdir = save_report(ranked)
    print(f"\n完成. 报告目录: {outdir}")
    passed = ranked[ranked.get("passed_guard", True)]
    print(f"过护栏候选 {len(passed)} / 明细 {len(ranked)} 只. Top 10:")
    cols = [c for c in ["code", "name", "industry", "score_total", "fscore", "roe"]
            if c in passed.columns]
    print(passed.head(10)[cols].to_string(index=False))
    return 0


def main():
    p = argparse.ArgumentParser(description="A股主板十五五冷门潜力筛选器")
    p.add_argument("--provider", default=None, help="akshare | tushare (默认取环境变量)")
    sub = p.add_subparsers(dest="cmd")

    def add_cache_flags(sp):
        sp.add_argument("--no-cache", action="store_true", help="禁用磁盘缓存(强制拉网)")
        sp.add_argument("--refresh", action="store_true", help="忽略已有缓存, 重新拉取并覆盖")

    sub.add_parser("selftest", help="离线自测因子与打分(无网络)")

    u = sub.add_parser("universe", help="只跑主板池过滤")
    u.add_argument("--out", default=None, help="保存CSV路径")
    add_cache_flags(u)

    s = sub.add_parser("screen", help="完整漏斗筛选并出报告")
    s.add_argument("--limit", type=int, default=config.DETAIL_LIMIT_DEFAULT,
                   help="明细取数上限只数")
    s.add_argument("--theme", default=None, help="主题池CSV(含code列), 限定候选")
    s.add_argument("--no-industry", action="store_true", help="跳过行业分类(全市场中性)")
    add_cache_flags(s)

    args = p.parse_args()
    if args.cmd == "selftest":
        return cmd_selftest(args)
    if args.cmd == "universe":
        return cmd_universe(args)
    if args.cmd == "screen":
        return cmd_screen(args)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
