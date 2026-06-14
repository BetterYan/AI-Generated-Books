#!/usr/bin/env python3
# ============================================================
# gui.py — SH513040 T+0 交易助手 · 图形界面
# ============================================================
"""
基于 CustomTkinter 构建的现代化桌面 GUI。
包含: 仪表盘 / 实时监控 / 信号中心 / 策略回测 / 每日提醒 / 系统设置
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import threading
import time
from typing import Optional

import numpy as np
import pandas as pd

# ── Windows 编码修正 ──
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import customtkinter as ctk
from tkinter import messagebox, filedialog
import tkinter as tk

# matplotlib 嵌入
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

if sys.platform == "win32":
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
else:
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# ── 项目模块 ──
import config as cfg
from data_provider import DataProvider
from signals import (
    SignalEngine, Signal, calc_vwap, calc_atr, calc_rsi, calc_ema,
    strategy_vwap_mean_reversion, strategy_atr_grid,
    strategy_momentum_breakout, strategy_intraday_t,
    strategy_event_ah_linkage,
)
from backtest import Backtester, run_backtest, generate_html_report

# ── 主题配色 ──
BG_DARK      = "#0f1117"
BG_CARD      = "#1a1d2e"
BG_SIDEBAR   = "#141625"
BG_INPUT     = "#252840"
ACCENT       = "#4f7cff"
ACCENT_HOVER = "#6b93ff"
GREEN        = "#22c55e"
RED          = "#ef4444"
ORANGE       = "#f59e0b"
TEXT_PRIMARY  = "#e2e8f0"
TEXT_SECONDARY= "#94a3b8"
TEXT_DIM      = "#64748b"
BORDER       = "#2d3154"


# ============================================================
#  工具组件
# ============================================================

class StatCard(ctk.CTkFrame):
    """指标卡片组件"""
    def __init__(self, master, title: str, value: str = "--",
                 subtitle: str = "", color: str = ACCENT, **kw):
        super().__init__(master, fg_color=BG_CARD, corner_radius=12,
                         border_width=1, border_color=BORDER, **kw)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=12),
                     text_color=TEXT_SECONDARY).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 0))
        self.val_label = ctk.CTkLabel(self, text=value, font=ctk.CTkFont(size=26, weight="bold"),
                                      text_color=color)
        self.val_label.grid(row=1, column=0, sticky="w", padx=14, pady=(2, 0))
        if subtitle:
            self.sub_label = ctk.CTkLabel(self, text=subtitle, font=ctk.CTkFont(size=11),
                                          text_color=TEXT_DIM)
            self.sub_label.grid(row=2, column=0, sticky="w", padx=14, pady=(2, 10))
        else:
            ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=4)).grid(row=2, column=0)

    def set_value(self, value: str, color: str | None = None):
        self.val_label.configure(text=value)
        if color:
            self.val_label.configure(text_color=color)

    def set_subtitle(self, text: str):
        if hasattr(self, "sub_label"):
            self.sub_label.configure(text=text)


class SectionTitle(ctk.CTkLabel):
    """页面分区标题"""
    def __init__(self, master, text: str, **kw):
        super().__init__(master, text=text, font=ctk.CTkFont(size=15, weight="bold"),
                         text_color=TEXT_PRIMARY, anchor="w", **kw)


# ============================================================
#  仪表盘页面
# ============================================================

class DashboardPage(ctk.CTkScrollableFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="col")

        # ── 顶部标题 ──
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(hdr, text="仪表盘", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(side="left")
        self.clock = ctk.CTkLabel(hdr, text="", font=ctk.CTkFont(size=13),
                                  text_color=TEXT_SECONDARY)
        self.clock.pack(side="right")

        # ── 指标卡片 ──
        self.card_price   = StatCard(self, "最新价", "--", "港股通互联网ETF")
        self.card_change  = StatCard(self, "涨跌幅", "--", "日内变动")
        self.card_volume  = StatCard(self, "成交额", "--", "场内成交")
        self.card_vwap    = StatCard(self, "VWAP", "--", "成交量加权均价")
        for i, card in enumerate([self.card_price, self.card_change, self.card_volume, self.card_vwap]):
            card.grid(row=1, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0), pady=4)

        # ── 交易阶段 + 风控面板 ──
        self.grid_columnconfigure((0, 1), weight=1, uniform="mid")
        phase_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12,
                                   border_width=1, border_color=BORDER)
        phase_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 4), padx=(0, 4))
        SectionTitle(phase_frame, "当前交易阶段").pack(anchor="w", padx=14, pady=(14, 4))
        self.phase_label = ctk.CTkLabel(phase_frame, text="--", font=ctk.CTkFont(size=18, weight="bold"),
                                        text_color=ACCENT)
        self.phase_label.pack(anchor="w", padx=14)
        self.phase_action = ctk.CTkLabel(phase_frame, text="--", font=ctk.CTkFont(size=13),
                                         text_color=TEXT_SECONDARY)
        self.phase_action.pack(anchor="w", padx=14, pady=(2, 14))

        risk_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12,
                                  border_width=1, border_color=BORDER)
        risk_frame.grid(row=2, column=2, columnspan=2, sticky="ew", pady=(16, 4), padx=(4, 0))
        SectionTitle(risk_frame, "风控状态").pack(anchor="w", padx=14, pady=(14, 4))
        self.risk_info = ctk.CTkLabel(risk_frame, text="", font=ctk.CTkFont(size=12),
                                      text_color=TEXT_SECONDARY, justify="left", anchor="w")
        self.risk_info.pack(anchor="w", padx=14, pady=(0, 14))

        # ── 今日待办时间线 ──
        tl_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12,
                                border_width=1, border_color=BORDER)
        tl_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(16, 4))
        SectionTitle(tl_frame, "今日待办时间线").pack(anchor="w", padx=14, pady=(14, 8))
        self.timeline_box = ctk.CTkFrame(tl_frame, fg_color="transparent")
        self.timeline_box.pack(fill="x", padx=14, pady=(0, 14))

        # ── 策略概览 ──
        strat_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12,
                                   border_width=1, border_color=BORDER)
        strat_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 20))
        SectionTitle(strat_frame, "策略体系概览").pack(anchor="w", padx=14, pady=(14, 8))
        strategies = [
            ("VWAP 均值回归", "主力策略", "胜率 62%  评分 9.2/10", GREEN),
            ("ATR 网格交易",  "震荡替代", "胜率 58%  评分 8.5/10", ACCENT),
            ("开盘动量突破",  "早盘专用", "胜率 56%  评分 8.0/10", ORANGE),
            ("日内做 T",     "底仓增强", "胜率 53%  评分 7.5/10", TEXT_SECONDARY),
            ("事件驱动+AH",  "特殊事件", "胜率 65%  评分 7.0/10", "#a78bfa"),
        ]
        for i, (name, tag, stats, color) in enumerate(strategies):
            row = ctk.CTkFrame(strat_frame, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=2)
            ctk.CTkLabel(row, text="●", text_color=color, font=ctk.CTkFont(size=14)).pack(side="left")
            ctk.CTkLabel(row, text=f"  {name}", font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=TEXT_PRIMARY).pack(side="left")
            ctk.CTkLabel(row, text=f"  [{tag}]", font=ctk.CTkFont(size=11),
                         text_color=TEXT_DIM).pack(side="left", padx=(4, 0))
            ctk.CTkLabel(row, text=stats, font=ctk.CTkFont(size=12),
                         text_color=TEXT_SECONDARY).pack(side="right")

    def refresh(self):
        """刷新仪表盘数据"""
        now = dt.datetime.now()
        self.clock.configure(text=now.strftime("%Y-%m-%d %H:%M:%S"))

        # 交易阶段
        engine = self.app.signal_engine
        phase_label, phase_action = engine.get_phase_label()
        self.phase_label.configure(text=phase_label)
        self.phase_action.configure(text=phase_action)

        # 风控状态
        ri = (f"底仓比例: {cfg.BASE_POSITION_RATIO:.0%}    浮仓比例: {cfg.FLOAT_POSITION_RATIO:.0%}\n"
              f"单笔止损: {cfg.SINGLE_STOP_LOSS:.1%}     日亏损熔断: {cfg.DAILY_LOSS_LIMIT:.1%}\n"
              f"今日交易: {engine.trade_count}/{cfg.MAX_TRADES_PER_DAY} 次")
        self.risk_info.configure(text=ri)

        # 尝试获取实时行情
        if self.app.latest_quote:
            q = self.app.latest_quote
            price = q.get("price", 0)
            change = q.get("change_pct", 0)
            vol = q.get("amount", 0)
            self.card_price.set_value(f"{price:.4f}" if price else "--",
                                      GREEN if change > 0 else RED if change < 0 else TEXT_PRIMARY)
            self.card_change.set_value(f"{change:+.2f}%" if change else "--",
                                       GREEN if change > 0 else RED)
            self.card_volume.set_value(f"{vol/1e8:.2f}亿" if vol else "--")

        # 时间线
        self._build_timeline()

    def _build_timeline(self):
        for w in self.timeline_box.winfo_children():
            w.destroy()
        now_t = dt.datetime.now().strftime("%H:%M")
        reminders = cfg.DAILY_REMINDERS
        cols = min(len(reminders), 7)
        for i, r in enumerate(reminders):
            done = r["time"] <= now_t
            color = GREEN if done else (ORANGE if r["time"] == now_t[:5] else TEXT_DIM)
            cell = ctk.CTkFrame(self.timeline_box, fg_color="transparent")
            cell.pack(side="left", padx=4, expand=True, fill="x")
            ctk.CTkLabel(cell, text="●" if done else "○", text_color=color,
                         font=ctk.CTkFont(size=13)).pack()
            ctk.CTkLabel(cell, text=r["time"], font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=color).pack()
            ctk.CTkLabel(cell, text=r["title"][:6], font=ctk.CTkFont(size=9),
                         text_color=TEXT_DIM).pack()


# ============================================================
#  盯盘监控页面
# ============================================================

class MonitorPage(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.monitoring = False
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(1, weight=1)

        # ── 顶部控制栏 ──
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(top, text="实时监控", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(side="left")
        self.btn_toggle = ctk.CTkButton(top, text="▶ 启动监控", width=120, fg_color=ACCENT,
                                        hover_color=ACCENT_HOVER, command=self._toggle_monitor)
        self.btn_toggle.pack(side="right", padx=4)
        self.btn_refresh = ctk.CTkButton(top, text="刷新行情", width=100, fg_color=BG_INPUT,
                                         hover_color=BORDER, command=self._manual_refresh)
        self.btn_refresh.pack(side="right", padx=4)
        self.status_dot = ctk.CTkLabel(top, text="● 已停止", text_color=RED,
                                       font=ctk.CTkFont(size=12))
        self.status_dot.pack(side="right", padx=8)

        # ── 左侧：图表 ──
        left = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12, border_width=1, border_color=BORDER)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=4)
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        self.fig = Figure(figsize=(6, 4), dpi=100, facecolor=BG_CARD)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(BG_CARD)
        self.ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
        self.ax.spines["bottom"].set_color(BORDER)
        self.ax.spines["left"].set_color(BORDER)
        self.ax.spines["top"].set_visible(False)
        self.ax.spines["right"].set_visible(False)
        self.canvas = FigureCanvasTkAgg(self.fig, master=left)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        # ── 右侧：信号列表 + 行情详情 ──
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=4)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # 行情详情卡
        detail = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=12,
                              border_width=1, border_color=BORDER)
        detail.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        SectionTitle(detail, "行情详情").pack(anchor="w", padx=12, pady=(10, 4))
        self.quote_text = ctk.CTkLabel(detail, text="点击「刷新行情」或启动监控",
                                       font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY,
                                       justify="left", anchor="w")
        self.quote_text.pack(anchor="w", padx=12, pady=(0, 10))

        # 信号列表
        sig_frame = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=12,
                                 border_width=1, border_color=BORDER)
        sig_frame.grid(row=1, column=0, sticky="nsew")
        SectionTitle(sig_frame, "实时信号").pack(anchor="w", padx=12, pady=(10, 4))
        self.signal_list = ctk.CTkScrollableFrame(sig_frame, fg_color="transparent")
        self.signal_list.pack(fill="both", expand=True, padx=6, pady=(0, 8))

    def _manual_refresh(self):
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        try:
            dp = self.app.data_provider
            quote = dp.get_realtime_quote()
            if not quote:
                return
            self.app.latest_quote = quote

            # 更新分钟K线
            price = quote["price"]
            now = dt.datetime.now()
            bar = {"datetime": now, "open": price, "high": price, "low": price,
                   "close": price, "volume": quote.get("volume", 0)}
            if self.app.minute_bars:
                last = self.app.minute_bars[-1]
                if (now - last["datetime"]).total_seconds() < 60:
                    last["close"] = price
                    last["high"] = max(last["high"], price)
                    last["low"] = min(last["low"], price)
                    last["volume"] = bar["volume"]
                else:
                    self.app.minute_bars.append(bar)
            else:
                self.app.minute_bars.append(bar)
            self.app.minute_bars = self.app.minute_bars[-240:]

            # 在主线程更新UI
            self.after(0, lambda: self._update_ui(quote))
        except Exception as e:
            self.after(0, lambda: self.quote_text.configure(text=f"获取失败: {e}"))

    def _update_ui(self, quote: dict):
        price = quote.get("price", 0)
        change = quote.get("change_pct", 0)
        o, h, l = quote.get("open", 0), quote.get("high", 0), quote.get("low", 0)
        vol = quote.get("volume", 0)
        amt = quote.get("amount", 0)
        color = GREEN if change >= 0 else RED

        self.quote_text.configure(
            text=f"最新价  {price:.4f}   涨跌幅  {change:+.2f}%\n"
                 f"今开  {o:.4f}   最高  {h:.4f}   最低  {l:.4f}\n"
                 f"成交量  {vol:,.0f}   成交额  {amt/1e8:.2f}亿")

        # 更新图表
        self._update_chart()

        # 检查信号
        if len(self.app.minute_bars) > 10:
            df = pd.DataFrame(self.app.minute_bars)
            engine = self.app.signal_engine
            gap = None
            prev_close = quote.get("close", 0)
            if prev_close > 0 and o > 0:
                gap = (o - prev_close) / prev_close * 100
            signals = engine.generate_signals(df, current_price=price, gap_pct=gap)
            for sig in signals:
                self._add_signal_card(sig)
                engine.record_trade(sig)

    def _update_chart(self):
        self.ax.clear()
        self.ax.set_facecolor(BG_CARD)
        self.ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
        for sp in self.ax.spines.values():
            sp.set_color(BORDER)
        self.ax.spines["top"].set_visible(False)
        self.ax.spines["right"].set_visible(False)

        bars = self.app.minute_bars
        if len(bars) < 2:
            self.ax.set_title("等待数据...", color=TEXT_DIM, fontsize=11)
            self.canvas.draw_idle()
            return

        prices = [b["close"] for b in bars]
        x = list(range(len(prices)))
        self.ax.plot(x, prices, color=ACCENT, linewidth=1.5, label="Price")

        df = pd.DataFrame(bars)
        if len(df) > 5:
            vwap = calc_vwap(df)
            self.ax.plot(x, vwap.values, color=ORANGE, linewidth=1, linestyle="--",
                         alpha=0.7, label="VWAP")

        self.ax.set_title("513040 日内走势", color=TEXT_PRIMARY, fontsize=12, fontweight="bold")
        self.ax.legend(fontsize=8, loc="upper left", facecolor=BG_CARD,
                       edgecolor=BORDER, labelcolor=TEXT_SECONDARY)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _add_signal_card(self, sig: Signal):
        card = ctk.CTkFrame(self.signal_list, fg_color=BG_INPUT, corner_radius=8,
                            border_width=1, border_color=BORDER)
        card.pack(fill="x", pady=3, padx=4)
        d = "BUY" if sig.direction == "BUY" else "SELL"
        dc = GREEN if d == "BUY" else RED
        icon = "🟢" if d == "BUY" else "🔴"
        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(row1, text=icon, font=ctk.CTkFont(size=14)).pack(side="left")
        ctk.CTkLabel(row1, text=f" {sig.strategy}", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(side="left")
        ctk.CTkLabel(row1, text=f"  {d}  ", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=dc).pack(side="left")
        ctk.CTkLabel(row1, text=sig.time.strftime("%H:%M:%S") if isinstance(sig.time, dt.datetime) else str(sig.time),
                     font=ctk.CTkFont(size=10), text_color=TEXT_DIM).pack(side="right")
        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=(2, 8))
        ctk.CTkLabel(row2, text=f"价格 {sig.price:.4f}  置信度 {sig.confidence}  强度 {sig.strength:.0%}",
                     font=ctk.CTkFont(size=10), text_color=TEXT_DIM).pack(side="left")

    def _toggle_monitor(self):
        if self.monitoring:
            self.monitoring = False
            self.btn_toggle.configure(text="▶ 启动监控", fg_color=ACCENT)
            self.status_dot.configure(text="● 已停止", text_color=RED)
        else:
            self.monitoring = True
            self.btn_toggle.configure(text="⏸ 停止监控", fg_color=RED)
            self.status_dot.configure(text="● 监控中", text_color=GREEN)
            threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        while self.monitoring:
            self._fetch_and_update()
            time.sleep(cfg.MONITOR_INTERVAL_SEC)


# ============================================================
#  信号中心页面
# ============================================================

class SignalPage(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(top, text="信号中心", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(side="left")
        self.btn_clear = ctk.CTkButton(top, text="清空记录", width=90, fg_color=BG_INPUT,
                                       hover_color=BORDER, command=self._clear)
        self.btn_clear.pack(side="right")

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew")

    def _clear(self):
        for w in self.scroll.winfo_children():
            w.destroy()
        self.app.signal_engine.reset_daily()

    def refresh(self):
        for w in self.scroll.winfo_children():
            w.destroy()
        signals = self.app.signal_engine.daily_signals
        if not signals:
            ctk.CTkLabel(self.scroll, text="暂无交易信号\n启动监控后将自动生成",
                         font=ctk.CTkFont(size=14), text_color=TEXT_DIM).pack(pady=60)
            return
        # 统计
        buy_c = sum(1 for s in signals if s["direction"] == "BUY")
        sell_c = sum(1 for s in signals if s["direction"] == "SELL")
        stat = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER)
        stat.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(stat, text=f"今日信号  {len(signals)}  买入  {buy_c}  卖出  {sell_c}",
                     font=ctk.CTkFont(size=13), text_color=TEXT_SECONDARY).pack(pady=12)
        for sig in reversed(signals):
            self._add_row(sig)

    def _add_row(self, sig: dict):
        card = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=8,
                            border_width=1, border_color=BORDER)
        card.pack(fill="x", pady=2)
        r = ctk.CTkFrame(card, fg_color="transparent")
        r.pack(fill="x", padx=12, pady=8)
        d = sig.get("direction", "")
        icon = "🟢" if d == "BUY" else "🔴"
        ctk.CTkLabel(r, text=f"{icon} {sig.get('strategy','')}", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(side="left")
        ctk.CTkLabel(r, text=f"{d}  {sig.get('price', 0):.4f}", font=ctk.CTkFont(size=11),
                     text_color=GREEN if d=="BUY" else RED).pack(side="left", padx=12)
        ctk.CTkLabel(r, text=sig.get("reason", "")[:40], font=ctk.CTkFont(size=10),
                     text_color=TEXT_DIM).pack(side="left", padx=4)


# ============================================================
#  回测页面
# ============================================================

class BacktestPage(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── 顶部 ──
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(top, text="策略回测", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT_PRIMARY).pack(side="left")

        # ── 主体：左右分栏 ──
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=2)
        body.grid_columnconfigure(1, weight=3)
        body.grid_rowconfigure(0, weight=1)

        # 左侧：参数配置
        left = ctk.CTkScrollableFrame(body, fg_color=BG_CARD, corner_radius=12,
                                      border_width=1, border_color=BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        SectionTitle(left, "回测参数").pack(anchor="w", padx=14, pady=(14, 8))

        fields = [
            ("起始日期", "start", cfg.BACKTEST_DEFAULT_START),
            ("结束日期", "end", dt.datetime.now().strftime("%Y%m%d")),
            ("初始资金", "capital", str(int(cfg.BACKTEST_INITIAL_CAPITAL))),
        ]
        self.entries = {}
        for label, key, default in fields:
            r = ctk.CTkFrame(left, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(r, text=label, width=80, font=ctk.CTkFont(size=12),
                         text_color=TEXT_SECONDARY).pack(side="left")
            e = ctk.CTkEntry(r, fg_color=BG_INPUT, border_color=BORDER, text_color=TEXT_PRIMARY)
            e.insert(0, default)
            e.pack(side="left", fill="x", expand=True)
            self.entries[key] = e

        self.chk_minute = ctk.CTkCheckBox(left, text="使用分钟K线（更精细）",
                                          font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY)
        self.chk_minute.pack(anchor="w", padx=14, pady=(12, 4))

        self.btn_run = ctk.CTkButton(left, text="🚀 运行回测", fg_color=ACCENT,
                                     hover_color=ACCENT_HOVER, height=40,
                                     font=ctk.CTkFont(size=14, weight="bold"),
                                     command=self._run_backtest)
        self.btn_run.pack(fill="x", padx=14, pady=(16, 8))

        self.progress = ctk.CTkProgressBar(left)
        self.progress.pack(fill="x", padx=14, pady=(0, 4))
        self.progress.set(0)

        self.log_label = ctk.CTkLabel(left, text="", font=ctk.CTkFont(size=11),
                                      text_color=TEXT_DIM, justify="left", anchor="w")
        self.log_label.pack(anchor="w", padx=14, pady=(4, 14))

        # 策略选择
        SectionTitle(left, "回测策略").pack(anchor="w", padx=14, pady=(4, 6))
        self.strategy_vars = {}
        for name in ["VWAP均值回归", "ATR网格交易", "开盘动量突破", "日内做T", "综合策略"]:
            var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(left, text=name, variable=var, font=ctk.CTkFont(size=12),
                            text_color=TEXT_SECONDARY).pack(anchor="w", padx=14, pady=1)
            self.strategy_vars[name] = var

        # 右侧：结果展示
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        SectionTitle(right, "回测结果").grid(row=0, column=0, sticky="w", padx=8, pady=(4, 4))

        self.result_scroll = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self.result_scroll.grid(row=1, column=0, sticky="nsew")

        self.result_placeholder = ctk.CTkLabel(
            self.result_scroll, text="配置参数后点击「运行回测」",
            font=ctk.CTkFont(size=14), text_color=TEXT_DIM)
        self.result_placeholder.pack(pady=60)

    def _run_backtest(self):
        self.btn_run.configure(state="disabled", text="回测中...")
        self.progress.set(0.3)
        self.log_label.configure(text="正在获取历史数据...")

        def _worker():
            try:
                start = self.entries["start"].get()
                end = self.entries["end"].get()
                capital = float(self.entries["capital"].get())
                use_min = self.chk_minute.get()

                selected = [k for k, v in self.strategy_vars.items() if v.get()]
                if not selected:
                    selected = None

                self.after(0, lambda: self._log("数据获取完成，开始回测..."))
                self.after(0, lambda: self.progress.set(0.5))

                dp = DataProvider()
                bt = Backtester(initial_capital=capital)
                ddf = dp.get_daily_data(start, end)

                if ddf.empty:
                    self.after(0, lambda: self._log("日K线数据获取失败，请检查网络"))
                    self.after(0, lambda: self.btn_run.configure(state="normal", text="🚀 运行回测"))
                    return

                self.after(0, lambda: self._log(f"获取到 {len(ddf)} 条日K线数据..."))
                self.after(0, lambda: self.progress.set(0.7))

                results = bt.backtest_daily(ddf, strategies=selected)

                self.after(0, lambda: self._log("生成报告..."))
                self.after(0, lambda: self.progress.set(0.9))

                output_dir = os.path.dirname(os.path.abspath(__file__))
                report_path = os.path.join(output_dir, "backtest_report.html")
                generate_html_report(results, ddf, report_path, "daily")

                self.after(0, lambda: self.progress.set(1.0))
                self.after(0, lambda: self._show_results(results, report_path))
                self.after(0, lambda: self._log(f"回测完成! 报告: {report_path}"))

            except Exception as e:
                self.after(0, lambda: self._log(f"回测出错: {e}"))
            finally:
                self.after(0, lambda: self.btn_run.configure(state="normal", text="🚀 运行回测"))

        threading.Thread(target=_worker, daemon=True).start()

    def _log(self, msg: str):
        self.log_label.configure(text=msg)

    def _show_results(self, results: dict, report_path: str):
        for w in self.result_scroll.winfo_children():
            w.destroy()

        # 汇总表
        for name, res in results.items():
            card = ctk.CTkFrame(self.result_scroll, fg_color=BG_CARD, corner_radius=10,
                                border_width=1, border_color=BORDER)
            card.pack(fill="x", pady=4)
            r1 = ctk.CTkFrame(card, fg_color="transparent")
            r1.pack(fill="x", padx=12, pady=(10, 2))
            pnl_color = GREEN if res.total_pnl >= 0 else RED
            ctk.CTkLabel(r1, text=f"  {name}", font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=TEXT_PRIMARY).pack(side="left")
            ctk.CTkLabel(r1, text=f"¥{res.total_pnl:+,.0f}", font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=pnl_color).pack(side="right")
            r2 = ctk.CTkFrame(card, fg_color="transparent")
            r2.pack(fill="x", padx=12, pady=(0, 10))
            ctk.CTkLabel(r2,
                text=f"交易 {len(res.trades)} 笔   胜率 {res.win_rate:.1%}   "
                     f"盈亏比 {res.profit_factor:.2f}   回撤 {res.max_drawdown:.2%}   "
                     f"夏普 {res.sharpe_ratio:.2f}",
                font=ctk.CTkFont(size=11), text_color=TEXT_SECONDARY).pack(side="left")

        # 打开报告按钮
        ctk.CTkButton(self.result_scroll, text="📄 打开 HTML 详细报告",
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, height=36,
                      command=lambda: os.startfile(report_path)).pack(pady=16)


# ============================================================
#  提醒页面
# ============================================================

class ReminderPage(ctk.CTkScrollableFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="每日待办提醒", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT_PRIMARY, anchor="w").pack(anchor="w", pady=(0, 12))

        self.cards = []
        for r in cfg.DAILY_REMINDERS:
            card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10,
                                border_width=1, border_color=BORDER)
            card.pack(fill="x", pady=3)
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=10)
            self.icon_lbl = ctk.CTkLabel(row, text="○", font=ctk.CTkFont(size=16), text_color=TEXT_DIM)
            self.icon_lbl.pack(side="left")
            time_lbl = ctk.CTkLabel(row, text=f"  {r['time']}", font=ctk.CTkFont(size=14, weight="bold"),
                                    text_color=TEXT_PRIMARY, width=55)
            time_lbl.pack(side="left")
            title_lbl = ctk.CTkLabel(row, text=r["title"], font=ctk.CTkFont(size=13, weight="bold"),
                                     text_color=TEXT_PRIMARY)
            title_lbl.pack(side="left", padx=(8, 0))
            desc_lbl = ctk.CTkLabel(row, text=r["desc"], font=ctk.CTkFont(size=11),
                                    text_color=TEXT_DIM)
            desc_lbl.pack(side="right")
            self.cards.append((r, card, self.icon_lbl, time_lbl))

    def refresh(self):
        now_t = dt.datetime.now().strftime("%H:%M")
        for r, card, icon, time_lbl in self.cards:
            if r["time"] <= now_t:
                icon.configure(text="●", text_color=GREEN)
                time_lbl.configure(text_color=GREEN)
            elif r["time"][:5] == now_t:
                icon.configure(text="◉", text_color=ORANGE)
                time_lbl.configure(text_color=ORANGE)
            else:
                icon.configure(text="○", text_color=TEXT_DIM)
                time_lbl.configure(text_color=TEXT_PRIMARY)


# ============================================================
#  设置页面
# ============================================================

class SettingsPage(ctk.CTkScrollableFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="系统设置", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT_PRIMARY, anchor="w").pack(anchor="w", pady=(0, 12))

        sections = [
            ("交易成本", [
                ("佣金费率 (单边)", "commission", f"{cfg.COMMISSION_RATE*10000:.0f}", "万分之"),
                ("印花税率", "stamp", "0", "%（ETF免征）"),
            ]),
            ("资金管理", [
                ("底仓比例", "base_pos", f"{cfg.BASE_POSITION_RATIO*100:.0f}", "%"),
                ("浮仓比例", "float_pos", f"{cfg.FLOAT_POSITION_RATIO*100:.0f}", "%"),
                ("单次T+0仓位上限", "single_pos", f"{cfg.SINGLE_TRADE_RATIO*100:.0f}", "%"),
            ]),
            ("风控参数", [
                ("日亏损熔断", "daily_loss", f"{cfg.DAILY_LOSS_LIMIT*100:.1f}", "%"),
                ("单笔止损", "stop_loss", f"{cfg.SINGLE_STOP_LOSS*100:.1f}", "%"),
                ("连续亏损熔断次数", "consec_loss", str(cfg.CONSECUTIVE_LOSS_LIMIT), "次"),
                ("每日最大交易次数", "max_trades", str(cfg.MAX_TRADES_PER_DAY), "次"),
            ]),
            ("VWAP 均值回归", [
                ("买入偏离阈值", "vwap_buy", f"{cfg.VWAP_BUY_THRESHOLD*100:.1f}", "%"),
                ("止盈回归阈值", "vwap_sell", f"{cfg.VWAP_SELL_THRESHOLD*100:.1f}", "%"),
                ("止损偏离阈值", "vwap_stop", f"{cfg.VWAP_STOP_LOSS*100:.1f}", "%"),
            ]),
            ("网格交易", [
                ("ATR 周期", "atr_period", str(cfg.GRID_ATR_PERIOD), ""),
                ("ATR 倍数", "atr_multi", str(cfg.GRID_ATR_MULTI), "x"),
                ("最大网格数", "max_grids", str(cfg.GRID_MAX_GRIDS), ""),
            ]),
            ("动量突破", [
                ("观察窗口 (分钟)", "mom_obs", str(cfg.MOMENTUM_OBSERVATION_MIN), ""),
                ("量比阈值", "mom_vol", str(cfg.MOMENTUM_VOLUME_MULTI), "x"),
            ]),
            ("通知设置", [
                ("桌面通知", "notify", "1" if cfg.DESKTOP_NOTIFY_ENABLED else "0", "（0关/1开）"),
                ("声音提醒", "sound", "1" if cfg.SOUND_ENABLED else "0", "（0关/1开）"),
                ("刷新间隔 (秒)", "interval", str(cfg.MONITOR_INTERVAL_SEC), ""),
            ]),
        ]

        self.setting_entries: dict[str, ctk.CTkEntry] = {}

        for section_title, items in sections:
            frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12,
                                 border_width=1, border_color=BORDER)
            frame.pack(fill="x", pady=6)
            SectionTitle(frame, section_title).pack(anchor="w", padx=14, pady=(12, 6))
            for label, key, default, unit in items:
                row = ctk.CTkFrame(frame, fg_color="transparent")
                row.pack(fill="x", padx=14, pady=3)
                ctk.CTkLabel(row, text=label, width=150, font=ctk.CTkFont(size=12),
                             text_color=TEXT_SECONDARY, anchor="w").pack(side="left")
                e = ctk.CTkEntry(row, width=80, fg_color=BG_INPUT, border_color=BORDER,
                                 text_color=TEXT_PRIMARY)
                e.insert(0, default)
                e.pack(side="left", padx=(0, 4))
                ctk.CTkLabel(row, text=unit, font=ctk.CTkFont(size=11),
                             text_color=TEXT_DIM).pack(side="left")
                self.setting_entries[key] = e
            ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=4)).pack()

        # 保存按钮
        ctk.CTkButton(self, text="💾 保存设置", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      height=40, font=ctk.CTkFont(size=14, weight="bold"),
                      command=self._save).pack(pady=20)

    def _save(self):
        try:
            e = self.setting_entries
            cfg.COMMISSION_RATE = float(e["commission"].get()) / 10000
            cfg.ROUND_TRIP_COST = cfg.COMMISSION_RATE * 2
            cfg.BASE_POSITION_RATIO = float(e["base_pos"].get()) / 100
            cfg.FLOAT_POSITION_RATIO = float(e["float_pos"].get()) / 100
            cfg.SINGLE_TRADE_RATIO = float(e["single_pos"].get()) / 100
            cfg.DAILY_LOSS_LIMIT = float(e["daily_loss"].get()) / 100
            cfg.SINGLE_STOP_LOSS = float(e["stop_loss"].get()) / 100
            cfg.CONSECUTIVE_LOSS_LIMIT = int(e["consec_loss"].get())
            cfg.MAX_TRADES_PER_DAY = int(e["max_trades"].get())
            cfg.VWAP_BUY_THRESHOLD = float(e["vwap_buy"].get()) / 100
            cfg.VWAP_SELL_THRESHOLD = float(e["vwap_sell"].get()) / 100
            cfg.VWAP_STOP_LOSS = float(e["vwap_stop"].get()) / 100
            cfg.GRID_ATR_PERIOD = int(e["atr_period"].get())
            cfg.GRID_ATR_MULTI = float(e["atr_multi"].get())
            cfg.GRID_MAX_GRIDS = int(e["max_grids"].get())
            cfg.MOMENTUM_OBSERVATION_MIN = int(e["mom_obs"].get())
            cfg.MOMENTUM_VOLUME_MULTI = float(e["mom_vol"].get())
            cfg.DESKTOP_NOTIFY_ENABLED = e["notify"].get() == "1"
            cfg.SOUND_ENABLED = e["sound"].get() == "1"
            cfg.MONITOR_INTERVAL_SEC = int(e["interval"].get())
            messagebox.showinfo("设置", "设置已保存（内存生效，重启后重置）")
        except Exception as ex:
            messagebox.showerror("错误", f"保存失败: {ex}")


# ============================================================
#  主应用
# ============================================================

class App(ctk.CTk):
    """SH513040 T+0 交易助手 — 主窗口"""

    NAV_ITEMS = [
        ("仪表盘",  "📊"),
        ("实时监控", "📡"),
        ("信号中心", "🔔"),
        ("策略回测", "📈"),
        ("每日提醒", "⏰"),
        ("系统设置", "⚙️"),
    ]

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"SH513040 T+0 交易助手 — {cfg.ETF_NAME}")
        self.geometry("1280x820")
        self.minsize(1024, 700)
        self.configure(fg_color=BG_DARK)

        # ── 共享状态 ──
        self.data_provider = DataProvider()
        self.signal_engine = SignalEngine()
        self.latest_quote: dict = {}
        self.minute_bars: list[dict] = []

        # ── 布局 ──
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── 侧栏 ──
        self.sidebar = ctk.CTkFrame(self, width=200, fg_color=BG_SIDEBAR, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)

        # Logo
        logo_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        logo_frame.pack(fill="x", pady=(20, 24))
        ctk.CTkLabel(logo_frame, text="513040", font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=ACCENT).pack()
        ctk.CTkLabel(logo_frame, text="T+0 交易助手", font=ctk.CTkFont(size=12),
                     text_color=TEXT_SECONDARY).pack()

        # Nav buttons
        self.nav_buttons: list[ctk.CTkButton] = []
        self.pages: list[ctk.CTkFrame] = []
        self.page_names = [item[0] for item in self.NAV_ITEMS]

        for i, (name, icon) in enumerate(self.NAV_ITEMS):
            btn = ctk.CTkButton(
                self.sidebar, text=f"  {icon}  {name}", anchor="w",
                font=ctk.CTkFont(size=13), height=40, corner_radius=8,
                fg_color="transparent", text_color=TEXT_SECONDARY,
                hover_color=BG_INPUT,
                command=lambda idx=i: self._switch_page(idx),
            )
            btn.pack(fill="x", padx=10, pady=3)
            self.nav_buttons.append(btn)

        # 底部版本信息
        ctk.CTkLabel(self.sidebar, text="v0.1.0", font=ctk.CTkFont(size=10),
                     text_color=TEXT_DIM).pack(side="bottom", pady=12)

        # ── 主内容区 ──
        self.main_area = ctk.CTkFrame(self, fg_color="transparent")
        self.main_area.grid(row=0, column=1, sticky="nsew", padx=16, pady=16)
        self.main_area.grid_columnconfigure(0, weight=1)
        self.main_area.grid_rowconfigure(0, weight=1)

        # 创建页面
        self.pages.append(DashboardPage(self.main_area, self))
        self.pages.append(MonitorPage(self.main_area, self))
        self.pages.append(SignalPage(self.main_area, self))
        self.pages.append(BacktestPage(self.main_area, self))
        self.pages.append(ReminderPage(self.main_area, self))
        self.pages.append(SettingsPage(self.main_area, self))

        # 默认显示仪表盘
        self._switch_page(0)

        # 启动定时刷新
        self._auto_refresh()

        # 后台获取一次行情
        threading.Thread(target=self._initial_fetch, daemon=True).start()

    def _switch_page(self, idx: int):
        for i, page in enumerate(self.pages):
            if i == idx:
                page.grid(row=0, column=0, sticky="nsew")
                self.nav_buttons[i].configure(fg_color=BG_INPUT, text_color=TEXT_PRIMARY)
            else:
                page.grid_forget()
                self.nav_buttons[i].configure(fg_color="transparent", text_color=TEXT_SECONDARY)

        # 切到信号页时刷新
        if idx == 2 and isinstance(self.pages[2], SignalPage):
            self.pages[2].refresh()

    def _auto_refresh(self):
        """定时刷新当前页面"""
        try:
            current = None
            for i, page in enumerate(self.pages):
                if page.winfo_ismapped():
                    current = page
                    break
            if current and hasattr(current, "refresh"):
                current.refresh()
        except Exception:
            pass
        self.after(5000, self._auto_refresh)  # 每5秒刷新

    def _initial_fetch(self):
        """启动时获取一次行情"""
        try:
            time.sleep(1)
            quote = self.data_provider.get_realtime_quote()
            if quote:
                self.latest_quote = quote
        except Exception:
            pass


# ============================================================
#  入口
# ============================================================

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
