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

## Hetzner Deploy

Recommended target:
- Ubuntu 24.04 VPS
- 1 vCPU / 2 GB RAM is enough for this bot

One-time server flow:

```bash
sudo apt-get update
sudo apt-get install -y git
sudo mkdir -p /opt
cd /opt
sudo git clone https://github.com/Indigenalabs/telegram-trader-signal-bot.git
sudo bash /opt/telegram-trader-signal-bot/deploy/hetzner/setup.sh
```

Then create the environment file:

```bash
cd /opt/telegram-trader-signal-bot
sudo cp .env.example .env
sudo nano .env
```

At minimum set:
- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_CHAT_IDS`
- `NEWSAPI_KEY`
- `BINANCE_API_KEY`

Install the service:

```bash
sudo cp /opt/telegram-trader-signal-bot/deploy/hetzner/telegram-trader-signal-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-trader-signal-bot
sudo systemctl start telegram-trader-signal-bot
sudo systemctl status telegram-trader-signal-bot
```

Useful operations:

```bash
sudo journalctl -u telegram-trader-signal-bot -f
sudo systemctl restart telegram-trader-signal-bot
cd /opt/telegram-trader-signal-bot && sudo git pull
sudo systemctl restart telegram-trader-signal-bot
```

Persistent learning data will live in:
- `/opt/telegram-trader-signal-bot/data/trade_history.json`
- `/opt/telegram-trader-signal-bot/data/learning_model.json`

Persistent bot state now defaults to SQLite:
- `/opt/telegram-trader-signal-bot/data/bot_state.db`

That database stores:
- watchlists
- portfolios
- alert mode
- tracked live trade lifecycle state

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
