"""Entry point: wire the pieces together and run."""

from __future__ import annotations

import logging

from slack_bolt.adapter.socket_mode import SocketModeHandler

from .classifier import Classifier
from .config import settings
from .pipeline import Pipeline
from .plane import PlaneClient
from .scheduler import build_scheduler
from .slack_app import build_app, capture_scope
from .store import init_db

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    init_db()

    plane = PlaneClient()
    if problems := plane.validate_setup():
        for problem in problems:
            log.error("Plane project is not set up: %s", problem)
        log.error("run: python scripts/verify_plane.py --create-labels")
        raise SystemExit(1)

    pipeline = Pipeline(Classifier(), plane)
    app = build_app(pipeline)

    scheduler = build_scheduler(app.client)
    scheduler.start()

    log.info("catalogbot starting — Slack capture scope: %s", capture_scope())
    try:
        if settings.slack_use_socket_mode:
            SocketModeHandler(app, settings.slack_app_token).start()
        else:
            app.start(port=3000)
    finally:
        scheduler.shutdown(wait=False)
        plane.close()


if __name__ == "__main__":
    main()
