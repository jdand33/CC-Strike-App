from flask import Flask, render_template, request
import yfinance as yf
import requests
import os
from datetime import datetime, timedelta
import math

app = Flask(__name__)

# ---------------------------------------------------------
# API KEYS
# ---------------------------------------------------------
TRADIER_KEY = os.getenv("TRADIER_KEY") or "Adg17LaQudeoRTdAgQxXUwB3nfWA"
POLYGON_KEY = os.getenv("POLYGON_KEY") or "nVuq_7o7g8SeySvC1zYPZY6drDdhbEv4"

TRADIER_EXP_URL = "https://api.tradier.com/v1/markets/options/expirations"
TRADIER_CHAIN_URL = "https://api.tradier.com/v1/markets/options/chains"

HEADERS = {
    "Authorization": f"Bearer {TRADIER_KEY}",
    "Accept": "application/json"
}

# ---------------------------------------------------------
# RISK DELTA TARGETS
# ---------------------------------------------------------
RISK_TO_DELTA = {
    "very_safe": 0.10,
    "safe": 0.15,
    "moderate": 0.20,
    "aggressive": 0.25,
    "very_aggressive": 0.30
}

# ---------------------------------------------------------
# VALIDATE TICKER
# ---------------------------------------------------------
def validate_ticker(ticker: str) -> bool:
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        return bool(info and getattr(info, "last_price", None))
    except Exception:
        return False

# ---------------------------------------------------------
# TRADIER: GET EXPIRATIONS
# ---------------------------------------------------------
def get_tradier_expirations(ticker: str):
    try:
        r = requests.get(TRADIER_EXP_URL, headers=HEADERS, params={"symbol": ticker})
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("expirations", {}).get("date", [])
    except Exception:
        return []

# ---------------------------------------------------------
# TRADIER: GET OPTION CHAIN
# ---------------------------------------------------------
def get_tradier_chain(ticker: str, expiration: str):
    try:
        r = requests.get(TRADIER_CHAIN_URL, headers=HEADERS,
                         params={"symbol": ticker, "expiration": expiration, "greeks": "true"})
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("options", {}).get("option")
    except Exception:
        return None

# ---------------------------------------------------------
# SELECT STRIKE BY DELTA
# ---------------------------------------------------------
def select_by_delta(options, target_delta):
    best = None
    best_diff = 999
    for opt in options:
        if opt.get("option_type") != "call":
            continue
        bid, ask = opt.get("bid", 0), opt.get("ask", 0)
        if bid == 0 and ask == 0:
            continue
        delta = opt.get("greeks", {}).get("delta")
        if delta is None:
            continue
        diff = abs(delta - target_delta)
        if diff < best_diff:
            best_diff = diff
            best = opt
    return best

# ---------------------------------------------------------
# POLYGON SYMBOL BUILDER
# ---------------------------------------------------------
def build_polygon_symbol(ticker, expiration, strike):
    year, month, day = expiration.split("-")
    yy = year[2:]
    strike_int = int(round(float(strike) * 1000))
    strike_str = f"{strike_int:08d}"
    return f"{ticker.upper()}{yy}{month}{day}C{strike_str}"

# ---------------------------------------------------------
# POLYGON IV
# ---------------------------------------------------------
def get_polygon_iv(symbol):
    try:
        r = requests.get(
            f"https://api.polygon.io/v3/reference/options/{symbol}",
            params={"apiKey": POLYGON_KEY}
        )
        if r.status_code != 200:
            return None
        data = r.json()
        results = data.get("results")
        if not results:
            return None
        return results.get("implied_volatility")
    except Exception:
        return None

# ---------------------------------------------------------
# BLACK-SCHOLES FALLBACK
# ---------------------------------------------------------
def black_scholes_call_price(S, K, T, r, sigma):
    d1 = (math.log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return S * N(d1) - K * math.exp(-r * T) * N(d2)

def estimate_iv_black_scholes(S, K, T, r, market_price):
    if market_price <= 0:
        return None
    sigma = 0.3
    for _ in range(40):
        price = black_scholes_call_price(S, K, T, r, sigma)
        vega = S * math.sqrt(T) * (1 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * ((math.log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * math.sqrt(T))) ** 2)
        if vega < 1e-6:
            break
        sigma -= (price - market_price) / vega
        sigma = max(0.0001, min(sigma, 5))
    return sigma

# ---------------------------------------------------------
# MAIN ROUTE
# ---------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = None
    expirations = []

    if request.method == "POST":
        action = request.form.get("action")
        ticker = request.form.get("ticker", "").upper().strip()
        expiration = request.form.get("expiration", "")
        risk_key = request.form.get("risk", "")

        if not ticker:
            return render_template("index.html", error="Please enter a ticker.", expirations=[])

        if not validate_ticker(ticker):
            return render_template("index.html", error=f"'{ticker}' is not a valid ticker.", expirations=[])

        # Load expirations
        raw_exps = get_tradier_expirations(ticker)
     
        # Earnings week detection
        try:
            t = yf.Ticker(ticker)
            earnings_df = t.get_earnings_dates()
            next_earnings = earnings_df.index[0].to_pydatetime().date()
            earnings_week_start = next_earnings - timedelta(days=7)
            earnings_week_end = next_earnings

            expirations = []
            for exp in raw_exps:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                is_earnings_week = earnings_week_start <= exp_date <= earnings_week_end
                expirations.append({"date": exp, "earnings_week": is_earnings_week})

        except Exception:
            expirations = [{"date": e, "earnings_week": False} for e in raw_exps]

        if action == "load":
            return render_template("index.html", expirations=expirations)

        # Validate expiration
        if not any(exp["date"] == expiration for exp in expirations):
            return render_template("index.html", error="Invalid expiration.", expirations=expirations)

        # Select strike
        target_delta = RISK_TO_DELTA[risk_key]
        chain = get_tradier_chain(ticker, expiration)
        if chain is None:
            return render_template("index.html", error="Unable to pull option data.", expirations=expirations)

        best = select_by_delta(chain, target_delta)
        if best is None:
            return render_template("index.html", error="No liquid strikes found.", expirations=expirations)

        # Stock price
        fi = yf.Ticker(ticker).fast_info
        stock_price = getattr(fi, "last_price", None)

        # Days out
        exp_date = datetime.strptime(expiration, "%Y-%m-%d")
        days_out = (exp_date - datetime.utcnow()).days
        T = max(days_out / 365, 0.0001)

        # Premium
        bid, ask = best.get("bid", 0), best.get("ask", 0)
        mid = round((bid + ask) / 2, 2)
        premium = round(mid * 100, 2)

        # Assignment probability
        delta = best.get("greeks", {}).get("delta")
        assign_prob = round(abs(delta) * 100, 1) if delta else None

        # IV chain
        poly_symbol = build_polygon_symbol(ticker, expiration, best["strike"])
        poly_iv = get_polygon_iv(poly_symbol)
        tradier_iv = best.get("greeks", {}).get("iv")
        bs_iv = None

        if poly_iv is None and tradier_iv is None:
            bs_iv = estimate_iv_black_scholes(stock_price, best["strike"], T, 0.00, mid)

        iv_value = poly_iv or tradier_iv or bs_iv

        result = {
            "ticker": ticker,
            "stock_price": stock_price,
            "expiration": expiration,
            "days_out": days_out,
            "risk_label": risk_key.replace("_", " ").title(),
            "strike": best["strike"],
            "iv": iv_value,
            "assign_prob": assign_prob,
            "premium": premium
        }

    return render_template("index.html", result=result, error=error, expirations=expirations)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
