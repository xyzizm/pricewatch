#!/usr/bin/env python3
"""
PriceWatch entry point.

    python3 run.py                     # uses config.json + state.json
    python3 run.py --config my.json    # custom config
    python3 run.py --once              # single pass, then exit (good for cron)
"""

import argparse

from pricewatch.app import (
    JsonStore,
    build_notifiers,
    build_watches,
    load_config,
    run_forever,
    run_once,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Crypto price alerts")
    parser.add_argument("--config", default="config.json", help="path to config file")
    parser.add_argument("--state", default="state.json", help="path to state file")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single pass and exit instead of looping",
    )
    args = parser.parse_args()

    if args.once:
        config = load_config(args.config)
        watches = build_watches(config)
        notifiers = build_notifiers(config)
        store = JsonStore(args.state)
        delivered = run_once(watches, notifiers, store)
        print(f"{delivered} alert(s) delivered")
        return

    run_forever(args.config, args.state)


if __name__ == "__main__":
    main()
