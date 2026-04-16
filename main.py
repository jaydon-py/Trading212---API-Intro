"""
Trading 212 Portfolio Summary Chart
====================================
Generates a weekly portfolio value line chart from your first-ever order
to the present day, using the Trading 212 Public API (Beta) — Live environment.

HOW IT WORKS (Historical Value Reconstruction)
-----------------------------------------------
The Trading 212 API does not provide a time-series of portfolio values.
Instead, this script reconstructs history by:

  1. Fetching ALL historical filled orders via /equity/history/orders
     (cursor-based pagination). Each order contains the executed quantity,
     the fill price, and a timestamp — giving us cost-basis snapshots.

  2. Fetching ALL historical transactions via /equity/history/transactions
     (deposits, withdrawals, dividends, fees, FX conversions).

  3. Building a running ledger of cash and share holdings week-by-week,
     using the fill prices from orders as the "price paid" at each
     transaction date.

  4. For the *current* (unfinalised) week, the script fetches live
     position data from /equity/positions to get today's market value,
     plus the current cash balance from /equity/account/cash.

  5. Between filled-order events the share count stays constant, so the
     reconstructed value is a lower bound between trades (it does not
     reprice mid-week using live market data — that would require a paid
     market-data provider). The current week's data point IS live-priced.
"""

import base64
import os
import time
import sys
from datetime import timedelta
from keys_config import api_key, secret_key
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker

key   = api_key
secret = secret_key

BASE_URL   = "https://live.trading212.com/api/v0"

REQUEST_DELAY_SECONDS = 5

PAGE_LIMIT = 50

def _build_auth_header(api_key: str, api_secret: str) -> str:
    raw = f"{api_key}:{api_secret}"
    encoded = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
    return f"Basic {encoded}"


def _validate_credentials():
    if key == "YOUR_API_KEY_HERE" or secret == "YOUR_API_SECRET_HERE":
        print(
            "\n❌  API credentials not set.\n"
            "   Please set T212_API_KEY and T212_API_SECRET environment\n"
            "   variables, add a .env file, or edit the placeholders at\n"
            "   the top of this script.\n"
        )
        sys.exit(1)


# ─────────────────────────────────────────────
# HTTP SESSION
# ─────────────────────────────────────────────

def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": _build_auth_header(key, secret),
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    })
    return session


# ─────────────────────────────────────────────
# SAFE GET WITH RATE-LIMIT HANDLING
# ─────────────────────────────────────────────

def _get(session: requests.Session, url: str, params: dict = None,
         max_retries: int = 20) -> dict:
    """
    HTTP GET with exponential back-off for 429 (Too Many Requests) and
    transient 5xx errors.
    """
    for attempt in range(max_retries):
        try:
            resp = session.get(url, params=params, timeout=30)
        except requests.exceptions.ConnectionError as exc:
            wait = 2 ** attempt
            print(f"   ⚠  Connection error ({exc}). Retrying in {wait}s…")
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429:
            # Respect Retry-After header if present, else back-off
            retry_after = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
            print(f"   ⏳  Rate limited (429). Waiting {retry_after}s…")
            time.sleep(retry_after)
            continue

        if resp.status_code in (500, 502, 503, 504):
            wait = 2 ** attempt
            print(f"   ⚠  Server error {resp.status_code}. Retrying in {wait}s…")
            time.sleep(wait)
            continue

        # Any other error is fatal
        resp.raise_for_status()

    raise RuntimeError(f"Failed to GET {url} after {max_retries} attempts.")


# ─────────────────────────────────────────────
# PAGINATED HISTORY FETCH
# ─────────────────────────────────────────────

