# ============================================================
# signals.py — 交易信号生成模块（5大核心策略）
# ============================================================
"""
实现研究报告中的5大T+0交易策略，
每个策略返回标准化的信号列表。

信号格式:
    {
        "time":       datetime,
        "strategy":   str,
        "direction":  "BUY" | "SELL" | "HOLD",
        "price":      float,
        "reason":     str,
        "confidence": "高" | "中" | "低",
        "strength":   float (0~1, 信号强度),
    }
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config as cfg


# ── 信号数据结构 ──────────────────────────────────────────

@dataclass
class Signal:
    time: dt.datetime
    strategy: str
    direction: str         # "BUY" / "SELL" / "HOLD"
    price: float
    reason: str
    confidence: str = "中"  # "高" / "中" / "低"
    strength: float = 0.5   # 0~1

    def to_dict(self) -> dict:
        return {
            "time": self.time,
            "strategy": self.strategy,
            "direction": self.direction,
            "price": self.price,
            "reason": self.reason,
            "confidence": self.confidence,
            "strength": self.strength,
        }


# ── 技术指标计算工具 ──────────────────────────────────────

def calc_vwap(df: pd.DataFrame) -> pd.Series:
    """
    计算 VWAP（成交量加权平均价）。

    参数: df 需包含 close, volume 列
    返回: VWAP Series（与 df 等长）
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cumvol = df["volume"].cumsum()
    cumtp = (typical_price * df["volume"]).cumsum()
    vwap = cumtp / cumvol.replace(0, np.nan)
    return vwap.ffill()


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    计算 ATR（平均真实波幅）。

    TR = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = SMA(TR, period)
    """
    high_low = df["high"] - df["low"]
    prev_close = df["close"].shift(1)
    high_prev = (df["high"] - prev_close).abs()
    low_prev = (df["low"] - prev_close).abs()
    tr = pd.concat([high_low, high_prev, low_prev], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=1).mean()
    return atr


def calc_ema(series: pd.Series, span: int) -> pd.Series:
    """计算指数移动平均"""
    return series.ewm(span=span, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """计算 RSI 指标"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def calc_bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0):
    """计算布林带: 返回 (middle, upper, lower)"""
    middle = series.rolling(window=window, min_periods=1).mean()
    std = series.rolling(window=window, min_periods=1).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return middle, upper, lower


# ============================================================
# 策略一：VWAP 均值回归
# ============================================================

