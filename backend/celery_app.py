"""
Celery application — background task queue and beat scheduler.
Start worker:  celery -A celery_app worker -l info
Start beat:    celery -A celery_app beat -l info
Combined:      celery -A celery_app worker --beat -l info -S celery.beat.PersistentScheduler
"""
from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

app = Celery(
    "secretaryai",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "tasks.overnight",
        "tasks.morning_briefing",
        "tasks.weekly_report",
        "tasks.sync_check",
        "tasks.account_health",
        "tasks.inventory_alerts",
        "tasks.file_ingestion",
    ],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,           # Only ack after task completes (safer)
    worker_prefetch_multiplier=1,  # Don't prefetch — tasks can be slow
    result_expires=3600,
)

# ─── Beat schedule ─────────────────────────────────────────────────────────────
app.conf.beat_schedule = {
    # Full overnight scan — 2 AM UTC every night
    "overnight-scan": {
        "task": "tasks.overnight.run_overnight_scan_all_companies",
        "schedule": crontab(hour=2, minute=0),
    },
    # Morning briefing — 7 AM UTC every weekday
    "morning-briefing": {
        "task": "tasks.morning_briefing.send_morning_briefing_all",
        "schedule": crontab(hour=7, minute=0, day_of_week="mon-fri"),
    },
    # Weekly leadership report — Sunday 6 AM UTC
    "weekly-report": {
        "task": "tasks.weekly_report.send_weekly_report_all",
        "schedule": crontab(hour=6, minute=0, day_of_week="sun"),
    },
    # Sync health check — every 4 hours
    "sync-check": {
        "task": "tasks.sync_check.run_sync_check_all",
        "schedule": crontab(minute=0, hour="*/4"),
    },
    # Account health refresh — every night at midnight
    "account-health-refresh": {
        "task": "tasks.account_health.refresh_all",
        "schedule": crontab(hour=0, minute=30),
    },
    # Inventory alert check — every 6 hours
    "inventory-alerts": {
        "task": "tasks.inventory_alerts.check_all",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    # File ingestion — every 30 minutes
    "file-ingestion": {
        "task": "tasks.file_ingestion.ingest_watched_files_all",
        "schedule": crontab(minute="*/30"),
    },
}
