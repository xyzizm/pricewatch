"""
Price sources.

Adding a new exchange or API means writing one class that implements
`fetch(symbol) -> float | None` and registering it in PROVIDERS.
Nothing else in the codebase needs to change.
"""

from __future__ import annotations

from typing import Dict, Optional, Protocol

import requests

DEFAULT_TIMEOUT = 20


class PriceProvider(Protocol):
    """Anything that can turn a symbol into a current price."""

    name: str

    def fetch(self, symbol: str) -> Optional[float]:
        """Return the current price, or None if it could not be retrieved."""
        ...


class CoinGeckoProvider:
    """
    Public CoinGecko API. No API key required.

    `symbol` is a CoinGecko coin id, e.g. "bitcoin", "solana", "ethereum".
    """

    name = "coingecko"
    URL = "https://api.coingecko.com/api/v3/simple/price"

    def __init__(self, vs_currency: str = "usd", timeout: int = DEFAULT_TIMEOUT):
        self.vs_currency = vs_currency
        self.timeout = timeout

    def fetch(self, symbol: str) -> Optional[float]:
        try:
            response = requests.get(
                self.URL,
                params={"ids": symbol, "vs_currencies": self.vs_currency},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"! {self.name}: request failed for {symbol}: {exc}")
            return None

        try:
            return float(payload[symbol][self.vs_currency])
        except (KeyError, TypeError, ValueError):
            print(f"! {self.name}: unexpected response shape for {symbol}")
            return None


class BinanceProvider:
    """
    Public Binance ticker. No API key required.

    `symbol` is a trading pair, e.g. "BTCUSDT", "SOLUSDT".
    """

    name = "binance"
    URL = "https://api.binance.com/api/v3/ticker/price"

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout

    def fetch(self, symbol: str) -> Optional[float]:
        try:
            response = requests.get(
                self.URL, params={"symbol": symbol}, timeout=self.timeout
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"! {self.name}: request failed for {symbol}: {exc}")
            return None

        try:
            return float(payload["price"])
        except (KeyError, TypeError, ValueError):
            print(f"! {self.name}: unexpected response shape for {symbol}")
            return None


# The registry. Adding a provider = add a line here.
PROVIDERS: Dict[str, type] = {
    CoinGeckoProvider.name: CoinGeckoProvider,
    BinanceProvider.name: BinanceProvider,
}


def build_provider(name: str, **kwargs) -> PriceProvider:
    """Create a provider by name. Raises ValueError on unknown names."""
    try:
        provider_class = PROVIDERS[name]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown provider '{name}'. Available: {known}") from None
    return provider_class(**kwargs)