def strategy_vwap_mean_reversion(
    df: pd.DataFrame,
    current_price: Optional[float] = None,
) -> list[Signal]:
    """
    VWAP均值回归策略：
    - 买入信号：价格 < VWAP × (1 - threshold)
    - 卖出信号：价格回归至 VWAP 附近
    - 止损：偏离 > VWAP_STOP_LOSS

    参数:
        df: 分钟K线 DataFrame (需含 datetime, open, high, low, close, volume)
        current_price: 当前最新价（可选，默认取最后一根K线收盘价）
    """
    signals = []
    if df.empty or len(df) < 10:
        return signals

    vwap = calc_vwap(df)
    last_idx = len(df) - 1
    price = current_price if current_price else df.iloc[last_idx]["close"]
    cur_vwap = vwap.iloc[last_idx]
    cur_time = df.iloc[last_idx]["datetime"] if "datetime" in df.columns else dt.datetime.now()

    if cur_vwap <= 0 or price <= 0:
        return signals

    deviation = (price - cur_vwap) / cur_vwap

    # ── 检查偏离持续性（回看 N 根K线）──
    lookback = min(cfg.VWAP_MIN_DURATION_MIN, len(df))
    recent_devs = []
    for i in range(last_idx - lookback + 1, last_idx + 1):
        if i >= 0 and vwap.iloc[i] > 0:
            d = (df.iloc[i]["close"] - vwap.iloc[i]) / vwap.iloc[i]
            recent_devs.append(d)

    sustained_negative = all(d < -cfg.VWAP_BUY_THRESHOLD for d in recent_devs[-3:]) if recent_devs else False
    sustained_positive = all(d > cfg.VWAP_BUY_THRESHOLD for d in recent_devs[-3:]) if recent_devs else False

    # ── 买入信号 ──
    if deviation < -cfg.VWAP_BUY_THRESHOLD and sustained_negative:
        confidence = "高" if deviation < -cfg.VWAP_BUY_THRESHOLD * 1.5 else "中"
        strength = min(abs(deviation) / cfg.VWAP_STOP_LOSS, 1.0)
        signals.append(Signal(
            time=cur_time,
            strategy="VWAP均值回归",
            direction="BUY",
            price=price,
            reason=f"价格偏离VWAP {deviation:.2%}（阈值 {cfg.VWAP_BUY_THRESHOLD:.1%}），持续 {lookback} 分钟",
            confidence=confidence,
            strength=strength,
        ))

    # ── 卖出信号（止盈）──
    elif abs(deviation) < cfg.VWAP_SELL_THRESHOLD:
        # 检查是否从偏离回归（回看是否有大偏离）
        had_deviation = any(abs(d) > cfg.VWAP_BUY_THRESHOLD * 0.8 for d in recent_devs[:-1]) if len(recent_devs) > 1 else False
        if had_deviation:
            signals.append(Signal(
                time=cur_time,
                strategy="VWAP均值回归",
                direction="SELL",
                price=price,
                reason=f"价格回归VWAP（偏离 {deviation:.2%}），止盈平仓",
                confidence="高",
                strength=0.8,
            ))

    # ── 反向做空信号 ──
    elif deviation > cfg.VWAP_BUY_THRESHOLD and sustained_positive:
        confidence = "高" if deviation > cfg.VWAP_BUY_THRESHOLD * 1.5 else "中"
        signals.append(Signal(
            time=cur_time,
            strategy="VWAP均值回归",
            direction="SELL",
            price=price,
            reason=f"价格高于VWAP {deviation:.2%}，反向T先卖后买",
            confidence=confidence,
            strength=min(deviation / cfg.VWAP_STOP_LOSS, 1.0),
        ))

    # ── 止损信号 ──
    if deviation < -cfg.VWAP_STOP_LOSS:
        signals.append(Signal(
            time=cur_time,
            strategy="VWAP均值回归",
            direction="SELL",
            price=price,
            reason=f"偏离VWAP达 {deviation:.2%}，触发止损（阈值 {cfg.VWAP_STOP_LOSS:.1%}）",
            confidence="高",
            strength=1.0,
        ))

    return signals


# ============================================================
# 策略二：ATR 网格交易
# ============================================================

def strategy_atr_grid(
    df: pd.DataFrame,
    current_price: Optional[float] = None,
    prev_grid_levels: Optional[list[float]] = None,
) -> tuple[list[Signal], list[float]]:
    """
    ATR网格交易策略：
    - 基于 ATR 动态计算网格间距
    - 价格触及下方网格线买入
    - 价格触及上方网格线卖出

    返回: (signals, grid_levels)
    """
    signals = []
    if df.empty or len(df) < cfg.GRID_ATR_PERIOD:
        return signals, prev_grid_levels or []

    atr = calc_atr(df, period=cfg.GRID_ATR_PERIOD)
    cur_atr = atr.iloc[-1]
    price = current_price if current_price else df.iloc[-1]["close"]
    cur_time = df.iloc[-1]["datetime"] if "datetime" in df.columns else dt.datetime.now()

    if cur_atr <= 0 or price <= 0:
        return signals, prev_grid_levels or []

    grid_spacing = cur_atr * cfg.GRID_ATR_MULTI

    # ── 计算网格线 ──
    if prev_grid_levels and len(prev_grid_levels) > 1:
        grid_levels = prev_grid_levels
    else:
        # 以当前价格为中枢，上下各建 N/2 格
        half = cfg.GRID_MAX_GRIDS // 2
        center = price
        grid_levels = [
            round(center + i * grid_spacing, 4)
            for i in range(-half, half + 1)
        ]

    # ── 检测网格穿越 ──
    if len(df) >= 2:
        prev_price = df.iloc[-2]["close"]
    else:
        prev_price = price

    for level in grid_levels:
        # 向下穿越网格线 → 买入
        if prev_price >= level > price and level < price + grid_spacing * 0.5:
            signals.append(Signal(
                time=cur_time,
                strategy="ATR网格交易",
                direction="BUY",
                price=price,
                reason=f"价格下穿网格线 {level:.4f}（间距 {grid_spacing:.4f}），买入一格",
                confidence="中",
                strength=0.6,
            ))
            break  # 每次只触发最近的网格

        # 向上穿越网格线 → 卖出
        elif prev_price <= level < price and level > price - grid_spacing * 0.5:
            signals.append(Signal(
                time=cur_time,
                strategy="ATR网格交易",
                direction="SELL",
                price=price,
                reason=f"价格上穿网格线 {level:.4f}（间距 {grid_spacing:.4f}），卖出一格",
                confidence="中",
                strength=0.6,
            ))
            break

    # ── 整体止损 ──
    if grid_levels:
        lowest_grid = min(grid_levels)
        if price < lowest_grid * (1 - cfg.GRID_STOP_LOSS):
            signals.append(Signal(
                time=cur_time,
                strategy="ATR网格交易",
                direction="SELL",
                price=price,
                reason=f"价格跌破最低网格线 {lowest_grid:.4f} 的 {cfg.GRID_STOP_LOSS:.1%}，整体止损",
                confidence="高",
                strength=1.0,
            ))

    return signals, grid_levels


