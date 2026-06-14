"""
sweeper.py — Server-side background sweep.

Runs every 60 seconds inside a daemon thread started from apps.py ready().
All timer logic lives here — nothing depends on the browser.

Rules (from problem statement):
  1. Session > 2 h        → abandoned
  2. Away   > 20 min      → abandoned
  3. Session > 110 min, no presence check sent yet → send "Still here?" warning
"""
import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_timer = None
_started = False


def run_sweep():
    """Single sweep pass, then re-schedule."""
    try:
        _do_sweep()
    except Exception:
        logger.exception("DeskGuard sweep error")
    finally:
        _schedule_next()


def _do_sweep():
    from datetime import timedelta

    from django.utils import timezone

    from .models import Desk

    now = timezone.now()
    session_limit    = now - timedelta(hours=2)          # 2 h → abandoned
    away_limit       = now - timedelta(minutes=20)       # 20 min away → abandoned
    warning_at       = now - timedelta(minutes=110)      # 10 min before 2 h

    # 0. Expired student logout grace period (5 minutes) → available
    logout_limit = now - timedelta(minutes=5)
    n_logout = Desk.objects.filter(
        status__in=["occupied", "away"],
        logged_out_at__isnull=False,
        logged_out_at__lt=logout_limit,
    ).update(
        status="available",
        occupied_by=None,
        purpose="",
        approximate_time="",
        session_started_at=None,
        away_started_at=None,
        break_end_at=None,
        presence_check_sent_at=None,
        presence_verified=False,
        notice="Seat automatically released after student logged out.",
        notice_kind="info",
        logged_out_at=None,
    )
    if n_logout:
        logger.info("Sweep: %d desk(s) released due to logout grace expiry", n_logout)

    # 1. Occupied sessions past 2 h → abandoned
    n = Desk.objects.filter(
        status="occupied",
        session_started_at__lt=session_limit,
    ).update(
        status="abandoned",
        notice="Session expired automatically. Desk has been freed.",
        notice_kind="expired",
        presence_verified=False,
    )
    if n:
        logger.info("Sweep: %d occupied desk(s) expired → abandoned", n)

    # 2. Away desks whose break_end_at + 1 minute has passed → abandoned
    #    Uses break_end_at (exact student-chosen expiry) with fallback to 20-min cap + 1 min grace.
    from django.db.models import Q
    away_grace_limit = now - timedelta(minutes=1)
    away_legacy_limit = away_limit - timedelta(minutes=1)
    n = Desk.objects.filter(status="away").filter(
        Q(break_end_at__lt=away_grace_limit)
        | Q(break_end_at__isnull=True, away_started_at__lt=away_legacy_limit)
    ).update(
        status="abandoned",
        notice="Away timer expired. Desk has been freed.",
        notice_kind="expired",
        presence_verified=False,
    )
    if n:
        logger.info("Sweep: %d away desk(s) expired + grace period → abandoned", n)

    # 3. Occupied desks past 110 min with no presence check yet → send warning
    n = Desk.objects.filter(
        status="occupied",
        session_started_at__lt=warning_at,
        session_started_at__gt=session_limit,   # not yet expired
        presence_check_sent_at__isnull=True,
    ).update(
        notice="Are you still here? Please confirm your presence in the next 10 minutes.",
        notice_kind="warning",
        presence_check_sent_at=now,
        presence_verified=False,
    )
    if n:
        logger.info("Sweep: presence-check warning sent to %d desk(s)", n)


def _schedule_next():
    global _timer
    _timer = threading.Timer(60.0, run_sweep)
    _timer.daemon = True
    _timer.name = "deskguard-sweep"
    _timer.start()


def start_background_sweep():
    """
    Start the sweep thread once. Safe to call multiple times — the _started
    flag prevents a second thread from launching.
    """
    global _started
    with _lock:
        if _started:
            return
        _started = True
    logger.info("DeskGuard: background sweep started (60 s interval)")
    _schedule_next()
