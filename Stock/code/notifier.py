# ============================================================
# notifier.py — 通知提醒模块
# ============================================================
"""
支持终端富文本输出 + 桌面系统通知 + 声音提醒。
"""
from __future__ import annotations

import datetime as dt
import os
import platform
import threading
import time
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box

import config as cfg

import sys

# Windows 终端需要 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

console = Console(force_terminal=True, color_system="truecolor")

# ── 桌面通知 ──────────────────────────────────────────────

def _desktop_notify(title: str, message: str):
    """发送桌面系统通知（后台线程，不阻塞主循环）"""
    if not cfg.DESKTOP_NOTIFY_ENABLED:
        return

    def _send():
        try:
            from plyer import notification
            notification.notify(
                title=f"[513040] {title}",
                message=message[:200],
                app_name="SH513040 T+0 交易助手",
                timeout=6,
            )
        except Exception:
            pass  # 桌面通知失败不中断主程序

    threading.Thread(target=_send, daemon=True).start()


def _sound_alert():
    """播放系统提示音"""
    if not cfg.SOUND_ENABLED:
        return

    def _beep():
        try:
            system = platform.system()
            if system == "Windows":
                import winsound
                winsound.Beep(800, 300)
            else:
                os.system("afplay /System/Library/Sounds/Glass.aiff &")
        except Exception:
            pass

    threading.Thread(target=_beep, daemon=True).start()


# ── 终端输出 ──────────────────────────────────────────────

def now_str() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def banner():
    """打印启动横幅"""
    console.print()
    panel = Panel(
        f"[bold cyan]港股通互联网ETF (513040) T+0 交易助手[/bold cyan]\n"
        f"[dim]基于《T+0短线交易策略研究报告》[/dim]\n"
        f"[dim]启动时间: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
        title="🔔 SH513040 Trader",
        border_style="bright_blue",
        padding=(1, 2),
    )
    console.print(panel)
    console.print()


def print_reminder(title: str, desc: str, is_urgent: bool = False):
    """打印待办提醒"""
    style = "bold red" if is_urgent else "bold yellow"
    border = "red" if is_urgent else "yellow"
    icon = "🚨" if is_urgent else "⏰"

    panel = Panel(
        f"[{style}]{title}[/{style}]\n[white]{desc}[/white]",
        title=f"{icon} 待办提醒 · {now_str()}",
        border_style=border,
        padding=(0, 2),
    )
    console.print(panel)
    _desktop_notify(title, desc)
    if is_urgent:
        _sound_alert()


def print_signal(
    strategy: str,
    direction: str,
    price: float,
    reason: str,
    confidence: str = "中",
):
    """打印交易信号"""
    dir_map = {
        "BUY":  ("🟢 买入", "green"),
        "SELL": ("🔴 卖出", "red"),
        "HOLD": ("⚪ 持有", "white"),
    }
    label, color = dir_map.get(direction.upper(), ("❓ 未知", "white"))

    conf_color = {"高": "red", "中": "yellow", "低": "dim"}.get(confidence, "dim")

    panel = Panel(
        f"[{color}]{label}[/{color}]  价格: [bold]{price:.4f}[/bold]\n"
        f"策略: [cyan]{strategy}[/cyan]  置信度: [{conf_color}]{confidence}[/{conf_color}]\n"
        f"[dim]理由: {reason}[/dim]",
        title=f"📡 交易信号 · {now_str()}",
        border_style=color,
        padding=(0, 2),
    )
    console.print(panel)
    _desktop_notify(f"交易信号: {label}", f"{strategy} | 价格 {price:.4f} | {reason}")
    _sound_alert()


def print_price_update(
    price: float,
    change_pct: float,
    volume: float,
    vwap: Optional[float] = None,
    phase: str = "",
):
    """打印实时行情更新（单行）"""
    color = "red" if change_pct > 0 else ("green" if change_pct < 0 else "dim")
    arrow = "▲" if change_pct > 0 else ("▼" if change_pct < 0 else "─")

    vwap_str = f"  VWAP: {vwap:.4f}" if vwap else ""
    phase_str = f"  [{phase}]" if phase else ""

    console.print(
        f"[dim]{now_str()}[/dim]  "
        f"[{color}]{arrow} {price:.4f} ({change_pct:+.2f}%)[/{color}]  "
        f"量: {volume:,.0f}{vwap_str}{phase_str}"
    )


