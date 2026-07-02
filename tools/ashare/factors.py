"""纯因子函数: 输入标准化数据, 输出因子值. 无任何网络依赖, 可离线单测.

这是"科学筛选"的量化核心: 用成熟学术因子替代拍脑袋阈值.
  质量: Piotroski F-Score(0-9) / Novy-Marx 毛利资产比 / 应计质量
  价值: 收益率类(1/PE,1/PB,1/PS), 由 screener 做行业内百分位
  动量: 12-1 价格动量(兼作暴跌排雷)
  拥挤: 换手率 / 研报覆盖(越低越"冷门", 由 screener 取负向)
"""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _num(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def annual_frame(fin: pd.DataFrame) -> pd.DataFrame:
    """只保留年报(报告期以 1231 结尾), 最新在前."""
    if fin is None or fin.empty:
        return pd.DataFrame()
    rows = [i for i in fin.index if str(i).endswith("1231")]
    return fin.loc[rows].sort_index(ascending=False)


def total_assets(equity: Optional[float], debt_ratio_pct: Optional[float]) -> Optional[float]:
    """由 净资产 与 资产负债率(%) 反推总资产: TA = E / (1 - d)."""
    e, d = _num(equity), _num(debt_ratio_pct)
    if e is None or d is None or d >= 100:
        return None
    return e / (1 - d / 100.0)


# ---------------------------------------------------------------------------
# 质量: Piotroski F-Score (0-9)
# ---------------------------------------------------------------------------

def piotroski_fscore(fin: pd.DataFrame) -> dict:
    """用最近两期年报计算 F-Score. 缺失的信号按 0 计(保守), 并返回明细."""
    af = annual_frame(fin)
    if len(af) < 2:
        return {"fscore": None, "detail": {}, "periods": list(af.index)}
    t = af.iloc[0]
    y = af.iloc[1]

    def g(row, col):
        return _num(row.get(col))

    roa_t, roa_y = g(t, "roa"), g(y, "roa")
    ocf_t = g(t, "ocf")
    ni_t, ni_y = g(t, "net_income_parent"), g(y, "net_income_parent")
    eps_t, eps_y = g(t, "eps"), g(y, "eps")
    dr_t, dr_y = g(t, "debt_ratio"), g(y, "debt_ratio")
    cr_t, cr_y = g(t, "current_ratio"), g(y, "current_ratio")
    gm_t, gm_y = g(t, "gross_margin"), g(y, "gross_margin")
    at_t, at_y = g(t, "asset_turnover"), g(y, "asset_turnover")
    ocf2ni_t = g(t, "ocf_to_ni")

    shares_t = (ni_t / eps_t) if (ni_t and eps_t) else None
    shares_y = (ni_y / eps_y) if (ni_y and eps_y) else None

    d = {}
    d["roa_pos"] = 1 if (roa_t is not None and roa_t > 0) else 0
    d["ocf_pos"] = 1 if (ocf_t is not None and ocf_t > 0) else 0
    d["roa_up"] = 1 if (roa_t is not None and roa_y is not None and roa_t > roa_y) else 0
    # 应计: 经营现金流优于净利润(收益含金量)
    if ocf2ni_t is not None:
        d["accrual"] = 1 if ocf2ni_t > 1 else 0
    else:
        d["accrual"] = 1 if (ocf_t is not None and ni_t is not None and ocf_t > ni_t) else 0
    d["lever_down"] = 1 if (dr_t is not None and dr_y is not None and dr_t < dr_y) else 0
    d["liquid_up"] = 1 if (cr_t is not None and cr_y is not None and cr_t > cr_y) else 0
    d["no_dilution"] = 1 if (shares_t is not None and shares_y is not None
                             and shares_t <= shares_y * 1.01) else 0
    d["margin_up"] = 1 if (gm_t is not None and gm_y is not None and gm_t > gm_y) else 0
    d["turn_up"] = 1 if (at_t is not None and at_y is not None and at_t > at_y) else 0

    return {"fscore": sum(d.values()), "detail": d, "periods": [af.index[0], af.index[1]]}


# ---------------------------------------------------------------------------
# 质量: Novy-Marx 毛利资产比 & 应计质量
# ---------------------------------------------------------------------------

def gross_profitability(fin: pd.DataFrame) -> Optional[float]:
    """(营业收入 - 营业成本) / 总资产. 越高越好."""
    af = annual_frame(fin)
    if af.empty:
        return None
    t = af.iloc[0]
    rev, cogs = _num(t.get("revenue")), _num(t.get("cogs"))
    ta = total_assets(t.get("equity"), t.get("debt_ratio"))
    if rev is None or cogs is None or ta is None or ta == 0:
        return None
    return (rev - cogs) / ta


def accrual_quality(fin: pd.DataFrame) -> Optional[float]:
    """经营现金流/归母净利润, 越高(>1)收益含金量越好."""
    af = annual_frame(fin)
    if af.empty:
        return None
    t = af.iloc[0]
    v = _num(t.get("ocf_to_ni"))
    if v is not None:
        return v
    ocf, ni = _num(t.get("ocf")), _num(t.get("net_income_parent"))
    if ocf is None or ni in (None, 0):
        return None
    return ocf / ni


def latest(fin: pd.DataFrame, col: str) -> Optional[float]:
    af = annual_frame(fin)
    if af.empty:
        return None
    return _num(af.iloc[0].get(col))


# ---------------------------------------------------------------------------
# 动量: 12-1 (跳过最近约1个月, 兼作暴跌排雷)
# ---------------------------------------------------------------------------

def momentum_12_1(price: pd.DataFrame, skip_days: int = 21, look_days: int = 252) -> Optional[float]:
    """收益 = P(t-skip)/P(t-skip-look) - 1. price 需含 close, 升序."""
    if price is None or price.empty or "close" not in price.columns:
        return None
    closes = pd.to_numeric(price["close"], errors="coerce").dropna().tolist()
    n = len(closes)
    if n < skip_days + look_days + 1:
        # 数据不足则用尽可能长的窗口
        if n < 40:
            return None
        recent = closes[-min(skip_days, n // 5) - 1]
        past = closes[0]
        return recent / past - 1 if past else None
    recent = closes[-(skip_days + 1)]
    past = closes[-(skip_days + look_days + 1)]
    return recent / past - 1 if past else None


# ---------------------------------------------------------------------------
# 价值: 收益率(越高越便宜). 由 screener 做行业内百分位
# ---------------------------------------------------------------------------

def value_yields(pe_ttm, pb, ps_ttm) -> dict:
    def inv(x):
        v = _num(x)
        return (1.0 / v) if (v is not None and v > 0) else None
    return {"ep": inv(pe_ttm), "bp": inv(pb), "sp": inv(ps_ttm)}
