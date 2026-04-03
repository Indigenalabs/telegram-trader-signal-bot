from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentProfile:
    ticker: str
    display_name: str
    news_query: str


_REGISTRY: dict[str, InstrumentProfile] = {
    "AAPL": InstrumentProfile("AAPL", "Apple", "Apple OR iPhone OR AAPL"),
    "MSFT": InstrumentProfile("MSFT", "Microsoft", "Microsoft OR Azure OR MSFT"),
    "NVDA": InstrumentProfile("NVDA", "NVIDIA", "NVIDIA OR AI chips OR NVDA"),
    "AMZN": InstrumentProfile("AMZN", "Amazon", "Amazon OR AWS OR AMZN"),
    "META": InstrumentProfile("META", "Meta Platforms", "Meta Platforms OR Facebook OR Instagram OR META"),
    "GOOGL": InstrumentProfile("GOOGL", "Alphabet", "Alphabet OR Google OR YouTube OR GOOGL"),
    "TSLA": InstrumentProfile("TSLA", "Tesla", "Tesla OR EV deliveries OR TSLA"),
    "SPY": InstrumentProfile("SPY", "SPDR S&P 500 ETF", "S&P 500 OR SPY ETF"),
    "QQQ": InstrumentProfile("QQQ", "Invesco QQQ", "Nasdaq 100 OR QQQ ETF"),
    "DIA": InstrumentProfile("DIA", "SPDR Dow Jones ETF", "Dow Jones OR DIA ETF"),
    "IWM": InstrumentProfile("IWM", "iShares Russell 2000 ETF", "Russell 2000 OR IWM ETF"),
    "EURUSD=X": InstrumentProfile("EURUSD=X", "EUR/USD", "EUR USD OR euro dollar OR ECB OR Federal Reserve"),
    "GBPUSD=X": InstrumentProfile("GBPUSD=X", "GBP/USD", "GBP USD OR pound dollar OR BOE OR Federal Reserve"),
    "USDJPY=X": InstrumentProfile("USDJPY=X", "USD/JPY", "USD JPY OR yen dollar OR BOJ OR Federal Reserve"),
    "AUDUSD=X": InstrumentProfile("AUDUSD=X", "AUD/USD", "AUD USD OR Australian dollar OR RBA"),
    "USDCAD=X": InstrumentProfile("USDCAD=X", "USD/CAD", "USD CAD OR Canadian dollar OR Bank of Canada"),
    "USDCHF=X": InstrumentProfile("USDCHF=X", "USD/CHF", "USD CHF OR Swiss franc OR SNB"),
    "GC=F": InstrumentProfile("GC=F", "Gold Futures", "gold price OR gold futures OR Federal Reserve OR yields"),
    "SI=F": InstrumentProfile("SI=F", "Silver Futures", "silver price OR silver futures OR industrial metals"),
    "GLD": InstrumentProfile("GLD", "SPDR Gold Shares", "gold ETF OR GLD OR gold price"),
    "SLV": InstrumentProfile("SLV", "iShares Silver Trust", "silver ETF OR SLV OR silver price"),
    "CL=F": InstrumentProfile("CL=F", "WTI Crude Oil Futures", "WTI crude OR oil futures OR OPEC OR inventories"),
    "BZ=F": InstrumentProfile("BZ=F", "Brent Crude Futures", "Brent crude OR oil futures OR OPEC"),
    "NG=F": InstrumentProfile("NG=F", "Natural Gas Futures", "natural gas OR Henry Hub OR gas inventories"),
    "BTC-USD": InstrumentProfile("BTC-USD", "Bitcoin", "Bitcoin OR BTC OR crypto market"),
    "ETH-USD": InstrumentProfile("ETH-USD", "Ethereum", "Ethereum OR ETH OR crypto market"),
    "SOL-USD": InstrumentProfile("SOL-USD", "Solana", "Solana OR SOL crypto"),
    "BNB-USD": InstrumentProfile("BNB-USD", "BNB", "BNB OR Binance Coin"),
    "XRP-USD": InstrumentProfile("XRP-USD", "XRP", "XRP OR Ripple"),
    "ADA-USD": InstrumentProfile("ADA-USD", "Cardano", "Cardano OR ADA crypto"),
    "DOGE-USD": InstrumentProfile("DOGE-USD", "Dogecoin", "Dogecoin OR DOGE crypto"),
}


def get_instrument_profile(ticker: str) -> InstrumentProfile:
    normalized = ticker.upper()
    if normalized in _REGISTRY:
        return _REGISTRY[normalized]
    if "-" in normalized:
        base = normalized.split("-")[0]
        return InstrumentProfile(normalized, normalized, f"{base} OR {normalized}")
    return InstrumentProfile(normalized, normalized, normalized)
