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

# Flask app
app = Flask(__name__)
CORS(app)

@app.route("/status")
def get_status():
    return jsonify(state)

@app.route("/health")
def health():
    return jsonify({"ok": True})

# ── BOT ─────────────────────────────────────────────────
def bot_loop():
    log.info("🤖 bot_loop() iniciado")
    
    if not API_KEY or not API_SECRET:
        state["status"] = "erro: sem credenciais"
        log.error("❌ API_KEY ou API_SECRET não configurados!")
        return

    try:
        client = HTTP(testnet=TESTNET, api_key=API_KEY, api_secret=API_SECRET)
        client.get_server_time()
        log.info(f"✅ Conectado {'TESTNET' if TESTNET else 'REAL'} — {SYMBOL}")
        state["status"] = "rodando"
    except Exception as e:
        log.error(f"❌ Erro conexão: {e}")
        state["status"] = f"erro: {e}"
        return

    support    = 0.0
    resistance = 0.0
    in_position = False
    entry_price = 0.0
    qty         = 0.0
    sr_tick     = 0
    bal_tick    = 0
    bal         = 0.0

    while True:
        try:
            log.info("🔄 tick...")

            # Atualiza S/R a cada 15min (180 ticks de 5s)
            sr_tick += 1
            if sr_tick >= 180 or support == 0.0:
                resp = client.get_kline(category="spot", symbol=SYMBOL, interval="15", limit=LOOKBACK+5)
                rows = resp["result"]["list"]
                df = pd.DataFrame(rows, columns=["time","open","high","low","close","volume","turnover"])
                df["high"]  = df["high"].astype(float)
                df["low"]   = df["low"].astype(float)
                support    = round(df["low"].astype(float).rolling(LOOKBACK).min().iloc[-1], 2)
                resistance = round(df["high"].astype(float).rolling(LOOKBACK).max().iloc[-1], 2)
                state["support"]    = support
                state["resistance"] = resistance
                sr_tick = 0
                log.info(f"📊 S/R | S={support} R={resistance}")

            # Atualiza saldo a cada 30s (6 ticks)
            bal_tick += 1
            if bal_tick >= 6:
                try:
                    resp2 = client.get_wallet_balance(accountType="UNIFIED")
                    coins = resp2["result"]["list"][0]["coin"]
                    bal = 0.0
                    for coin in coins:
                        log.info(f"💰 {coin['coin']}: {coin.get('availableToWithdraw',0)}")
                        if "USDT" in coin["coin"].upper():
                            bal += float(coin.get("availableToWithdraw") or 0)
                    state["balance"] = round(bal, 2)
                    bal_tick = 0
                except Exception as e:
                    log.error(f"Erro saldo: {e}")

            # Preço atual
            resp3  = client.get_tickers(category="spot", symbol=SYMBOL)
            price  = float(resp3["result"]["list"][0]["lastPrice"])

            # Sinal
            zone = price * TOLERANCE
            if abs(price - support) <= zone:
                sig = "BUY"
            elif abs(price - resistance) <= zone:
                sig = "SELL"
            else:
                sig = "HOLD"

            state.update({
                "price":       round(price, 2),
                "signal":      sig,
                "in_position": in_position,
                "entry_price": round(entry_price, 2),
                "last_update": datetime.now().strftime("%H:%M:%S"),
            })

            log.info(f"💲 {price:.2f} | S={support} R={resistance} | {sig} | bal={bal:.2f}")

            # P&L ao vivo
            if in_position and entry_price > 0:
                state["pnl_live_pct"] = round((price - entry_price) / entry_price * 100, 2)

            # COMPRA
            if sig == "BUY" and not in_position and bal >= TRADE_USDT:
                qty = round(TRADE_USDT / price, 6)
                client.place_order(category="spot", symbol=SYMBOL, side="Buy", orderType="Market", qty=str(qty))
                entry_price = price
                in_position = True
                state["trades_today"] += 1
                log.info(f"🟢 COMPRA {qty} @ ${price:.2f}")
                state["history"].insert(0, {"side":"BUY","price":price,"time":datetime.now().strftime("%H:%M"),"pnl":None})

            # VENDA
            elif in_position:
                sl = entry_price * (1 - STOP_PCT)
                tp = entry_price * (1 + TP_PCT)
                pnl_usd = (price - entry_price) * qty
                if price <= sl or price >= tp or sig == "SELL":
                    client.place_order(category="spot", symbol=SYMBOL, side="Sell", orderType="Market", qty=str(qty))
                    in_position = False
                    state["in_position"] = False
                    state["pnl_today"] = round(state["pnl_today"] + pnl_usd, 2)
                    if pnl_usd >= 0: state["wins"] += 1
                    else: state["losses"] += 1
                    reason = "TP" if price >= tp else ("SL" if price <= sl else "SELL")
                    log.info(f"🔴 VENDA [{reason}] @ ${price:.2f} | P&L: ${pnl_usd:+.2f}")
                    state["history"].insert(0, {"side":"SELL","price":price,"time":datetime.now().strftime("%H:%M"),"pnl":round(pnl_usd,2)})
                    state["history"] = state["history"][:20]

        except Exception as e:
            log.error(f"❌ Erro: {e}")

        time.sleep(5)

# Inicia bot em thread antes do Flask
log.info("🚀 Iniciando thread do bot...")
t = threading.Thread(target=bot_loop, daemon=True)
t.start()
log.info("🚀 Thread iniciada!")

if __name__ == "__main__":
    log.info(f"🌐 Flask na porta {PORT}")
    app.run(host="0.0.0.0", port=PORT)
