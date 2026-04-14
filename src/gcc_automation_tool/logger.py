import logging
from pathlib import Path

def setup_logging(
    level: int = logging.INFO,
    log_file: str | None = None,
) -> None:
    handlers: list[logging.Handler] = []

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    handlers.append(sh)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        handlers.append(fh)

    logging.basicConfig(
        level=level,
        handlers=handlers,
    )