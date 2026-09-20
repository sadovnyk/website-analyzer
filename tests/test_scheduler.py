from unittest.mock import AsyncMock, patch

from scheduler import run_schedule


async def test_run_schedule_scans_all_active_sites():
    active_sites = {
        "success": True,
        "error": None,
        "result": [
            {"id": 1, "url": "https://a.com"},
            {"id": 2, "url": "https://b.com"},
        ],
    }

    with patch("scheduler.get_sites", return_value=active_sites) as mock_get_sites, \
         patch("scheduler.scan_one_site", new=AsyncMock()) as mock_scan_one_site:

        await run_schedule()

        # run_schedule calls asyncio.to_thread(get_sites, True) -> positional argument
        mock_get_sites.assert_called_once_with(True)
        assert mock_scan_one_site.await_count == 2
        mock_scan_one_site.assert_any_await("https://a.com", 1)
        mock_scan_one_site.assert_any_await("https://b.com", 2)


async def test_run_schedule_scans_sites_sequentially_in_order():
    active_sites = {
        "success": True, "error": None,
        "result": [{"id": 1, "url": "https://a.com"}, {"id": 2, "url": "https://b.com"}],
    }

    with patch("scheduler.get_sites", return_value=active_sites), \
         patch("scheduler.scan_one_site", new=AsyncMock()) as mock_scan_one_site:

        await run_schedule()

    assert [c.args for c in mock_scan_one_site.await_args_list] == [
        ("https://a.com", 1), ("https://b.com", 2),
    ]


async def test_run_schedule_no_active_sites_logs_warning_and_skips_scan():
    active_sites = {"success": True, "error": None, "result": []}

    with patch("scheduler.get_sites", return_value=active_sites), \
         patch("scheduler.scan_one_site", new=AsyncMock()) as mock_scan_one_site, \
         patch("scheduler.logger") as mock_logger:

        await run_schedule()

        mock_scan_one_site.assert_not_awaited()
        mock_logger.warning.assert_called_once_with("There are no active sites to scan.")


async def test_run_schedule_get_sites_fails_logs_error_and_skips_scan():
    active_sites = {"success": False, "error": "db_select_failed", "result": []}

    with patch("scheduler.get_sites", return_value=active_sites), \
         patch("scheduler.scan_one_site", new=AsyncMock()) as mock_scan_one_site, \
         patch("scheduler.logger") as mock_logger:

        await run_schedule()

        mock_scan_one_site.assert_not_awaited()
        mock_logger.error.assert_called_once_with("Database error: db_select_failed")


async def test_run_schedule_logs_launch_message():
    active_sites = {"success": True, "error": None, "result": []}

    with patch("scheduler.get_sites", return_value=active_sites), \
         patch("scheduler.scan_one_site", new=AsyncMock()), \
         patch("scheduler.logger") as mock_logger:

        await run_schedule()

        mock_logger.info.assert_any_call("Launching the scheduler...")


async def test_run_schedule_logs_info_per_site_before_scanning():
    active_sites = {
        "success": True,
        "error": None,
        "result": [{"id": 5, "url": "https://example.com"}],
    }

    with patch("scheduler.get_sites", return_value=active_sites), \
         patch("scheduler.scan_one_site", new=AsyncMock()) as mock_scan_one_site, \
         patch("scheduler.logger") as mock_logger:

        await run_schedule()

        mock_logger.info.assert_any_call("Website scan: https://example.com")
        mock_scan_one_site.assert_awaited_once_with("https://example.com", 5)


async def test_run_schedule_continues_scanning_remaining_sites_if_one_scan_raises():
    active_sites = {
        "success": True,
        "error": None,
        "result": [
            {"id": 1, "url": "https://a.com"},
            {"id": 2, "url": "https://b.com"},
        ],
    }
    mock_scan_one_site = AsyncMock(side_effect=[Exception("boom"), None])

    with patch("scheduler.get_sites", return_value=active_sites), \
         patch("scheduler.scan_one_site", new=mock_scan_one_site), \
         patch("scheduler.logger") as mock_logger:

        await run_schedule()          # the error is logged, it must not stop the loop

    assert mock_scan_one_site.await_count == 2
    mock_scan_one_site.assert_any_await("https://b.com", 2)
    mock_logger.error.assert_called_once()
    assert "https://a.com" in mock_logger.error.call_args.args[0]
    assert "boom" in mock_logger.error.call_args.args[0]
