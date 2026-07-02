"""AKShare 数据源实现(默认).

注意: AKShare 底层多为东财/新浪等公开接口, 字段偶有变动.
所有接口调用集中在本文件, 若某接口 schema 变化, 只需在此修一处.
接口连通性以国内网络为准; 海外/沙箱可能拦截东财实时端点(spot/研报).
"""

from __future__ import annotations

import datetime as dt
import re
import time

import akshare as ak
import pandas as pd

from . import config
from .provider import DataProvider, FIN_COLS, SPOT_COLS, with_retry

_DATE_COL_RE = re.compile(r"^\d{8}$")


def _to_num(s):
    return pd.to_numeric(s, errors="coerce")


def _zpad(code) -> str:
    """归一为6位数字代码, 剥离 sh/sz/bj 等市场前缀(不同源格式不同)."""
    digits = re.sub(r"\D", "", str(code))
    return digits.zfill(6) if digits else str(code).strip()


class AKShareProvider(DataProvider):
    name = "akshare"

    # ------------------------------------------------------------------ 股票池
    def list_stocks(self) -> pd.DataFrame:
        df = with_retry(ak.stock_info_a_code_name)
        df = df.rename(columns={"code": "code", "name": "name"})
        df["code"] = df["code"].map(_zpad)
        return df[["code", "name"]].copy()

    def get_spot(self) -> pd.DataFrame:
        """全市场快照(含估值/市值/换手). 用东财端点; 该端点偶尔抖动, 已加重试.

        注意: 不再回退新浪 stock_zh_a_spot —— 新浪快照没有 市盈率/市净率/市值/换手率,
        回退只会得到一片 NaN 且代码带 sh/sz 前缀, 反而误导. 宁可显式重试/报错.
        """
        try:
            raw = with_retry(ak.stock_zh_a_spot_em, retries=max(config.MAX_RETRY, 4), sleep=1.0)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "东财实时快照获取失败(网络抖动或被限频). 请稍后重试; "
                "已开启缓存, 成功一次后即可复用. 原始错误: " + str(e)
            ) from e
        rename = {
            "代码": "code", "名称": "name", "最新价": "price",
            "涨跌幅": "pct_chg", "市盈率-动态": "pe_ttm", "市净率": "pb",
            "总市值": "total_mktcap", "流通市值": "float_mktcap", "换手率": "turnover",
        }
        df = raw.rename(columns=rename)
        df["ps_ttm"] = pd.NA  # 东财快照无市销率, 明细阶段用 营收/市值 补
        df["code"] = df["code"].map(_zpad)
        for c in SPOT_COLS:
            if c not in df.columns:
                df[c] = pd.NA
        for c in ["price", "pct_chg", "pe_ttm", "pb", "ps_ttm",
                  "total_mktcap", "float_mktcap", "turnover"]:
            df[c] = _to_num(df[c])
        return df[SPOT_COLS].copy()

    def get_industry_map(self) -> pd.DataFrame:
        """东财行业 -> 成分股, 拼成 code->industry(约80+次调用, 仅跑一次)."""
        rows = []
        try:
            names = with_retry(ak.stock_board_industry_name_em)["板块名称"].tolist()
        except Exception:
            return pd.DataFrame(columns=["code", "industry"])
        for ind in names:
            try:
                cons = with_retry(ak.stock_board_industry_cons_em, symbol=ind, sleep=0.3)
                for c in cons["代码"].tolist():
                    rows.append((_zpad(c), ind))
            except Exception:
                continue
            time.sleep(0.15)
        return pd.DataFrame(rows, columns=["code", "industry"]).drop_duplicates("code")

    # ------------------------------------------------------------------ 明细
    def get_financials(self, code: str) -> pd.DataFrame:
        raw = with_retry(ak.stock_financial_abstract, symbol=_zpad(code))
        date_cols = [c for c in raw.columns if _DATE_COL_RE.match(str(c))]
        # 按指标中文名取第一条匹配行(不同"选项"分组下同名指标值相同)
        out = {}
        for std, cn in FIN_COLS.items():
            hit = raw[raw["指标"] == cn]
            if hit.empty:
                out[std] = pd.Series({d: pd.NA for d in date_cols})
            else:
                out[std] = _to_num(hit.iloc[0][date_cols])
        df = pd.DataFrame(out)
        df.index = [str(d) for d in date_cols]
        # 报告期降序(最新在前)
        df = df.sort_index(ascending=False)
        return df

    def get_price_history(self, code: str, days: int) -> pd.DataFrame:
        end = dt.date.today()
        start = end - dt.timedelta(days=int(days * 1.8) + 40)  # 日历日留足交易日
        raw = with_retry(
            ak.stock_zh_a_hist,
            symbol=_zpad(code), period="daily",
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            adjust="hfq",
        )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=["date", "close"])
        df = raw.rename(columns={"日期": "date", "收盘": "close"})[["date", "close"]]
        df["close"] = _to_num(df["close"])
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def get_report_count(self, code: str, months: int) -> int:
        try:
            raw = with_retry(ak.stock_research_report_em, symbol=_zpad(code))
        except Exception:
            return -1
        date_col = next((c for c in raw.columns if "日期" in str(c)), None)
        if date_col is None:
            return -1
        cutoff = dt.date.today() - dt.timedelta(days=int(months * 30.5))
        d = pd.to_datetime(raw[date_col], errors="coerce").dt.date
        return int((d >= cutoff).sum())
