# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Colored, timestamped logging configuration for MikuInvidious and Granian."""

from __future__ import annotations

import logging
import logging.config
import os
import re
import sys
import threading
from typing import Any, TextIO

_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[90m"
_ANSI_TIMESTAMP = "\033[38;5;208m"
_ANSI_BOLD = "\033[1m"

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}

_LEVEL_LABELS: dict[int, str] = {
    logging.DEBUG: "[DEBUG]",
    logging.INFO: "[INFO]",
    logging.WARNING: "[WARN]",
    logging.ERROR: "[ERROR]",
    logging.CRITICAL: "[CRIT]",
}

_TAG_COLORS: dict[str, str] = {
    "Init": "\033[1;34m",
    "Shutdown": "\033[1;34m",
    "Security": "\033[1;35m",
    "RateLimit": "\033[1;35m",
    "Timeout": "\033[1;33m",
    "ERROR": "\033[1;31m",
    "Access": "\033[1;32m",
    "Granian": "\033[1;34m",
}

_DEFAULT_TAG_COLOR = "\033[1;36m"

_TAG_PREFIX_RE = re.compile(r"^\[([A-Za-z0-9_.:-]+)\]\s*(.*)$", re.DOTALL)

_LOGGER_ALIASES: dict[str, str] = {
    "_granian": "Granian",
    "granian.access": "Access",
    "miku.app": "App",
    "miku.stdio": "App",
}


def should_use_color() -> bool:
    """Return whether ANSI colors should be emitted based on environment settings."""
    if os.environ.get("NO_COLOR", ""):
        return False
    log_color = os.environ.get("LOG_COLOR", "").strip().lower()
    if log_color in {"0", "false", "no", "never", "off"}:
        return False
    return True


class MikuFormatter(logging.Formatter):
    """Formatter producing timestamped, aligned, optionally colored log lines."""

    def __init__(
        self,
        *,
        use_color: bool | None = None,
        datefmt: str = "%m-%d %H:%M:%S",
        default_tag: str | None = None,
    ) -> None:
        super().__init__(datefmt=datefmt)
        self._use_color = should_use_color() if use_color is None else use_color
        self._default_tag = default_tag

    def _colorize(self, text: str, code: str) -> str:
        if not self._use_color or not code:
            return text
        return f"{code}{text}{_ANSI_RESET}"

    def _extract_tag(self, record: logging.LogRecord, message: str) -> tuple[str | None, str]:
        match = _TAG_PREFIX_RE.match(message)
        if match:
            tag, rest = match.group(1), match.group(2)
            if tag.upper() == "ERROR" and record.levelno >= logging.ERROR:
                return self._default_tag or _LOGGER_ALIASES.get(record.name), rest
            return tag, rest
        if self._default_tag:
            return self._default_tag, message
        alias = _LOGGER_ALIASES.get(record.name)
        if alias:
            return alias, message
        if record.name and record.name != "root":
            return record.name, message
        return None, message

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        level_name = _LEVEL_LABELS.get(record.levelno, f"[{record.levelname[:5]}]")
        raw_message = record.getMessage()

        tag, body = self._extract_tag(record, raw_message)
        ts_part = self._colorize(timestamp, _ANSI_TIMESTAMP)
        lvl_part = self._colorize(level_name, _LEVEL_COLORS.get(record.levelno, ""))

        if tag:
            tag_color = _TAG_COLORS.get(tag, _DEFAULT_TAG_COLOR)
            tag_part = self._colorize(f"[{tag}]", tag_color)
            line = f"{ts_part} {lvl_part} {tag_part} {body}"
        else:
            line = f"{ts_part} {lvl_part} {body}"

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line = f"{line}\n{record.exc_text}"
        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"
        return line


def _infer_level(message: str, default_level: int) -> int:
    stripped = message.lstrip()
    if stripped.startswith("[ERROR]") or stripped.startswith("ERROR:"):
        return logging.ERROR
    if stripped.startswith("[Timeout]") or "Warning:" in stripped:
        return logging.WARNING
    if stripped.startswith("[Shutdown]") and "failed:" in stripped:
        return logging.WARNING
    return default_level


class StreamToLogger:
    """Line-buffered stream adapter that forwards print/write output to a logger."""

    def __init__(
        self,
        logger: logging.Logger,
        level: int = logging.INFO,
        fallback_stream: TextIO | None = None,
    ) -> None:
        self._logger = logger
        self._level = level
        self._fallback = fallback_stream or sys.__stderr__
        self._buffer = ""
        self._lock = threading.Lock()

    @property
    def encoding(self) -> str:
        return getattr(self._fallback, "encoding", "utf-8")

    @property
    def errors(self) -> str:
        return getattr(self._fallback, "errors", "replace")

    def isatty(self) -> bool:
        return bool(getattr(self._fallback, "isatty", lambda: False)())

    def fileno(self) -> int:
        return self._fallback.fileno()

    def writable(self) -> bool:
        return True

    def write(self, data: str) -> int:
        if not data:
            return 0
        with self._lock:
            self._buffer += data
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                cleaned = line.rstrip("\r")
                if cleaned:
                    level = _infer_level(cleaned, self._level)
                    self._logger.log(level, "%s", cleaned)
        return len(data)

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        with self._lock:
            if self._buffer:
                cleaned = self._buffer.rstrip("\r\n")
                self._buffer = ""
                if cleaned:
                    level = _infer_level(cleaned, self._level)
                    self._logger.log(level, "%s", cleaned)


def get_granian_log_dictconfig(level: str = "INFO", use_color: bool | None = None) -> dict[str, Any]:
    """Build a dictConfig mapping for Granian and MikuInvidious loggers."""
    resolved_color = should_use_color() if use_color is None else use_color
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "miku_generic": {
                "()": "miku.core.logging.MikuFormatter",
                "use_color": resolved_color,
            },
            "miku_access": {
                "()": "miku.core.logging.MikuFormatter",
                "use_color": resolved_color,
                "default_tag": "Access",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "miku_generic",
                "stream": "ext://sys.__stderr__",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "miku_access",
                "stream": "ext://sys.__stdout__",
            },
        },
        "loggers": {
            "_granian": {
                "handlers": ["console"],
                "level": level,
                "propagate": False,
            },
            "granian.access": {
                "handlers": ["access"],
                "level": "INFO",
                "propagate": False,
            },
            "miku": {
                "handlers": ["console"],
                "level": level,
                "propagate": False,
            },
            "httpx": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
            "httpcore": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
        },
        "root": {
            "handlers": ["console"],
            "level": level,
        },
    }


def configure_logging(*, debug: bool = False, wrap_stdio: bool = True) -> None:
    """Configure root/Granian/app logging and optionally route stdout/stderr."""
    level = "DEBUG" if debug else "INFO"
    logging.config.dictConfig(get_granian_log_dictconfig(level=level))
    if wrap_stdio:
        stdio_logger = logging.getLogger("miku.stdio")
        if not isinstance(sys.stdout, StreamToLogger):
            sys.stdout = StreamToLogger(stdio_logger, logging.INFO, sys.__stdout__)
        if not isinstance(sys.stderr, StreamToLogger):
            sys.stderr = StreamToLogger(stdio_logger, logging.INFO, sys.__stderr__)
