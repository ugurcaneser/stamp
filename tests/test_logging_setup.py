import logging
import os

from core.logging_setup import configure_logging, get_logger


def test_configure_logging_creates_file_and_writes(tmp_path):
    log_path = str(tmp_path / "sub" / "stamp.log")
    logger = configure_logging(log_path=log_path)
    logger.info("hello from test")
    for handler in logger.handlers:
        handler.flush()

    assert os.path.exists(log_path)
    content = open(log_path).read()
    assert "hello from test" in content
    assert "session started" in content.lower()


def test_configure_logging_is_idempotent(tmp_path):
    log_path = str(tmp_path / "stamp.log")
    logger1 = configure_logging(log_path=log_path)
    handler_count_1 = len(logger1.handlers)
    logger2 = configure_logging(log_path=log_path)
    assert len(logger2.handlers) == handler_count_1  # no duplicate handlers stacked


def test_get_logger_returns_named_logger():
    assert get_logger().name == "stamp"
