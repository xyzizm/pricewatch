# PriceWatch

Crypto price alerts you configure in one JSON file. Watches any number of coins across multiple exchanges and tells you when something you care about happens.

Self-hosted, no account, no API key required for the default setup.

---

## Quick start

```bash
pip install requests
cp config.example.json config.json
python3 run.py
```

That's it. Edit `config.json` to change what you watch.

```bash
python3 run.py --once        # single pass, then exit — good for cron
python3 run.py --config other.json --state other-state.json
```

---

## Config

```json
{
  "interval_seconds": 60,
  "notifiers": [
    { "type": "telegram", "bot_token": "...", "chat_id": "..." }
  ],
  "watches": [
    {
      "label": "Bitcoin",
      "symbol": "bitcoin",
      "provider": "coingecko",
      "rules": [
        { "type": "above", "threshold": 150000 },
        { "type": "percent_move", "percent": 5 }
      ]
    }
  ]
}
```

### Providers

| provider | `symbol` format | example |
|---|---|---|
| `coingecko` (default) | CoinGecko coin id | `bitcoin`, `solana` |
| `binance` | trading pair | `BTCUSDT`, `SOLUSDT` |

The same coin can be watched on both at once — each keeps its own history.

### Rules

| rule | fires when | parameter |
|---|---|---|
| `above` | price crosses above a level | `threshold` |
| `below` | price crosses below a level | `threshold` |
| `percent_move` | price moves N% since last check | `percent` |

`above` and `below` fire **on the crossing**, not on every check while the price stays there. Without that you would get the same alert every minute.

### Notifiers

| notifier | parameters |
|---|---|
| `console` | none — prints to stdout |
| `telegram` | `bot_token`, `chat_id` |
| `webhook` | `url` — POSTs JSON, works with Discord, Slack, n8n |

You can list several. Each is independent: if one is down, the others still deliver.

Telegram setup: message **@BotFather** → `/newbot` → copy token. Message **@userinfobot** → copy your id.

---

## Architecture

Four layers that know as little about each other as possible:

```
providers.py   where prices come from     fetch(symbol) -> float | None
rules.py       when to alert              evaluate(symbol, price, previous) -> Alert | None
notifiers.py   where alerts go            send(alert) -> bool
store.py       what we saw last time      get / set / save
app.py         wiring (deliberately thin)
```

**Adding anything is one class plus one registry line.**

New exchange:

```python
class KrakenProvider:
    name = "kraken"
    def fetch(self, symbol): ...

PROVIDERS["kraken"] = KrakenProvider   # done
```

New rule, e.g. "alert if price is flat for an hour" — same shape. New channel, e.g. email — same shape. No other file changes.

Rules are pure functions of `(symbol, price, previous)`: no network, no files, no clock. That is why the whole rule engine is testable without mocking anything.

---

## Tests

```bash
python3 tests/test_pricewatch.py
```

54 tests covering rule edge cases, config validation, state persistence, and failure isolation.

### Failure isolation

Three bugs were found by probing the first version and are now locked down by tests:

- a notifier that raised an exception killed the entire cycle
- when one notifier failed, the remaining ones never received the alert
- a provider that raised aborted the whole pass, silencing every other watch

All three are contained now. One dead API or one broken channel cannot take down the monitor.

---

## Running it continuously

```bash
nohup python3 run.py > pricewatch.log 2>&1 &
```

systemd, for a small VPS:

```ini
[Unit]
Description=PriceWatch
After=network.target

[Service]
WorkingDirectory=/path/to/pricewatch
ExecStart=/usr/bin/python3 /path/to/pricewatch/run.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Or skip the daemon entirely and use cron with `--once`:

```
*/5 * * * * cd /path/to/pricewatch && /usr/bin/python3 run.py --once
```

---

## Behaviour notes

- **First run** records current prices and stays quiet. `percent_move` has nothing to compare against yet; `above`/`below` will report once if the price is already past the threshold, so you learn the current state.
- **Minimum interval is 15 seconds** — the default APIs are free and public, hammering them gets you rate-limited.
- State lives in `state.json` and is written atomically. Delete it to reset.
- A rate-limited or unreachable API logs a line and is retried next cycle. Nothing crashes.

## Limits

- Public CoinGecko and Binance endpoints have rate limits. For many coins at high frequency, use your own API key or raise the interval.
- `percent_move` compares against the *previous check*, not a rolling window. A 5% move spread over an hour of 1-minute checks will not fire.
