"""
╔══════════════════════════════════════════════════════════════╗
║        NEPSE Historical OHLCV Scraper - ShareSansar          ║
║  • Headless Chrome (GitHub Actions compatible)               ║
║  • Daily auto-run at 5:00 PM Nepal Time via GitHub Actions   ║
║  • Manual trigger supports custom FROM/TO date via inputs    ║
║  • Saves one CSV per trading day  →  data/YYYY-MM-DD.csv     ║
║  • Skips dates already downloaded (safe to restart)          ║
║  • Combines everything into  nepse_all_data.csv at the end   ║
╚══════════════════════════════════════════════════════════════╝

Local usage:
    pip install selenium pandas tqdm
    python nepse_scraper.py
    python nepse_scraper.py --from 2015-01-01 --to 2016-01-01

GitHub Actions usage (set env vars):
    FROM_DATE=2015-01-01 TO_DATE=2016-01-01 python nepse_scraper.py
"""

import os
import sys
import time
import argparse
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
from tqdm import tqdm

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
except ImportError:
    print("\n❌  Missing dependencies. Please run:\n")
    print("    pip install selenium pandas tqdm\n")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════
OUTPUT_DIR   = "data"
COMBINED_CSV = "nepse_all_data.csv"
URL          = "https://www.sharesansar.com/today-share-price"

COLUMNS_MAP = {
    "S.No":            "sno",
    "Symbol":          "symbol",
    "Conf.":           "conf",
    "Open":            "open",
    "High":            "high",
    "Low":             "low",
    "Close":           "close",
    "LTP":             "ltp",
    "Close - LTP":     "close_ltp_diff",
    "Close - LTP %":   "close_ltp_pct",
    "VWAP":            "vwap",
    "Vol":             "volume",
    "Prev. Close":     "prev_close",
    "Turnover":        "turnover",
    "Trans.":          "transactions",
}


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def nepal_today() -> str:
    """Return today's date in Nepal Time (UTC+5:45) as YYYY-MM-DD."""
    nepal_tz_offset = timedelta(hours=5, minutes=45)
    nepal_now = datetime.now(timezone.utc) + nepal_tz_offset
    return nepal_now.strftime("%Y-%m-%d")


def ask_date(prompt: str, default: str) -> str:
    """Ask user for a YYYY-MM-DD date, fall back to default."""
    while True:
        raw = input(f"{prompt} (YYYY-MM-DD) [default: {default}]: ").strip()
        if raw == "":
            return default
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            return raw
        except ValueError:
            print("  ⚠  Invalid format. Please use YYYY-MM-DD (e.g. 2015-01-01)")


def date_range(start: str, end: str):
    """Yield each date from start to end inclusive."""
    current = datetime.strptime(start, "%Y-%m-%d")
    stop    = datetime.strptime(end,   "%Y-%m-%d")
    while current <= stop:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)


