# Ultimate Trader Signal Bot

Production-leaning Python scaffold for a Telegram trading signal bot. This first version implements:

- Telegram bot commands via `python-telegram-bot`
- Multi-asset ticker normalization for stocks, forex, crypto, futures, ETFs, and staking-style yield ideas
- Live market snapshots via Yahoo Finance
- Modular analysis engine for technical, sentiment, macro, and risk scoring
- Weighted signal generation with entries, stops, targets, rationale, and confidence
- Daily multiverse gameplan generation and optional scheduled delivery

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies:

```powershell
pip install -e .
```

3. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN`.
4. Start the bot:

```powershell
python -m trader_signal_bot.app
```

Or on Windows PowerShell:

```powershell
.\run_bot.ps1
```

## Commands

- `/start`
- `/signals BTC-USD`
- `/scan`
- `/scan stocks`
- `/scan forex`
- `/scan metals`
- `/scan energy`
- `/scan BTC-USD ETH-USD SOL-USD`
- `/gameplan`
- `/analyze SPY`
- `/watchlist add BTC-USD ETH-USD SOL-USD`
- `/watchlist set BTC-USD ETH-USD SPY QQQ`
- `/watchlist clear`
- `/portfolio add BTC-USD 42000 0.25`
- `/risk`
- `/settings`

## Disclaimer

This software is for research and informational use. It is not financial advice. Trading and investing involve risk of loss.
