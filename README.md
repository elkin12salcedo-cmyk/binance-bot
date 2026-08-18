# Binance Signal Bot

Bot de señales para Binance. No coloca órdenes.

## Funciones
- RSI
- EMA 9 / EMA 21
- MACD
- Filtro de volumen
- Señales BUY/SELL para Spot
- Señales LONG/SHORT para Futures
- Stop Loss y TP1/TP2/TP3 para Futures
- Envío de señales a Telegram
- Endpoint HTTP para mantener el servicio activo en Render

## Variables de Render
- `TELEGRAM_BOT_TOKEN` = token de tu bot de Telegram
- `TELEGRAM_CHAT_ID` = chat donde recibirás las señales
- `MODE` = `futures` o `spot`
- `SYMBOLS` = por ejemplo `BTCUSDT,ETHUSDT`
- `INTERVAL` = por ejemplo `15m`
- `CHECK_SECONDS` = `60`

## Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`gunicorn bot:app`

Importante: este proyecto es únicamente de señales. No usa claves API de Binance y no ejecuta operaciones.
