"""Central config. Env overrides defaults. No keys in code."""
import os

def _get(k, default, cast=str):
    v = os.environ.get(k, default)
    try:
        return cast(v)
    except Exception:
        return cast(default)

# load .env if present (no dependency)
def _load_dotenv(path=".env"):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
_load_dotenv(".env")

BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
BINANCE_BASE_URL = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/")
BINANCE_FUTURES_URL = os.environ.get("BINANCE_FUTURES_URL", "https://fapi.binance.com").rstrip("/")

PAPER_START_EQUITY = _get("PAPER_START_EQUITY", 10000.0, float)
PAPER_DB = _get("PAPER_DB", "data/paper.db", str)
RISK_PER_TRADE = _get("RISK_PER_TRADE", 0.01, float)
MAX_POSITIONS = _get("MAX_POSITIONS", 5, int)
MAX_PORTFOLIO_HEAT = _get("MAX_PORTFOLIO_HEAT", 0.05, float)
MIN_QUOTE_VOL_24H = _get("MIN_QUOTE_VOL_24H", 5_000_000.0, float)
UNIVERSE_RANK_LO = _get("UNIVERSE_RANK_LO", 30, int)
UNIVERSE_RANK_HI = _get("UNIVERSE_RANK_HI", 180, int)

# Hard exclusions: user said NOT these majors
ALWAYS_EXCLUDE_BASES = {"BTC", "ETH", "XRP"}
# Stables / fiat / leveraged tokens never traded
STABLE_BASES = {"USDT", "USDC", "FDUSD", "DAI", "TUSD", "USDP", "AEUR", "EUR", "TRY", "BRL", "ARS"}
LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

FEE_RATE = 0.001       # 0.10% spot taker
SLIPPAGE_RATE = 0.0005 # 0.05%

# --- Futures (USDT-M perps, paper only) ---
FUTURES_FEE_RATE = 0.0005      # 0.05% taker per side
FUTURES_SLIPPAGE = 0.0005
FUNDING_PER_8H = 0.0002        # 2bp per 8h conservative
FUTURES_DEFAULT_LEV = 3
FUTURES_MAX_LEV = 5
FUTURES_RISK_PER_TRADE = 0.01
FUTURES_MAX_POSITIONS = 3
FUTURES_MMR = 0.005            # 0.5% maintenance margin assumption
FUTURES_DB = "data/futures_paper.db"

# Smart cohort: only names that stayed positive through the ~1000d backtest.
# Live paper cycles gate new entries to this list.
SMART_SYMBOLS = {
    "SOLUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "UNIUSDT",
    "ETCUSDT", "LTCUSDT", "NEARUSDT", "ATOMUSDT", "FILUSDT", "ARBUSDT",
}
# Always-blocked regardless of SMART_SYMBOLS (long-run losers / danger).
BLOCKED_SYMBOLS = {"BNBUSDT", "APTUSDT", "OPUSDT", "INJUSDT"}
