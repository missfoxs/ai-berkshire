"""数据源抽象层.

统一接口 DataProvider: 上层筛选/因子代码只依赖它, 不依赖具体数据源.
切换数据源只需改 ASHARE_PROVIDER 环境变量(或调 get_provider 传名).

标准化列名(SCHEMA): 各 provider 必须把原始字段翻译成这些统一英文列,
这样 factors/screener 完全与数据源解耦.
"""

from __future__ import annotations

import abc
import time
from typing import Optional

import pandas as pd

from . import config


# ---------------------------------------------------------------------------
# 标准化列名 —— 所有 provider 的输出都按这套列名对齐
# ---------------------------------------------------------------------------

# get_spot() 批量快照
SPOT_COLS = [
    "code", "name", "price", "pct_chg",
    "pe_ttm", "pb", "ps_ttm",
    "total_mktcap",   # 总市值(元)
    "float_mktcap",   # 流通市值(元)
    "turnover",       # 换手率(%)
]

# get_financials() 单只财务(index=报告期字符串 YYYYMMDD, 降序), 标准化英文列
FIN_COLS = {
    "net_income_parent": "归母净利润",
    "revenue": "营业总收入",
    "cogs": "营业成本",
    "net_income": "净利润",
    "equity": "股东权益合计(净资产)",
    "goodwill": "商誉",
    "ocf": "经营现金流量净额",
    "eps": "基本每股收益",
    "bvps": "每股净资产",
    "roe": "净资产收益率(ROE)",
    "roa": "总资产报酬率(ROA)",
    "gross_margin": "毛利率",
    "net_margin": "销售净利率",
    "debt_ratio": "资产负债率",
    "roic": "投入资本回报率",
    "current_ratio": "流动比率",
    "asset_turnover": "总资产周转率",
    "ocf_to_ni": "经营活动净现金/归属母公司的净利润",
    "rev_growth": "营业总收入增长率",
    "ni_growth": "归属母公司净利润增长率",
}


def with_retry(fn, *args, retries: Optional[int] = None, sleep: float = 0.5, **kwargs):
    """带重试的取数封装(网络型数据源常抖动)."""
    retries = config.MAX_RETRY if retries is None else retries
    last = None
    for i in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - 数据源异常种类多, 统一重试
            last = e
            time.sleep(sleep * (i + 1))
    raise last  # type: ignore[misc]


class DataProvider(abc.ABC):
    """数据源统一接口. 新增数据源只需实现这些方法并在工厂注册."""

    name: str = "base"

    # ---- 股票池 / 行业 ----
    @abc.abstractmethod
    def list_stocks(self) -> pd.DataFrame:
        """返回全部A股: 列 [code, name]."""

    @abc.abstractmethod
    def get_spot(self) -> pd.DataFrame:
        """返回全市场实时快照: 列见 SPOT_COLS(缺失列可为 NaN)."""

    @abc.abstractmethod
    def get_industry_map(self) -> pd.DataFrame:
        """返回 [code, industry] 行业分类(用于行业中性化)."""

    # ---- 单只明细(在候选子集上调用) ----
    @abc.abstractmethod
    def get_financials(self, code: str) -> pd.DataFrame:
        """返回单只全历史财务, index=报告期(YYYYMMDD, 降序), 列=FIN_COLS.keys()."""

    @abc.abstractmethod
    def get_price_history(self, code: str, days: int) -> pd.DataFrame:
        """返回后复权日线: 列 [date, close], 升序."""

    @abc.abstractmethod
    def get_report_count(self, code: str, months: int) -> int:
        """返回近 months 个月券商研报数(拥挤/关注度代理). 不可得返回 -1."""


class CachedProvider(DataProvider):
    """给任意 provider 套一层磁盘缓存(装饰器模式), 与具体数据源解耦.

    取数成功一次即落盘, 后续同 key 命中缓存直接读, 反复测试不再拉网.
    单只明细(financials/price/report)按 code 分文件缓存.
    """

    def __init__(self, base: DataProvider, cache, refresh: bool = False):
        self.base = base
        self.cache = cache
        self.refresh = refresh
        self.name = f"{base.name}+cache"

    def _k(self, *parts) -> str:
        return "_".join([self.base.name, *[str(p) for p in parts]])

    def list_stocks(self) -> pd.DataFrame:
        return self.cache.get_or_compute(
            self._k("list"), config.CACHE_TTL["list"], self.base.list_stocks, self.refresh)

    def get_spot(self) -> pd.DataFrame:
        return self.cache.get_or_compute(
            self._k("spot"), config.CACHE_TTL["spot"], self.base.get_spot, self.refresh)

    def get_industry_map(self) -> pd.DataFrame:
        return self.cache.get_or_compute(
            self._k("industry"), config.CACHE_TTL["industry"],
            self.base.get_industry_map, self.refresh)

    def get_financials(self, code: str) -> pd.DataFrame:
        return self.cache.get_or_compute(
            self._k("fin", code), config.CACHE_TTL["financials"],
            lambda: self.base.get_financials(code), self.refresh)

    def get_price_history(self, code: str, days: int) -> pd.DataFrame:
        return self.cache.get_or_compute(
            self._k("px", code, days), config.CACHE_TTL["price"],
            lambda: self.base.get_price_history(code, days), self.refresh)

    def get_report_count(self, code: str, months: int) -> int:
        return self.cache.get_or_compute(
            self._k("rep", code, months), config.CACHE_TTL["report"],
            lambda: self.base.get_report_count(code, months), self.refresh)


# ---------------------------------------------------------------------------
# 工厂: 按名字返回 provider 实例(默认套磁盘缓存)
# ---------------------------------------------------------------------------

def get_provider(name: Optional[str] = None,
                 use_cache: Optional[bool] = None,
                 refresh: bool = False) -> DataProvider:
    name = (name or config.DEFAULT_PROVIDER).strip().lower()
    if name == "akshare":
        from .provider_akshare import AKShareProvider
        base: DataProvider = AKShareProvider()
    elif name == "tushare":
        from .provider_tushare import TushareProvider
        base = TushareProvider(token=config.TUSHARE_TOKEN)
    else:
        raise ValueError(f"未知数据源: {name!r} (可选: akshare | tushare)")

    use_cache = config.USE_CACHE if use_cache is None else use_cache
    if use_cache:
        from .cache import DiskCache
        return CachedProvider(base, DiskCache(config.CACHE_DIR), refresh=refresh)
    return base
