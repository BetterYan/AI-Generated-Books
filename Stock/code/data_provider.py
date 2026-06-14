# ============================================================
# data_provider.py — 数据获取层（akshare 封装）
# ============================================================
"""
封装 akshare 数据接口，提供统一的数据获取 API。
支持：实时行情、分钟K线、日K线、历史分时数据。
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import numpy as np
import pandas as pd

try:
    import akshare as ak
except ImportError:
    ak = None

import config as cfg


# ── 工具函数 ──────────────────────────────────────────────

def _ensure_akshare():
    """检查 akshare 是否已安装"""
    if ak is None:
        raise ImportError(
            "akshare 未安装，请执行: pip install akshare"
        )


def _retry(fn, retries: int = 3, delay: float = 3.0):
    """带重试的函数调用"""
    import time as _time
    last_err = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                wait = delay * (attempt + 1)
                print(f"[DataProvider] 请求失败，{wait:.0f}秒后重试 ({attempt+1}/{retries})...")
                _time.sleep(wait)
    raise last_err


def _safe_float(val, default=0.0) -> float:
    """安全类型转换"""
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ── 数据获取类 ────────────────────────────────────────────

class DataProvider:
    """
    统一数据接口，封装 akshare 调用。
    所有方法均返回标准 pandas DataFrame。
    """

    def __init__(self, symbol: str = cfg.ETF_CODE):
        _ensure_akshare()
        self.symbol = symbol

    # ─── 实时行情 ─────────────────────────────────────────

    def get_realtime_quote(self) -> dict:
        """
        获取 ETF 实时行情快照。

        返回 dict 包含:
            code, name, price, open, high, low, close(昨收),
            volume, amount, change_pct, bid1_price, ask1_price
        """
        try:
            df = _retry(lambda: ak.fund_etf_spot_em())
            row = df[df["代码"] == self.symbol]
            if row.empty:
                return {}
            row = row.iloc[0]
            return {
                "code":       str(row.get("代码", self.symbol)),
                "name":       str(row.get("名称", cfg.ETF_NAME)),
                "price":      _safe_float(row.get("最新价")),
                "open":       _safe_float(row.get("今开")),
                "high":       _safe_float(row.get("最高")),
                "low":        _safe_float(row.get("最低")),
                "close":      _safe_float(row.get("昨收")),
                "volume":     _safe_float(row.get("成交量")),
                "amount":     _safe_float(row.get("成交额")),
                "change_pct": _safe_float(row.get("涨跌幅")),
                "bid1_price": _safe_float(row.get("买一价")),
                "ask1_price": _safe_float(row.get("卖一价")),
                "timestamp":  dt.datetime.now(),
            }
        except Exception as e:
            print(f"[DataProvider] 实时行情获取失败: {e}")
            return {}

    # ─── 分钟级K线 ────────────────────────────────────────

    def get_minute_data(self, period: str = "1") -> pd.DataFrame:
        """
        获取分钟级K线数据（近期可用，通常为最近 5~10 个交易日）。

        参数:
            period: "1" / "5" / "15" / "30" / "60"

        返回 DataFrame 列:
            datetime, open, high, low, close, volume, amount
        """
        try:
            df = _retry(lambda: ak.fund_etf_hist_min_em(
                symbol=self.symbol,
                period=period,
                adjust="",
            ))
            if df is None or df.empty:
                return pd.DataFrame()

            # 统一列名
            col_map = {}
            for col in df.columns:
                cl = col.lower().strip()
                if "时间" in cl or "日期" in cl or "time" in cl:
                    col_map[col] = "datetime"
                elif cl in ("open", "开盘"):
                    col_map[col] = "open"
                elif cl in ("high", "最高"):
                    col_map[col] = "high"
                elif cl in ("low", "最低"):
                    col_map[col] = "low"
                elif cl in ("close", "收盘", "最新"):
                    col_map[col] = "close"
                elif "成交量" in cl or "volume" in cl:
                    col_map[col] = "volume"
                elif "成交额" in cl or "amount" in cl:
                    col_map[col] = "amount"
            df = df.rename(columns=col_map)

            for c in ("open", "high", "low", "close", "volume", "amount"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

            if "datetime" in df.columns:
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.sort_values("datetime").reset_index(drop=True)

            return df

        except Exception as e:
            print(f"[DataProvider] 分钟K线获取失败: {e}")
            return pd.DataFrame()

    # ─── 日K线历史 ────────────────────────────────────────

    def get_daily_data(
        self,
        start_date: str = cfg.BACKTEST_DEFAULT_START,
        end_date: str = "",
    ) -> pd.DataFrame:
        """
        获取日K线历史数据。

        参数:
            start_date: "YYYYMMDD"
            end_date:   "YYYYMMDD"，空字符串表示至今

        返回 DataFrame 列:
            date, open, high, low, close, volume, amount
        """
        try:
            _sd, _ed = start_date, end_date
            df = _retry(lambda: ak.fund_etf_hist_em(
                symbol=self.symbol,
                period="daily",
                start_date=_sd,
                end_date=_ed,
                adjust="qfq",
            ))
            if df is None or df.empty:
                return pd.DataFrame()

            col_map = {}
            for col in df.columns:
                cl = col.lower().strip()
                if "日期" in cl or "date" in cl:
                    col_map[col] = "date"
                elif cl in ("open", "开盘"):
                    col_map[col] = "open"
                elif cl in ("high", "最高"):
                    col_map[col] = "high"
                elif cl in ("low", "最低"):
                    col_map[col] = "low"
                elif cl in ("close", "收盘"):
                    col_map[col] = "close"
                elif "成交量" in cl or "volume" in cl:
                    col_map[col] = "volume"
                elif "成交额" in cl or "amount" in cl:
                    col_map[col] = "amount"
            df = df.rename(columns=col_map)

            for c in ("open", "high", "low", "close", "volume", "amount"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)

            return df

        except Exception as e:
            print(f"[DataProvider] 日K线获取失败: {e}")
            return pd.DataFrame()

    # ─── 历史分钟K线（按日期范围）─────────────────────────

    def get_historical_minute(
        self,
        period: str = "5",
        days_back: int = 30,
    ) -> pd.DataFrame:
        """
        获取近期历史分钟K线。
        akshare fund_etf_hist_min_em 支持获取近期数据，
        days_back 为期望天数（实际可用范围取决于接口限制）。
        """
        df = self.get_minute_data(period=period)
        if df.empty or "datetime" not in df.columns:
            return df
        cutoff = dt.datetime.now() - dt.timedelta(days=days_back)
        df = df[df["datetime"] >= cutoff].reset_index(drop=True)
        return df

    # ─── 模拟日内分钟数据（用于演示回测）─────────────────

    @staticmethod
    def simulate_intraday(
        daily_df: pd.DataFrame,
        minutes_per_day: int = 240,
    ) -> pd.DataFrame:
        """
        基于日K线数据模拟日内分钟K线（GBM 模型），
        用于在分钟数据不可用时的回测演示。

        参数:
            daily_df: 包含 open/high/low/close/volume 的日K线
            minutes_per_day: 每日分钟数（默认240，即4小时交易时段）

        返回:
            包含 datetime/open/high/low/close/volume 的分钟K线
        """
        if daily_df.empty:
            return pd.DataFrame()

        rng = np.random.default_rng(42)
        all_minutes = []

        for _, day in daily_df.iterrows():
            o, h, l, c = day["open"], day["high"], day["low"], day["close"]
            vol = day.get("volume", 1_000_000)
            if o <= 0 or c <= 0:
                continue

            day_range = max(h - l, o * 0.005)
            day_date = day["date"] if hasattr(day["date"], "date") else pd.to_datetime(day["date"])

            # 生成分钟收盘价路径（GBM + 约束到日范围）
            returns = rng.normal(
                loc=np.log(c / o) / minutes_per_day,
                scale=day_range / o / np.sqrt(minutes_per_day) * 0.6,
                size=minutes_per_day,
            )
            prices = o * np.exp(np.cumsum(returns))

            # 缩放到 [low, high] 范围
            p_min, p_max = prices.min(), prices.max()
            if p_max > p_min:
                prices = l + (prices - p_min) / (p_max - p_min) * (h - l)
            prices[0] = o
            prices[-1] = c

            for i in range(minutes_per_day):
                minute_time = day_date + pd.Timedelta(hours=9, minutes=30 + i)
                if i >= 120:
                    # 午后：跳过11:30-13:00的90分钟
                    minute_time = day_date + pd.Timedelta(hours=13, minutes=i - 120)

                p = prices[i]
                noise = rng.uniform(-day_range * 0.01, day_range * 0.01)
                m_open = prices[max(0, i - 1)] + noise
                m_close = p
                m_high = max(m_open, m_close) + abs(rng.normal(0, day_range * 0.005))
                m_low = min(m_open, m_close) - abs(rng.normal(0, day_range * 0.005))
                m_vol = vol / minutes_per_day * rng.uniform(0.3, 2.5)

                all_minutes.append({
                    "datetime": minute_time,
                    "open":   round(m_open, 4),
                    "high":   round(m_high, 4),
                    "low":    round(m_low, 4),
                    "close":  round(m_close, 4),
                    "volume": int(m_vol),
                })

        result = pd.DataFrame(all_minutes)
        if not result.empty and "datetime" in result.columns:
            result = result.sort_values("datetime").reset_index(drop=True)
        return result


# ── 模块测试 ──────────────────────────────────────────────

if __name__ == "__main__":
    dp = DataProvider()
    print("=== 实时行情 ===")
    quote = dp.get_realtime_quote()
    for k, v in quote.items():
        print(f"  {k}: {v}")

    print("\n=== 分钟K线(最近) ===")
    mdf = dp.get_minute_data("5")
    print(f"  行数: {len(mdf)}")
    if not mdf.empty:
        print(mdf.tail(5))

    print("\n=== 日K线 ===")
    ddf = dp.get_daily_data("20260101")
    print(f"  行数: {len(ddf)}")
    if not ddf.empty:
        print(ddf.tail(5))
