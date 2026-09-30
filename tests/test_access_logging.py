import logging

from app.access_logging import RedactQueryStrings


def test_oauth_codes_are_removed_but_access_metadata_remains():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:50000", "GET", "/auth/google/callback?code=private-code&state=private-state", "1.1", 303), None)
    assert RedactQueryStrings().filter(record)
    assert record.getMessage() == '127.0.0.1:50000 - "GET /auth/google/callback HTTP/1.1" 303'


def test_ordinary_access_logs_still_work():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:50000", "GET", "/health", "1.1", 200), None)
    assert RedactQueryStrings().filter(record)
    assert "/health HTTP/1.1" in record.getMessage()
