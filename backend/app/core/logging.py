from logging.config import dictConfig


def configure_logging(level: str) -> None:
    """Configure a consistent process-wide logging baseline."""
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": ("%(asctime)s %(levelname)s %(name)s %(message)s")
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"handlers": ["default"], "level": level},
        }
    )
