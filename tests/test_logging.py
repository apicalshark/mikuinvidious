# Copyright (c) 2023-2026 MikuInvidious Team
# SPDX-License-Identifier: AGPL-3.0-or-later

import io
import logging
import os
import re
import unittest
from unittest.mock import patch

from miku.core.logging import (
    MikuFormatter,
    StreamToLogger,
    get_granian_log_dictconfig,
    should_use_color,
)


class TestMikuLogging(unittest.TestCase):
    def test_should_use_color_respects_no_color_and_log_color(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(should_use_color())

        with patch.dict(os.environ, {"NO_COLOR": "1"}, clear=True):
            self.assertFalse(should_use_color())

        with patch.dict(os.environ, {"LOG_COLOR": "false"}, clear=True):
            self.assertFalse(should_use_color())

        with patch.dict(os.environ, {"LOG_COLOR": "always"}, clear=True):
            self.assertTrue(should_use_color())

    def test_formatter_includes_timestamp_level_and_tag_without_color(self) -> None:
        fmt = MikuFormatter(use_color=False)
        record = logging.LogRecord(
            name="miku.stdio",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="[DashProxy] audio stream ready",
            args=(),
            exc_info=None,
        )
        formatted = fmt.format(record)
        self.assertRegex(
            formatted,
            r"^\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[INFO\] \[DashProxy\] audio stream ready$",
        )

    def test_formatter_emits_ansi_colors_when_enabled(self) -> None:
        fmt = MikuFormatter(use_color=True)
        record = logging.LogRecord(
            name="_granian",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Worker restarting",
            args=(),
            exc_info=None,
        )
        formatted = fmt.format(record)
        self.assertIn("\033[38;5;208m", formatted)
        self.assertIn("\033[0m", formatted)
        plain = re.sub(r"\033\[[0-9;]*m", "", formatted)
        self.assertRegex(
            plain,
            r"^\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[WARN\] \[Granian\] Worker restarting$",
        )

    def test_formatter_strips_redundant_error_tag(self) -> None:
        fmt = MikuFormatter(use_color=False)
        record = logging.LogRecord(
            name="miku.stdio",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="[ERROR] Live list error: boom",
            args=(),
            exc_info=None,
        )
        formatted = fmt.format(record)
        self.assertNotIn("[ERROR] [ERROR]", formatted)
        self.assertIn("[ERROR] [App] Live list error: boom", formatted)

    def test_stream_to_logger_buffers_lines_and_infers_levels(self) -> None:
        target_logger = logging.getLogger("test.miku.stream")
        target_logger.setLevel(logging.DEBUG)
        target_logger.propagate = False
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(MikuFormatter(use_color=False))
        target_logger.handlers = [handler]

        stream = StreamToLogger(target_logger, level=logging.INFO)
        stream.write("[Init] Booting")
        self.assertEqual(buf.getvalue(), "")
        stream.write(" up\n[ERROR] Something broke\n")

        lines = buf.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("[INFO] [Init] Booting up", lines[0])
        self.assertIn("[ERROR] Something broke", lines[1])

    def test_stream_to_logger_reentrant_handler_falls_back_without_deadlock(self) -> None:
        target_logger = logging.getLogger("test.miku.stream.reentrant")
        target_logger.setLevel(logging.DEBUG)
        target_logger.propagate = False
        fallback = io.StringIO()
        stream = StreamToLogger(target_logger, level=logging.INFO, fallback_stream=fallback)

        class ReentrantHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                stream.write("reentrant line\n")

        target_logger.handlers = [ReentrantHandler()]
        stream.write("outer line\n")
        self.assertIn("reentrant line\n", fallback.getvalue())

    def test_granian_dictconfig_structure(self) -> None:
        cfg = get_granian_log_dictconfig(level="DEBUG", use_color=False)
        self.assertEqual(cfg["version"], 1)
        self.assertEqual(cfg["loggers"]["_granian"]["level"], "DEBUG")
        self.assertEqual(cfg["loggers"]["granian.access"]["handlers"], ["access"])
        self.assertEqual(cfg["loggers"]["httpx"]["level"], "WARNING")
        self.assertEqual(cfg["loggers"]["httpcore"]["level"], "WARNING")
        self.assertEqual(cfg["handlers"]["console"]["stream"], "ext://sys.__stderr__")
