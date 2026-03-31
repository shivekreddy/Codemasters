import logging

def setup_logging(level=logging.INFO, log_file: str | None = None) -> logging.Logger:
    logger = logging.getLogger("gridcode_tool")
    logger.setLevel(level)

    # Avoid duplicate handlers if setup_logging is called multiple times (tests)
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger
