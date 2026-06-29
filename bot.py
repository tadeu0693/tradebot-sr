import os
import time
import logging
import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ── CONFIG (via variáveis de ambiente no Railway) ────────
API_KEY    = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
TESTNET    = os.environ.get("TESTNET", "True") == "True"
SYMBOL     = os.environ.get("SYMBOL", "BTCUSDT")
TIMEFRAME  = os.environ.get("TIMEFRAME", "1h")
TRADE_PCT  = float(os.environ.get("TRADE_PCT", "0.10"))   # 10% do saldo
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

INTERVAL_MAP = {
    "1m":  Client.KLINE_INTERVAL_1MINUTE,
    "5m":  Client.KLINE_INTERVAL_5MINUTE,
    "15m": Client.KLINE_INTERVAL_15MINUTE,
    "1h":  Client.KLINE_INTERVAL_1HOUR,
    "4h":  Client.KLINE_INTERVAL_4HOUR,
    "1d":  Client.KLINE_INTERVAL_1DAY,
}
SLEEP_MAP = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400
}

def get_client():
    client = Client(API_KEY, API_SECRET, testnet=TESTNET)
    log.info(f"Conectado {'TESTNET' if TESTNET else 'REAL'} — {SYMBOL}")
    return client

def get_klines(client, interval):
    klines = client.get_klines(
        symbol=SYMBOL,
        interval=interval,
        limit=LOOKBACK + 10
    )
    df = pd.DataFrame(klines, columns=[
        'time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_vol', 'trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    return df

def get_sr_levels(df):
    """Detecta suporte e resistência por máximas/mínimas do período."""
    highs = df['high'].rolling(LOOKBACK).max()
    lows  = df['low'].rolling(LOOKBACK).min()
    resistance = highs.iloc[-1]
    support    = lows.iloc[-1]

    # Níveis intermediários (média dos últimos picos/vales)
    r2 = highs.nlargest(3).mean()
    s2 = lows.nsmallest(3).mean()

    return support, resistance, s2, r2

def get_signal(price, support, resistance):
    """BUY perto do suporte, SELL perto da resistência."""
    zone = price * TOLERANCE
    if abs(price - support) <= zone:
        return "BUY"
    elif abs(price - resistance) <= zone:
        return "SELL"
    return "HOLD"

def get_balance(client, asset="USDT"):
    bal = client.get_asset_balance(asset=asset)
    return float(bal['free'])

def get_qty(client, price):
    """Calcula quantidade baseada no % do saldo."""
    balance = get_balance(client)
    usdt_to_use = balance * TRADE_PCT
    qty = usdt_to_use / price
    # Arredonda para 5 casas decimais (BTC)
    qty = round(qty, 5)
    return qty

def run_bot():
    if not API_KEY or not API_SECRET:
        log.error("❌ API_KEY ou API_SECRET não configurados!")
        log.error("Configure as variáveis de ambiente no Railway.")
        return

    client   = get_client()
    interval = INTERVAL_MAP.get(TIMEFRAME, Client.KLINE_INTERVAL_1HOUR)
    sleep_s  = SLEEP_MAP.get(TIMEFRAME, 3600)

    in_position = False
    entry_price = 0.0
    qty         = 0.0
    trade_count = 0
    wins        = 0

    log.info(f"🤖 Bot iniciado | Par: {SYMBOL} | TF: {TIMEFRAME}")
    log.info(f"   Stop: {STOP_PCT*100}% | TP: {TP_PCT*100}% | Capital/trade: {TRADE_PCT*100}%")

    while True:
        try:
            df    = get_klines(client, interval)
            price = float(df['close'].iloc[-1])
            s1, r1, s2, r2 = get_sr_levels(df)
            sig   = get_signal(price, s1, r1)

            log.info(
                f"💲 {price:.2f} | S1={s1:.2f} R1={r1:.2f} | "
                f"Sinal: {sig} | Posição: {'✅ ABERTA' if in_position else '—'}"
            )

            # ── ABRIR POSIÇÃO ────────────────────────────
            if sig == "BUY" and not in_position:
                qty = get_qty(client, price)
                if qty <= 0:
                    log.warning("⚠️ Saldo insuficiente para abrir posição.")
                else:
                    client.order_market_buy(symbol=SYMBOL, quantity=qty)
                    entry_price = price
                    in_position = True
                    trade_count += 1
                    log.info(f"🟢 COMPRA #{trade_count} | {qty} {SYMBOL} @ ${price:.2f}")

            # ── GERENCIAR POSIÇÃO ABERTA ─────────────────
            elif in_position:
                stop_price = entry_price * (1 - STOP_PCT)
                tp_price   = entry_price * (1 + TP_PCT)
                pnl_pct    = (price - entry_price) / entry_price * 100

                if price <= stop_price:
                    client.order_market_sell(symbol=SYMBOL, quantity=qty)
                    in_position = False
                    log.warning(
                        f"🛑 STOP LOSS | Vendeu @ ${price:.2f} | "
                        f"P&L: {pnl_pct:.2f}%"
                    )

                elif price >= tp_price or sig == "SELL":
                    client.order_market_sell(symbol=SYMBOL, quantity=qty)
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

        except BinanceAPIException as e:
            log.error(f"❌ Erro Binance API: {e}")
            time.sleep(30)

        except Exception as e:
            log.error(f"❌ Erro inesperado: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run_bot()
