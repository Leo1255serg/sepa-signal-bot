"""
SEPA paper-trading bot for GitHub Actions.
All schedule logic uses America/New_York.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

NY = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).resolve().parent / "data"
PORTFOLIO_FILE = DATA_DIR / "portfolio_log.xlsx"
SIGNALS_LOG_FILE = DATA_DIR / "signals_log.xlsx"
WEEKLY_REPORT_FILE = DATA_DIR / "weekly_report.xlsx"
MONTHLY_REPORT_FILE = DATA_DIR / "monthly_report.xlsx"
STATE_FILE = DATA_DIR / "state.json"

CAPITAL = 100_000
RISK_PER_TRADE = 0.005
RISK_REWARD_RATIO = 2
MIN_VOLUME_THRESHOLD = 1_000_000
MIN_PRICE_THRESHOLD = 10
MIN_STOP_LOSS_PERCENT = 0.05
MIN_CHANGE_THRESHOLD = 0.005
MAX_PORTFOLIO_ALLOCATION = 0.3
MAX_DAILY_SIGNALS = 12
MAX_HOLD_DAYS = 14
MA_TOLERANCE = 0.01  # 1% around 50-day MA

BIG_TEN = {
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "TSLA",
    "META",
    "NVDA",
    "BRK-B",
    "JPM",
    "WMT",
}

# NYSE holidays (observed). Extend yearly as needed.
NYSE_HOLIDAYS = {
    date(2025, 1, 1),
    date(2025, 1, 20),
    date(2025, 2, 17),
    date(2025, 4, 18),
    date(2025, 5, 26),
    date(2025, 6, 19),
    date(2025, 7, 4),
    date(2025, 9, 1),
    date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 3, 26),
    date(2027, 5, 31),
    date(2027, 6, 18),
    date(2027, 7, 5),
    date(2027, 9, 6),
    date(2027, 11, 25),
    date(2027, 12, 24),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
    "Referer": "https://finviz.com",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("sepa")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def now_ny() -> datetime:
    return datetime.now(tz=NY)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def parse_percent(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(str(value).replace("%", "").strip()) / 100.0
    except (TypeError, ValueError):
        return None


# --- State -----------------------------------------------------------------


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"jobs": {}, "last_weekly": None, "last_monthly": None}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def job_already_done(state: dict, job: str, day_key: str) -> bool:
    return job in state.get("jobs", {}).get(day_key, [])


def mark_job_done(state: dict, job: str, day_key: str) -> None:
    state.setdefault("jobs", {}).setdefault(day_key, [])
    if job not in state["jobs"][day_key]:
        state["jobs"][day_key].append(job)
    # keep only last 60 days of job markers
    keys = sorted(state["jobs"].keys())
    for old in keys[:-60]:
        state["jobs"].pop(old, None)
    save_state(state)


# --- Telegram --------------------------------------------------------------


def tg_api(method: str, **payload: Any) -> bool:
    token = require_env("TELEGRAM_TOKEN")
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            log.error("Telegram API error: %s", data)
            return False
        time.sleep(0.8)
        return True
    except Exception as e:
        log.error("Telegram request failed: %s", e)
        return False


def send_message(text: str) -> None:
    chat_id = require_env("TELEGRAM_CHAT_ID")
    # Telegram limit ~4096
    chunk = 3500
    for i in range(0, len(text), chunk):
        tg_api("sendMessage", chat_id=chat_id, text=text[i : i + chunk])


def send_document(path: Path, caption: str) -> None:
    token = require_env("TELEGRAM_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with open(path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption[:1024]},
                files={"document": (path.name, f)},
                timeout=120,
            )
        resp.raise_for_status()
        time.sleep(0.8)
    except Exception as e:
        log.error("Telegram sendDocument failed: %s", e)


# --- Portfolio -------------------------------------------------------------


PORTFOLIO_COLUMNS = [
    "symbol",
    "action",
    "price",
    "stop_loss",
    "take_profit",
    "position_size",
    "entry_date",
    "status",
    "strategy",
    "exit_date",
    "exit_price",
    "exit_reason",
    "profit_loss",
    "profit_loss_percent",
]


def _empty_portfolio() -> pd.DataFrame:
    return pd.DataFrame(columns=PORTFOLIO_COLUMNS)


def load_portfolio() -> pd.DataFrame:
    if not PORTFOLIO_FILE.exists():
        return _empty_portfolio()
    df = pd.read_excel(PORTFOLIO_FILE)
    for col in PORTFOLIO_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df["exit_date"] = pd.to_datetime(df["exit_date"], errors="coerce")
    for col in ("symbol", "action", "status", "strategy", "exit_reason"):
        df[col] = df[col].astype("object")
    return df[PORTFOLIO_COLUMNS]


def save_portfolio(df: pd.DataFrame) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_excel(PORTFOLIO_FILE, index=False)


def load_signals_log() -> pd.DataFrame:
    cols = PORTFOLIO_COLUMNS + ["timestamp"]
    if not SIGNALS_LOG_FILE.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_excel(SIGNALS_LOG_FILE)
    for col in cols:
        if col not in df.columns:
            df[col] = pd.NA
    return df


def save_signals_log(df: pd.DataFrame) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_excel(SIGNALS_LOG_FILE, index=False)


def calc_pnl(action: str, entry: float, exit_price: float, size: int) -> tuple[float, float]:
    if action == "Buy":
        pnl = (exit_price - entry) * size
        pct = ((exit_price - entry) / entry) * 100 if entry else 0.0
    else:
        pnl = (entry - exit_price) * size
        pct = ((entry - exit_price) / entry) * 100 if entry else 0.0
    return float(pnl), float(pct)


def open_allocation(portfolio: pd.DataFrame) -> float:
    open_pos = portfolio[portfolio["status"] == "Open"]
    if open_pos.empty:
        return 0.0
    return float((open_pos["position_size"] * open_pos["price"]).sum())


def can_allocate(portfolio: pd.DataFrame, new_value: float) -> bool:
    return open_allocation(portfolio) + new_value <= CAPITAL * MAX_PORTFOLIO_ALLOCATION


# --- Market data -----------------------------------------------------------


def get_finviz_stocks() -> pd.DataFrame:
    """Finviz Elite export with 50-day MA (SMA50 as % vs price; EMA50 not in CSV export)."""
    token = require_env("FINVIZ_API_TOKEN")
    # Custom columns: Ticker, Company, Price, Change, Volume, 50-Day SMA (%)
    cols = "1,2,65,66,67,53"
    filters = "sh_avgvol_o1000,fa_pe_profitable,fa_debteq_u1,fa_pfcf_u30,sh_price_o10"
    url = (
        "https://elite.finviz.com/export.ashx"
        f"?v=152&c={cols}&f={filters}&o=-volume&auth={token}"
    )
    log.info("Fetching stocks from Finviz...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        if "Price" in df.columns:
            df = df[df["Price"] >= MIN_PRICE_THRESHOLD]
        log.info("Fetched %s stocks from Finviz", len(df))
        return df.reset_index(drop=True)
    except Exception as e:
        log.error("Finviz fetch failed: %s", e)
        return pd.DataFrame()


def get_quote(symbol: str) -> Optional[dict]:
    token = require_env("FINNHUB_API_TOKEN")
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={token}"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        current = data.get("c")
        if current is None or current == 0:
            log.error("No price for %s: %s", symbol, data)
            return None
        quote = {
            "current": float(current),
            "high": float(data.get("h") or current),
            "low": float(data.get("l") or current),
        }
        time.sleep(1.05)  # Finnhub free ~60/min
        return quote
    except Exception as e:
        log.error("Finnhub quote failed for %s: %s", symbol, e)
        time.sleep(1.05)
        return None


# --- Strategy --------------------------------------------------------------


def calculate_support_resistance(price: float, change: float, action: str) -> tuple[Optional[float], Optional[float]]:
    try:
        if action == "Buy":
            support = price / (1 + change) if change > 0 else price * 0.90
            resistance = price * 1.15
        else:
            resistance = price * (1 - change) if change < 0 else price * 1.15
            support = price / (1 - abs(change)) if change != 0 else price * 0.90
        return support, resistance
    except Exception as e:
        log.error("Support/resistance error: %s", e)
        return None, None


def generate_signal(
    stock_data: pd.Series,
    portfolio: pd.DataFrame,
    daily_signals_count: int,
) -> Optional[dict]:
    if daily_signals_count >= MAX_DAILY_SIGNALS:
        return None

    symbol = str(stock_data.get("Ticker", "")).strip()
    if not symbol or symbol in BIG_TEN:
        return None

    open_symbols = set(portfolio.loc[portfolio["status"] == "Open", "symbol"].astype(str))
    if symbol in open_symbols:
        return None

    price = float(stock_data["Price"])
    if price < MIN_PRICE_THRESHOLD:
        return None

    volume = float(stock_data.get("Volume", 0) or 0)
    change = parse_percent(stock_data.get("Change"))
    if change is None:
        return None
    if abs(change) < MIN_CHANGE_THRESHOLD:
        return None
    if volume <= MIN_VOLUME_THRESHOLD:
        return None

    # Finviz "50-Day Simple Moving Average" is % distance of price vs SMA50.
    # EMA50 is not available in Elite CSV export — SMA50 used as MA filter.
    ma_pct = parse_percent(stock_data.get("50-Day Simple Moving Average"))
    if ma_pct is None:
        return None

    action = None
    if change > 0 and ma_pct >= -MA_TOLERANCE:
        action = "Buy"
    elif change < 0 and ma_pct <= MA_TOLERANCE:
        action = "Sell"
    else:
        return None

    support, resistance = calculate_support_resistance(price, change, action)
    if support is None or resistance is None:
        return None

    if action == "Buy":
        stop_loss_distance = max(price * MIN_STOP_LOSS_PERCENT, price - support)
        if stop_loss_distance <= 0:
            return None
        position_size = int(CAPITAL * RISK_PER_TRADE / stop_loss_distance)
        if position_size <= 0:
            return None
        if not can_allocate(portfolio, position_size * price):
            return None
        stop_loss = price - stop_loss_distance
        take_profit_distance = stop_loss_distance * RISK_REWARD_RATIO
        if price < resistance:
            take_profit_distance = min(take_profit_distance, max(resistance - price - 0.01, 0.01))
        take_profit = price + take_profit_distance
    else:
        stop_loss_distance = max(price * MIN_STOP_LOSS_PERCENT, resistance - price)
        if stop_loss_distance <= 0:
            return None
        position_size = int(CAPITAL * RISK_PER_TRADE / stop_loss_distance)
        if position_size <= 0:
            return None
        if not can_allocate(portfolio, position_size * price):
            return None
        stop_loss = price + stop_loss_distance
        take_profit_distance = stop_loss_distance * RISK_REWARD_RATIO
        if price > support:
            take_profit_distance = min(take_profit_distance, max(price - support - 0.01, 0.01))
        take_profit = price - take_profit_distance

    today = now_ny().date()
    return {
        "symbol": symbol,
        "action": action,
        "price": round(price, 4),
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "position_size": int(position_size),
        "entry_date": pd.Timestamp(today),
        "status": "Open",
        "strategy": "SEPA",
        "exit_date": pd.NaT,
        "exit_price": None,
        "exit_reason": None,
        "profit_loss": None,
        "profit_loss_percent": None,
        "timestamp": now_ny().strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def format_entry_message(signal: dict) -> str:
    side = "Buy 🟢" if signal["action"] == "Buy" else "Sell 🔴"
    return (
        f"Вход {side}\n"
        f"Тикер: {signal['symbol']}\n"
        f"Цена входа: ${signal['price']:.2f}\n"
        f"Кол-во бумаг: {signal['position_size']}\n"
        f"Цена стопа: ${signal['stop_loss']:.2f}\n"
        f"Цена тейка: ${signal['take_profit']:.2f}"
    )


# --- Monitoring ------------------------------------------------------------


def close_position(
    portfolio: pd.DataFrame,
    index: Any,
    exit_price: float,
    reason: str,
) -> str:
    row = portfolio.loc[index]
    pnl, pct = calc_pnl(row["action"], float(row["price"]), exit_price, int(row["position_size"]))
    portfolio.at[index, "status"] = f"Closed ({reason})"
    portfolio.at[index, "exit_date"] = pd.Timestamp(now_ny().date())
    portfolio.at[index, "exit_price"] = round(exit_price, 4)
    portfolio.at[index, "exit_reason"] = reason
    portfolio.at[index, "profit_loss"] = round(pnl, 2)
    portfolio.at[index, "profit_loss_percent"] = round(pct, 2)
    sign = "прибылью" if pnl >= 0 else "убытком"
    return (
        f"{row['symbol']} закрыт ({reason}) с {sign} {pct:.2f}% (${pnl:.2f})\n"
        f"Выход: ${exit_price:.2f}"
    )


def monitor_prices(close_expired: bool = False) -> list[str]:
    portfolio = load_portfolio()
    open_positions = portfolio[portfolio["status"] == "Open"]
    if open_positions.empty:
        log.info("No open positions to monitor.")
        return []

    messages: list[str] = []
    today = now_ny().date()

    for index, position in open_positions.iterrows():
        symbol = str(position["symbol"])
        action = position["action"]
        stop_loss = float(position["stop_loss"])
        take_profit = float(position["take_profit"])
        entry_date = pd.to_datetime(position["entry_date"]).date()
        held_days = (today - entry_date).days

        quote = get_quote(symbol)
        if quote is None:
            continue
        current = quote["current"]

        # Primary check: current price at reconciliation moment
        if action == "Buy":
            if current <= stop_loss:
                messages.append(close_position(portfolio, index, stop_loss, "Stop-Loss"))
                continue
            if current >= take_profit:
                messages.append(close_position(portfolio, index, take_profit, "Take-Profit"))
                continue
        else:  # Sell / short
            if current >= stop_loss:
                messages.append(close_position(portfolio, index, stop_loss, "Stop-Loss"))
                continue
            if current <= take_profit:
                messages.append(close_position(portfolio, index, take_profit, "Take-Profit"))
                continue

        if close_expired and held_days >= MAX_HOLD_DAYS:
            messages.append(close_position(portfolio, index, current, f"Max Hold {MAX_HOLD_DAYS}d"))

    save_portfolio(portfolio)
    return messages


def generate_and_send_signals() -> int:
    portfolio = load_portfolio()
    signals_log = load_signals_log()
    df = get_finviz_stocks()
    if df.empty:
        send_message("Нет данных для генерации сигналов от Finviz")
        return 0

    today = now_ny().date()
    if not portfolio.empty:
        entry_dates = pd.to_datetime(portfolio["entry_date"], errors="coerce").dt.date
        daily_signals_count = int((entry_dates == today).sum())
    else:
        daily_signals_count = 0

    signals: list[dict] = []
    for _, stock_data in df.iterrows():
        if daily_signals_count >= MAX_DAILY_SIGNALS:
            break
        signal = generate_signal(stock_data, portfolio, daily_signals_count)
        if not signal:
            continue
        signals.append(signal)
        daily_signals_count += 1
        # Update in-memory portfolio so allocation limits apply within the same run
        row = {col: signal.get(col) for col in PORTFOLIO_COLUMNS}
        portfolio = pd.concat([portfolio, pd.DataFrame([row])], ignore_index=True)

    if not signals:
        send_message("Новых сигналов сегодня нет.")
        return 0

    signals_df = pd.DataFrame(signals)
    save_portfolio(portfolio[PORTFOLIO_COLUMNS])
    log_rows = signals_df.copy()
    if signals_log.empty:
        signals_log = log_rows
    else:
        signals_log = pd.concat([signals_log, log_rows], ignore_index=True)
    save_signals_log(signals_log)

    for signal in signals:
        send_message(format_entry_message(signal))

    send_message(f"Итого новых входов: {len(signals)}")
    return len(signals)


# --- Reports ---------------------------------------------------------------


def unrealized_pnl(portfolio: pd.DataFrame) -> float:
    total = 0.0
    open_pos = portfolio[portfolio["status"] == "Open"]
    for _, row in open_pos.iterrows():
        quote = get_quote(str(row["symbol"]))
        if quote is None:
            continue
        pnl, _ = calc_pnl(row["action"], float(row["price"]), quote["current"], int(row["position_size"]))
        total += pnl
    return total


def build_weekly_report() -> Path:
    portfolio = load_portfolio()
    today = now_ny().date()
    week_start = today - timedelta(days=today.weekday())  # Monday

    open_pos = portfolio[portfolio["status"] == "Open"].copy()
    closed = portfolio[portfolio["status"].astype(str).str.startswith("Closed")].copy()
    if not closed.empty:
        closed["_exit"] = pd.to_datetime(closed["exit_date"], errors="coerce").dt.date
        closed_week = closed[(closed["_exit"] >= week_start) & (closed["_exit"] <= today)].copy()
        closed_week = closed_week.drop(columns=["_exit"], errors="ignore")
    else:
        closed_week = closed

    realized = float(closed_week["profit_loss"].fillna(0).sum()) if not closed_week.empty else 0.0
    wins = int((closed_week["profit_loss"].fillna(0) > 0).sum()) if not closed_week.empty else 0
    losses = int((closed_week["profit_loss"].fillna(0) < 0).sum()) if not closed_week.empty else 0
    trades = len(closed_week)
    winrate = (wins / trades * 100) if trades else 0.0
    unrealized = unrealized_pnl(portfolio) if not open_pos.empty else 0.0
    equity = CAPITAL + float(portfolio.loc[portfolio["status"].astype(str).str.startswith("Closed"), "profit_loss"].fillna(0).sum()) + unrealized

    best = worst = None
    if not closed_week.empty and closed_week["profit_loss"].notna().any():
        best_row = closed_week.loc[closed_week["profit_loss"].idxmax()]
        worst_row = closed_week.loc[closed_week["profit_loss"].idxmin()]
        best = f"{best_row['symbol']} ${best_row['profit_loss']:.2f}"
        worst = f"{worst_row['symbol']} ${worst_row['profit_loss']:.2f}"

    summary = pd.DataFrame(
        [
            {
                "week_ending": today.isoformat(),
                "capital_start": CAPITAL,
                "open_positions": len(open_pos),
                "closed_this_week": trades,
                "wins": wins,
                "losses": losses,
                "win_rate_pct": round(winrate, 2),
                "realized_pnl_week": round(realized, 2),
                "unrealized_pnl": round(unrealized, 2),
                "equity_estimate": round(equity, 2),
                "best_trade": best or "-",
                "worst_trade": worst or "-",
            }
        ]
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(WEEKLY_REPORT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        open_pos.to_excel(writer, sheet_name="Open", index=False)
        closed_week.to_excel(writer, sheet_name="Closed_This_Week", index=False)
        portfolio.to_excel(writer, sheet_name="Full_Portfolio", index=False)
    return WEEKLY_REPORT_FILE


def build_monthly_report(for_month: Optional[date] = None) -> Path:
    """Report for previous calendar month (default when run on 1st)."""
    today = now_ny().date()
    if for_month is None:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        month_start = last_prev.replace(day=1)
        month_end = last_prev
    else:
        month_start = for_month.replace(day=1)
        if month_start.month == 12:
            month_end = date(month_start.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)

    portfolio = load_portfolio()
    closed = portfolio[portfolio["status"].astype(str).str.startswith("Closed")].copy()
    if closed.empty:
        month_closed = closed
    else:
        closed["_exit"] = pd.to_datetime(closed["exit_date"], errors="coerce").dt.date
        month_closed = closed[(closed["_exit"] >= month_start) & (closed["_exit"] <= month_end)].copy()
        month_closed = month_closed.drop(columns=["_exit"], errors="ignore")

    trades = len(month_closed)
    wins = int((month_closed["profit_loss"].fillna(0) > 0).sum()) if trades else 0
    losses = int((month_closed["profit_loss"].fillna(0) < 0).sum()) if trades else 0
    winrate = (wins / trades * 100) if trades else 0.0
    total_profit = float(month_closed["profit_loss"].fillna(0).sum()) if trades else 0.0

    summary = pd.DataFrame(
        [
            {
                "month": month_start.strftime("%Y-%m"),
                "trades": trades,
                "wins": wins,
                "losses": losses,
                "win_rate": f"{winrate:.2f}%",
                "total_profit": f"${total_profit:.2f}",
                "total_profit_percent": f"{(total_profit / CAPITAL) * 100:.2f}%",
            }
        ]
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(MONTHLY_REPORT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        month_closed.to_excel(writer, sheet_name="Closed_Trades", index=False)
    return MONTHLY_REPORT_FILE


# --- Jobs ------------------------------------------------------------------


def job_morning() -> None:
    log.info("Running morning job (NY)")
    messages = monitor_prices(close_expired=True)
    if messages:
        send_message("Утренняя сверка позиций:\n\n" + "\n\n".join(messages))
    else:
        send_message("Утренняя сверка: изменений по открытым позициям нет.")
    generate_and_send_signals()


def job_monitor(label: str) -> None:
    log.info("Running monitor job: %s", label)
    messages = monitor_prices(close_expired=False)
    if messages:
        send_message(f"Сверка ({label}):\n\n" + "\n\n".join(messages))
    else:
        send_message(f"Сверка ({label}): портфель без изменений по стоп/тейк.")


def job_weekly() -> None:
    log.info("Running weekly report")
    # Final check before weekly file
    messages = monitor_prices(close_expired=False)
    if messages:
        send_message("Сверка перед недельным отчётом:\n\n" + "\n\n".join(messages))
    path = build_weekly_report()
    send_document(path, "Итог недели (портфель SEPA)")
    summary = pd.read_excel(path, sheet_name="Summary").iloc[0]
    send_message(
        "Недельный итог:\n"
        f"Открытых: {int(summary['open_positions'])}\n"
        f"Закрыто за неделю: {int(summary['closed_this_week'])}\n"
        f"Winrate: {summary['win_rate_pct']}%\n"
        f"Realized PnL: ${summary['realized_pnl_week']}\n"
        f"Unrealized PnL: ${summary['unrealized_pnl']}\n"
        f"Оценка equity: ${summary['equity_estimate']}\n"
        f"Лучшая: {summary['best_trade']}\n"
        f"Худшая: {summary['worst_trade']}"
    )


def job_monthly() -> None:
    log.info("Running monthly report")
    path = build_monthly_report()
    send_document(path, "Итоги прошлого месяца (SEPA)")
    summary = pd.read_excel(path, sheet_name="Summary").iloc[0]
    send_message(
        f"Месячный отчёт {summary['month']}:\n"
        f"Сделок: {summary['trades']}\n"
        f"Wins/Losses: {summary['wins']}/{summary['losses']}\n"
        f"Winrate: {summary['win_rate']}\n"
        f"PnL: {summary['total_profit']} ({summary['total_profit_percent']})"
    )


def within_window(ny_dt: datetime, hour: int, minute: int, tolerance_min: int = 20) -> bool:
    target = ny_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = abs((ny_dt - target).total_seconds()) / 60.0
    return delta <= tolerance_min


def resolve_auto_jobs(ny_dt: datetime, state: dict) -> list[str]:
    """Pick jobs for current NY time. DST-safe via America/New_York clock."""
    day_key = ny_dt.date().isoformat()
    jobs: list[str] = []

    # Monthly: 1st of month ~09:00 NY (even if weekend/holiday)
    if ny_dt.day == 1 and within_window(ny_dt, 9, 0, 25):
        month_key = ny_dt.strftime("%Y-%m")
        if state.get("last_monthly") != month_key:
            jobs.append("monthly")

    if not is_trading_day(ny_dt.date()):
        return jobs

    if within_window(ny_dt, 10, 0, 20) and not job_already_done(state, "morning", day_key):
        jobs.append("morning")
    if within_window(ny_dt, 13, 0, 20) and not job_already_done(state, "midday", day_key):
        jobs.append("midday")
    if within_window(ny_dt, 15, 30, 20) and not job_already_done(state, "close_check", day_key):
        jobs.append("close_check")

    # Friday after close ~16:05
    if ny_dt.weekday() == 4 and within_window(ny_dt, 16, 5, 25):
        if state.get("last_weekly") != day_key and not job_already_done(state, "weekly", day_key):
            jobs.append("weekly")

    return jobs


def run_job(job: str, state: dict) -> None:
    day_key = now_ny().date().isoformat()
    if job == "morning":
        job_morning()
        mark_job_done(state, "morning", day_key)
    elif job == "midday":
        job_monitor("13:00 NY")
        mark_job_done(state, "midday", day_key)
    elif job == "close_check":
        job_monitor("15:30 NY")
        mark_job_done(state, "close_check", day_key)
    elif job == "weekly":
        job_weekly()
        mark_job_done(state, "weekly", day_key)
        state["last_weekly"] = day_key
        save_state(state)
    elif job == "monthly":
        job_monthly()
        mark_job_done(state, "monthly", day_key)
        state["last_monthly"] = now_ny().strftime("%Y-%m")
        save_state(state)
    else:
        raise ValueError(f"Unknown job: {job}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SEPA paper bot")
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "morning", "midday", "close_check", "weekly", "monthly"],
        help="Job to run (auto = decide by America/New_York time)",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ny_dt = now_ny()
    log.info("SEPA start mode=%s NY time=%s", args.mode, ny_dt.isoformat())

    # Validate secrets early
    for key in ("FINVIZ_API_TOKEN", "FINNHUB_API_TOKEN", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        require_env(key)

    state = load_state()

    if args.mode == "auto":
        jobs = resolve_auto_jobs(ny_dt, state)
        if not jobs:
            log.info("No job due at this NY time — exit.")
            return
        for job in jobs:
            log.info("Auto-selected job: %s", job)
            run_job(job, state)
    else:
        run_job(args.mode, state)

    log.info("SEPA finished.")


if __name__ == "__main__":
    main()
