import os
import math
import requests
from datetime import datetime
from flask import Flask, request, render_template

app = Flask(__name__)

TRADIER_TOKEN = os.getenv("TRADIER_TOKEN")
TRADIER_BASE = "https://api.tradier.com/v1"


def tradier_headers():
    return {
        "Authorization": f"Bearer {TRADIER_TOKEN}",
        "Accept": "application/json"
    }


# -----------------------------
# Black-Scholes helpers
# -----------------------------
def norm_cdf(x):
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def black_scholes_call_delta(S, K, T, r, sigma):
    if sigma <= 0 or T <= 0:
        return None
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1)


def estimate_iv_call(S, K, T, r, price):
    sigma = 0.30
    for _ in range(20):
        if sigma <= 0:
            sigma = 0.01
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        model_price = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
        vega = S * math.sqrt(T) * (1 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * d1 * d1)
        diff = model_price - price
        if abs(diff) < 1e-6:
            break
        sigma -= diff / max(vega, 1e-8)
    return max(sigma, 0.0001)


# -----------------------------
# API helpers
# -----------------------------
def get_stock_price(symbol):
    r = requests.get(
        f"{TRADIER_BASE}/markets/quotes",
        headers=tradier_headers(),
        params={"symbols": symbol}
    )
    if r.status_code != 200:
        return None
    return r.json().get("quotes", {}).get("quote", {}).get("last")


def get_expirations(symbol):
    r = requests.get(
        f"{TRADIER_BASE}/markets/options/expirations",
        headers=tradier_headers(),
        params={"symbol": symbol, "includeAllRoots": "true", "strikes": "false"}
    )
    if r.status_code != 200:
        return []
    return r.json().get("expirations", {}).get("date", [])


def get_chain(symbol, expiration):
    r = requests.get(
        f"{TRADIER_BASE}/markets/options/chains",
        headers=tradier_headers(),
        params={"symbol": symbol, "expiration": expiration}
    )
    return r.json() if r.status_code == 200 else {}


# -----------------------------
# Delta targets
# -----------------------------
DELTA_TARGETS = {
    "very_safe": 0.10,
    "safe": 0.15,
    "moderate": 0.20,
    "aggressive": 0.25,
    "very_aggressive": 0.30,
}


# -----------------------------
# MAIN ROUTE
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    expirations = None
    result = None
    error = None

    if request.method == "POST":
        action = request.form.get("action")
        ticker = request.form.get("ticker", "").upper().strip()

        if not ticker:
            return render_template("index.html", error="Ticker required.")

        expirations = get_expirations(ticker)

        if action == "load":
            if not expirations:
                error = "Could not load expirations."
            return render_template("index.html", expirations=expirations, error=error)

        if action == "calculate":
            expiration = request.form.get("expiration")
            risk = request.form.get("risk")

            if not expiration:
                return render_template("index.html", expirations=expirations,
                                       error="Select an expiration.")

            stock_price = get_stock_price(ticker)
            if not stock_price:
                return render_template("index.html", expirations=expirations,
                                       error="Could not fetch stock price.")

            chain = get_chain(ticker, expiration).get("options", {}).get("option", [])
            if not chain:
                return render_template("index.html", expirations=expirations,
                                       error="Could not load option chain.")

            # -------------------------
            # CALL FILTER + FALLBACK DELTA/IV
            # -------------------------
            calls = [c for c in chain if c.get("option_type") == "call"]
            enhanced = []

            exp_clean = expiration.split(":")[0]
            d_exp = datetime.strptime(exp_clean, "%Y-%m-%d")
            T = max((d_exp - datetime.now()).days / 365.0, 0.0001)
            r = 0.045

            for c in calls:
                strike = c.get("strike")
                bid = c.get("bid") or 0
                ask = c.get("ask") or 0
                mid = (bid + ask) / 2 if (bid and ask) else (bid or ask or 0)

                greeks = c.get("greeks") or {}
                delta = greeks.get("delta")
                iv = greeks.get("mid_iv")

                if iv is None and mid:
                    iv = estimate_iv_call(stock_price, strike, T, r, mid)

                if delta is None and iv:
                    delta = black_scholes_call_delta(stock_price, strike, T, r, iv)

                if delta is None:
                    continue

                c["computed_delta"] = delta
                c["computed_iv"] = iv
                c["mid"] = mid
                enhanced.append(c)

            if not enhanced:
                return render_template("index.html", expirations=expirations,
                                       error="No valid call options found for this expiration.")

            # -------------------------
            # PICK BEST STRIKE
            # -------------------------
            target_delta = DELTA_TARGETS.get(risk, 0.20)
            best = min(enhanced, key=lambda c: abs(c["computed_delta"] - target_delta))

            premium = round(best["mid"] * 100, 2)
            assign_prob = round(abs(best["computed_delta"]) * 100, 1)
            days_out = (d_exp - datetime.now()).days

            result = {
                "ticker": ticker,
                "stock_price": round(stock_price, 2),
                "expiration": exp_clean,
                "days_out": days_out,
                "risk_label": risk.replace("_", " ").title(),
                "strike": best["strike"],
                "iv": best["computed_iv"],
                "iv_estimated": best["computed_iv"] is None,
                "assign_prob": assign_prob,
                "premium": premium,
            }

            return render_template("index.html", expirations=expirations, result=result)

    return render_template("index.html")


# -----------------------------
# DEBUG PAGE
# -----------------------------
@app.route("/debug")
def debug():
    symbol = request.args.get("symbol", "AAPL")
    expiration = request.args.get("expiration")

    debug_data = {}

    debug_data["quote_raw"] = requests.get(
        f"{TRADIER_BASE}/markets/quotes",
        headers=tradier_headers(),
        params={"symbols": symbol}
    ).text

    debug_data["expirations_raw"] = requests.get(
        f"{TRADIER_BASE}/markets/options/expirations",
        headers=tradier_headers(),
        params={"symbol": symbol, "includeAllRoots": "true", "strikes": "false"}
    ).text

    if expiration:
        debug_data["chain_raw"] = requests.get(
            f"{TRADIER_BASE}/markets/options/chains",
            headers=tradier_headers(),
            params={"symbol": symbol, "expiration": expiration}
        ).text
    else:
        debug_data["chain_raw"] = "Add ?expiration=YYYY-MM-DD"

    return debug_data


@app.route("/health")
def health():
    return {"status": "ok", "has_token": bool(TRADIER_TOKEN)}


if __name__ == "__main__":
    app.run(debug=True)
