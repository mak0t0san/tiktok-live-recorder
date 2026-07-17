import logging
from logging.handlers import RotatingFileHandler

from utils.logger_manager import LoggerManager


def _console_handlers():
    logger = LoggerManager().logger
    return [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, RotatingFileHandler)
    ]


def _record(level):
    return logging.LogRecord("logger", level, __file__, 0, "msg", None, None)


def test_warning_reaches_a_console_handler():
    record = _record(logging.WARNING)
    assert any(
        h.level <= record.levelno and h.filter(record) for h in _console_handlers()
    )


def test_info_reaches_exactly_one_console_handler():
    record = _record(logging.INFO)
    passing = [
        h for h in _console_handlers() if h.level <= record.levelno and h.filter(record)
    ]
    assert len(passing) == 1
