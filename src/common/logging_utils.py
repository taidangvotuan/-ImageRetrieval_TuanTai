"""Cấu hình logging thống nhất (console + file trong ``logs/``)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_CONFIGURED = False


def setup_logging(level: str = "INFO", log_dir: Path | None = None, name: str = "video_intel") -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        # Console Windows (cp1252) không in được tiếng Việt khi output bị chuyển hướng.
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        handlers: list[logging.Handler] = [logging.StreamHandler()]
        if log_dir is not None:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(Path(log_dir) / f"{name}.log", encoding="utf-8"))
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            handlers=handlers,
        )
        _CONFIGURED = True
    return logging.getLogger(name)
