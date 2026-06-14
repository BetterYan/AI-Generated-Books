#!/usr/bin/env python3
# ============================================================
# main.py — SH513040 T+0 交易助手 · 主程序
# ============================================================
"""
港股通互联网ETF (513040) T+0 短线交易助手
基于《T+0短线交易策略研究报告》

功能:
  1. 每日待办提醒   — 不同时间点提醒需要做什么
  2. 实时盯盘监控   — 行情刷新 + 异动告警
  3. 交易信号提醒   — 5大策略实时信号生成
  4. 盘后策略回测   — 收盘后自动运行回测并生成报告

用法:
  python main.py monitor     实时监控 + 信号提醒（盘中使用）
  python main.py reminder    待办提醒模式（仅提醒）
  python main.py backtest    运行策略回测
  python main.py all         全部功能（提醒 + 盯盘 + 自动回测）
  python main.py --help      查看帮助
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
import threading
from typing import Optional

# ── 项目模块 ──
import config as cfg
from notifier import (
    console, banner, print_reminder, print_signal,
    print_price_update, print_alert, print_phase_change,
    print_info, print_error, print_separator, print_daily_summary,
    now_str,
)
from data_provider import DataProvider
from signals import SignalEngine, Signal, calc_vwap
from backtest import run_backtest


# ============================================================
# 待办提醒调度器
# ============================================================

class ReminderScheduler:
    """
    基于配置中的时间列表，在对应时刻触发待办提醒。
    """

    def __init__(self):
        self.reminders = cfg.DAILY_REMINDERS
        self.fired_today: set[str] = set()
        self.today = dt.date.today()

    def check(self):
        """检查是否到达提醒时间"""
        now = dt.datetime.now()
        today = now.date()

        # 跨日重置
        if today != self.today:
            self.fired_today.clear()
            self.today = today

        current_time = now.strftime("%H:%M")

        for r in self.reminders:
            key = f"{r['time']}_{r['title']}"
            if key in self.fired_today:
                continue

            if current_time >= r["time"]:
                is_urgent = r["time"] in ("09:45", "14:45", "15:00")
                print_reminder(r["title"], r["desc"], is_urgent=is_urgent)
                self.fired_today.add(key)

    def run_loop(self, interval: int = 30):
        """循环检查提醒"""
        while True:
            try:
                self.check()
            except Exception as e:
                print_error(f"提醒检查异常: {e}")
            time.sleep(interval)


# ============================================================
# 盯盘监控器
# ============================================================

class MarketMonitor:
    """
    实时行情监控：
    - 定时刷新价格
    - 检测异动（急涨急跌、量能异动）
    - 计算并展示VWAP
    - 触发交易信号
    """

    def __init__(self):
        self.dp = DataProvider()
        self.engine = SignalEngine()
        self.last_price: float = 0.0
        self.last_alert_time: dt.datetime = dt.datetime.min
        self.minute_bars: list[dict] = []
        self.daily_trades: list[dict] = []
        self.consecutive_losses: int = 0
        self.daily_pnl: float = 0.0
        self.last_phase: str = ""

    def fetch_and_update(self) -> Optional[dict]:
        """获取最新行情"""
        quote = self.dp.get_realtime_quote()
        if not quote or quote.get("price", 0) <= 0:
            return None

        price = quote["price"]
        change_pct = quote.get("change_pct", 0)
        volume = quote.get("volume", 0)

        # 构建分钟K线（用于信号计算）
        now = dt.datetime.now()
        bar = {
            "datetime": now,
            "open":  price,
            "high":  max(price, quote.get("high", price)),
            "low":   min(price, quote.get("low", price)),
            "close": price,
            "volume": volume,
        }

        # 聚合为1分钟K线
        if self.minute_bars:
            last_bar = self.minute_bars[-1]
            if (now - last_bar["datetime"]).total_seconds() < 60:
                last_bar["high"] = max(last_bar["high"], price)
                last_bar["low"] = min(last_bar["low"], price)
                last_bar["close"] = price
                last_bar["volume"] = volume
            else:
                self.minute_bars.append(bar)
        else:
            self.minute_bars.append(bar)

        # 保留最近240根（一天的分钟数）
        self.minute_bars = self.minute_bars[-240:]

        # 计算VWAP
        import pandas as pd
        import numpy as np
        df = pd.DataFrame(self.minute_bars)
        vwap_val = None
        if len(df) > 5:
            vwap_series = calc_vwap(df)
            vwap_val = float(vwap_series.iloc[-1]) if not vwap_series.empty else None

        # 获取当前阶段
        phase_label, phase_action = self.engine.get_phase_label()

        # 打印行情
        print_price_update(
            price=price,
            change_pct=change_pct,
            volume=volume,
            vwap=vwap_val,
            phase=phase_label,
        )

        # 阶段切换提醒
        current_phase = self.engine.get_current_phase()
        if current_phase != self.last_phase and self.last_phase != "":
            print_phase_change(phase_label, phase_action)
        self.last_phase = current_phase

        return {
            "price": price,
            "change_pct": change_pct,
            "volume": volume,
            "vwap": vwap_val,
            "df": df,
            "quote": quote,
        }

    def check_alerts(self, data: dict):
        """检测异动"""
        price = data["price"]
        change_pct = data["change_pct"]
        now = dt.datetime.now()

        # 避免短时间内重复告警
        if (now - self.last_alert_time).total_seconds() < 60:
            return

        # 涨跌幅异动
        if abs(change_pct) >= cfg.PRICE_ALERT_PCT * 100:
            direction = "急涨" if change_pct > 0 else "急跌"
            print_alert(
                f"价格{direction} {change_pct:+.2f}%",
                f"当前价格 {price:.4f}，涨跌幅已超过 {cfg.PRICE_ALERT_PCT:.0%}，请关注风险。",
                level="warning",
            )
            self.last_alert_time = now

        # 异常波动（快速变动）
        if self.last_price > 0:
            quick_change = abs(price - self.last_price) / self.last_price
            if quick_change >= cfg.ABNORMAL_VOLATILITY:
                print_alert(
                    f"异常波动 {quick_change:.2%}",
                    f"价格在短时间内剧烈变动，建议暂停操作或减仓。",
                    level="danger",
                )
                self.last_alert_time = now

        # 日亏损熔断检查
        if self.daily_pnl < 0:
            pnl_pct = self.daily_pnl / cfg.BACKTEST_INITIAL_CAPITAL
            if abs(pnl_pct) >= cfg.DAILY_LOSS_LIMIT:
                print_alert(
                    "日亏损熔断！",
                    f"今日累计亏损 {pnl_pct:.2%}，已触发日亏损熔断线 {cfg.DAILY_LOSS_LIMIT:.0%}。"
                    f"请立即停止所有操作！",
                    level="danger",
                )
                self.last_alert_time = now

        # 连续亏损检查
        if self.consecutive_losses >= cfg.CONSECUTIVE_LOSS_LIMIT:
            print_alert(
                "连续亏损熔断！",
                f"已连续 {self.consecutive_losses} 笔亏损，触发熔断。请休息至少2小时。",
                level="danger",
            )
            self.last_alert_time = now

        self.last_price = price

    def check_signals(self, data: dict):
        """生成并展示交易信号"""
        import pandas as pd

        df = data["df"]
        price = data["price"]

        if not self.engine.should_trade():
            return

        # 计算跳空幅度
        quote = data.get("quote", {})
        prev_close = quote.get("close", 0)
        open_price = quote.get("open", 0)
        gap_pct = None
        if prev_close > 0 and open_price > 0:
            gap_pct = (open_price - prev_close) / prev_close * 100

        signals = self.engine.generate_signals(
            df=df,
            current_price=price,
            gap_pct=gap_pct,
        )

        for sig in signals:
            print_signal(
                strategy=sig.strategy,
                direction=sig.direction,
                price=sig.price,
                reason=sig.reason,
                confidence=sig.confidence,
            )
            self.engine.record_trade(sig)

            # 记录交易用于统计
            self.daily_trades.append(sig.to_dict())

    def run_loop(self, interval: int = cfg.MONITOR_INTERVAL_SEC):
        """主监控循环"""
        print_info(f"盯盘监控已启动，刷新间隔 {interval} 秒")
        print_separator()

        while True:
            try:
                now = dt.datetime.now()
                t = now.strftime("%H:%M")

                # 只在交易时段监控
                if "09:25" <= t <= "15:05":
                    data = self.fetch_and_update()
                    if data:
                        self.check_alerts(data)
                        self.check_signals(data)
                elif t > "15:05" and t < "15:10":
                    # 收盘后打印日汇总
                    self._print_end_of_day()
                    time.sleep(300)  # 打印后等5分钟避免重复
                else:
                    time.sleep(60)  # 非交易时段每分钟检查一次

            except KeyboardInterrupt:
                print_info("用户中断，退出监控")
                break
            except Exception as e:
                print_error(f"监控异常: {e}")

            time.sleep(interval)

    def _print_end_of_day(self):
        """打印每日汇总"""
        if not self.daily_trades:
            print_info("今日无交易信号")
            return

        wins = sum(1 for t in self.daily_trades if t.get("strength", 0) > 0.5)
        total = len(self.daily_trades)
        print_daily_summary(
            total_trades=total,
            win_trades=wins,
            total_pnl=self.daily_pnl,
            daily_pnl_pct=self.daily_pnl / cfg.BACKTEST_INITIAL_CAPITAL * 100,
            trades=self.daily_trades,
        )

    def reset_daily(self):
        """每日重置"""
        self.minute_bars.clear()
        self.daily_trades.clear()
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.last_phase = ""
        self.engine.reset_daily()


# ============================================================
# 主程序
# ============================================================

def mode_monitor():
    """监控模式：盯盘 + 信号"""
    banner()
    print_info("启动实时监控模式")
    print_info(f"标的: {cfg.ETF_NAME} ({cfg.ETF_CODE})")
    print_separator()

    monitor = MarketMonitor()
    try:
        monitor.run_loop()
    except KeyboardInterrupt:
        print_info("已退出")


def mode_reminder():
    """提醒模式：仅待办提醒"""
    banner()
    print_info("启动待办提醒模式")
    print_info(f"共 {len(cfg.DAILY_REMINDERS)} 个提醒时间点")
    print_separator()

    scheduler = ReminderScheduler()

    # 先立即检查一次
    scheduler.check()

    try:
        scheduler.run_loop(interval=30)
    except KeyboardInterrupt:
        print_info("已退出")


def mode_backtest(start: str = "", end: str = "", minute: bool = False):
    """回测模式"""
    banner()
    print_info("启动策略回测模式")
    print_separator()

    start_date = start or cfg.BACKTEST_DEFAULT_START
    end_date = end or dt.datetime.now().strftime("%Y%m%d")
    output_dir = os.path.dirname(os.path.abspath(__file__))

    print_info(f"回测区间: {start_date} ~ {end_date}")
    print_info(f"输出目录: {output_dir}")
    print_separator()

    results = run_backtest(
        start_date=start_date,
        end_date=end_date,
        use_minute=minute,
        output_dir=output_dir,
    )

    if results:
        print_info("回测完成！请查看 HTML 报告")
    else:
        print_error("回测未产生结果，请检查数据源")


def mode_all():
    """全功能模式：提醒 + 盯盘 + 自动回测"""
    banner()
    print_info("启动全功能模式（提醒 + 盯盘 + 自动回测）")
    print_separator()

    # 启动提醒调度器（后台线程）
    reminder = ReminderScheduler()
    reminder_thread = threading.Thread(
        target=reminder.run_loop,
        kwargs={"interval": 30},
        daemon=True,
    )
    reminder_thread.start()
    print_info("待办提醒调度器已启动（后台）")

    # 启动盯盘监控（主线程）
    monitor = MarketMonitor()

    # 注册收盘后自动回测
    def auto_backtest_check():
        """在15:10后自动触发回测"""
        last_backtest_date = None
        while True:
            now = dt.datetime.now()
            t = now.strftime("%H:%M")
            today = now.date()

            if "15:10" <= t <= "15:15" and last_backtest_date != today:
                print_separator()
                print_info("收盘自动回测启动...")
                output_dir = os.path.dirname(os.path.abspath(__file__))
                run_backtest(
                    start_date=cfg.BACKTEST_DEFAULT_START,
                    output_dir=output_dir,
                )
                last_backtest_date = today
                print_info("自动回测完成")

            time.sleep(60)

    backtest_thread = threading.Thread(
        target=auto_backtest_check,
        daemon=True,
    )
    backtest_thread.start()
    print_info("自动回测调度器已启动（后台）")
    print_separator()

    # 主线程运行盯盘
    try:
        monitor.run_loop()
    except KeyboardInterrupt:
        print_info("已退出全功能模式")


def mode_status():
    """状态查看"""
    banner()
    print_info("系统状态")
    print_separator()

    # 尝试获取实时行情
    try:
        dp = DataProvider()
        quote = dp.get_realtime_quote()
        if quote:
            from rich.table import Table
            from rich import box
            table = Table(title=f"{cfg.ETF_NAME} ({cfg.ETF_CODE}) 实时行情", box=box.ROUNDED)
            table.add_column("项目", style="cyan")
            table.add_column("数值", justify="right")
            table.add_row("最新价", f"{quote.get('price', 0):.4f}")
            table.add_row("涨跌幅", f"{quote.get('change_pct', 0):+.2f}%")
            table.add_row("今开", f"{quote.get('open', 0):.4f}")
            table.add_row("最高", f"{quote.get('high', 0):.4f}")
            table.add_row("最低", f"{quote.get('low', 0):.4f}")
            table.add_row("成交量", f"{quote.get('volume', 0):,.0f}")
            table.add_row("成交额", f"{quote.get('amount', 0):,.0f}")
            console.print(table)
        else:
            print_info("无法获取实时行情（可能非交易时段）")
    except Exception as e:
        print_error(f"获取行情失败: {e}")

    # 当前阶段
    engine = SignalEngine()
    phase_label, phase_action = engine.get_phase_label()
    print_info(f"当前阶段: {phase_label} — {phase_action}")

    # 配置概览
    from rich.table import Table
    from rich import box
    table = Table(title="策略配置概览", box=box.ROUNDED)
    table.add_column("参数", style="cyan")
    table.add_column("值", justify="right")
    table.add_row("VWAP买入阈值", f"{cfg.VWAP_BUY_THRESHOLD:.1%}")
    table.add_row("VWAP止损阈值", f"{cfg.VWAP_STOP_LOSS:.1%}")
    table.add_row("网格ATR周期", str(cfg.GRID_ATR_PERIOD))
    table.add_row("单笔止损", f"{cfg.SINGLE_STOP_LOSS:.1%}")
    table.add_row("日亏损熔断", f"{cfg.DAILY_LOSS_LIMIT:.1%}")
    table.add_row("每日最大交易", f"{cfg.MAX_TRADES_PER_DAY} 次")
    table.add_row("底仓比例", f"{cfg.BASE_POSITION_RATIO:.0%}")
    table.add_row("浮仓比例", f"{cfg.FLOAT_POSITION_RATIO:.0%}")
    console.print(table)


# ── CLI 入口 ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="sh513040",
        description="港股通互联网ETF(513040) T+0 短线交易助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py gui                  启动图形界面（推荐）
  python main.py monitor              启动实时监控+交易信号
  python main.py reminder             启动待办提醒
  python main.py backtest             运行日K线回测
  python main.py backtest --minute    运行分钟K线回测
  python main.py backtest --start 20250601 --end 20260101
  python main.py all                  全功能模式
  python main.py status               查看系统状态
        """,
    )

    sub = parser.add_subparsers(dest="command", help="运行模式")

    sub.add_parser("gui", help="启动图形界面（推荐）")
    sub.add_parser("monitor", help="实时监控行情 + 交易信号提醒")
    sub.add_parser("reminder", help="每日待办事项提醒")

    bt = sub.add_parser("backtest", help="收盘后策略回测")
    bt.add_argument("--start", "-s", type=str, default="", help="回测起始日期 YYYYMMDD")
    bt.add_argument("--end", "-e", type=str, default="", help="回测结束日期 YYYYMMDD")
    bt.add_argument("--minute", "-m", action="store_true", help="使用分钟K线回测（更精细）")

    sub.add_parser("all", help="全功能模式（提醒+盯盘+自动回测）")
    sub.add_parser("status", help="查看当前系统状态")

    args = parser.parse_args()

    if args.command is None:
        # 默认启动 GUI
        import gui
        gui.main()
        sys.exit(0)

    commands = {
        "gui":      lambda: __import__("gui").main(),
        "monitor":  mode_monitor,
        "reminder": mode_reminder,
        "all":      mode_all,
        "status":   mode_status,
    }

    if args.command == "backtest":
        mode_backtest(
            start=args.start,
            end=args.end,
            minute=args.minute,
        )
    elif args.command in commands:
        commands[args.command]()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
