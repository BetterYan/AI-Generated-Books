# ============================================================
# config.py — SH513040 T+0 交易系统全局配置
# 基于《港股通互联网ETF(513040) T+0短线交易策略研究报告》
# ============================================================

# ── 标的信息 ──────────────────────────────────────────────
ETF_CODE       = "513040"
ETF_NAME       = "港股通互联网ETF易方达"
ETF_FULL_NAME  = "易方达中证港股通互联网ETF"
INDEX_CODE     = "931637"
INDEX_NAME     = "中证港股通互联网指数"

# ── 交易成本 ──────────────────────────────────────────────
COMMISSION_RATE = 0.0001   # 万1 单边佣金
STAMP_TAX       = 0.0      # ETF场内交易免印花税
ROUND_TRIP_COST = COMMISSION_RATE * 2  # ≈ 0.02% 单次买卖合计

# ── 资金管理 ──────────────────────────────────────────────
BASE_POSITION_RATIO  = 0.55    # 底仓占总资金 55%
FLOAT_POSITION_RATIO = 0.35    # 浮仓占总资金 35%
RESERVE_RATIO        = 0.10    # 现金储备 10%
SINGLE_TRADE_RATIO   = 0.18    # 单次T+0仓位上限 18%

# ── 风控参数 ──────────────────────────────────────────────
DAILY_LOSS_LIMIT           = 0.01   # 日亏损熔断 1%
CONSECUTIVE_LOSS_LIMIT     = 3      # 连续亏损熔断次数
SINGLE_STOP_LOSS           = 0.008  # 单笔止损 0.8%
SINGLE_TAKE_PROFIT_MIN     = 0.008  # 单笔止盈下限
SINGLE_TAKE_PROFIT_MAX     = 0.015  # 单笔止盈上限
ABNORMAL_VOLATILITY        = 0.03   # 异常波动阈值（5分钟内）
MAX_TRADES_PER_DAY         = 3      # 每日最大交易次数
MAX_SPREAD                 = 0.005  # 买卖价差上限

# ── 策略一: VWAP均值回归 ─────────────────────────────────
VWAP_BUY_THRESHOLD    = 0.015   # 偏离VWAP -1.5% 买入
VWAP_SELL_THRESHOLD   = 0.003   # 偏离VWAP +0.3% 止盈
VWAP_STOP_LOSS        = 0.03    # 偏离VWAP -3% 止损
VWAP_MIN_DURATION_MIN = 5       # 偏离持续最小分钟数
VWAP_EXPECTED_WINRATE = 0.62    # 预期胜率 62%

# ── 策略二: ATR网格交易 ──────────────────────────────────
GRID_ATR_PERIOD   = 14          # ATR计算周期
GRID_ATR_MULTI    = 0.5         # 网格间距 = 0.5 × ATR
GRID_POSITION_PCT = 0.12        # 每格仓位 12%
GRID_MAX_GRIDS    = 8           # 日内最大网格数
GRID_STOP_LOSS    = 0.015       # 网格整体止损

# ── 策略三: 开盘动量突破 ─────────────────────────────────
MOMENTUM_OBSERVATION_MIN   = 15    # 观察窗口（分钟）
MOMENTUM_BREAKOUT_HOUR     = 9     # 突破判定开始时间
MOMENTUM_BREAKOUT_MIN      = 45    # 突破判定开始分钟
MOMENTUM_VOLUME_MULTI      = 1.5   # 成交量放大倍数
MOMENTUM_TAKE_PROFIT_MIN   = 0.008 # 止盈下限
MOMENTUM_TAKE_PROFIT_MAX   = 0.015 # 止盈上限
MOMENTUM_EXPECTED_WINRATE  = 0.58  # 预期胜率

