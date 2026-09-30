"""Keep request logging useful without recording OAuth authorization codes."""
import logging


class RedactQueryStrings(logging.Filter):
    def filter(self, record):
        # Uvicorn logs (client, method, path_with_query, version, status).
        if isinstance(record.args, tuple) and len(record.args) == 5 and isinstance(record.args[2], str):
            record.args = (*record.args[:2], record.args[2].split("?", 1)[0], *record.args[3:])
        return True


def configure_access_logging():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, RedactQueryStrings) for item in logger.filters):
        logger.addFilter(RedactQueryStrings())
