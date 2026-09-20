from unittest.mock import AsyncMock, MagicMock, patch
import main

async def test_analyze_combines_all_checks_into_one_result():
    status = {"status_code": 200, "title": "T", "description": "D", "duration_ms": 10, "error": None}
    cert = {"ssl_days_left": 50, "valid": True, "error": None}
    links = {"url": "https://example.com", "broken_links": [], "error": None, "total_links": 7}
    geo = {"country": "UA", "city": "Kyiv", "org": "Org", "error": None}

    with patch("main.check_status", new=AsyncMock(return_value=status)), \
         patch("main.get_ip", return_value="1.2.3.4") as mock_get_ip, \
         patch("main.certificate", return_value=cert) as mock_certificate, \
         patch("main.broken_links", new=AsyncMock(return_value=links)), \
         patch("main.get_geolocation", new=AsyncMock(return_value=geo)) as mock_geo:

        result = await main.analyze("https://example.com")

    assert result == {
        "status": status,
        "ip": "1.2.3.4",
        "geo": geo,
        "cert": cert,
        "links": links,
        "number_of_links": 7,
    }
    mock_get_ip.assert_called_once_with("https://example.com")
    mock_certificate.assert_called_once_with("https://example.com")
    mock_geo.assert_awaited_once_with("1.2.3.4")   # geolocation uses the resolved IP


async def test_analyze_number_of_links_is_none_when_links_check_failed():
    links = {"url": "u", "broken_links": [], "error": "timeout", "total_links": None}

    with patch("main.check_status", new=AsyncMock(return_value={})), \
         patch("main.get_ip", return_value=None), \
         patch("main.certificate", return_value={}), \
         patch("main.broken_links", new=AsyncMock(return_value=links)), \
         patch("main.get_geolocation", new=AsyncMock(return_value={"error": "invalid_ip"})):

        result = await main.analyze("https://example.com")

    assert result["number_of_links"] is None
    assert result["links"]["error"] == "timeout"
