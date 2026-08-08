"""
Price sources.

Adding a new exchange or API means writing one class that implements
`fetch(symbol) -> float | None` and registering it in PROVIDERS.
Nothing else in the codebase needs to change.

A provider may also implement `fetch_many(symbols)` when its API can price
several symbols in one request. Watching ten coins one request at a time is
what puts a free API tier over its rate limit; batching turns that cycle into
a single call. Providers that cannot batch inherit a loop and behave exactly
as before.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Protocol

import requests

DEFAULT_TIMEOUT = 20


def _rate_limit_note(response, name: str) -> str:
    retry_after = response.headers.get("Retry-After")
    when = f" Retry-After: {retry_after}s." if retry_after else ""
    return (
        f"! {name}: rate limited (HTTP {response.status_code}).{when} "
        f"Raise interval_seconds, watch fewer symbols, or use a keyed plan."
    )


class PriceProvider(Protocol):
    """Anything that can turn a symbol into a current price."""

    name: str

    def fetch(self, symbol: str) -> Optional[float]:
        """Return the current price, or None if it could not be retrieved."""
        ...

    def fetch_many(self, symbols: Iterable[str]) -> Dict[str, Optional[float]]:
        """Price several symbols at once. Falls back to one call per symbol."""
        ...


class _OneAtATime:
    """Default `fetch_many` for APIs that price a single symbol per request."""

    def fetch_many(self, symbols: Iterable[str]) -> Dict[str, Optional[float]]:
        return {symbol: self.fetch(symbol) for symbol in symbols}


class CoinGeckoProvider:
    """
    Public CoinGecko API. No API key required.

    `symbol` is a CoinGecko coin id, e.g. "bitcoin", "solana", "ethereum".

    The endpoint accepts comma-separated ids, so any number of coins costs one
    request. That matters: the free tier allows roughly 10-30 calls a minute,
    which a per-symbol loop exhausts at ten coins on a one-minute interval.
    """

    name = "coingecko"
    URL = "https://api.coingecko.com/api/v3/simple/price"

    def __init__(self, vs_currency: str = "usd", timeout: int = DEFAULT_TIMEOUT):
        self.vs_currency = vs_currency
        self.timeout = timeout

    def fetch(self, symbol: str) -> Optional[float]:
        return self.fetch_many([symbol]).get(symbol)

    def fetch_many(self, symbols: Iterable[str]) -> Dict[str, Optional[float]]:
        wanted = list(dict.fromkeys(symbols))  # dedupe, keep order
        if not wanted:
            return {}
        blank: Dict[str, Optional[float]] = {symbol: None for symbol in wanted}

        try:
            response = requests.get(
                self.URL,
                params={
                    "ids": ",".join(wanted),
                    "vs_currencies": self.vs_currency,
                },
                timeout=self.timeout,
            )
            if response.status_code == 429:
                print(_rate_limit_note(response, self.name))
                return blank
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"! {self.name}: request failed for "
                  f"{', '.join(wanted[:5])}{'...' if len(wanted) > 5 else ''}: {exc}")
            return blank

        prices = dict(blank)
        for symbol in wanted:
            try:
                prices[symbol] = float(payload[symbol][self.vs_currency])
            except (KeyError, TypeError, ValueError):
                # One unknown coin id must not blank out the whole batch.
                print(f"! {self.name}: no {self.vs_currency} price for '{symbol}' "
                      f"— check the coin id at https://api.coingecko.com/api/v3/coins/list")
        return prices


class BinanceProvider(_OneAtATime):
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
            if response.status_code in (418, 429):
                print(_rate_limit_note(response, self.name))
                return None
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
