from flask import Flask, render_template, request
import yfinance as yf
from datetime import datetime

app = Flask(__name__)

# Risk categories mapped to approximate deltas
RISK_TO_DELTA = {
    "very_safe": 0.10,
    "safe": 0.15,
    "moderate": 0.20,
    "aggressive": 0.25,
    "very_aggressive": 0.30
}

# -----------------------------
# VALIDATE TICKER
# -----------------------------
def validate_ticker(ticker: str) -> bool:
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info

        if not info:
            print("DEBUG: fast_info empty")
            return False

        if not getattr(info, "last_price", None):
            print("DEBUG: last_price missing")
            return False

        if not t.options:
            print("DEBUG: ticker has no options")
            return False

        return True

    except Exception as e:
        print("VALIDATE_TICKER EXCEPTION:", e)
        return False


# -----------------------------
# GET EXPIRATIONS (WITH RETRY)
# -----------------------------
def get_expirations(ticker: str):
    try:
        t = yf.Ticker(ticker)

        for attempt in range(3):
            expirations = t.options
            print(f"DEBUG: Expiration attempt {attempt+1}: {expirations}")

            if expirations:
                return expirations

        print("DEBUG: Expirations empty after retries")
        return []

    except Exception as e:
        print("GET_EXPIRATIONS EXCEPTION:", e)
        return []


# -----------------------------
# GET OPTION CHAIN + DELTA MATCH
# -----------------------------
def get_closest_delta_strike(ticker: str, expiration: str, target_delta: float):
    print("\n=== OPTION DEBUG START ===")
    print("Ticker:", ticker)
    print("Expiration:", expiration)

    try:
        t = yf.Ticker(ticker)

        # Get current price for fallback logic
        last_price = None
        try:
            fi = t.fast_info
            last_price = getattr(fi, "last_price", None)
            print("DEBUG: last_price:", last_price)
        except Exception as e:
            print("DEBUG: fast_info error:", e)

        calls = None

        # Retry twice for cold starts
        for attempt in range(2):
            chain = t.option_chain(expiration)
            calls = chain.calls
            print(f"DEBUG: Chain attempt {attempt+1}, calls empty? {calls.empty}")

            if not calls.empty:
                break

        if calls is None or calls.empty:
            print("DEBUG ERROR: Calls are empty")
            print("=== OPTION DEBUG END ===\n")
            return None

        print("DEBUG: Calls columns:", list(calls.columns))

        # -----------------------------
        # CASE 1: DELTA EXISTS → USE IT
        # -----------------------------
        if "delta" in calls.columns:
            calls = calls.dropna(subset=["delta"])
            print("DEBUG: Calls after dropping NaN deltas:", len(calls))

            if not calls.empty:
                calls["abs_diff"] = (calls["delta"] - target_delta).abs()
                best = calls.loc[calls["abs_diff"].idxmin()]
                print("DEBUG: Selected by delta:", best["contractSymbol"])

                return {
                    "symbol": best["contractSymbol"],
                    "strike": float(best["strike"]),
                    "delta": float(best["delta"]),
                    "bid": float(best["bid"]),
                    "ask": float(best["ask"]),
                    "last": float(best["lastPrice"]),
                    "iv": float(best["impliedVolatility"]) if "impliedVolatility" in best else None
                }

            print("DEBUG: Delta exists but all NaN — falling back to strike logic")

        # -----------------------------
        # CASE 2: NO DELTA → FALLBACK
        # -----------------------------
        print("DEBUG: Using strike-based fallback")

        if last_price is None:
            mid_idx = len(calls) // 2
            best = calls.iloc[mid_idx]
            print("DEBUG: No last_price, picked middle strike:", best["contractSymbol"])
        else:
            calls["moneyness"] = calls["strike"] - last_price
            otm = calls[calls["moneyness"] >= 0]

            if not otm.empty:
                best = otm.loc[otm["moneyness"].idxmin()]
                print("DEBUG: Picked nearest OTM strike:", best["contractSymbol"])
            else:
                calls["abs_diff_strike"] = (calls["strike"] - last_price).abs()
                best = calls.loc[calls["abs_diff_strike"].idxmin()]
                print("DEBUG: All ITM, picked closest strike:", best["contractSymbol"])

        print("=== OPTION DEBUG END ===\n")

        return {
            "symbol": best["contractSymbol"],
            "strike": float(best["strike"]),
            "delta": None,
            "bid": float(best["bid"]),
            "ask": float(best["ask"]),
            "last": float(best["lastPrice"]),
            "iv": float(best["impliedVolatility"]) if "impliedVolatility" in best else None
        }

    except Exception as e:
        print("OPTION_CHAIN EXCEPTION:", e)
        print("=== OPTION DEBUG END ===\n")
        return None


# -----------------------------
# MAIN ROUTE
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = None
    expirations = []

    if request.method == "POST":
        action = request.form.get("action")
        ticker = request.form.get("ticker", "").upper().strip()
        expiration = request.form.get("expiration", "").strip()
        risk_key = request.form.get("risk", "").strip()

        print("\n=== FORM DEBUG ===")
        print("Action:", action)
        print("Ticker:", ticker)
        print("Expiration from POST:", expiration)
        print("Risk:", risk_key)
        print("===================\n")

        # Validate ticker
        if not ticker:
            return render_template("index.html",
                                   error="Please enter a ticker.",
                                   expirations=expirations)

        if not validate_ticker(ticker):
            return render_template("index.html",
                                   error=f"'{ticker}' is not a valid ticker with options.",
                                   expirations=[])

        # Load expirations
        expirations = get_expirations(ticker)
        if not expirations:
            return render_template("index.html",
                                   error="No expirations available for this ticker.",
                                   expirations=[])

        # If user clicked "Get Expirations"
        if action == "load":
            return render_template("index.html",
                                   expirations=expirations)

        # User clicked "Calculate"
        if not expiration:
            return render_template("index.html",
                                   error="Please select an expiration.",
                                   expirations=expirations)

        if expiration not in expirations:
            return render_template("index.html",
                                   error=f"{expiration} is not a valid expiration.",
                                   expirations=expirations)

        if risk_key not in RISK_TO_DELTA:
            return render_template("index.html",
                                   error="Invalid risk level.",
                                   expirations=expirations)

        target_delta = RISK_TO_DELTA[risk_key]

        # Get option result
        result = get_closest_delta_strike(ticker, expiration, target_delta)
        if result is None:
            return render_template("index.html",
                                   error="Unable to pull option data.",
                                   expirations=expirations)

        # -----------------------------
        # ADD ENHANCED OUTPUT FIELDS
        # -----------------------------

        # Stock price
        t = yf.Ticker(ticker)
        fi = t.fast_info
        result["stock_price"] = getattr(fi, "last_price", None)

        # Days until expiration
        exp_date = datetime.strptime(expiration, "%Y-%m-%d")
        today = datetime.utcnow()
        result["days_out"] = (exp_date - today).days

        # Risk label
        result["risk_label"] = risk_key.replace("_", " ").title()

        # Mid price
        result["mid"] = round((result["bid"] + result["ask"]) / 2, 2)

        # Premium (mid × 100)
        result["premium"] = round(result["mid"] * 100, 2)

        # Assignment probability
        if result["delta"] is not None:
            result["assign_prob"] = round(abs(result["delta"]) * 100, 1)
        else:
            result["assign_prob"] = None

    return render_template("index.html",
                           result=result,
                           error=error,
                           expirations=expirations)


# -----------------------------
# RUN APP
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