# ============================================================
# 策略三：开盘动量突破
# ============================================================

def strategy_momentum_breakout(
    df: pd.DataFrame,
    current_price: Optional[float] = None,
) -> list[Signal]:
    """
    开盘动量突破策略：
    - 观察开盘后15分钟形成的价格区间
    - 向上突破 + 成交量放大 → 做多
    - 向下突破 + 成交量放大 → 做空（反向T先卖）

    参数:
        df: 当日分钟K线
    """
    signals = []
    if df.empty:
        return signals

    cur_time = df.iloc[-1]["datetime"] if "datetime" in df.columns else dt.datetime.now()
    price = current_price if current_price else df.iloc[-1]["close"]

    # ── 确定观察窗口 ──
    obs_bars = cfg.MOMENTUM_OBSERVATION_MIN
    if len(df) < obs_bars + 1:
        return signals

    # 检查当前是否在动量窗口时间内
    if "datetime" in df.columns:
        first_time = pd.to_datetime(df.iloc[0]["datetime"])
        cur_dt = pd.to_datetime(df.iloc[-1]["datetime"])
        elapsed_min = (cur_dt - first_time).total_seconds() / 60
        if elapsed_min < obs_bars:
            return signals  # 还在观察期内，不生成信号

    # ── 计算观察期范围 ──
    obs_data = df.iloc[:obs_bars]
    range_high = obs_data["high"].max()
    range_low = obs_data["low"].min()
    obs_avg_vol = obs_data["volume"].mean()

    # ── 突破后数据 ──
    post_obs = df.iloc[obs_bars:]
    if post_obs.empty:
        return signals

    cur_bar = post_obs.iloc[-1]
    cur_vol = cur_bar["volume"]
    vol_ratio = cur_vol / obs_avg_vol if obs_avg_vol > 0 else 0

    # ── 向上突破 ──
    if price > range_high and vol_ratio >= cfg.MOMENTUM_VOLUME_MULTI:
        confidence = "高" if vol_ratio >= cfg.MOMENTUM_VOLUME_MULTI * 1.5 else "中"
        signals.append(Signal(
            time=cur_time,
            strategy="开盘动量突破",
            direction="BUY",
            price=price,
            reason=(
                f"突破开盘区间高点 {range_high:.4f}，"
                f"当前 {price:.4f}，量比 {vol_ratio:.1f}x"
            ),
            confidence=confidence,
            strength=min(vol_ratio / 3.0, 1.0),
        ))

    # ── 向下突破 ──
    elif price < range_low and vol_ratio >= cfg.MOMENTUM_VOLUME_MULTI:
        confidence = "高" if vol_ratio >= cfg.MOMENTUM_VOLUME_MULTI * 1.5 else "中"
        signals.append(Signal(
            time=cur_time,
            strategy="开盘动量突破",
            direction="SELL",
            price=price,
            reason=(
                f"跌破开盘区间低点 {range_low:.4f}，"
                f"当前 {price:.4f}，量比 {vol_ratio:.1f}x"
            ),
            confidence=confidence,
            strength=min(vol_ratio / 3.0, 1.0),
        ))

    return signals


# ============================================================
# 策略四：日内做T（底仓增强型）
# ============================================================

