import os
import time
import logging
import threading
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS
import pandas as pd
from pybit.unified_trading import HTTP

API_KEY    = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
TESTNET    = os.environ.get("TESTNET", "True") == "True"
SYMBOL     = os.environ.get("SYMBOL", "BTCUSDT")
TIMEFRAME  = "15"
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))
STOP_PCT   = float(os.environ.get("STOP_PCT", "0.02"))
TP_PCT     = float(os.environ.get("TP_PCT", "0.04"))
LOOKBACK   = int(os.environ.get("LOOKBACK", "20"))
TOLERANCE  = float(os.environ.get("TOLERANCE", "0.005"))
PORT       = int(os.environ.get("PORT", "8080"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

state = {
    "status": "iniciando", "symbol": SYMBOL,
    "price": 0.0, "support": 0.0, "resistance": 0.0,
    "signal": "AGUARDANDO", "in_position": False,
    "entry_price": 0.0, "pnl_live_pct": 0.0,
    "balance": 0.0, "pnl_today": 0.0,
    "trades_today": 0, "wins": 0, "losses": 0,
    "last_update": "", "history": [], "testnet": TESTNET,
}

sr_levels    = {"support": 0.0, "resistance": 0.0}
in_position  = False
entry_price  = 0.0
qty          = 0.0
position_lock = threading.Lock()

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

def get_current_price(client):
    resp = client.get_tickers(category="spot", symbol=SYMBOL)
    return float(resp["result"]["list"][0]["lastPrice"])

def get_klines(client):
    resp = client.get_kline(category="spot", symbol=SYMBOL, interval=TIMEFRAME, limit=LOOKBACK+10)
    rows = resp["result"]["list"]
    df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume","turnover"])
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
        total = 0.0
        for coin in coins:
            log.info(f"💰 COIN: {coin['coin']} | disponivel: {coin.get('availableToWithdraw',0)}")
            name = coin["coin"].upper()
            if "USDT" in name or name == "USD":
                try:
                    total += float(coin.get("availableToWithdraw") or 0)
                except:
                    pass
        return total
    except Exception as e:
        log.error(f"Erro get_balance: {e}")
    return 0.0

def sr_updater(client):
    while True:
        try:
            df = get_klines(client)
            s1, r1 = get_sr_levels(df)
            sr_levels["support"]  = round(s1, 2)
            sr_levels["resistance"] = round(r1, 2)
            state["support"]    = sr_levels["support"]
            state["resistance"] = sr_levels["resistance"]
            log.info(f"📊 S/R | S={s1:.2f} R={r1:.2f}")
        except Exception as e:
            log.error(f"Erro S/R: {e}")
        time.sleep(15 * 60)

def main_loop(client):
    global in_position, entry_price, qty
    bal = 0.0
    tick = 0

    while True:
        try:
            price = get_current_price(client)
            s1    = sr_levels["support"]
            r1    = sr_levels["resistance"]

            if s1 == 0.0 or r1 == 0.0:
                state["price"]       = round(price, 2)
                state["last_update"] = datetime.now().strftime("%H:%M:%S")
                time.sleep(5)
                continue

            sig = get_signal(price, s1, r1)

            # Atualiza saldo a cada 30s
            tick += 1
            if tick >= 6:
                bal = get_balance(client)
                state["balance"] = round(bal, 2)
                tick = 0

            state.update({
                "price":       round(price, 2),
                "signal":      sig,
                "in_position": in_position,
                "entry_price": round(entry_price, 2),
                "last_update": datetime.now().strftime("%H:%M:%S"),
            })

            log.info(f"💲 {price:.2f} | S={s1:.2f} R={r1:.2f} | {sig} | bal={bal:.2f}")

            if in_position and entry_price > 0:
                pnl_pct = (price - entry_price) / entry_price * 100
                state["pnl_live_pct"] = round(pnl_pct, 2)

            with position_lock:
                # COMPRA
                if sig == "BUY" and not in_position and bal >= TRADE_USDT:
                    qty = round(TRADE_USDT / price, 6)
                    client.place_order(category="spot", symbol=SYMBOL, side="Buy", orderType="Market", qty=str(qty))
                    entry_price = price
                    in_position = True
                    state["trades_today"] += 1
                    state["in_position"]   = True
                    state["entry_price"]   = round(entry_price, 2)
                    log.info(f"🟢 COMPRA {qty} @ ${price:.2f}")
                    state["history"].insert(0, {"side":"BUY","price":price,"time":datetime.now().strftime("%H:%M"),"pnl":None})

                # VENDA
                elif in_position:
                    sl      = entry_price * (1 - STOP_PCT)
                    tp      = entry_price * (1 + TP_PCT)
                    pnl_usd = (price - entry_price) * qty
                    if price <= sl or price >= tp or sig == "SELL":
                        client.place_order(category="spot", symbol=SYMBOL, side="Sell", orderType="Market", qty=str(qty))
                        in_position = False
                        state["in_position"] = False
                        state["pnl_today"]   = round(state["pnl_today"] + pnl_usd, 2)
                        if pnl_usd >= 0: state["wins"] += 1
                        else: state["losses"] += 1
                        reason = "TP" if price >= tp else ("SL" if price <= sl else "SELL")
                        log.info(f"🔴 VENDA [{reason}] @ ${price:.2f} | P&L: ${pnl_usd:+.2f}")
                        state["history"].insert(0, {"side":"SELL","price":price,"time":datetime.now().strftime("%H:%M"),"pnl":round(pnl_usd,2)})
                        state["history"] = state["history"][:20]

            time.sleep(5)

        except Exception as e:
            log.error(f"❌ Erro loop: {e}")
            time.sleep(10)

def run_bot():
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

    # Carrega S/R inicial
    try:
        df = get_klines(client)
        s1, r1 = get_sr_levels(df)
        sr_levels["support"]    = round(s1, 2)
        sr_levels["resistance"] = round(r1, 2)
        state["support"]    = sr_levels["support"]
        state["resistance"] = sr_levels["resistance"]
        log.info(f"📊 S/R inicial | S={s1:.2f} R={r1:.2f}")
    except Exception as e:
        log.error(f"Erro S/R inicial: {e}")

    # Carrega saldo inicial
    try:
        bal = get_balance(client)
        state["balance"] = round(bal, 2)
        log.info(f"💰 Saldo inicial: {bal:.2f}")
    except Exception as e:
        log.error(f"Erro saldo inicial: {e}")

    threading.Thread(target=sr_updater, args=(client,), daemon=True).start()
    threading.Thread(target=main_loop, args=(client,), daemon=True).start()

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    log.info(f"🌐 API rodando na porta {PORT}")
    app.run(host="0.0.0.0", port=PORT)
