import threading
from unittest.mock import AsyncMock, patch

from core.scan_runner import scan_one_site


def make_scan_data():
    return {
        "status": {
            "status_code": 200,
            "title": "Example",
            "description": "desc",
            "duration_ms": 123,
            "error": None,
        },
        "ip": "1.2.3.4",
        "geo": {"country": "UA", "city": "Kyiv", "org": "Org", "error": None},
        "cert": {"ssl_days_left": 90, "valid": True, "error": None},
        "number_of_links": 5,
        "links": {"error": None, "broken_links": []},
    }


async def test_scan_one_site_success():
    fake_scan_data = make_scan_data()

    with patch("core.scan_runner.analyze", new=AsyncMock(return_value=fake_scan_data)) as mock_analyze, \
         patch("core.scan_runner.save_scan") as mock_save_scan, \
         patch("core.scan_runner.save_links") as mock_save_links, \
         patch("core.scan_runner.logger") as mock_logger:

        mock_save_scan.return_value = {"success": True, "scan_id": 42, "error": None}
        mock_save_links.return_value = {"success": True, "scan_id": 42, "error": None}

        await scan_one_site("https://example.com", site_id=1)

        mock_analyze.assert_awaited_once_with("https://example.com")
        mock_save_scan.assert_called_once_with("https://example.com", fake_scan_data, 1)
        mock_save_links.assert_called_once_with(42, fake_scan_data["links"])
        mock_logger.error.assert_not_called()


async def test_scan_one_site_save_scan_fails():
    fake_scan_data = {"links": {"broken_links": []}}

    with patch("core.scan_runner.analyze", new=AsyncMock(return_value=fake_scan_data)), \
         patch("core.scan_runner.save_scan") as mock_save_scan, \
         patch("core.scan_runner.save_links") as mock_save_links, \
         patch("core.scan_runner.logger") as mock_logger:

        mock_save_scan.return_value = {"success": False, "error": "db_insert_failed", "scan_id": None}

        await scan_one_site("https://example.com", site_id=1)

        mock_save_links.assert_not_called()
        mock_logger.error.assert_called_once()
        assert "db_insert_failed" in mock_logger.error.call_args.args[0]


async def test_scan_one_site_logs_error_when_mongo_save_fails():
    with patch("core.scan_runner.analyze", new=AsyncMock(return_value=make_scan_data())), \
         patch("core.scan_runner.save_scan", return_value={"success": True, "scan_id": 42, "error": None}), \
         patch("core.scan_runner.save_links",
               return_value={"success": False, "scan_id": 42, "error": "db_insert_failed"}), \
         patch("core.scan_runner.logger") as mock_logger:

        await scan_one_site("https://example.com", site_id=1)

        mock_logger.error.assert_called_once()
        assert "Mongo" in mock_logger.error.call_args.args[0]


async def test_scan_one_site_blocks_unsafe_host(public_dns):
    public_dns.return_value = {"192.168.1.10"}

    with patch("core.scan_runner.analyze", new=AsyncMock()) as mock_analyze, \
         patch("core.scan_runner.save_scan") as mock_save_scan, \
         patch("core.scan_runner.logger") as mock_logger:

        await scan_one_site("https://sneaky.example.com", site_id=1)

        mock_analyze.assert_not_awaited()
        mock_save_scan.assert_not_called()
        mock_logger.warning.assert_called_once()
        assert "sneaky.example.com" in mock_logger.warning.call_args.args[0]


async def test_scan_one_site_never_raises_when_analyze_crashes():
    """It runs as a FastAPI background task: an exception must be logged, not propagated."""
    with patch("core.scan_runner.analyze", new=AsyncMock(side_effect=RuntimeError("kaboom"))), \
         patch("core.scan_runner.save_scan") as mock_save_scan, \
         patch("core.scan_runner.logger") as mock_logger:

        await scan_one_site("https://example.com", site_id=1)   # must not raise

        mock_save_scan.assert_not_called()
        mock_logger.error.assert_called_once()
        assert "kaboom" in mock_logger.error.call_args.args[0]


async def test_scan_one_site_never_raises_when_db_layer_raises():
    with patch("core.scan_runner.analyze", new=AsyncMock(return_value=make_scan_data())), \
         patch("core.scan_runner.save_scan", side_effect=RuntimeError("db exploded")), \
         patch("core.scan_runner.logger") as mock_logger:

        await scan_one_site("https://example.com", site_id=1)

        mock_logger.error.assert_called_once()

async def test_scan_one_site_does_not_run_blocking_db_calls_on_the_event_loop():
    threads = []

    def fake_save_scan(*args):
        threads.append(threading.current_thread())
        return {"success": True, "scan_id": 1, "error": None}

    def fake_save_links(*args):
        threads.append(threading.current_thread())
        return {"success": True, "scan_id": 1, "error": None}

    with patch("core.scan_runner.analyze", new=AsyncMock(return_value=make_scan_data())), \
         patch("core.scan_runner.save_scan", side_effect=fake_save_scan), \
         patch("core.scan_runner.save_links", side_effect=fake_save_links):

        await scan_one_site("https://example.com", site_id=1)

    assert len(threads) == 2
    assert all(t is not threading.main_thread() for t in threads)