def print_alert(title: str, message: str, level: str = "warning"):
    """打印告警信息"""
    style_map = {
        "warning": ("bold yellow", "yellow", "⚠️"),
        "danger":  ("bold red",    "red",    "🚨"),
        "info":    ("bold blue",   "blue",   "ℹ️"),
        "success": ("bold green",  "green",  "✅"),
    }
    style, border, icon = style_map.get(level, style_map["info"])

    panel = Panel(
        f"[{style}]{title}[/{style}]\n{message}",
        title=f"{icon} 告警 · {now_str()}",
        border_style=border,
        padding=(0, 2),
    )
    console.print(panel)
    _desktop_notify(title, message)
    if level in ("warning", "danger"):
        _sound_alert()


def print_trade_result(
    direction: str,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    strategy: str,
):
    """打印单笔交易结果"""
    pnl_color = "green" if pnl_pct > 0 else "red"
    console.print(
        f"[dim]{now_str()}[/dim]  "
        f"[cyan]{strategy}[/cyan]  "
        f"{'买入' if direction == 'BUY' else '卖出'} "
        f"{entry_price:.4f} → {exit_price:.4f}  "
        f"[{pnl_color}]{pnl_pct:+.3f}%[/{pnl_color}]"
    )


def print_daily_summary(
    total_trades: int,
    win_trades: int,
    total_pnl: float,
    daily_pnl_pct: float,
    trades: list,
):
    """打印每日交易汇总"""
    table = Table(
        title=f"📊 每日交易汇总 · {dt.date.today()}",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("序号", style="dim", width=4)
    table.add_column("策略", style="cyan")
    table.add_column("方向", width=4)
    table.add_column("买入价", justify="right")
    table.add_column("卖出价", justify="right")
    table.add_column("盈亏%", justify="right")
    table.add_column("时间", style="dim")

    for i, t in enumerate(trades, 1):
        pnl = t.get("pnl_pct", 0)
        pnl_style = "green" if pnl > 0 else "red"
        table.add_row(
            str(i),
            t.get("strategy", "-"),
            t.get("direction", "-"),
            f"{t.get('entry_price', 0):.4f}",
            f"{t.get('exit_price', 0):.4f}",
            f"[{pnl_style}]{pnl:+.3f}%[/{pnl_style}]",
            t.get("time", "-"),
        )

    console.print(table)

    # 汇总
    win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0
    pnl_color = "green" if daily_pnl_pct >= 0 else "red"
    console.print(
        f"  总交易: {total_trades}  "
        f"胜率: [bold]{win_rate:.1f}%[/bold]  "
        f"日盈亏: [{pnl_color}]{daily_pnl_pct:+.3f}%[/{pnl_color}]  "
        f"盈亏金额: [{pnl_color}]{total_pnl:+,.2f}[/{pnl_color}]"
    )
    console.print()


def print_phase_change(phase_name: str, action: str):
    """打印交易阶段切换"""
    panel = Panel(
        f"[bold cyan]当前阶段: {phase_name}[/bold cyan]\n"
        f"[white]操作建议: {action}[/white]",
        title=f"📋 阶段切换 · {now_str()}",
        border_style="cyan",
        padding=(0, 2),
    )
    console.print(panel)
    _desktop_notify(f"阶段: {phase_name}", action)


def print_separator(char: str = "─", width: int = 60):
    console.print(f"[dim]{char * width}[/dim]")


def print_info(msg: str):
    console.print(f"[dim]{now_str()}[/dim]  [blue]ℹ[/blue]  {msg}")


def print_error(msg: str):
    console.print(f"[dim]{now_str()}[/dim]  [red]✗[/red]  {msg}")
    _desktop_notify("错误", msg)