# ── 日内时段定义 ──────────────────────────────────────────
TRADING_PHASES = {
    "pre_open":    {"start": "09:25", "end": "09:30", "label": "集合竞价",   "action": "观察竞价，确认方向"},
    "observe":     {"start": "09:30", "end": "09:45", "label": "观察期",     "action": "不操作，记录VWAP基准"},
    "momentum":    {"start": "09:45", "end": "10:30", "label": "动量窗口",   "action": "执行开盘动量突破策略"},
    "trend":       {"start": "10:30", "end": "11:00", "label": "趋势确认",   "action": "评估趋势，切换VWAP策略"},
    "quiet_am":    {"start": "11:00", "end": "11:30", "label": "午盘静默",   "action": "降低操作频率"},
    "lunch":       {"start": "11:30", "end": "13:00", "label": "午间休市",   "action": "关注港股12:00-13:00信息"},
    "afternoon":   {"start": "13:00", "end": "14:30", "label": "均值回归窗口","action": "执行VWAP均值回归策略"},
    "close_prep":  {"start": "14:30", "end": "14:50", "label": "收仓阶段",   "action": "平掉所有日内浮仓"},
    "closed":      {"start": "15:00", "end": "15:30", "label": "收盘复盘",   "action": "记录交易，盘后复盘"},
}

# ── 每日待办提醒时间点 ──────────────────────────────────────
DAILY_REMINDERS = [
    {"time": "08:30", "title": "盘前准备", "desc": "查看港股夜盘/ADR表现，阅读隔夜重要新闻"},
    {"time": "09:00", "title": "港股竞价", "desc": "关注港股早盘竞价盘走势，判断今日方向"},
    {"time": "09:15", "title": "A股集合竞价", "desc": "查看513040竞价偏离度，制定初步计划"},
    {"time": "09:25", "title": "竞价定价", "desc": "集合竞价定价完成，准备开盘"},
    {"time": "09:30", "title": "开盘", "desc": "开盘！进入观察期，不要急于操作"},
    {"time": "09:45", "title": "动量窗口开启", "desc": "黄金半小时！执行开盘动量突破策略"},
    {"time": "10:30", "title": "趋势确认", "desc": "评估早盘趋势，必要时切换均值回归策略"},
    {"time": "11:00", "title": "午盘静默", "desc": "避免新开仓，可适当降低仓位"},
    {"time": "13:00", "title": "午后开盘", "desc": "关注午后跳空方向，捕捉反向T机会"},
    {"time": "13:30", "title": "白银一小时", "desc": "均值回归策略最佳窗口"},
    {"time": "14:30", "title": "收仓提醒", "desc": "开始平掉日内浮仓"},
    {"time": "14:45", "title": "最后收仓", "desc": "务必在14:50前完成所有平仓！"},
    {"time": "15:00", "title": "收盘", "desc": "盘后复盘：记录交易、计算盈亏、分析信号"},
]

# ── 盯盘监控参数 ──────────────────────────────────────────
MONITOR_INTERVAL_SEC    = 10    # 行情刷新间隔（秒）
PRICE_ALERT_PCT         = 0.02  # 价格变动提醒阈值 2%
VOLUME_ALERT_MULTI      = 2.0   # 成交量异动倍数
DESKTOP_NOTIFY_ENABLED  = True  # 桌面通知开关
SOUND_ENABLED           = True  # 声音提醒开关

# ── 回测参数 ──────────────────────────────────────────────
BACKTEST_INITIAL_CAPITAL = 100_000.0   # 初始资金
BACKTEST_DEFAULT_START   = "20250101"  # 默认回测起始日
BACKTEST_DEFAULT_END     = ""          # 默认回测结束日（空=至今）

# ── 颜色主题（终端输出）────────────────────────────────────
COLORS = {
    "up":      "#e74c3c",
    "down":    "#27ae60",
    "neutral": "#95a5a6",
    "signal":  "#f39c12",
    "alert":   "#e74c3c",
    "info":    "#3498db",
    "success": "#27ae60",
}
