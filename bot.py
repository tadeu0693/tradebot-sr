import os
import time
import logging
import pandas as pd
from pybit.unified_trading import HTTP

# ── CONFIG (variáveis de ambiente no Railway) ────────────
API_KEY    = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
TESTNET    = os.environ.get("TESTNET", "True") == "True"
SYMBOL     = os.environ.get("SYMBOL", "BTCUSDT")
TIMEFRAME  = os.environ.get("TIMEFRAME", "60")   # minutos: 1,5,15,60,240,D
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))    # valor fixo em USDT
STOP_PCT   = float(os.environ.get("STOP_PCT", "0.02"))    # 2% stop loss
TP_PCT     = float(os.environ.get("TP_PCT", "0.04"))      # 4% take profit
LOOKBACK   = int(os.environ.get("LOOKBACK", "20"))        # períodos SR
TOLERANCE  = float(os.environ.get("TOLERANCE", "0.005"))  # 0.5% zona SR

# ── LOGGING ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

SLEEP_MAP = {
    "1": 60, "5": 300, "15": 900,
    "60": 3600, "240": 14400, "D": 86400
}

def get_client():
    client = HTTP(
        testnet=TESTNET,
        api_key=API_KEY,
        api_secret=API_SECRET,
    )
    # Testa conexão
    client.get_server_time()
    log.info(f"✅ Conectado {'TESTNET' if TESTNET else 'REAL'} — Bybit | {SYMBOL}")
    return client

def get_klines(client):
    resp = client.get_kline(
        category="spot",
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=LOOKBACK + 10
    )
    rows = resp["result"]["list"]
    df = pd.DataFrame(rows, columns=[
        "time", "open", "high", "low", "close", "volume", "turnover"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    # Bybit retorna do mais recente para o mais antigo — inverte
    df = df.iloc[::-1].reset_index(drop=True)
    return df

def get_sr_levels(df):
    highs = df["high"].rolling(LOOKBACK).max()
    lows  = df["low"].rolling(LOOKBACK).min()
    resistance = highs.iloc[-1]
    support    = lows.iloc[-1]
    return support, resistance

def get_signal(price, support, resistance):
    zone = price * TOLERANCE
    if abs(price - support) <= zone:
        return "BUY"
    elif abs(price - resistance) <= zone:
        return "SELL"
    return "HOLD"

def get_balance(client):
    resp = client.get_wallet_balance(accountType="UNIFIED")
    coins = resp["result"]["list"][0]["coin"]
    for coin in coins:
        if coin["coin"] == "USDT":
            return float(coin["availableToWithdraw"])
    return 0.0

def calc_qty(price):
    qty = TRADE_USDT / price
    # Arredonda para 6 casas decimais
    return round(qty, 6)

def run_bot():
    if not API_KEY or not API_SECRET:
        log.error("❌ API_KEY ou API_SECRET não configurados!")
        log.error("Configure as variáveis de ambiente no Railway.")
        return

    client      = get_client()
    sleep_s     = SLEEP_MAP.get(TIMEFRAME, 3600)
    in_position = False
    entry_price = 0.0
    qty         = 0.0
    trade_count = 0
    wins        = 0

    log.info(f"🤖 Bot iniciado | Par: {SYMBOL} | TF: {TIMEFRAME}min")
    log.info(f"   Stop: {STOP_PCT*100}% | TP: {TP_PCT*100}% | Capital/trade: ${TRADE_USDT}")

    while True:
        try:
            df    = get_klines(client)
            price = float(df["close"].iloc[-1])
            s1, r1 = get_sr_levels(df)
            sig    = get_signal(price, s1, r1)

            log.info(
                f"💲 {price:.2f} | S1={s1:.2f} R1={r1:.2f} | "
                f"Sinal: {sig} | Posição: {'✅ ABERTA' if in_position else '—'}"
            )

            # ── ABRIR COMPRA ─────────────────────────────
            if sig == "BUY" and not in_position:
                balance = get_balance(client)
                if balance < TRADE_USDT:
                    log.warning(f"⚠️ Saldo insuficiente: ${balance:.2f} < ${TRADE_USDT}")
                else:
                    qty = calc_qty(price)
                    client.place_order(
                        category="spot",
                        symbol=SYMBOL,
                        side="Buy",
                        orderType="Market",
                        qty=str(qty)
                    )
                    entry_price = price
                    in_position = True
                    trade_count += 1
                    log.info(f"🟢 COMPRA #{trade_count} | {qty} {SYMBOL} @ ${price:.2f}")

            # ── GERENCIAR POSIÇÃO ────────────────────────
            elif in_position:
                stop_price = entry_price * (1 - STOP_PCT)
                tp_price   = entry_price * (1 + TP_PCT)
                pnl_pct    = (price - entry_price) / entry_price * 100

                if price <= stop_price:
                    client.place_order(
                        category="spot",
                        symbol=SYMBOL,
                        side="Sell",
                        orderType="Market",
                        qty=str(qty)
                    )
                    in_position = False
                    log.warning(
                        f"🛑 STOP LOSS | Vendeu @ ${price:.2f} | "
                        f"P&L: {pnl_pct:.2f}%"
                    )

                elif price >= tp_price or sig == "SELL":
                    client.place_order(
                        category="spot",
                        symbol=SYMBOL,
                        side="Sell",
                        orderType="Market",
                        qty=str(qty)
                    )
                    in_position = False
                    wins += 1
                    reason = "TAKE PROFIT" if price >= tp_price else "RESISTÊNCIA"
                    log.info(
                        f"🔴 {reason} | Vendeu @ ${price:.2f} | "
                        f"P&L: +{pnl_pct:.2f}% | "
                        f"Win rate: {wins}/{trade_count}"
                    )
                else:
                    log.info(
                        f"   Aguardando... TP=${tp_price:.2f} "
                        f"SL=${stop_price:.2f} | P&L atual: {pnl_pct:+.2f}%"
                    )

            time.sleep(sleep_s)

        except Exception as e:
            log.error(f"❌ Erro: {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_bot()
