import os
import time
import logging
import threading
import json
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS
import pandas as pd
from pybit.unified_trading import HTTP

# ── CONFIG ───────────────────────────────────────────────
API_KEY    = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
TESTNET    = os.environ.get("TESTNET", "True") == "True"
SYMBOL     = os.environ.get("SYMBOL", "BTCUSDT")
TIMEFRAME  = os.environ.get("TIMEFRAME", "60")
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))
STOP_PCT   = float(os.environ.get("STOP_PCT", "0.02"))
TP_PCT     = float(os.environ.get("TP_PCT", "0.04"))
LOOKBACK   = int(os.environ.get("LOOKBACK", "20"))
TOLERANCE  = float(os.environ.get("TOLERANCE", "0.005"))
PORT       = int(os.environ.get("PORT", "8080"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

state = {
    "status": "iniciando",
    "symbol": SYMBOL,
    "price": 0.0,
    "support": 0.0,
    "resistance": 0.0,
    "signal": "AGUARDANDO",
    "in_position": False,
    "entry_price": 0.0,
    "balance": 0.0,
    "pnl_today": 0.0,
    "trades_today": 0,
    "wins": 0,
    "losses": 0,
    "last_update": "",
    "history": [],
    "testnet": TESTNET,
}

SLEEP_MAP = {"1":60,"5":300,"15":900,"60":3600,"240":14400,"D":86400}

app = Flask(__name__)
CORS(app)

@app.route("/status")
def get_status():
    return jsonify(state)

@app.route("/health")
def health():
    return jsonify({"ok": True})

def get_client():
    client = HTTP(testnet=TESTNET, api_key=API_KEY, api_secret=API_SECRET)
    client.get_server_time()
    return client

def get_klines(client):
    resp = client.get_kline(
        category="spot", symbol=SYMBOL,
        interval=TIMEFRAME, limit=LOOKBACK + 10
    )
    rows = resp["result"]["list"]
    df = pd.DataFrame(rows, columns=[
        "time","open","high","low","close","volume","turnover"
    ])
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    return df.iloc[::-1].reset_index(drop=True)

def get_sr_levels(df):
    highs = df["high"].rolling(LOOKBACK).max()
    lows  = df["low"].rolling(LOOKBACK).min()
    return lows.iloc[-1], highs.iloc[-1]

def get_signal(price, support, resistance):
    zone = price * TOLERANCE
    if abs(price - support) <= zone:
        return "BUY"
    elif abs(price - resistance) <= zone:
        return "SELL"
    return "HOLD"

def get_balance(client):
    try:
        resp  = client.get_wallet_balance(accountType="UNIFIED")
        coins = resp["result"]["list"][0]["coin"]
        for coin in coins:
            if coin["coin"] == "USDT":
                return float(coin["availableToWithdraw"])
    except:
        pass
    return 0.0

def run_bot():
    global state
    if not API_KEY or not API_SECRET:
        state["status"] = "erro: sem credenciais"
        return

    try:
        client = get_client()
        state["status"] = "rodando"
        log.info(f"✅ Conectado {'TESTNET' if TESTNET else 'REAL'} — {SYMBOL}")
    except Exception as e:
        state["status"] = f"erro: {e}"
        return

    sleep_s     = SLEEP_MAP.get(TIMEFRAME, 3600)
    in_position = False
    entry_price = 0.0
    qty         = 0.0

    while True:
        try:
            df    = get_klines(client)
            price = float(df["close"].iloc[-1])
            s1, r1 = get_sr_levels(df)
            sig    = get_signal(price, s1, r1)
            bal    = get_balance(client)

            state.update({
                "price":       round(price, 2),
                "support":     round(s1, 2),
                "resistance":  round(r1, 2),
                "signal":      sig,
                "in_position": in_position,
                "entry_price": round(entry_price, 2),
                "balance":     round(bal, 2),
                "last_update": datetime.now().strftime("%H:%M:%S"),
            })

            log.info(f"💲 {price:.2f} | S={s1:.2f} R={r1:.2f} | {sig}")

            if sig == "BUY" and not in_position and bal >= TRADE_USDT:
                qty = round(TRADE_USDT / price, 6)
                client.place_order(
                    category="spot", symbol=SYMBOL,
                    side="Buy", orderType="Market", qty=str(qty)
                )
                entry_price = price
                in_position = True
                state["trades_today"] += 1
                state["in_position"]   = True
                state["entry_price"]   = round(entry_price, 2)
                log.info(f"🟢 COMPRA {qty} @ ${price:.2f}")
                state["history"].insert(0, {
                    "side": "BUY", "price": price,
                    "time": datetime.now().strftime("%H:%M"), "pnl": None
                })

            elif in_position:
                sl      = entry_price * (1 - STOP_PCT)
                tp      = entry_price * (1 + TP_PCT)
                pnl_pct = (price - entry_price) / entry_price * 100
                pnl_usd = (price - entry_price) * qty

                if price <= sl or price >= tp or sig == "SELL":
                    client.place_order(
                        category="spot", symbol=SYMBOL,
                        side="Sell", orderType="Market", qty=str(qty)
                    )
                    in_position = False
                    state["in_position"] = False
                    state["pnl_today"]   = round(state["pnl_today"] + pnl_usd, 2)

                    if pnl_usd >= 0:
                        state["wins"] += 1
                    else:
                        state["losses"] += 1

                    reason = "TP" if price >= tp else ("SL" if price <= sl else "SELL")
                    log.info(f"🔴 VENDA [{reason}] @ ${price:.2f} | P&L: {pnl_pct:+.2f}%")
                    state["history"].insert(0, {
                        "side": "SELL", "price": price,
                        "time": datetime.now().strftime("%H:%M"),
                        "pnl": round(pnl_usd, 2)
                    })
                    state["history"] = state["history"][:20]

            time.sleep(sleep_s)

        except Exception as e:
            log.error(f"❌ Erro: {e}")
            state["status"] = "erro temporário"
            time.sleep(30)
            state["status"] = "rodando"

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    log.info(f"🌐 API rodando na porta {PORT}")
    app.run(host="0.0.0.0", port=PORT)
