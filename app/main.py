"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from app.config import load_settings
from app.logging import configure_logging
from app.models import DeliveryState
from app.renderers.plain_text import render_plain_text
from app.renderers.telegram import render_telegram
from app.scheduler import heartbeat_is_healthy, run_scheduler
from app.service import DigestService, fixture_report
from app.storage import Storage


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m app.main")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="run the scheduler")

    collect = commands.add_parser("collect", help="collect and persist a digest")
    collect.add_argument("--date", required=True, type=parse_date)
    collect.add_argument("--fixture", action="store_true")

    render = commands.add_parser("render", help="render a stored digest")
    render.add_argument("--date", required=True, type=parse_date)
    render.add_argument("--fixture", action="store_true")

    send = commands.add_parser("send", help="send or preview a digest")
    send.add_argument("--date", required=True, type=parse_date)
    send.add_argument("--dry-run", action="store_true")
    send.add_argument("--fixture", action="store_true")

    commands.add_parser("snapshot", help="capture current noncanonical observations")
    commands.add_parser("healthcheck", help="check local persistence and scheduler heartbeat")

    reconcile = commands.add_parser("reconcile", help="resolve an unknown delivery outcome")
    reconcile.add_argument("--date", required=True, type=parse_date)
    reconcile.add_argument("--state", required=True, choices=("sent", "failed"))
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    configure_logging()
    try:
        settings = load_settings()
        storage = Storage(settings.database_path)
        storage.initialize()
        service = DigestService(settings, storage)

        if args.command == "run":
            run_scheduler(service)
        elif args.command == "collect":
            report = (
                fixture_report(args.date, settings.timezone)
                if args.fixture
                else service.collect(args.date)
            )
            if args.fixture:
                storage.save_digest(report, render_telegram(report))
            print(report.model_dump_json(indent=2))
        elif args.command == "render":
            if args.fixture:
                report = fixture_report(args.date, settings.timezone)
            else:
                stored = storage.get_latest_digest(args.date, settings.timezone)
                if stored is None:
                    raise RuntimeError("no stored digest for this date")
                report = stored[1]
            print(render_plain_text(report))
        elif args.command == "send":
            if args.fixture:
                if not args.dry_run:
                    raise RuntimeError("fixture reports may only be used with --dry-run")
                print(render_plain_text(fixture_report(args.date, settings.timezone)))
            else:
                print(service.send(args.date, dry_run=args.dry_run))
        elif args.command == "snapshot":
            print("\n".join(service.snapshot(canonical=False)) or "No enabled snapshot providers")
        elif args.command == "healthcheck":
            with storage.connect() as connection:
                connection.execute("SELECT 1").fetchone()
            heartbeat = settings.state_directory / "scheduler-heartbeat"
            if not heartbeat_is_healthy(heartbeat):
                raise RuntimeError("scheduler heartbeat is missing or stale")
            print("healthy")
        elif args.command == "reconcile":
            changed = storage.reconcile_unknown(
                args.date, settings.timezone, DeliveryState(args.state)
            )
            if not changed:
                raise RuntimeError("no unknown delivery found for this date")
            print("reconciled")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