def is_weekend(date_str: str) -> bool:
    """NEPSE is closed Saturday & Sunday."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.weekday() >= 5


def already_downloaded(date_str: str) -> bool:
    path = os.path.join(OUTPUT_DIR, f"{date_str}.csv")
    return os.path.exists(path) and os.path.getsize(path) > 100


# ══════════════════════════════════════════════════════════════
#  BROWSER SETUP
# ══════════════════════════════════════════════════════════════

def create_driver(headless: bool = True) -> webdriver.Chrome:
    """
    Launch Chrome.
    - headless=True  for GitHub Actions / CI
    - headless=False for local watching
    Uses Selenium Manager (Selenium 4.6+) — no webdriver-manager needed.
    """
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--log-level=3")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])

    driver = webdriver.Chrome(options=opts)
    return driver


# ══════════════════════════════════════════════════════════════
#  SCRAPE ONE DATE
# ══════════════════════════════════════════════════════════════

def scrape_date(driver: webdriver.Chrome, date_str: str) -> pd.DataFrame | None:
    wait = WebDriverWait(driver, 30)

    try:
        if driver.current_url != URL:
            driver.get(URL)
            time.sleep(2)

        date_input = wait.until(
            EC.presence_of_element_located((By.ID, "fromdate"))
        )
        driver.execute_script(
            "arguments[0].removeAttribute('readonly'); "
            "arguments[0].value = arguments[1];",
            date_input, date_str
        )

        search_btn = driver.find_element(By.ID, "btn_todayshareprice_submit")
        driver.execute_script("arguments[0].click();", search_btn)
        time.sleep(2.5)

        try:
            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "table#headFixed tbody tr")
                )
            )
        except TimeoutException:
            return None

        table = driver.find_element(By.ID, "headFixed")
        rows  = table.find_elements(By.CSS_SELECTOR, "tbody tr")

        if not rows:
            return None

        headers = [
            th.text.strip()
            for th in table.find_elements(By.CSS_SELECTOR, "thead th")
        ]

        data = []
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if not cells:
                continue
            data.append([c.text.strip() for c in cells])

        if not data:
            return None

        df = pd.DataFrame(data, columns=headers[:len(data[0])])
        df.rename(columns=COLUMNS_MAP, inplace=True)
        df.insert(0, "date", date_str)

        num_cols = ["open","high","low","close","ltp","vwap",
                    "volume","turnover","prev_close","transactions",
                    "close_ltp_diff","close_ltp_pct","conf"]
        for col in num_cols:
            if col in df.columns:
                df[col] = (
                    df[col]
                    .astype(str)
                    .str.replace(",", "", regex=False)
                    .str.replace("-", "", regex=False)
                    .replace("", None)
                )
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df[df["symbol"].str.match(r"^[A-Z]", na=False)]
        df.reset_index(drop=True, inplace=True)

        return df if len(df) > 0 else None

    except Exception as e:
        print(f"\n  ⚠  Error on {date_str}: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  COMBINE
# ══════════════════════════════════════════════════════════════

def build_combined():
    csv_files = sorted([f for f in os.listdir(OUTPUT_DIR) if f.endswith(".csv")])

    if not csv_files:
        print("\n⚠  No CSV files found to combine.")
        return

    print(f"\n🔗  Combining {len(csv_files)} daily files → {COMBINED_CSV} …")
    frames = []
    for f in csv_files:
        try:
            df = pd.read_csv(os.path.join(OUTPUT_DIR, f), low_memory=False)
            frames.append(df)
        except Exception:
            pass

    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined.sort_values(["date", "symbol"], inplace=True)
        combined.to_csv(COMBINED_CSV, index=False)
        print(f"   ✅  {COMBINED_CSV} saved")
        print(f"   📊  {len(combined):,} rows  |  "
              f"{combined['date'].nunique()} trading days  |  "
              f"{combined['symbol'].nunique()} symbols")
    else:
        print("   ⚠  Nothing to combine.")

    print("\n🎉  Done!\n")


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── Determine if running in CI / GitHub Actions ────────────
    is_ci = os.environ.get("CI", "false").lower() == "true"

    # ── Parse CLI args (used for local runs) ──────────────────
    parser = argparse.ArgumentParser(description="NEPSE OHLCV Scraper")
    parser.add_argument("--from", dest="from_date", default=None,
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--to",   dest="to_date",   default=None,
                        help="End date YYYY-MM-DD (default: today Nepal time)")
    parser.add_argument("--headless", action="store_true", default=is_ci,
                        help="Run browser headlessly")
    args = parser.parse_args()

    today = nepal_today()

    # ── Resolve FROM / TO date ─────────────────────────────────
    # Priority: CLI arg → Environment variable → interactive prompt (local) / today (CI)

    if args.from_date:
        from_date = args.from_date
    elif os.environ.get("FROM_DATE"):
        from_date = os.environ["FROM_DATE"]
    elif is_ci:
        # Scheduled daily run: just today
        from_date = today
    else:
        # Interactive local run
        from_date = ask_date("Enter FROM date", "2015-01-01")

    if args.to_date:
        to_date = args.to_date
    elif os.environ.get("TO_DATE"):
        to_date = os.environ["TO_DATE"]
    elif is_ci:
        to_date = today
    else:
        to_date = ask_date("Enter TO   date", today)

    print("\n" + "═"*60)
    print("  NEPSE Historical OHLCV Downloader — ShareSansar")
    print("═"*60 + "\n")
    print(f"📅  Range   : {from_date}  →  {to_date}")
    print(f"🖥️   Headless : {args.headless}")
    print(f"📁  Output  : {os.path.abspath(OUTPUT_DIR)}\n")

    all_dates = list(date_range(from_date, to_date))
    weekdays  = [d for d in all_dates if not is_weekend(d)]
    pending   = [d for d in weekdays  if not already_downloaded(d)]
    skipped   = len(weekdays) - len(pending)

    print(f"📊  Total weekdays   : {len(weekdays)}")
    print(f"✅  Already saved    : {skipped}")
    print(f"🔄  To download      : {len(pending)}")

    if not pending:
        print("\n🎉  All dates already downloaded!")
        build_combined()
        return

    print("\n🌐  Launching Chrome…\n")
    driver = create_driver(headless=args.headless)

    try:
        driver.get(URL)
        time.sleep(3)

        success_count = 0
        no_data_count = 0

        bar = tqdm(pending, desc="Downloading", unit="day",
                   bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]")

        for date_str in bar:
            bar.set_description(f"Fetching {date_str}")
            df = scrape_date(driver, date_str)

            if df is not None and len(df) > 0:
                path = os.path.join(OUTPUT_DIR, f"{date_str}.csv")
                df.to_csv(path, index=False)
                success_count += 1
                bar.set_postfix({"rows": len(df), "status": "✅ saved"})
            else:
                no_data_count += 1
                bar.set_postfix({"status": "⏭ no data (holiday?)"})

            time.sleep(1.2)

    except KeyboardInterrupt:
        print("\n\n⛔  Interrupted. Partial data saved.")
    except Exception:
        print("\n\n❌  Unexpected error:")
        traceback.print_exc()
        sys.exit(1)
    finally:
        driver.quit()

    print(f"\n\n📈  Results:")
    print(f"   ✅ Saved    : {success_count} days")
    print(f"   ⏭  No data : {no_data_count} days (holidays / market closed)")

    build_combined()


if __name__ == "__main__":
    main()