def strategy_intraday_t(
    df: pd.DataFrame,
    current_price: Optional[float] = None,
    holding_position: bool = True,
) -> list[Signal]:
    """
    日内做T策略：
    - 正向T：日内低点买入浮仓 → 反弹后卖出底仓
    - 反向T：日内高点卖出 → 回落买回
    - 辅助指标：分时均线偏离 + RSI + MACD
    """
    signals = []
    if df.empty or len(df) < 20:
        return signals

    price = current_price if current_price else df.iloc[-1]["close"]
    cur_time = df.iloc[-1]["datetime"] if "datetime" in df.columns else dt.datetime.now()

    # ── 计算分时均线（日内均价线）──
    vwap = calc_vwap(df)
    cur_vwap = vwap.iloc[-1]

    # ── RSI ──
    rsi = calc_rsi(df["close"], period=14)
    cur_rsi = rsi.iloc[-1]

    # ── 短期MACD(5,13,5) ──
    ema_fast = calc_ema(df["close"], 5)
    ema_slow = calc_ema(df["close"], 13)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, 5)
    macd_hist = macd_line - signal_line

    cur_macd_hist = macd_hist.iloc[-1]
    prev_macd_hist = macd_hist.iloc[-2] if len(macd_hist) > 1 else 0

    # ── 价格偏离分时均线 ──
    deviation = (price - cur_vwap) / cur_vwap if cur_vwap > 0 else 0

    # ── 正向T买入信号 ──
    # 条件：价格低于均价线 > 0.8% + RSI < 35 + MACD柱由负收窄
    if (deviation < -0.008
            and cur_rsi < 35
            and macd_hist.iloc[-1] > macd_hist.iloc[-2] if len(macd_hist) > 1 else False):
        signals.append(Signal(
            time=cur_time,
            strategy="日内做T",
            direction="BUY",
            price=price,
            reason=(
                f"正向T买入: 偏离均价 {deviation:.2%}, "
                f"RSI={cur_rsi:.1f}, MACD柱收窄"
            ),
            confidence="中" if cur_rsi < 30 else "低",
            strength=0.5,
        ))

    # ── 反向T卖出信号 ──
    # 条件：价格高于均价线 > 0.8% + RSI > 65 + MACD柱由正收窄
    elif (deviation > 0.008
          and cur_rsi > 65
          and macd_hist.iloc[-1] < macd_hist.iloc[-2] if len(macd_hist) > 1 else False):
        signals.append(Signal(
            time=cur_time,
            strategy="日内做T",
            direction="SELL",
            price=price,
            reason=(
                f"反向T卖出: 偏离均价 +{deviation:.2%}, "
                f"RSI={cur_rsi:.1f}, MACD柱收窄"
            ),
            confidence="中" if cur_rsi > 70 else "低",
            strength=0.5,
        ))

    return signals


# ============================================================
# 策略五：事件驱动 + AH联动
# ============================================================

def strategy_event_ah_linkage(
    df: pd.DataFrame,
    hk_morning_change_pct: Optional[float] = None,
    overnight_gap_pct: Optional[float] = None,
    current_price: Optional[float] = None,
) -> list[Signal]:
    """
    事件驱动 + AH联动策略：
    - 港股早盘涨幅 > 2% → A股端可能滞后跟涨
    - 跳空缺口未回补 → 延续方向操作

    参数:
        hk_morning_change_pct: 港股早盘涨跌幅（需外部传入）
        overnight_gap_pct:     A股跳空幅度
    """
    signals = []
    if df.empty:
        return signals

    price = current_price if current_price else df.iloc[-1]["close"]
    cur_time = df.iloc[-1]["datetime"] if "datetime" in df.columns else dt.datetime.now()

    # ── AH联动信号 ──
    if hk_morning_change_pct is not None:
        if hk_morning_change_pct > 2.0:
            signals.append(Signal(
                time=cur_time,
                strategy="AH联动",
                direction="BUY",
                price=price,
                reason=f"港股早盘涨 {hk_morning_change_pct:+.2f}%，A股端滞后跟涨",
                confidence="高" if hk_morning_change_pct > 3.0 else "中",
                strength=min(hk_morning_change_pct / 5.0, 1.0),
            ))
        elif hk_morning_change_pct < -2.0:
            signals.append(Signal(
                time=cur_time,
                strategy="AH联动",
                direction="SELL",
                price=price,
                reason=f"港股早盘跌 {hk_morning_change_pct:+.2f}%，A股端可能滞后跟跌",
                confidence="高" if hk_morning_change_pct < -3.0 else "中",
                strength=min(abs(hk_morning_change_pct) / 5.0, 1.0),
            ))

    # ── 跳空缺口信号 ──
    if overnight_gap_pct is not None and len(df) >= 5:
        # 检查缺口是否已回补
        if overnight_gap_pct > 0.5:
            low_since_open = df["low"].min()
            prev_close = df.iloc[0]["open"] / (1 + overnight_gap_pct / 100) if overnight_gap_pct != 0 else df.iloc[0]["open"]
            if low_since_open > prev_close * 1.003:
                signals.append(Signal(
                    time=cur_time,
                    strategy="跳空缺口",
                    direction="BUY",
                    price=price,
                    reason=f"向上跳空 {overnight_gap_pct:+.2f}% 未回补，趋势延续",
                    confidence="中",
                    strength=0.6,
                ))
        elif overnight_gap_pct < -0.5:
            high_since_open = df["high"].max()
            prev_close = df.iloc[0]["open"] / (1 + overnight_gap_pct / 100) if overnight_gap_pct != 0 else df.iloc[0]["open"]
            if high_since_open < prev_close * 0.997:
                signals.append(Signal(
                    time=cur_time,
                    strategy="跳空缺口",
                    direction="SELL",
                    price=price,
                    reason=f"向下跳空 {overnight_gap_pct:+.2f}% 未回补，趋势延续",
                    confidence="中",
                    strength=0.6,
                ))

    return signals


