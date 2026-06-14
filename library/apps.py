import os
import sys

from django.apps import AppConfig


class LibraryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "library"

    def ready(self):
        # ── Skip management commands that don't need the sweep ────────────
        mgmt_skip = {"migrate", "makemigrations", "shell", "collectstatic",
                     "test", "check", "showmigrations", "sqlmigrate", "flush"}
        if any(cmd in sys.argv for cmd in mgmt_skip):
            return

        # ── Avoid double-start with Django's autoreloader ─────────────────
        # The autoreloader runs the child process with RUN_MAIN=true.
        # With --noreload or gunicorn there is no such var (single process).
        if "runserver" in sys.argv:
            if os.environ.get("RUN_MAIN") != "true" and "--noreload" not in sys.argv:
                return  # We are the file-watcher parent — skip

        # ── Seed desks on first boot ──────────────────────────────────────
        from .data import ensure_desks_seeded, ensure_default_accounts
        try:
            ensure_desks_seeded()
            ensure_default_accounts()
        except Exception as exc:
            # DB might not be ready yet (first-time before migrate)
            import logging
            logging.getLogger(__name__).warning("Could not seed desks: %s", exc)

        # ── Start the background sweep thread ─────────────────────────────
        from .sweeper import start_background_sweep
        start_background_sweep()
