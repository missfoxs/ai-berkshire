"""Tushare 数据源实现(同签名替换桩).

用途: 与 AKShareProvider 完全同接口, 通过 ASHARE_PROVIDER=tushare 即可切换.
需要环境变量 TUSHARE_TOKEN. 未安装 tushare 或无 token 时给出清晰报错.

字段映射基于 Tushare Pro 常用接口(stock_basic / daily_basic / fina_indicator /
income / cashflow / balancesheet / pro_bar). 若积分不足或字段调整, 只需改本文件.
标注 # VERIFY 处建议首次接入时按你账号权限核对一次.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from .provider import DataProvider, FIN_COLS, SPOT_COLS, with_retry


def _ts_code(code: str) -> str:
    code = str(code).strip().zfill(6)
    if code.startswith(("600", "601", "603", "605", "688", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


class TushareProvider(DataProvider):
    name = "tushare"

    def __init__(self, token: str = ""):
        if not token:
            raise RuntimeError("Tushare 需要 TUSHARE_TOKEN 环境变量")
        try:
            import tushare as ts
        except ImportError as e:
            raise RuntimeError("未安装 tushare, 请先 pip install tushare") from e
        ts.set_token(token)
        self._ts = ts
        self._pro = ts.pro_api()
        self._basic_cache: pd.DataFrame | None = None

    # ------------------------------------------------------------------ 基础
    def _basic(self) -> pd.DataFrame:
        if self._basic_cache is None:
            df = with_retry(
                self._pro.stock_basic,
                exchange="", list_status="L",
                fields="ts_code,symbol,name,industry,list_date",
            )
            df["code"] = df["symbol"].str.zfill(6)
            self._basic_cache = df
        return self._basic_cache

    def list_stocks(self) -> pd.DataFrame:
        return self._basic()[["code", "name"]].copy()

    def get_spot(self) -> pd.DataFrame:
        """用最近交易日的 daily_basic 近似"快照"(估值/市值/换手)."""
        # 找最近有数据的交易日
        for back in range(0, 7):
            day = (dt.date.today() - dt.timedelta(days=back)).strftime("%Y%m%d")
            db = with_retry(
                self._pro.daily_basic, trade_date=day,
                fields="ts_code,close,pe_ttm,pb,ps_ttm,total_mv,circ_mv,turnover_rate",
            )
            if db is not None and not db.empty:
                break
        db["code"] = db["ts_code"].str[:6]
        db = db.rename(columns={
            "close": "price", "turnover_rate": "turnover",
            "total_mv": "total_mktcap", "circ_mv": "float_mktcap",
        })
        # Tushare 市值单位: 万元 -> 元  # VERIFY
        db["total_mktcap"] = pd.to_numeric(db["total_mktcap"], errors="coerce") * 1e4
        db["float_mktcap"] = pd.to_numeric(db["float_mktcap"], errors="coerce") * 1e4
        db = db.merge(self._basic()[["code", "name"]], on="code", how="left")
        db["pct_chg"] = pd.NA  # daily_basic 无涨跌幅, 如需可 join daily
        for c in SPOT_COLS:
            if c not in db.columns:
                db[c] = pd.NA
        return db[SPOT_COLS].copy()

    def get_industry_map(self) -> pd.DataFrame:
        b = self._basic()
        return b[["code", "industry"]].rename(columns={"industry": "industry"}).copy()

    # ------------------------------------------------------------------ 明细
    def get_financials(self, code: str) -> pd.DataFrame:
        ts_code = _ts_code(code)
        fi = with_retry(self._pro.fina_indicator, ts_code=ts_code)      # 比率
        inc = with_retry(self._pro.income, ts_code=ts_code)             # 利润表
        cf = with_retry(self._pro.cashflow, ts_code=ts_code)           # 现金流
        bs = with_retry(self._pro.balancesheet, ts_code=ts_code)       # 资产负债

        def idx(df):
            return df.set_index("end_date") if "end_date" in df.columns else df

        fi, inc, cf, bs = map(idx, (fi, inc, cf, bs))
        periods = sorted(set(fi.index) | set(inc.index), reverse=True)

        def pick(df, col):
            return pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series(dtype=float)

        # Tushare 字段映射(# VERIFY: 视账号权限字段可能不同)
        series = {
            "net_income_parent": pick(inc, "n_income_attr_p"),
            "revenue": pick(inc, "total_revenue"),
            "cogs": pick(inc, "oper_cost"),
            "net_income": pick(inc, "n_income"),
            "equity": pick(bs, "total_hldr_eqy_exc_min_int"),
            "goodwill": pick(bs, "goodwill"),
            "ocf": pick(cf, "n_cashflow_act"),
            "eps": pick(fi, "eps"),
            "bvps": pick(fi, "bps"),
            "roe": pick(fi, "roe"),
            "roa": pick(fi, "roa"),
            "gross_margin": pick(fi, "grossprofit_margin"),
            "net_margin": pick(fi, "netprofit_margin"),
            "debt_ratio": pick(fi, "debt_to_assets"),
            "roic": pick(fi, "roic"),
            "current_ratio": pick(fi, "current_ratio"),
            "asset_turnover": pick(fi, "assets_turn"),
            "ocf_to_ni": pick(fi, "ocf_to_profit"),
            "rev_growth": pick(fi, "tr_yoy"),
            "ni_growth": pick(fi, "netprofit_yoy"),
        }
        df = pd.DataFrame(series).reindex(periods)
        df = df.reindex(columns=list(FIN_COLS.keys()))
        df.index = [str(p) for p in df.index]
        return df

    def get_price_history(self, code: str, days: int) -> pd.DataFrame:
        end = dt.date.today()
        start = end - dt.timedelta(days=int(days * 1.8) + 40)
        raw = with_retry(
            self._ts.pro_bar, ts_code=_ts_code(code), adj="hfq",
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
        )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=["date", "close"])
        df = raw.rename(columns={"trade_date": "date", "close": "close"})[["date", "close"]]
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df.sort_values("date").reset_index(drop=True)

    def get_report_count(self, code: str, months: int) -> int:
        return -1  # Tushare 研报覆盖需高积分, 默认不可得
