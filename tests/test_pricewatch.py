"""
Tests for PriceWatch.

Run with:  python3 -m pytest tests/ -v
      or:  python3 tests/test_pricewatch.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pricewatch.app import (
    Watch,
    build_notifiers,
    build_watches,
    check_watch,
    deliver,
    fetch_prices,
    run_once,
)
from pricewatch.notifiers import ConsoleNotifier, build_notifier
from pricewatch.providers import build_provider
from pricewatch.rules import (
    AboveRule,
    BelowRule,
    PercentMoveRule,
    build_rule,
)
from pricewatch.store import JsonStore, MemoryStore

FAILURES = []


def check(name, condition, extra=""):
    status = "PASS  " if condition else "FAIL  "
    print(status + name + ("" if condition else f"   <-- {extra}"))
    if not condition:
        FAILURES.append(name)


# ---------------------------------------------------------------- rules

def test_above_rule():
    rule = AboveRule(threshold=100)

    check("above: silent below threshold",
          rule.evaluate("BTC", 99, None) is None)
    check("above: silent exactly at threshold",
          rule.evaluate("BTC", 100, None) is None)
    check("above: fires on first observation above",
          rule.evaluate("BTC", 101, None) is not None)
    check("above: fires on crossing up",
          rule.evaluate("BTC", 101, 99) is not None)
    check("above: silent while staying above (no spam)",
          rule.evaluate("BTC", 105, 101) is None)

    alert = rule.evaluate("BTC", 101, 99)
    check("above: alert carries price", alert.price == 101, alert)
    check("above: alert carries rule name", alert.rule == "above", alert)


def test_below_rule():
    rule = BelowRule(threshold=100)

    check("below: silent above threshold",
          rule.evaluate("BTC", 101, None) is None)
    check("below: silent exactly at threshold",
          rule.evaluate("BTC", 100, None) is None)
    check("below: fires on crossing down",
          rule.evaluate("BTC", 99, 101) is not None)
    check("below: silent while staying below (no spam)",
          rule.evaluate("BTC", 95, 99) is None)


def test_percent_move_rule():
    rule = PercentMoveRule(percent=5)

    check("percent: silent on first run (nothing to compare)",
          rule.evaluate("BTC", 100, None) is None)
    check("percent: silent on small move",
          rule.evaluate("BTC", 104, 100) is None)
    check("percent: fires on move up",
          rule.evaluate("BTC", 106, 100) is not None)
    check("percent: fires on move down",
          rule.evaluate("BTC", 94, 100) is not None)
    check("percent: fires exactly at threshold",
          rule.evaluate("BTC", 105, 100) is not None)
    check("percent: survives zero previous price (no ZeroDivisionError)",
          rule.evaluate("BTC", 100, 0) is None)

    up = rule.evaluate("BTC", 110, 100)
    check("percent: reports direction up", "up" in up.message, up.message)
    down = rule.evaluate("BTC", 90, 100)
    check("percent: reports direction down", "down" in down.message, down.message)

    check("percent: negative config is treated as magnitude",
          PercentMoveRule(percent=-5).evaluate("BTC", 106, 100) is not None)


def test_rule_factory():
    check("factory: builds above rule",
          isinstance(build_rule({"type": "above", "threshold": 1}), AboveRule))
    check("factory: builds percent rule",
          isinstance(build_rule({"type": "percent_move", "percent": 1}),
                     PercentMoveRule))

    try:
        build_rule({"type": "nonsense"})
        check("factory: rejects unknown rule", False)
    except ValueError as exc:
        check("factory: rejects unknown rule", "Unknown rule" in str(exc))

    try:
        build_rule({"threshold": 1})
        check("factory: rejects missing type", False)
    except ValueError:
        check("factory: rejects missing type", True)

    try:
        build_rule({"type": "above", "wrong_param": 1})
        check("factory: rejects bad params", False)
    except ValueError:
        check("factory: rejects bad params", True)


# ---------------------------------------------------------------- store

def test_memory_store():
    store = MemoryStore()
    check("memory store: missing key returns None", store.get("x") is None)
    store.set("x", 5)
    check("memory store: round-trips value", store.get("x") == 5)
    store.set("x", 7)
    check("memory store: overwrites", store.get("x") == 7)


def test_json_store():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state.json")

        store = JsonStore(path)
        check("json store: missing file is not an error", store.get("x") is None)
        store.set("x", 42.5)
        store.save()
        check("json store: file written", os.path.exists(path))

        reopened = JsonStore(path)
        check("json store: value survives restart", reopened.get("x") == 42.5,
              reopened.get("x"))

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        recovered = JsonStore(path)
        check("json store: corrupt file does not crash",
              recovered.get("x") is None)


# ---------------------------------------------------------------- wiring

class FakeProvider:
    """Returns a scripted sequence of prices. No network."""

    name = "fake"

    def __init__(self, prices):
        self.prices = list(prices)
        self.calls = 0

    def fetch(self, symbol):
        if self.calls >= len(self.prices):
            return None
        value = self.prices[self.calls]
        self.calls += 1
        return value


class RecordingNotifier:
    name = "recording"

    def __init__(self, succeed=True):
        self.sent = []
        self.succeed = succeed

    def send(self, alert):
        self.sent.append(alert)
        return self.succeed


def test_check_watch():
    store = MemoryStore()
    provider = FakeProvider([100, 106])
    watch = Watch(symbol="BTC", provider=provider,
                  rules=[PercentMoveRule(percent=5)])

    first = check_watch(watch, store)
    check("check_watch: no alert on first observation", first == [], first)
    check("check_watch: state recorded", store.get("fake:BTC") == 100)

    second = check_watch(watch, store)
    check("check_watch: alert on qualifying move", len(second) == 1, second)
    check("check_watch: state advanced", store.get("fake:BTC") == 106)


def test_check_watch_provider_failure():
    store = MemoryStore()
    watch = Watch(symbol="BTC", provider=FakeProvider([]),
                  rules=[AboveRule(threshold=1)])

    check("check_watch: provider failure yields no alerts",
          check_watch(watch, store) == [])
    check("check_watch: provider failure leaves state untouched",
          store.get("fake:BTC") is None)


def test_multiple_rules_on_one_watch():
    store = MemoryStore({"fake:BTC": 100})
    watch = Watch(
        symbol="BTC",
        provider=FakeProvider([200]),
        rules=[AboveRule(threshold=150), PercentMoveRule(percent=5)],
    )
    alerts = check_watch(watch, store)
    check("multi-rule: both rules fire", len(alerts) == 2, alerts)
    fired = {a.rule for a in alerts}
    check("multi-rule: distinct rule names",
          fired == {"above", "percent_move"}, fired)


def test_label_used_in_alert():
    store = MemoryStore()
    watch = Watch(symbol="bitcoin", provider=FakeProvider([200]),
                  rules=[AboveRule(threshold=1)], label="Bitcoin")
    alerts = check_watch(watch, store)
    check("label: used in alert text", alerts[0].symbol == "Bitcoin", alerts[0])


def test_state_key_isolates_providers():
    a = Watch(symbol="BTC", provider=FakeProvider([1]), rules=[])
    b = Watch(symbol="BTC", provider=build_provider("binance"), rules=[])
    check("state key: same symbol on two providers stays separate",
          a.state_key != b.state_key, (a.state_key, b.state_key))


# ------------------------------------------------- failure isolation
# These cover three bugs found by probing the first version:
#   - a notifier that raised killed the whole cycle
#   - a failing notifier blocked delivery to the remaining ones
#   - a provider that raised aborted the pass, silencing every other watch


class ExplodingNotifier:
    name = "exploding"

    def send(self, alert):
        raise RuntimeError("network down")


class ExplodingProvider:
    name = "exploding"

    def fetch(self, symbol):
        raise RuntimeError("api down")


def test_exploding_notifier_does_not_kill_cycle():
    watch = Watch(symbol="X", provider=FakeProvider([200]),
                  rules=[AboveRule(threshold=1)])
    try:
        run_once([watch], [ExplodingNotifier()], MemoryStore())
        check("isolation: raising notifier is contained", True)
    except Exception as exc:
        check("isolation: raising notifier is contained", False, exc)


def test_healthy_notifier_still_receives_after_sibling_fails():
    recorder = RecordingNotifier()
    watch = Watch(symbol="X", provider=FakeProvider([200]),
                  rules=[AboveRule(threshold=1)])
    run_once([watch], [ExplodingNotifier(), recorder], MemoryStore())
    check("isolation: healthy notifier still gets the alert",
          len(recorder.sent) == 1, len(recorder.sent))


def test_exploding_provider_does_not_stop_other_watches():
    recorder = RecordingNotifier()
    watches = [
        Watch(symbol="A", provider=ExplodingProvider(),
              rules=[AboveRule(threshold=1)]),
        Watch(symbol="B", provider=FakeProvider([200]),
              rules=[AboveRule(threshold=1)]),
    ]
    run_once(watches, [recorder], MemoryStore())
    check("isolation: healthy watch still checked after a broken one",
          len(recorder.sent) == 1, len(recorder.sent))


def test_failed_delivery_is_not_counted():
    watch = Watch(symbol="X", provider=FakeProvider([200]),
                  rules=[AboveRule(threshold=1)])
    delivered = run_once([watch], [RecordingNotifier(succeed=False)],
                         MemoryStore())
    check("isolation: refused delivery is not counted as sent",
          delivered == 0, delivered)


# ---------------------------------------------------------------- config

def test_build_watches():
    config = {
        "watches": [
            {
                "symbol": "bitcoin",
                "provider": "coingecko",
                "label": "BTC",
                "rules": [{"type": "above", "threshold": 100000}],
            }
        ]
    }
    watches = build_watches(config)
    check("config: one watch built", len(watches) == 1)
    check("config: label applied", watches[0].display == "BTC")
    check("config: default provider works",
          build_watches({"watches": [
              {"symbol": "bitcoin",
               "rules": [{"type": "above", "threshold": 1}]}
          ]})[0].provider.name == "coingecko")


def test_build_notifiers_defaults_to_console():
    notifiers = build_notifiers({})
    check("config: defaults to console notifier",
          len(notifiers) == 1 and isinstance(notifiers[0], ConsoleNotifier))


def test_notifier_factory_validation():
    try:
        build_notifier({"type": "telegram", "bot_token": "", "chat_id": ""})
        check("notifier: rejects empty telegram credentials", False)
    except ValueError:
        check("notifier: rejects empty telegram credentials", True)

    try:
        build_notifier({"type": "webhook"})
        check("notifier: rejects webhook without url", False)
    except ValueError:
        check("notifier: rejects webhook without url", True)

    try:
        build_notifier({"type": "carrier_pigeon"})
        check("notifier: rejects unknown type", False)
    except ValueError as exc:
        check("notifier: rejects unknown type", "Unknown notifier" in str(exc))


def test_provider_factory_validation():
    try:
        build_provider("myspace")
        check("provider: rejects unknown name", False)
    except ValueError as exc:
        check("provider: rejects unknown name", "Unknown provider" in str(exc))


# ---------------------------------------------------------------- batching

class BatchingProvider:
    """Prices every symbol in one call and counts how many calls it took."""

    name = "batching"

    def __init__(self, prices):
        self.prices = dict(prices)
        self.batch_calls = 0
        self.single_calls = 0

    def fetch(self, symbol):
        self.single_calls += 1
        return self.prices.get(symbol)

    def fetch_many(self, symbols):
        self.batch_calls += 1
        return {symbol: self.prices.get(symbol) for symbol in symbols}


def test_fetch_prices_uses_batching_when_available():
    provider = BatchingProvider({"BTC": 100, "ETH": 50, "SOL": 20})
    watches = [
        Watch(symbol=s, provider=provider, rules=[AboveRule(1)])
        for s in ("BTC", "ETH", "SOL")
    ]
    prices = fetch_prices(watches)

    check("batch: three symbols cost one request",
          provider.batch_calls == 1, provider.batch_calls)
    check("batch: no per-symbol calls were made",
          provider.single_calls == 0, provider.single_calls)
    check("batch: every watch got its price",
          sorted(prices.values()) == [20, 50, 100], prices)


def test_fetch_prices_falls_back_to_single_fetch():
    # A provider implementing only fetch() is the documented contract.
    # It must keep working without knowing fetch_many exists.
    provider = FakeProvider([100, 200])
    watches = [
        Watch(symbol="BTC", provider=provider, rules=[AboveRule(1)]),
        Watch(symbol="ETH", provider=provider, rules=[AboveRule(1)]),
    ]
    prices = fetch_prices(watches)

    check("batch: fetch-only provider is still priced",
          sorted(p for p in prices.values() if p is not None) == [100, 200], prices)


def test_run_once_delivers_with_fetch_only_provider():
    # Guards the whole path: a provider with no fetch_many must still produce
    # delivered alerts, not a silent zero.
    provider = FakeProvider([150])
    watch = Watch(symbol="BTC", provider=provider, rules=[AboveRule(100)])
    recorder = RecordingNotifier()

    delivered = run_once([watch], [recorder], MemoryStore())
    check("batch: fetch-only provider still delivers alerts",
          delivered == 1 and len(recorder.sent) == 1,
          f"delivered={delivered} sent={len(recorder.sent)}")


def test_fetch_prices_isolates_a_broken_provider():
    class Broken:
        name = "broken"

        def fetch_many(self, symbols):
            raise RuntimeError("boom")

    good = BatchingProvider({"ETH": 50})
    watches = [
        Watch(symbol="BTC", provider=Broken(), rules=[AboveRule(1)]),
        Watch(symbol="ETH", provider=good, rules=[AboveRule(1)]),
    ]
    prices = fetch_prices(watches)
    values = sorted(prices.values(), key=lambda v: (v is None, v))

    check("batch: a broken provider does not blank the others",
          values == [50, None], values)


def test_fetch_prices_keeps_duplicate_symbols_independent():
    provider = BatchingProvider({"BTC": 100})
    first = Watch(symbol="BTC", provider=provider, rules=[AboveRule(50)])
    second = Watch(symbol="BTC", provider=provider, rules=[BelowRule(200)])
    prices = fetch_prices([first, second])

    check("batch: two watches on one symbol both get a price",
          prices[id(first)] == 100 and prices[id(second)] == 100, prices)


def test_check_watch_accepts_a_supplied_price():
    provider = BatchingProvider({"BTC": 999})
    watch = Watch(symbol="BTC", provider=provider, rules=[AboveRule(100)])

    alerts = check_watch(watch, MemoryStore(), 150)
    check("batch: supplied price is used", len(alerts) == 1 and alerts[0].price == 150,
          alerts)
    check("batch: supplied price skips the provider entirely",
          provider.batch_calls == 0 and provider.single_calls == 0)


def test_check_watch_supplied_none_is_a_skip():
    provider = BatchingProvider({"BTC": 999})
    watch = Watch(symbol="BTC", provider=provider, rules=[AboveRule(100)])

    alerts = check_watch(watch, MemoryStore(), None)
    check("batch: a supplied None is treated as a failed fetch", alerts == [], alerts)
    check("batch: a supplied None does not fall back to fetching",
          provider.batch_calls == 0 and provider.single_calls == 0)


def test_identical_watches_share_one_provider():
    config = {
        "watches": [
            {"symbol": "bitcoin", "rules": [{"type": "above", "threshold": 1}]},
            {"symbol": "ethereum", "rules": [{"type": "above", "threshold": 1}]},
            {"symbol": "solana", "provider": "binance",
             "rules": [{"type": "above", "threshold": 1}]},
        ]
    }
    watches = build_watches(config)
    coingecko = {id(w.provider) for w in watches if w.provider.name == "coingecko"}
    binance = {id(w.provider) for w in watches if w.provider.name == "binance"}

    check("wiring: same provider and options share one instance",
          len(coingecko) == 1, len(coingecko))
    check("wiring: a different provider stays separate", len(binance) == 1)


def test_differing_options_do_not_share_a_provider():
    config = {
        "watches": [
            {"symbol": "bitcoin", "provider_options": {"vs_currency": "usd"},
             "rules": [{"type": "above", "threshold": 1}]},
            {"symbol": "ethereum", "provider_options": {"vs_currency": "eur"},
             "rules": [{"type": "above", "threshold": 1}]},
        ]
    }
    watches = build_watches(config)
    check("wiring: different provider options get separate instances",
          watches[0].provider is not watches[1].provider)


# ---------------------------------------------------------------- run

if __name__ == "__main__":
    for fn in [
        test_above_rule,
        test_below_rule,
        test_percent_move_rule,
        test_rule_factory,
        test_memory_store,
        test_json_store,
        test_check_watch,
        test_check_watch_provider_failure,
        test_multiple_rules_on_one_watch,
        test_label_used_in_alert,
        test_state_key_isolates_providers,
        test_exploding_notifier_does_not_kill_cycle,
        test_healthy_notifier_still_receives_after_sibling_fails,
        test_exploding_provider_does_not_stop_other_watches,
        test_failed_delivery_is_not_counted,
        test_build_watches,
        test_build_notifiers_defaults_to_console,
        test_notifier_factory_validation,
        test_provider_factory_validation,
        test_fetch_prices_uses_batching_when_available,
        test_fetch_prices_falls_back_to_single_fetch,
        test_run_once_delivers_with_fetch_only_provider,
        test_fetch_prices_isolates_a_broken_provider,
        test_fetch_prices_keeps_duplicate_symbols_independent,
        test_check_watch_accepts_a_supplied_price,
        test_check_watch_supplied_none_is_a_skip,
        test_identical_watches_share_one_provider,
        test_differing_options_do_not_share_a_provider,
    ]:
        fn()

    print()
    if FAILURES:
        print(f"FAILURES ({len(FAILURES)}): {FAILURES}")
        sys.exit(1)
    print("all tests passed")
