from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from trader_signal_bot.domain import PortfolioPosition, Signal
from trader_signal_bot.services.state import UserStateStore


class GoogleSheetsStateStore(UserStateStore):
    def __init__(
        self,
        default_risk_per_trade: float,
        service_account_json_path: str,
        spreadsheet_id: str,
    ) -> None:
        super().__init__(default_risk_per_trade)
        self.service_account_json_path = service_account_json_path
        self.spreadsheet_id = spreadsheet_id
        self._session = self._build_session()
        self._load_profiles()

    def backend_name(self) -> str:
        return "google_sheets"

    def persistence_enabled(self) -> bool:
        return True

    def _build_session(self):  # type: ignore[no-untyped-def]
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.service_account import Credentials

        credentials = Credentials.from_service_account_file(
            self.service_account_json_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return AuthorizedSession(credentials)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/{path}"
        response = self._session.request(method, url, timeout=20.0, **kwargs)
        response.raise_for_status()
        return response.json()

    def _load_profiles(self) -> None:
        try:
            payload = self._request("GET", "values/profiles!A2:F")
        except Exception:
            return

        for row in payload.get("values", []):
            if len(row) < 4:
                continue
            chat_id = int(row[0])
            profile = self.get_profile(chat_id)
            profile.risk_per_trade = float(row[1] or self.default_risk_per_trade)
            profile.watchlist = json.loads(row[2]) if row[2] else []
            profile.portfolio = [PortfolioPosition(**item) for item in json.loads(row[3])] if row[3] else []
            if len(row) > 4 and row[4]:
                profile.alert_mode = row[4]

    def _sync_profiles(self) -> None:
        values = [["chat_id", "risk_per_trade", "watchlist_json", "portfolio_json", "alert_mode", "updated_at"]]
        timestamp = datetime.now(timezone.utc).isoformat()
        for chat_id in self.list_chat_ids():
            profile = self.get_profile(chat_id)
            values.append(
                [
                    str(chat_id),
                    str(profile.risk_per_trade),
                    json.dumps(profile.watchlist),
                    json.dumps(
                        [
                            {
                                "ticker": position.ticker,
                                "entry_price": position.entry_price,
                                "size": position.size,
                            }
                            for position in profile.portfolio
                        ]
                    ),
                    profile.alert_mode,
                    timestamp,
                ]
            )
        try:
            self._request(
                "PUT",
                "values/profiles!A1?valueInputOption=RAW",
                json={"range": "profiles!A1", "majorDimension": "ROWS", "values": values},
            )
        except Exception:
            return

    def log_alert(self, chat_id: int, signal: Signal) -> None:
        try:
            self._request(
                "POST",
                "values/alerts!A1:append?valueInputOption=RAW",
                json={
                    "values": [[
                        datetime.now(timezone.utc).isoformat(),
                        str(chat_id),
                        signal.ticker,
                        signal.side.value,
                        str(signal.confidence),
                        signal.market_session,
                        signal.signal_quality,
                    ]]
                },
            )
        except Exception:
            return

    def add_watchlist(self, chat_id: int, ticker: str):
        profile = super().add_watchlist(chat_id, ticker)
        self._sync_profiles()
        return profile

    def set_watchlist(self, chat_id: int, tickers: list[str]):
        profile = super().set_watchlist(chat_id, tickers)
        self._sync_profiles()
        return profile

    def remove_watchlist(self, chat_id: int, ticker: str):
        profile = super().remove_watchlist(chat_id, ticker)
        self._sync_profiles()
        return profile

    def clear_watchlist(self, chat_id: int):
        profile = super().clear_watchlist(chat_id)
        self._sync_profiles()
        return profile

    def add_portfolio_position(self, chat_id: int, ticker: str, entry_price: float, size: float):
        profile = super().add_portfolio_position(chat_id, ticker, entry_price, size)
        self._sync_profiles()
        return profile

    def remove_portfolio_position(self, chat_id: int, ticker: str):
        profile = super().remove_portfolio_position(chat_id, ticker)
        self._sync_profiles()
        return profile

    def set_alert_mode(self, chat_id: int, mode: str):
        profile = super().set_alert_mode(chat_id, mode)
        self._sync_profiles()
        return profile
