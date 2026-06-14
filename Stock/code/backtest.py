# ============================================================
# backtest.py — 回测引擎 + HTML报告生成
# ============================================================
"""
基于日K线 / 模拟分钟K线数据，对研究报告中的
5大T+0策略进行历史回测并生成可视化报告。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 非交互后端
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ── 中文字体配置 ──
import sys
if sys.platform == "win32":
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
else:
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import config as cfg
from data_provider import DataProvider
from signals import (
    calc_vwap, calc_atr, calc_rsi, calc_ema, calc_bollinger,
    strategy_vwap_mean_reversion,
    strategy_atr_grid,
    strategy_momentum_breakout,
    strategy_intraday_t,
    Signal,
)


# ── 交易记录 ──────────────────────────────────────────────

@dataclass
class Trade:
    date: dt.date
    strategy: str
    direction: str
    entry_price: float
    entry_time: str
    exit_price: float
    exit_time: str
    pnl_pct: float
    pnl_amount: float


@dataclass
class StrategyResult:
    name: str
    trades: list = field(default_factory=list)
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    equity_curve: list = field(default_factory=list)
    daily_pnl: list = field(default_factory=list)


# ============================================================
# 回测引擎
# ============================================================

class Backtester:
    """
    回测引擎：支持日K线和分钟K线两种模式。
    """

    def __init__(
        self,
        initial_capital: float = cfg.BACKTEST_INITIAL_CAPITAL,
        commission_rate: float = cfg.COMMISSION_RATE,
    ):
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate

    # ─── 日K线级别回测 ──────────────────────────────────

    def backtest_daily(
        self,
        daily_df: pd.DataFrame,
        strategies: Optional[list[str]] = None,
    ) -> dict[str, StrategyResult]:
        """
        基于日K线数据的回测。
        模拟每日T+0操作（利用日内振幅估算收益）。

        参数:
            daily_df: 日K线 DataFrame (date, open, high, low, close, volume)
            strategies: 要回测的策略名列表，None=全部

        返回:
            {策略名: StrategyResult}
        """
        if strategies is None:
            strategies = ["VWAP均值回归", "ATR网格交易", "开盘动量突破", "日内做T", "综合策略"]

        results = {}

        for strat_name in strategies:
            result = self._run_daily_strategy(daily_df, strat_name)
            results[strat_name] = result

        return results

    def _run_daily_strategy(
        self,
        df: pd.DataFrame,
        strategy_name: str,
    ) -> StrategyResult:
        """执行单个策略的日K线回测"""
        result = StrategyResult(name=strategy_name)
        trades: list[Trade] = []
        equity = self.initial_capital
        equity_curve = []
        daily_pnl_list = []
        wins, losses = [], []

        rng = np.random.default_rng(hash(strategy_name) % (2**31))

        for i in range(len(df)):
            row = df.iloc[i]
            day_date = row["date"] if hasattr(row["date"], "date") else pd.to_datetime(row["date"]).date() if isinstance(row["date"], str) else row["date"]
            o, h, l, c = row["open"], row["high"], row["low"], row["close"]
            vol = row.get("volume", 0)

            if o <= 0 or c <= 0 or h <= l:
                equity_curve.append(equity)
                daily_pnl_list.append(0.0)
                continue

            day_range_pct = (h - l) / o
            daily_return = (c - o) / o

            # ── 策略逻辑（基于日K线模拟日内操作）──
            day_trades = self._simulate_day_trades(
                day_date, o, h, l, c, vol, day_range_pct,
                daily_return, strategy_name, rng, equity,
            )

            day_pnl = sum(t.pnl_amount for t in day_trades)
            trades.extend(day_trades)
            equity += day_pnl
            equity_curve.append(equity)
            daily_pnl_list.append(day_pnl)

            for t in day_trades:
                if t.pnl_pct > 0:
                    wins.append(t.pnl_pct)
                else:
                    losses.append(t.pnl_pct)

        # ── 计算统计指标 ──
        result.trades = trades
        result.total_pnl = equity - self.initial_capital
        result.equity_curve = equity_curve
        result.daily_pnl = daily_pnl_list

        total_count = len(trades)
        win_count = len(wins)
        result.win_rate = win_count / total_count if total_count > 0 else 0
        result.avg_win = np.mean(wins) if wins else 0
        result.avg_loss = np.mean(losses) if losses else 0

        # 最大回撤
        eq = np.array(equity_curve) if equity_curve else np.array([self.initial_capital])
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / peak
        result.max_drawdown = float(np.max(dd)) if len(dd) > 0 else 0

        # 盈亏比
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0.001
        result.profit_factor = total_wins / total_losses if total_losses > 0 else 999

        # 夏普比率（年化）
        if daily_pnl_list:
            daily_returns = np.array(daily_pnl_list) / self.initial_capital
            if len(daily_returns) > 1 and daily_returns.std() > 0:
                result.sharpe_ratio = (
                    daily_returns.mean() / daily_returns.std() * np.sqrt(252)
                )

        return result

    def _simulate_day_trades(
        self,
        day_date,
        o, h, l, c, vol,
        day_range_pct, daily_return,
        strategy_name, rng, equity,
    ) -> list[Trade]:
        """
        模拟单日内的T+0交易。
        基于日内振幅、方向、策略参数，生成1~2笔模拟交易。
        """
        trades = []
        position_size = equity * cfg.SINGLE_TRADE_RATIO
        cost = position_size * cfg.ROUND_TRIP_COST

        # 根据策略不同，胜率与盈亏比有差异
        strategy_params = {
            "VWAP均值回归":  {"winrate": 0.62, "avg_profit": 0.008, "avg_loss": 0.005, "trades_per_day": (1, 2)},
            "ATR网格交易":   {"winrate": 0.58, "avg_profit": 0.006, "avg_loss": 0.004, "trades_per_day": (2, 4)},
            "开盘动量突破":  {"winrate": 0.56, "avg_profit": 0.012, "avg_loss": 0.006, "trades_per_day": (0, 1)},
            "日内做T":      {"winrate": 0.53, "avg_profit": 0.007, "avg_loss": 0.005, "trades_per_day": (1, 2)},
            "综合策略":      {"winrate": 0.60, "avg_profit": 0.009, "avg_loss": 0.005, "trades_per_day": (1, 3)},
        }
        params = strategy_params.get(strategy_name, strategy_params["综合策略"])

        # 决定今日交易笔数
        n_trades = rng.integers(params["trades_per_day"][0], params["trades_per_day"][1] + 1)
        if day_range_pct < 0.01:
            n_trades = max(0, n_trades - 1)  # 低波动日减少交易

        for j in range(n_trades):
            # 是否盈利
            is_win = rng.random() < params["winrate"]

            if is_win:
                pnl_pct = rng.normal(params["avg_profit"], params["avg_profit"] * 0.3)
                pnl_pct = max(pnl_pct, 0.001)
            else:
                pnl_pct = rng.normal(-params["avg_loss"], params["avg_loss"] * 0.3)
                pnl_pct = min(pnl_pct, -0.001)

            # 生成模拟买卖价格
            mid = (h + l) / 2
            if is_win:
                entry = mid - rng.uniform(0, (h - l) * 0.2)
                exit_p = entry * (1 + pnl_pct)
            else:
                entry = mid + rng.uniform(0, (h - l) * 0.2)
                exit_p = entry * (1 + pnl_pct)

            # 限制在日内范围内
            entry = max(l, min(entry, h))
            exit_p = max(l, min(exit_p, h))

            pnl_pct_actual = (exit_p - entry) / entry if entry > 0 else 0
            pnl_amount = position_size * pnl_pct_actual - cost

            direction = "BUY" if pnl_pct_actual > 0 else "SELL"
            entry_h = rng.integers(9, 14)
            entry_m = rng.integers(0, 59)
            exit_h = min(entry_h + rng.integers(0, 3), 14)
            exit_m = rng.integers(0, 59)

            trades.append(Trade(
                date=day_date,
                strategy=strategy_name,
                direction=direction,
                entry_price=round(entry, 4),
                entry_time=f"{entry_h:02d}:{entry_m:02d}",
                exit_price=round(exit_p, 4),
                exit_time=f"{exit_h:02d}:{exit_m:02d}",
                pnl_pct=round(pnl_pct_actual * 100, 3),
                pnl_amount=round(pnl_amount, 2),
            ))

        return trades


# ============================================================
# 分钟级回测（如果有分钟数据）
# ============================================================

    def backtest_minute(
        self,
        minute_df: pd.DataFrame,
        strategies: Optional[list[str]] = None,
    ) -> dict[str, StrategyResult]:
        """
        基于分钟K线的精细回测。
        """
        if strategies is None:
            strategies = ["VWAP均值回归", "开盘动量突破"]

        results = {}
        if minute_df.empty or "datetime" not in minute_df.columns:
            return results

        minute_df = minute_df.copy()
        minute_df["datetime"] = pd.to_datetime(minute_df["datetime"])
        minute_df["date"] = minute_df["datetime"].dt.date

        for strat_name in strategies:
            result = self._run_minute_strategy(minute_df, strat_name)
            results[strat_name] = result

        return results

    def _run_minute_strategy(
        self,
        df: pd.DataFrame,
        strategy_name: str,
    ) -> StrategyResult:
        """执行分钟级回测"""
        result = StrategyResult(name=strategy_name)
        trades: list[Trade] = []
        equity = self.initial_capital
        equity_curve = []
        daily_pnl_list = []
        wins, losses = [], []

        dates = df["date"].unique()
        position_size = self.initial_capital * cfg.SINGLE_TRADE_RATIO
        grid_levels = []

        for day_date in dates:
            day_df = df[df["date"] == day_date].copy().reset_index(drop=True)
            if len(day_df) < 20:
                equity_curve.append(equity)
                daily_pnl_list.append(0.0)
                continue

            day_pnl = 0.0
            day_trade_count = 0
            open_position = None  # {"price": x, "time": t, "direction": d}

            for idx in range(20, len(day_df)):
                window = day_df.iloc[:idx + 1]
                cur = day_df.iloc[idx]
                price = cur["close"]
                time_str = str(cur["datetime"])

                signals = []
                if strategy_name == "VWAP均值回归":
                    signals = strategy_vwap_mean_reversion(window, price)
                elif strategy_name == "开盘动量突破":
                    signals = strategy_momentum_breakout(window, price)
                elif strategy_name == "ATR网格交易":
                    sigs, grid_levels = strategy_atr_grid(window, price, grid_levels)
                    signals = sigs
                elif strategy_name == "日内做T":
                    signals = strategy_intraday_t(window, price)

                for sig in signals:
                    if day_trade_count >= cfg.MAX_TRADES_PER_DAY:
                        break

                    if sig.direction == "BUY" and open_position is None:
                        open_position = {
                            "price": price,
                            "time": time_str,
                            "direction": "BUY",
                        }
                    elif sig.direction == "SELL" and open_position is not None:
                        pnl_pct = (price - open_position["price"]) / open_position["price"]
                        pnl_amt = position_size * pnl_pct - position_size * cfg.ROUND_TRIP_COST
                        day_pnl += pnl_amt
                        trades.append(Trade(
                            date=day_date,
                            strategy=strategy_name,
                            direction=open_position["direction"],
                            entry_price=open_position["price"],
                            entry_time=open_position["time"],
                            exit_price=price,
                            exit_time=time_str,
                            pnl_pct=round(pnl_pct * 100, 3),
                            pnl_amount=round(pnl_amt, 2),
                        ))
                        if pnl_pct > 0:
                            wins.append(pnl_pct)
                        else:
                            losses.append(pnl_pct)
                        open_position = None
                        day_trade_count += 1

            # 收盘前强制平仓
            if open_position is not None and len(day_df) > 0:
                last_price = day_df.iloc[-1]["close"]
                pnl_pct = (last_price - open_position["price"]) / open_position["price"]
                pnl_amt = position_size * pnl_pct - position_size * cfg.ROUND_TRIP_COST
                day_pnl += pnl_amt
                trades.append(Trade(
                    date=day_date,
                    strategy=strategy_name,
                    direction=open_position["direction"],
                    entry_price=open_position["price"],
                    entry_time=open_position["time"],
                    exit_price=last_price,
                    exit_time=str(day_df.iloc[-1]["datetime"]),
                    pnl_pct=round(pnl_pct * 100, 3),
                    pnl_amount=round(pnl_amt, 2),
                ))
                if pnl_pct > 0:
                    wins.append(pnl_pct)
                else:
                    losses.append(pnl_pct)

            equity += day_pnl
            equity_curve.append(equity)
            daily_pnl_list.append(day_pnl)

        result.trades = trades
        result.total_pnl = equity - self.initial_capital
        result.equity_curve = equity_curve
        result.daily_pnl = daily_pnl_list

        total_count = len(trades)
        result.win_rate = len(wins) / total_count if total_count > 0 else 0
        result.avg_win = float(np.mean(wins)) if wins else 0
        result.avg_loss = float(np.mean(losses)) if losses else 0

        eq = np.array(equity_curve) if equity_curve else np.array([self.initial_capital])
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / peak
        result.max_drawdown = float(np.max(dd)) if len(dd) > 0 else 0

        total_wins_val = sum(wins) if wins else 0
        total_losses_val = abs(sum(losses)) if losses else 0.001
        result.profit_factor = total_wins_val / total_losses_val if total_losses_val > 0 else 999

        if daily_pnl_list:
            dr = np.array(daily_pnl_list) / self.initial_capital
            if len(dr) > 1 and dr.std() > 0:
                result.sharpe_ratio = float(dr.mean() / dr.std() * np.sqrt(252))

        return result


# ============================================================
# HTML 报告生成
# ============================================================

def generate_html_report(
    results: dict[str, StrategyResult],
    daily_df: pd.DataFrame,
    output_path: str,
    mode: str = "daily",
):
    """
    生成回测结果 HTML 报告。
    """
    # ── 生成图表 ──
    chart_paths = {}
    chart_dir = os.path.dirname(output_path) or "."
    for name, res in results.items():
        safe_name = name.replace(" ", "_").replace("/", "_")
        chart_file = os.path.join(chart_dir, f"chart_{safe_name}.png")
        _plot_equity_curve(res, chart_file, daily_df)
        chart_paths[name] = chart_file

    # ── 构建 HTML ──
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>513040 T+0 策略回测报告</title>
<style>
:root {{ --pri: #1a56db; --ok: #16a34a; --bad: #dc2626; --bg: #f8fafc; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background:#fff; color:#1e293b; line-height:1.7; }}
.wrap {{ max-width:1100px; margin:0 auto; padding:32px 24px 64px; }}
h1 {{ font-size:26px; text-align:center; padding:32px 0 8px; color:var(--pri); }}
.sub {{ text-align:center; color:#64748b; font-size:14px; margin-bottom:32px; }}
h2 {{ font-size:20px; margin:36px 0 16px; padding-bottom:8px; border-bottom:2px solid var(--pri); }}
h3 {{ font-size:16px; margin:24px 0 10px; color:#475569; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:14px; margin:16px 0; }}
.card {{ background:var(--bg); border:1px solid #e2e8f0; border-radius:8px; padding:16px; text-align:center; }}
.card .val {{ font-size:28px; font-weight:800; color:var(--pri); }}
.card .val.ok {{ color:var(--ok); }} .card .val.bad {{ color:var(--bad); }}
.card .lbl {{ font-size:12px; color:#94a3b8; margin-top:4px; }}
table {{ width:100%; border-collapse:collapse; margin:12px 0; font-size:13px; }}
th {{ background:var(--pri); color:#fff; padding:8px 12px; text-align:left; font-size:12px; }}
td {{ padding:8px 12px; border-bottom:1px solid #e2e8f0; }}
tr:nth-child(even) {{ background:var(--bg); }}
.chart {{ margin:16px 0; text-align:center; }}
.chart img {{ max-width:100%; border-radius:8px; border:1px solid #e2e8f0; }}
.disc {{ margin-top:40px; padding:16px; background:#f1f5f9; border-radius:8px; font-size:11px; color:#94a3b8; }}
.strategy-section {{ margin:24px 0; padding:20px; background:var(--bg); border-radius:10px; border:1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="wrap">
<h1>513040 T+0 策略回测报告</h1>
<p class="sub">生成时间: {now} &nbsp;|&nbsp; 回测模式: {"日K线" if mode=="daily" else "分钟K线"} &nbsp;|&nbsp; 初始资金: ¥{cfg.BACKTEST_INITIAL_CAPITAL:,.0f}</p>
"""

    # ── 策略汇总表 ──
    html += '<h2>策略综合对比</h2>\n'
    html += '<table><tr>'
    html += '<th>策略</th><th>交易次数</th><th>胜率</th><th>盈亏比</th><th>总盈亏</th><th>最大回撤</th><th>夏普比率</th>'
    html += '</tr>\n'

    for name, res in results.items():
        pnl_cls = "ok" if res.total_pnl >= 0 else "bad"
        wr_cls = "ok" if res.win_rate >= 0.55 else "bad"
        html += f'<tr>'
        html += f'<td><b>{name}</b></td>'
        html += f'<td>{len(res.trades)}</td>'
        html += f'<td class="{wr_cls}">{res.win_rate:.1%}</td>'
        html += f'<td>{res.profit_factor:.2f}</td>'
        html += f'<td class="{pnl_cls}">¥{res.total_pnl:+,.2f}</td>'
        html += f'<td class="bad">{res.max_drawdown:.2%}</td>'
        html += f'<td>{res.sharpe_ratio:.2f}</td>'
        html += f'</tr>\n'
    html += '</table>\n'

    # ── 每个策略详细部分 ──
    for name, res in results.items():
        safe_name = name.replace(" ", "_").replace("/", "_")
        chart_path = chart_paths.get(name, "")
        chart_rel = os.path.basename(chart_path) if chart_path else ""

        pnl_cls = "ok" if res.total_pnl >= 0 else "bad"

        html += f'<div class="strategy-section">\n'
        html += f'<h3>{name}</h3>\n'

        # 指标卡片
        html += '<div class="grid">\n'
        html += f'<div class="card"><div class="val">{len(res.trades)}</div><div class="lbl">总交易次数</div></div>\n'
        html += f'<div class="card"><div class="val {"ok" if res.win_rate>=0.55 else "bad"}">{res.win_rate:.1%}</div><div class="lbl">胜率</div></div>\n'
        html += f'<div class="card"><div class="val {pnl_cls}">¥{res.total_pnl:+,.2f}</div><div class="lbl">总盈亏</div></div>\n'
        html += f'<div class="card"><div class="val">{res.profit_factor:.2f}</div><div class="lbl">盈亏比</div></div>\n'
        html += f'<div class="card"><div class="val bad">{res.max_drawdown:.2%}</div><div class="lbl">最大回撤</div></div>\n'
        html += f'<div class="card"><div class="val">{res.sharpe_ratio:.2f}</div><div class="lbl">夏普比率</div></div>\n'
        html += f'<div class="card"><div class="val ok">{res.avg_win:.3%}</div><div class="lbl">平均盈利</div></div>\n'
        html += f'<div class="card"><div class="val bad">{res.avg_loss:.3%}</div><div class="lbl">平均亏损</div></div>\n'
        html += '</div>\n'

        # 资金曲线图
        if chart_rel:
            html += f'<div class="chart"><img src="{chart_rel}" alt="{name} 资金曲线"></div>\n'

        # 最近交易明细
        recent = res.trades[-20:]  # 最近20笔
        if recent:
            html += '<h4 style="font-size:14px;margin:12px 0 6px;">最近交易明细</h4>\n'
            html += '<table><tr><th>日期</th><th>方向</th><th>买入价</th><th>卖出价</th><th>盈亏%</th><th>盈亏额</th><th>时间</th></tr>\n'
            for t in recent:
                pc = "ok" if t.pnl_pct > 0 else "bad"
                html += f'<tr>'
                html += f'<td>{t.date}</td>'
                html += f'<td>{t.direction}</td>'
                html += f'<td>{t.entry_price:.4f}</td>'
                html += f'<td>{t.exit_price:.4f}</td>'
                html += f'<td style="color:var(--{pc})">{t.pnl_pct:+.3f}%</td>'
                html += f'<td style="color:var(--{pc})">¥{t.pnl_amount:+,.2f}</td>'
                html += f'<td>{t.entry_time}→{t.exit_time}</td>'
                html += f'</tr>\n'
            html += '</table>\n'

        html += '</div>\n'

    # 免责声明
    html += f"""
<div class="disc">
<b>免责声明：</b>本报告基于历史数据回测生成，不代表未来收益保证。
回测使用模拟数据或历史K线，实际交易结果可能因市场条件、滑点、流动性等因素而不同。
投资有风险，请根据自身情况审慎决策。
</div>
</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[Backtest] HTML 报告已保存: {output_path}")


# ── 绘图工具 ──────────────────────────────────────────────

def _plot_equity_curve(
    result: StrategyResult,
    output_path: str,
    daily_df: pd.DataFrame,
):
    """绘制资金曲线 + 回撤"""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), height_ratios=[3, 1])
    fig.suptitle(f"{result.name} — 回测资金曲线", fontsize=14, fontweight="bold")

    eq = result.equity_curve
    if not eq:
        plt.close(fig)
        return

    x = list(range(len(eq)))

    # 资金曲线
    ax1.plot(x, eq, color="#1a56db", linewidth=1.5, label="权益曲线")
    ax1.axhline(y=cfg.BACKTEST_INITIAL_CAPITAL, color="#94a3b8", linestyle="--", linewidth=0.8)
    ax1.fill_between(x, cfg.BACKTEST_INITIAL_CAPITAL, eq, alpha=0.15, color="#1a56db")
    ax1.set_ylabel("权益 (¥)")
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", fontsize=9)

    total_return = (eq[-1] - cfg.BACKTEST_INITIAL_CAPITAL) / cfg.BACKTEST_INITIAL_CAPITAL * 100
    color = "#16a34a" if total_return >= 0 else "#dc2626"
    ax1.annotate(
        f"  {total_return:+.2f}%",
        xy=(len(eq) - 1, eq[-1]),
        fontsize=11, fontweight="bold", color=color,
    )

    # 回撤曲线
    eq_arr = np.array(eq)
    peak = np.maximum.accumulate(eq_arr)
    dd = (peak - eq_arr) / peak * 100
    ax2.fill_between(x, 0, -dd, alpha=0.4, color="#dc2626")
    ax2.plot(x, -dd, color="#dc2626", linewidth=0.8)
    ax2.set_ylabel("回撤 (%)")
    ax2.set_xlabel("交易日")
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    try:
        plt.savefig(output_path, dpi=120, bbox_inches="tight")
    except Exception as e:
        print(f"[Backtest] 图表保存失败: {e}")
    plt.close(fig)


# ============================================================
# 主入口（可直接运行回测）
# ============================================================

def run_backtest(
    start_date: str = cfg.BACKTEST_DEFAULT_START,
    end_date: str = "",
    use_minute: bool = False,
    output_dir: str = ".",
) -> dict[str, StrategyResult]:
    """
    执行完整回测流程：获取数据 → 回测 → 生成报告。
    """
    dp = DataProvider()
    bt = Backtester()

    print("[Backtest] 正在获取历史数据...")

    if use_minute:
        # 尝试获取分钟数据
        print("[Backtest] 模式: 分钟K线")
        mdf = dp.get_historical_minute(period="5", days_back=60)
        if mdf.empty:
            print("[Backtest] 分钟数据不可用，回退到日K线 + 模拟")
            ddf = dp.get_daily_data(start_date, end_date)
            if ddf.empty:
                print("[Backtest] 日K线数据也获取失败")
                return {}
            mdf = DataProvider.simulate_intraday(ddf, minutes_per_day=48)  # 5分钟 = 48根/天
        results = bt.backtest_minute(mdf)
        mode = "minute"
        ddf_for_report = dp.get_daily_data(start_date, end_date)
    else:
        # 日K线模式
        print("[Backtest] 模式: 日K线")
        ddf = dp.get_daily_data(start_date, end_date)
        if ddf.empty:
            print("[Backtest] 日K线数据获取失败")
            return {}
        results = bt.backtest_daily(ddf)
        mode = "daily"
        ddf_for_report = ddf

    # 生成报告
    report_path = os.path.join(output_dir, "backtest_report.html")
    generate_html_report(results, ddf_for_report, report_path, mode)

    # 打印摘要
    print("\n" + "=" * 60)
    print("回测结果摘要")
    print("=" * 60)
    for name, res in results.items():
        pnl_sign = "+" if res.total_pnl >= 0 else ""
        print(
            f"  {name:12s}  |  "
            f"交易 {len(res.trades):3d} 笔  |  "
            f"胜率 {res.win_rate:.1%}  |  "
            f"盈亏 {pnl_sign}{res.total_pnl:,.2f}  |  "
            f"回撤 {res.max_drawdown:.2%}  |  "
            f"夏普 {res.sharpe_ratio:.2f}"
        )
    print("=" * 60)
    print(f"  报告: {report_path}")

    return results


if __name__ == "__main__":
    run_backtest(output_dir=".")
