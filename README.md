# 🤖 TradeBot SR — Suporte & Resistência

Bot de trading automático para Binance com estratégia de Suporte e Resistência.

## 📁 Arquivos

| Arquivo | Descrição |
|---|---|
| `bot.py` | Código principal do bot |
| `requirements.txt` | Dependências Python |
| `Procfile` | Configuração para o Railway |

## ⚙️ Variáveis de Ambiente (Railway)

Configure estas variáveis em **Variables** no Railway:

| Variável | Exemplo | Descrição |
|---|---|---|
| `API_KEY` | `yF0vkeQG...` | Sua API Key da Binance |
| `API_SECRET` | `xxxxx...` | Sua Secret Key da Binance |
| `TESTNET` | `True` | `True` = simulação, `False` = dinheiro real |
| `SYMBOL` | `BTCUSDT` | Par de moedas |
| `TIMEFRAME` | `1h` | Timeframe: 1m, 5m, 15m, 1h, 4h, 1d |
| `TRADE_PCT` | `0.10` | % do saldo por trade (0.10 = 10%) |
| `STOP_PCT` | `0.02` | Stop loss (0.02 = 2%) |
| `TP_PCT` | `0.04` | Take profit (0.04 = 4%) |
| `LOOKBACK` | `20` | Períodos para calcular S/R |

## 🚀 Deploy no Railway

1. Faça fork ou clone este repositório
2. Acesse [railway.app](https://railway.app) e crie um novo projeto
3. Selecione **GitHub Repository** → escolha este repo
4. Vá em **Variables** e adicione todas as variáveis acima
5. O Railway vai detectar o `Procfile` e iniciar o bot automaticamente

## ⚠️ Aviso

Sempre teste com `TESTNET=True` antes de usar dinheiro real.
Trading automatizado envolve risco de perda de capital.
