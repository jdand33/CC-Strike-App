import os
import math
from functools import lru_cache
from datetime import datetime, timedelta

from flask import Flask, render_template, request, jsonify
import yfinance as yf

app = Flask(__name__)

# ---------- Black-Scholes helpers ----------

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def call_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1)

def find_strike(S, T, r, sigma, target_delta):
    best_K = S
    best_diff = 1
    K = S
    while K <= S * 1.2:
        d = call_delta(S, K, T, r, sigma)
        diff = abs(d - target_delta)
        if diff < best_diff:
            best_diff = diff
            best_K = K
        K += 0.5
    return round(best_K, 2)

def get_real_strikes(ticker_obj, expiration):
    chain = ticker_obj.option_chain(expiration)
    calls = chain.calls
    return sorted(list(calls["strike"]))

def round_to_real_strike(theoretical, strike_list, direction="up"):
    if not strike_list:
        return round(theoretical, 2)

    if direction == "up":
        for s in strike_list:
            if s >= theoretical:
                return float(s)
        return float(strike_list[-1])

    if direction == "down":
        for s in reversed(strike_list):
            if s <= theoretical:
                return float(s)
        return float(strike_list[0])

    return float(min(strike_list, key=lambda x: abs(x - theoretical)))

# ---------- Simple in-memory cache for price/IV ----------

_price_cache = {}
_iv_cache = {}
CACHE_TTL = timedelta(seconds=60)

def _is_fresh(ts):
    return datetime.utcnow() - ts < CACHE_TTL

def get_live_price(symbol):
    symbol = symbol.upper()
    now = datetime.utcnow()

    if symbol in _price_cache:
        value, ts = _price_cache[symbol]
        if _is_fresh(ts):
            return value

    t = yf.Ticker(symbol)
    data = t.history(period="1d")
    price = float(data["Close"].iloc[-1])
    _price_cache[symbol] = (price, now)
    return price

def get_live_iv(symbol, spot):
    symbol = symbol.upper()
    now = datetime.utcnow()

    if symbol in _iv_cache:
        value, ts = _iv_cache[symbol]
        if _is_fresh(ts):
            return value

    t = yf.Ticker(symbol)
    expirations = t.options
    if not expirations:
        return None

    nearest_exp = expirations[0]
    chain = t.option_chain(nearest_exp)
    calls = chain.calls
    calls["diff"] = (calls["strike"] - spot).abs()
    atm = calls.sort_values("diff").iloc[0]
    iv = float(atm["impliedVolatility"])

    _iv_cache[symbol] = (iv, now)
    return iv

# ---------- API endpoints ----------

@app.route("/price")
def price():
    symbol = request.args.get("ticker", "MCD").upper()
    p = get_live_price(symbol)
    return jsonify({"price": round(p, 2)})

@app.route("/iv")
def iv():
    symbol = request.args.get("ticker", "MCD").upper()
    spot = get_live_price(symbol)
    iv_val = get_live_iv(symbol, spot)
    if iv_val is None:
        return jsonify({"iv": None})
    return jsonify({"iv": round(iv_val, 4)})

# ---------- Main page ----------

@app.route("/", methods=["GET", "POST"])
def index():
    result = None

    if request.method == "POST":
        ticker = request.form["ticker"].upper()
        days = int(request.form["days"])
        risk = request.form["risk"]

        price = get_live_price(ticker)
        iv = get_live_iv(ticker, price)

        T = days / 365
        r = 0.02
        target = {"low": 0.10, "moderate": 0.20, "high": 0.30}[risk]

        theoretical_strike = find_strike(price, T, r, iv, target)

        t = yf.Ticker(ticker)
        expirations = t.options
        nearest_exp = expirations[0] if expirations else None
        real_strike = theoretical_strike

        if nearest_exp:
            real_strikes = get_real_strikes(t, nearest_exp)
            real_strike = round_to_real_strike(theoretical_strike, real_strikes, direction="up")

        result = {
            "ticker": ticker,
            "target_delta": target,
            "strike": real_strike,
            "theoretical": round(theoretical_strike, 2),
            "risk": risk.capitalize(),
            "iv": round(iv, 3) if iv is not None else None
        }

    return render_template("index.html", result=result)

# ---------- Render entrypoint ----------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
