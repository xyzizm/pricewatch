"""
Wiring.

This module is the only place that knows about all three layers at once.
Everything here is deliberately thin — the interesting logic lives in
rules.py, and that is where changes usually belong.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .notifiers import Notifier, build_notifier
from .providers import PriceProvider, build_provider
from .rules import Alert, Rule, build_rule
from .store import JsonStore, Store

MIN_INTERVAL_SECONDS = 15  # protects the free public APIs from being hammered


@dataclass
class Watch:
    """One symbol, watched by one provider, against one or more rules."""

    symbol: str
    provider: PriceProvider
    rules: Sequence[Rule]
    label: str = ""

    @property
    def display(self) -> str:
        return self.label or self.symbol

    @property
    def state_key(self) -> str:
        # Provider is part of the key so the same symbol on two exchanges
        # keeps two independent histories.
        return f"{self.provider.name}:{self.symbol}"


def check_watch(watch: Watch, store: Store) -> List[Alert]:
    """
    Fetch one price, run every rule against it, update state.

    Returns the alerts that fired. Does not send them — sending is the
    caller's job, which keeps this function easy to test.

    A provider that raises is treated the same as one that returns None:
    this watch is skipped, the rest keep working.
    """
    try:
        price = watch.provider.fetch(watch.symbol)
    except Exception as exc:
        print(f"! {watch.display}: provider raised {type(exc).__name__}: {exc}")
        return []

    if price is None:
        return []  # provider already logged the reason

    previous = store.get(watch.state_key)
    alerts = [
        alert
        for rule in watch.rules
        if (alert := rule.evaluate(watch.display, price, previous)) is not None
    ]

    store.set(watch.state_key, price)
    return alerts


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        raise SystemExit(
            f"Config not found: {path}\n"
            "Copy config.example.json to config.json and edit it."
        )
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_watches(config: dict) -> List[Watch]:
    """Turn the config dict into Watch objects, failing loudly on bad input."""
    entries = config.get("watches")
    if not isinstance(entries, list) or not entries:
        raise SystemExit("Config field 'watches' must be a non-empty list.")

    watches: List[Watch] = []
    for index, entry in enumerate(entries):
        where = f"watches[{index}]"
        try:
            symbol = entry["symbol"]
            provider_name = entry.get("provider", "coingecko")
            rule_specs = entry["rules"]
        except (KeyError, TypeError) as exc:
            raise SystemExit(f"{where}: missing required field {exc}") from None

        if not isinstance(rule_specs, list) or not rule_specs:
            raise SystemExit(f"{where}: 'rules' must be a non-empty list")

        try:
            provider = build_provider(
                provider_name, **entry.get("provider_options", {})
            )
            rules = [build_rule(spec) for spec in rule_specs]
        except ValueError as exc:
            raise SystemExit(f"{where}: {exc}") from None

        watches.append(
            Watch(
                symbol=symbol,
                provider=provider,
                rules=rules,
                label=entry.get("label", ""),
            )
        )
    return watches


def build_notifiers(config: dict) -> List[Notifier]:
    specs = config.get("notifiers") or [{"type": "console"}]
    try:
        return [build_notifier(spec) for spec in specs]
    except ValueError as exc:
        raise SystemExit(f"notifiers: {exc}") from None


def deliver(alert: Alert, notifiers: Sequence[Notifier]) -> int:
    """
    Send one alert to every notifier. Returns how many succeeded.

    Each notifier is isolated: a channel that is down must not stop the
    others from receiving the alert.
    """
    delivered = 0
    for notifier in notifiers:
        try:
            if notifier.send(alert):
                delivered += 1
        except Exception as exc:
            name = getattr(notifier, "name", type(notifier).__name__)
            print(f"! notifier '{name}' raised {type(exc).__name__}: {exc}")
    return delivered


def run_once(
    watches: Sequence[Watch], notifiers: Sequence[Notifier], store: Store
) -> int:
    """
    One full pass over every watch. Returns how many alerts were delivered.

    Every watch is isolated, so a single broken symbol or dead API cannot
    silence the rest of the monitor.
    """
    delivered = 0
    for watch in watches:
        try:
            alerts = check_watch(watch, store)
        except Exception as exc:
            print(f"! {watch.display}: check failed — {type(exc).__name__}: {exc}")
            continue

        for alert in alerts:
            delivered += deliver(alert, notifiers)

    try:
        store.save()
    except Exception as exc:
        print(f"! could not persist state: {exc}")
    return delivered


def run_forever(config_path: str, state_path: str) -> None:
    config = load_config(config_path)
    watches = build_watches(config)
    notifiers = build_notifiers(config)
    store = JsonStore(state_path)

    interval = max(int(config.get("interval_seconds", 60)), MIN_INTERVAL_SECONDS)

    print(
        f"PriceWatch started — {len(watches)} watch(es), "
        f"{len(notifiers)} notifier(s), every {interval}s"
    )
    print("Ctrl+C to stop.\n")

    while True:
        try:
            sent = run_once(watches, notifiers, store)
            if sent == 0:
                print(f"[{time.strftime('%H:%M:%S')}] no alerts")
        except KeyboardInterrupt:
            print("\nStopped.")
            store.save()
            return
        except Exception as exc:  # one bad cycle must not kill the watcher
            print(f"! unexpected error: {exc}")

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            store.save()
            return
