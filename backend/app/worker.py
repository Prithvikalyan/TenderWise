"""Manual entry point for the independent TenderWise processing worker.

From the backend directory run ``python -m app.worker``. If no custom
production handler is registered, startup registers the format dispatcher for
the currently implemented PDF text extractor.
"""

import logging
import signal
import threading

from app.core.config import get_settings
from app.services.processing_handler import get_production_handler, register_production_handler
from app.services.processing_handlers import create_production_handler
from app.services.processing_worker import WorkerConfigurationError, run_worker_process

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    handler = get_production_handler()
    if handler is None:
        handler = create_production_handler(get_settings())
        register_production_handler(handler)

    shutdown_requested = threading.Event()

    def request_shutdown(_signal_number, _frame) -> None:
        logger.info("Worker shutdown requested")
        shutdown_requested.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    try:
        run_worker_process(get_settings(), handler, shutdown_requested)
    except WorkerConfigurationError as exc:
        logger.error("Worker startup refused: %s", str(exc))
        return 2
    except Exception:
        logger.error("Worker stopped after an internal operational error")
        return 1
    logger.info("Worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
