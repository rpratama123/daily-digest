"""Jakarta-aware scheduler and startup catch-up policy."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.clock import require_aware, utc_now
from app.service import DigestService

LOGGER = logging.getLogger(__name__)


def should_catch_up(now: datetime, digest_hour: int) -> bool:
    require_aware(now)
    return now.hour >= digest_hour


def write_heartbeat(path: Path, now: datetime | None = None) -> None:
    now = now or utc_now()
    require_aware(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(now.isoformat(), encoding="utf-8")
    temporary.replace(path)


def heartbeat_is_healthy(
    path: Path, now: datetime | None = None, max_age_seconds: int = 300
) -> bool:
    now = now or utc_now()
    require_aware(now)
    try:
        recorded = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
        require_aware(recorded)
    except (OSError, ValueError):
        return False
    age = now - recorded
    return timedelta(0) <= age <= timedelta(seconds=max_age_seconds)


def run_scheduler(service: DigestService) -> None:
    settings = service.settings
    timezone = ZoneInfo(settings.timezone)
    heartbeat = settings.state_directory / "scheduler-heartbeat"
    write_heartbeat(heartbeat)

    now = datetime.now(timezone)
    if settings.telegram.enabled and should_catch_up(now, settings.schedule.digest_hour):
        try:
            service.send(now.date())
        except RuntimeError as exc:
            LOGGER.info("Startup catch-up skipped: %s", exc)

    scheduler = BlockingScheduler(timezone=timezone)

    def daily_job() -> None:
        delivery_date = datetime.now(timezone).date()
        if settings.telegram.enabled:
            service.send(delivery_date)
        else:
            service.collect(delivery_date)

    scheduler.add_job(
        lambda: service.snapshot(canonical=True),
        CronTrigger(hour=settings.schedule.snapshot_hour, minute=0, timezone=timezone),
        id="provider-boundary-snapshot",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=settings.schedule.misfire_grace_seconds,
    )
    scheduler.add_job(
        daily_job,
        CronTrigger(hour=settings.schedule.digest_hour, minute=0, timezone=timezone),
        id="daily-digest-delivery",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=settings.schedule.misfire_grace_seconds,
    )
    scheduler.add_job(
        lambda: write_heartbeat(heartbeat),
        IntervalTrigger(minutes=1, timezone=timezone),
        id="scheduler-heartbeat",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