def _fetch_all_pages(session: requests.Session, path: str) -> list[dict]:
    """
    Fetch every page of a cursor-paginated history endpoint.
    Trading 212 returns `nextPagePath` as a relative path string
    (e.g. "/api/v0/equity/history/orders?cursor=...&limit=...").
    Returns a flat list of all `items` across all pages.
    """
    url    = f"{BASE_URL}{path}?limit={PAGE_LIMIT}"
    all_items: list[dict] = []
    page_num = 1

    while url:
        print(f"   📄  Fetching page {page_num} → {url.split('?')[0]}")
        data = _get(session, url)

        items = data.get("items", [])
        all_items.extend(items)

        next_path = data.get("nextPagePath")
        if next_path:
            # nextPagePath is a full path like /api/v0/equity/history/orders?cursor=…
            base = BASE_URL.rstrip("/api/v0")  # https://live.trading212.com
            url = base + next_path
            page_num += 1
            time.sleep(REQUEST_DELAY_SECONDS)
        else:
            url = None

    print(f"   ✅  {len(all_items)} records fetched from {path}")
    return all_items


# ─────────────────────────────────────────────
# MAIN DATA FETCHING
# ─────────────────────────────────────────────

def fetch_all_orders(session: requests.Session) -> pd.DataFrame:
    """Fetch full order history. Returns a DataFrame sorted oldest→newest."""
    print("\n[1/4] Fetching order history…")
    items = _fetch_all_pages(session, "/equity/history/orders")

    if not items:
        print("   ⚠  No orders found. Is this account new?")
        return pd.DataFrame()

    df = pd.json_normalize(items)

    # Keep only FILLED orders (status == "FILLED")
    if "status" in df.columns:
        df = df[df["status"] == "FILLED"].copy()

    # Parse timestamps
    date_col = next(
        (c for c in ("dateExecuted", "dateModified", "dateCreated") if c in df.columns),
        None,
    )
    if date_col is None:
        raise ValueError("Cannot find a date column in order history response.")

    df["executed_at"] = pd.to_datetime(df[date_col], utc=True)
    df.sort_values("executed_at", inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Normalise key numeric fields
    for col in ("filledQuantity", "filledValue", "quantity"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    print(f"   📅  Earliest order: {df['executed_at'].min()}")
    print(f"   📅  Latest order:   {df['executed_at'].max()}")
    return df


def fetch_all_transactions(session: requests.Session) -> pd.DataFrame:
    """Fetch full transaction history (deposits, withdrawals, dividends, etc.)."""
    print("\n[2/4] Fetching transaction history…")
    items = _fetch_all_pages(session, "/equity/history/transactions")

    if not items:
        return pd.DataFrame()

    df = pd.json_normalize(items)

    date_col = next(
        (c for c in ("dateTime", "date", "dateExecuted") if c in df.columns), None
    )
    if date_col:
        df["executed_at"] = pd.to_datetime(df[date_col], utc=True)
        df.sort_values("executed_at", inplace=True)
        df.reset_index(drop=True, inplace=True)

    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)

    return df


def fetch_current_state(session: requests.Session) -> tuple[float, pd.DataFrame]:
    """
    Fetch the live portfolio state:
      - current cash balance (account cash endpoint)
      - all open positions with current market values
    Returns (cash_balance, positions_df).
    """
    print("\n[3/4] Fetching current portfolio state…")

    # Cash
    cash_data = _get(session, f"{BASE_URL}/equity/account/cash")
    # The cash endpoint returns { "free": ..., "total": ..., "ppl": ... } etc.
    # We want "free" (uninvested cash) or "total"
    cash = float(cash_data.get("free", cash_data.get("total", 0.0)))
    print(f"   💷  Current free cash: {cash:.2f}")

    time.sleep(REQUEST_DELAY_SECONDS)

    # Positions
    pos_data = _get(session, f"{BASE_URL}/equity/positions")
    if isinstance(pos_data, list):
        positions = pos_data
    else:
        positions = pos_data.get("items", [])

    if not positions:
        print("   ℹ   No open positions.")
        return cash, pd.DataFrame()

    df = pd.json_normalize(positions)

    # currentPrice * quantity gives us the current market value per position
    for col in ("currentPrice", "quantity"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if "currentPrice" in df.columns and "quantity" in df.columns:
        df["market_value"] = df["currentPrice"] * df["quantity"]
    elif "walletImpact.currentValue" in df.columns:
        df["market_value"] = pd.to_numeric(
            df["walletImpact.currentValue"], errors="coerce"
        ).fillna(0.0)
    else:
        df["market_value"] = 0.0

    total_equity = df["market_value"].sum()
    print(f"   📈  Current equity value: {total_equity:.2f}")
    return cash, df


# ─────────────────────────────────────────────
# PORTFOLIO RECONSTRUCTION
# ─────────────────────────────────────────────

def reconstruct_weekly_portfolio(
    orders_df: pd.DataFrame,
    transactions_df: pd.DataFrame,
    current_cash: float,
    current_positions_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Reconstruct total portfolio value at each Monday (week start) from the
    date of the first order to today.

    Methodology
    -----------
    Because the Trading 212 API does not provide historical price feeds,
    we use the *fill price* of each order as the best available proxy for
    the per-share value at that moment in time.

    For each weekly snapshot:
      portfolio_value = Σ (shares_held_per_ticker × last_known_fill_price)
                        + running_cash_balance

    The running cash balance is updated by:
      + deposits / dividend credits  (positive transactions)
      - withdrawals / fees            (negative transactions)
      - buy order value               (filledValue for BUY orders)
      + sell order value              (filledValue for SELL orders)

    For the CURRENT week we use live position values + free cash instead,
    which gives an accurate present-day data point.

    Limitations
    -----------
    * Between trades, share values are *frozen* at the last fill price.
      This means the chart shows invested capital cost rather than a
      mark-to-market history between events.
    * FX movements are not repriced intra-week.
    * The final data point (today) IS live-priced via the positions API.
    """

    if orders_df.empty:
        print("   ⚠  No order data — cannot reconstruct history.")
        return pd.DataFrame()

    # ── Build a combined event timeline ──────────────────────────────────
    events: list[dict] = []

    for _, row in orders_df.iterrows():
        side = str(row.get("type", "BUY")).upper()
        # BUY  → positive quantity, negative cash
        # SELL → negative quantity, positive cash
        qty   = float(row.get("filledQuantity", row.get("quantity", 0)))
        value = float(row.get("filledValue", 0))
        if "SELL" in side:
            qty   = -abs(qty)
            value = abs(value)   # cash in
        else:
            qty   = abs(qty)
            value = -abs(value)  # cash out

        ticker = str(
            row.get("ticker", row.get("instrument.ticker", "UNKNOWN"))
        )

        # Derive fill price per share
        fill_price = abs(row.get("filledValue", 0)) / abs(qty) if qty != 0 else 0

        events.append({
            "ts":         row["executed_at"],
            "kind":       "ORDER",
            "ticker":     ticker,
            "qty_delta":  qty,
            "cash_delta": value,
            "fill_price": fill_price,
        })

    if not transactions_df.empty and "executed_at" in transactions_df.columns:
        for _, row in transactions_df.iterrows():
            amount = float(row.get("amount", 0))
            t_type = str(row.get("type", "")).upper()
            # Skip order-related transactions (already captured via orders endpoint)
            if any(kw in t_type for kw in ("ORDER", "BUY", "SELL")):
                continue
            events.append({
                "ts":         row["executed_at"],
                "kind":       "TRANSACTION",
                "ticker":     "",
                "qty_delta":  0.0,
                "cash_delta": amount,
                "fill_price": 0.0,
            })

    if not events:
        print("   ⚠  No events to process.")
        return pd.DataFrame()

    events_df = pd.DataFrame(events).sort_values("ts").reset_index(drop=True)

    # ── Set up weekly date range ──────────────────────────────────────────
    start_date = events_df["ts"].min().floor("D")
    end_date   = pd.Timestamp.now(tz="UTC").floor("D")

    # Weekly Mondays
    weeks = pd.date_range(
        start=start_date,
        end=end_date + timedelta(days=7),
        freq="W-MON",
        tz="UTC",
    )
    weeks = weeks[weeks <= end_date]

    # ── Simulate portfolio state week by week ─────────────────────────────
    holdings:    dict[str, float] = {}   # ticker → shares held
    fill_prices: dict[str, float] = {}   # ticker → latest fill price (proxy)
    cash = 0.0

    # Estimate starting cash as total deposits before first order
    # (rough estimate from transactions, if available)
    if not transactions_df.empty and "executed_at" in transactions_df.columns:
        pre_orders = transactions_df[
            transactions_df["executed_at"] < events_df["ts"].min()
        ]
        if not pre_orders.empty and "amount" in pre_orders.columns:
            cash = float(pre_orders["amount"].sum())

    weekly_values: list[dict] = []
    event_ptr = 0
    total_events = len(events_df)

    for week_end in weeks:
        # Apply all events that occurred before this week boundary
        while event_ptr < total_events:
            ev = events_df.iloc[event_ptr]
            if ev["ts"] > week_end:
                break

            cash += ev["cash_delta"]

            if ev["kind"] == "ORDER" and ev["ticker"]:
                ticker = ev["ticker"]
                holdings[ticker]    = holdings.get(ticker, 0.0) + ev["qty_delta"]
                if ev["fill_price"] > 0:
                    fill_prices[ticker] = ev["fill_price"]
                # Clamp to zero (avoid tiny negatives from rounding)
                if holdings[ticker] < 1e-8:
                    holdings.pop(ticker, None)

            event_ptr += 1

        # Compute equity value at this snapshot
        equity = sum(
            holdings.get(t, 0) * fill_prices.get(t, 0) for t in holdings
        )
        total = max(0.0, equity + max(0.0, cash))

        weekly_values.append({
            "date":         week_end,
            "equity_value": equity,
            "cash":         max(0.0, cash),
            "total_value":  total,
        })

    result_df = pd.DataFrame(weekly_values)

    # ── Override the most recent data point with live values ──────────────
    live_equity = 0.0
    if not current_positions_df.empty and "market_value" in current_positions_df.columns:
        live_equity = float(current_positions_df["market_value"].sum())

    live_total = max(0.0, live_equity + current_cash)

    now = pd.Timestamp.now(tz="UTC")
    live_row = pd.DataFrame([{
        "date":         now,
        "equity_value": live_equity,
        "cash":         current_cash,
        "total_value":  live_total,
    }])

    result_df = pd.concat([result_df, live_row], ignore_index=True)
    result_df.sort_values("date", inplace=True)
    result_df.drop_duplicates(subset="date", keep="last", inplace=True)
    result_df.reset_index(drop=True, inplace=True)

    return result_df


# ─────────────────────────────────────────────
# CHART GENERATION
# ─────────────────────────────────────────────

def plot_portfolio_chart(df: pd.DataFrame, account_currency: str = "GBP"):
    """Render and save the portfolio value line chart."""
    print("\n[4/4] Rendering chart…")

    if df.empty:
        print("   ❌  No data to plot.")
        return

    # Filter out any zero-value leading rows (before first meaningful deposit)
    first_nonzero = df[df["total_value"] > 0].index.min()
    if pd.isna(first_nonzero):
        print("   ❌  All values are zero — nothing to plot.")
        return
    df = df.loc[first_nonzero:].copy()

    fig, ax = plt.subplots(figsize=(14, 7))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    dates  = df["date"].dt.to_pydatetime()
    values = df["total_value"].values

    # Shade area under the curve
    ax.fill_between(
        dates, values,
        alpha=0.18,
        color="#00c4a3",
    )

    # Main line
    ax.plot(
        dates, values,
        color="#00c4a3",
        linewidth=2.2,
        zorder=3,
        label="Portfolio Value",
    )

    # Highlight current (live) value
    ax.scatter(
        [dates[-1]], [values[-1]],
        color="#00c4a3",
        s=70,
        zorder=5,
        label=f"Current: {values[-1]:,.2f} {account_currency}",
    )

    # Annotate peak
    peak_idx = values.argmax()
    ax.annotate(
        f"Peak: {values[peak_idx]:,.0f}",
        xy=(dates[peak_idx], values[peak_idx]),
        xytext=(10, 12),
        textcoords="offset points",
        fontsize=8,
        color="#f0c040",
        arrowprops=dict(arrowstyle="->", color="#f0c040", lw=1.0),
    )

    # Axes formatting
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=max(1, len(df) // 24)))
    plt.xticks(rotation=35, ha="right", fontsize=8, color="#aab4be")
    plt.yticks(fontsize=9, color="#aab4be")

    def _currency_fmt(x, _):
        if x >= 1_000_000:
            return f"{account_currency}{x/1_000_000:.1f}M"
        if x >= 1_000:
            return f"{account_currency}{x/1_000:.0f}k"
        return f"{account_currency}{x:.0f}"

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_currency_fmt))

    for spine in ax.spines.values():
        spine.set_color("#2a2f38")

    ax.tick_params(colors="#aab4be", which="both")
    ax.grid(axis="y", color="#2a2f38", linestyle="--", linewidth=0.7, alpha=0.7)
    ax.grid(axis="x", color="#2a2f38", linestyle=":", linewidth=0.5, alpha=0.5)

    # Labels
    ax.set_title(
        "Trading 212 Portfolio Value History",
        fontsize=15,
        fontweight="bold",
        color="#e6edf3",
        pad=16,
    )
    ax.set_xlabel("Date", fontsize=10, color="#8b949e", labelpad=8)
    ax.set_ylabel(
        f"Total Portfolio Value ({account_currency})",
        fontsize=10,
        color="#8b949e",
        labelpad=8,
    )

    # Legend
    legend = ax.legend(
        loc="upper left",
        fontsize=9,
        facecolor="#161b22",
        edgecolor="#2a2f38",
        labelcolor="#e6edf3",
    )

    # Watermark / note
    fig.text(
        0.99, 0.01,
        "Note: Historical values based on order fill prices, not daily market re-pricing.",
        ha="right",
        va="bottom",
        fontsize=7,
        color="#4a5568",
        style="italic",
    )

    plt.tight_layout()

    output_path = "t212_portfolio_history.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"   💾  Chart saved to: {output_path}")
    plt.show()
    plt.close(fig)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Trading 212 Portfolio History Chart")
    print("  Live Environment | api/v0")
    print("=" * 60)

    _validate_credentials()
    session = _make_session()

    # Quick connectivity test
    print("\n🔑  Verifying credentials…")
    try:
        account_info = _get(session, f"{BASE_URL}/equity/account/info")
        currency = account_info.get("currencyCode", "GBP")
        print(f"   ✅  Connected — Account currency: {currency}")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            print(
                "\n❌  Authentication failed (401).\n"
                "   Check that your API_KEY and API_SECRET are correct and\n"
                "   that you are using a Live account key.\n"
            )
            sys.exit(1)
        raise

    time.sleep(REQUEST_DELAY_SECONDS)

    # Fetch data
    orders_df       = fetch_all_orders(session)
    time.sleep(REQUEST_DELAY_SECONDS)
    transactions_df = fetch_all_transactions(session)
    time.sleep(REQUEST_DELAY_SECONDS)
    current_cash, current_positions_df = fetch_current_state(session)

    # Reconstruct weekly timeline
    print("\n📊  Reconstructing weekly portfolio values…")
    weekly_df = reconstruct_weekly_portfolio(
        orders_df,
        transactions_df,
        current_cash,
        current_positions_df,
    )

    if weekly_df.empty:
        print("\n⚠  Could not build portfolio timeline. Exiting.")
        sys.exit(0)

    print(f"\n   📆  Timeline: {weekly_df['date'].min().date()} → {weekly_df['date'].max().date()}")
    print(f"   🗓   Weekly data points: {len(weekly_df)}")
    print(f"   💰  First value: {weekly_df['total_value'].iloc[0]:,.2f} {currency}")
    print(f"   💰  Latest value: {weekly_df['total_value'].iloc[-1]:,.2f} {currency}")

    # Export data to CSV alongside chart
    csv_path = "t212_portfolio_history.csv"
    weekly_df.to_csv(csv_path, index=False)
    print(f"   📄  Raw data saved to: {csv_path}")

    # Render chart
    plot_portfolio_chart(weekly_df, account_currency=currency)

    print("\n✅  Done!\n")


if __name__ == "__main__":
    main()