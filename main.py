"""pinger — VPS network stability monitor (CLI mode).

Usage
─────
  python main.py [--config config.yaml]

Keybindings
───────────
  Ctrl-C  quit
"""
from __future__ import annotations

import argparse
import sys
import time

from rich.live import Live

from display import build_table
from engine import ProbeEngine, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="VPS network stability monitor")
    parser.add_argument(
        "--config", default="config.yaml", metavar="FILE",
        help="Path to config file (default: config.yaml)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    hosts      = cfg.get("hosts", [])
    thresholds = cfg.get("thresholds", {})

    if not hosts:
        print("No hosts defined in config.yaml", file=sys.stderr)
        sys.exit(1)

    engine = ProbeEngine(cfg)
    engine.start()

    try:
        snap_r, snap_h = engine.snapshot()
        with Live(
            build_table(hosts, snap_r, thresholds, snap_h),
            refresh_per_second=1,
            screen=False,
        ) as live:
            while True:
                time.sleep(1)
                snap_r, snap_h = engine.snapshot()
                live.update(build_table(
                    cfg.get("hosts", []),
                    snap_r, thresholds, snap_h,
                ))
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
