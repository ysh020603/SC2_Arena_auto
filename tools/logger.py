import logging
import sys
import os
from typing import Optional
from datetime import datetime


class ColoredFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[94m",  # Blue
        "INFO": "\033[92m",  # Green
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[91m",  # Red
        "CRITICAL": "\033[1;91m",  # Bright Red
    }
    RESET = "\033[0m"

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.RESET)
        message = super().format(record)
        return f"{log_color}{message}{self.RESET}"


def setup_logger(
    name: str,
    level: int = logging.INFO,
    log_dir: Optional[str] = None,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    format_config = {
        "fmt": "%(asctime)s [%(name)s] [%(levelname)s]: %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
    }

    stream_formatter = ColoredFormatter(**format_config)
    file_formatter = logging.Formatter(**format_config)

    if not logger.hasHandlers():
        # Stream Handler
        s_handler = logging.StreamHandler(stream=sys.stdout)
        s_handler.setFormatter(stream_formatter)
        s_handler.setLevel(level)
        logger.addHandler(s_handler)

        # File Handler
        if log_dir is None:
            log_dir = "./logs"
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"{name}_{datetime.now().strftime('%Y%m%d')}.log")
        try:
            f_handler = logging.FileHandler(log_file, "a")
            f_handler.setFormatter(file_formatter)
            f_handler.setLevel(level)
            logger.addHandler(f_handler)
        except OSError as e:
            logger.error(f"Failed to create log file: {e}")

    return logger


def test_setup_logger():
    logger = setup_logger("my_logger", logging.DEBUG, "./my_logs")
    logger.debug("This is a DEBUG message.")
    logger.info("This is an INFO message.")
    logger.warning("This is a WARNING message.")
    logger.error("This is an ERROR message.")
    logger.critical("This is a CRITICAL message.")


if __name__ == "__main__":
    test_setup_logger()