# ============================================================
# 综合信号引擎
# ============================================================

class SignalEngine:
    """
    综合信号引擎，整合5大策略。
    根据当前交易阶段自动启用对应策略。
    """

    def __init__(self):
        self.grid_levels: list[float] = []
        self.daily_signals: list[dict] = []
        self.trade_count: int = 0

    def get_current_phase(self) -> str:
        """根据当前时间返回交易阶段"""
        now = dt.datetime.now()
        t = now.strftime("%H:%M")
        for phase_key, phase in cfg.TRADING_PHASES.items():
            if phase["start"] <= t < phase["end"]:
                return phase_key
        return "closed"

    def get_phase_label(self) -> tuple[str, str]:
        """返回 (阶段名称, 操作建议)"""
        phase = self.get_current_phase()
        info = cfg.TRADING_PHASES.get(phase, {"label": "休市", "action": "无"})
        return info["label"], info["action"]

    def should_trade(self) -> bool:
        """当前阶段是否应该交易"""
        phase = self.get_current_phase()
        return phase in ("momentum", "trend", "afternoon")

    def generate_signals(
        self,
        df: pd.DataFrame,
        current_price: Optional[float] = None,
        hk_change_pct: Optional[float] = None,
        gap_pct: Optional[float] = None,
    ) -> list[Signal]:
        """
        根据当前阶段生成综合信号。
        """
        all_signals: list[Signal] = []
        phase = self.get_current_phase()

        if not self.should_trade():
            return all_signals

        # 检查日交易次数限制
        if self.trade_count >= cfg.MAX_TRADES_PER_DAY:
            return all_signals

        # ── 动量窗口：使用开盘动量突破 ──
        if phase == "momentum":
            all_signals.extend(
                strategy_momentum_breakout(df, current_price)
            )

        # ── 趋势确认 / 午后：使用VWAP均值回归 ──
        if phase in ("trend", "afternoon"):
            all_signals.extend(
                strategy_vwap_mean_reversion(df, current_price)
            )
            sigs, self.grid_levels = strategy_atr_grid(
                df, current_price, self.grid_levels
            )
            all_signals.extend(sigs)

        # ── 全阶段可用：日内做T ──
        all_signals.extend(
            strategy_intraday_t(df, current_price)
        )

        # ── 事件驱动（如果有外部信息）──
        all_signals.extend(
            strategy_event_ah_linkage(df, hk_change_pct, gap_pct, current_price)
        )

        # 按强度排序，去重（同方向只保留最强信号）
        if all_signals:
            all_signals.sort(key=lambda s: s.strength, reverse=True)
            # 同方向只保留最强
            seen_dir = set()
            filtered = []
            for s in all_signals:
                if s.direction not in seen_dir:
                    filtered.append(s)
                    seen_dir.add(s.direction)
            all_signals = filtered

        return all_signals

    def record_trade(self, signal: Signal):
        """记录已执行的交易"""
        self.trade_count += 1
        self.daily_signals.append(signal.to_dict())

    def reset_daily(self):
        """每日重置"""
        self.grid_levels = []
        self.daily_signals = []
        self.trade_count = 0
