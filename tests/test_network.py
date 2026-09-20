import asyncio

import aiohttp
import pytest
from unittest.mock import MagicMock

from core.config import HEADERS
from core.network import check_status

URL = "https://www.google.com"


def page(html, status=200):
    return {"status": status, "html": html}


@pytest.mark.parametrize("exception_to_raise, expected_error", [
    (aiohttp.InvalidURL(url="hdfhdfhu"), "invalid_url"),
    (ValueError, "invalid_url"),
    (aiohttp.ClientConnectorError(connection_key=MagicMock(), os_error=OSError()), "connection_failed"),
    (asyncio.TimeoutError, "timeout"),
    (aiohttp.ClientError, "client_error"),
])
async def test_get_exception_returns_error_for_check_status(fake_http, exception_to_raise, expected_error):
    fake_http.get(URL, exception=exception_to_raise)

    result = await check_status(URL)

    assert result["error"] == expected_error
    assert result["status_code"] is None
    assert result["description"] is None
    assert result["title"] is None
    assert result["duration_ms"] is None


async def test_check_status_success_extracts_title_and_description(fake_http):
    fake_http.get(URL, html="""
        <html>
            <head>
                <title>  My Page Title  </title>
                <meta name="description" content="My page description">
            </head>
            <body>Content</body>
        </html>
    """)

    result = await check_status(URL)

    assert result["error"] is None
    assert result["status_code"] == 200
    assert result["title"] == "My Page Title"
    assert result["description"] == "My page description"
    assert isinstance(result["duration_ms"], int)
    assert result["duration_ms"] >= 0


async def test_check_status_no_title_tag_returns_none(fake_http):
    fake_http.get(URL, html="<html><head><meta name='description' content='Desc only'></head></html>")

    result = await check_status(URL)

    assert result["title"] is None
    assert result["description"] == "Desc only"
    assert result["error"] is None


async def test_check_status_no_meta_description_returns_none(fake_http):
    fake_http.get(URL, html="<html><head><title>Only Title</title></head><body></body></html>")

    result = await check_status(URL)

    assert result["title"] == "Only Title"
    assert result["description"] is None
    assert result["error"] is None


async def test_check_status_no_title_and_no_description(fake_http):
    fake_http.get(URL, html="<html><head></head><body>Nothing here</body></html>")

    result = await check_status(URL)

    assert result["title"] is None
    assert result["description"] is None
    assert result["error"] is None
    assert result["status_code"] == 200


@pytest.mark.parametrize("status_code", [200, 404, 500])
async def test_check_status_returns_actual_status_code_regardless_of_value(fake_http, status_code):
    fake_http.get(URL, status=status_code, html="<html><head><title>T</title></head></html>")

    result = await check_status(URL)

    assert result["status_code"] == status_code
    assert result["error"] is None
    assert result["title"] == "T"


async def test_check_status_strips_whitespace_from_title(fake_http):
    fake_http.get(URL, html="<html><head><title>\n   Spaced Title   \n</title></head></html>")

    assert (await check_status(URL))["title"] == "Spaced Title"


async def test_check_status_meta_description_with_empty_content(fake_http):
    fake_http.get(URL, html='<html><head><meta name="description" content=""></head></html>')

    assert (await check_status(URL))["description"] == ""

async def test_check_status_meta_description_without_content_attribute(fake_http):
    fake_http.get(URL, html='<html><head><meta name="description"></head></html>')

    result = await check_status(URL)

    assert result["error"] is None
    assert result["description"] is None


async def test_check_status_non_html_content_type(fake_http):
    fake_http.get(URL, body=b"%PDF-1.7 ...", headers={"Content-Type": "application/pdf"})

    result = await check_status(URL)

    assert result["status_code"] == 200
    assert result["title"] == "Not HTML"
    assert result["description"] is None
    assert result["error"] is None


async def test_check_status_missing_content_type_is_treated_as_not_html(fake_http):
    fake_http.get(URL, body=b"<html><title>x</title></html>")

    assert (await check_status(URL))["title"] == "Not HTML"


async def test_check_status_uses_10_second_timeout(fake_http):
    fake_http.get(URL, html="<html><head><title>T</title></head></html>")

    await check_status(URL)

    (_, kwargs) = fake_http.session_class.call_args
    assert kwargs["timeout"].total == 10


async def test_check_status_passes_headers_to_session(fake_http):
    fake_http.get(URL, html="<html><head><title>T</title></head></html>")

    await check_status(URL)

    (_, kwargs) = fake_http.session_class.call_args
    assert kwargs["headers"] == HEADERS


async def test_check_status_calls_get_with_correct_url_and_no_auto_redirects(fake_http):
    """Redirects must be followed manually so every hop can be checked by is_safe_host."""
    fake_http.get("https://www.example.com/page", html="<html><head><title>T</title></head></html>")

    await check_status("https://www.example.com/page")

    assert fake_http.calls == [("GET", "https://www.example.com/page", {"allow_redirects": False})]


@pytest.mark.parametrize("redirect_status", [301, 302, 303, 307, 308])
async def test_check_status_follows_redirect_and_reports_final_page(fake_http, redirect_status):
    fake_http.get(URL, status=redirect_status, headers={"Location": "https://www.google.com/final"})
    fake_http.get("https://www.google.com/final", html="<html><head><title>Final</title></head></html>")

    result = await check_status(URL)

    assert result["status_code"] == 200
    assert result["title"] == "Final"
    assert fake_http.requested("GET") == [URL, "https://www.google.com/final"]


async def test_check_status_resolves_relative_redirect_location(fake_http):
    fake_http.get(URL, status=302, headers={"Location": "/login"})
    fake_http.get("https://www.google.com/login", html="<html><head><title>Login</title></head></html>")

    assert (await check_status(URL))["title"] == "Login"


async def test_check_status_blocks_initial_unsafe_host(fake_http, public_dns):
    public_dns.return_value = {"10.0.0.5"}

    result = await check_status(URL)

    assert result["error"] == "unsafe_url"
    assert result["status_code"] is None
    assert fake_http.calls == []  # no request must be sent at all


async def test_check_status_blocks_redirect_to_internal_address(fake_http, public_dns):
    """A public site redirecting to the cloud-metadata IP must not be followed."""
    public_dns.side_effect = [{"93.184.216.34"}, {"169.254.169.254"}]
    fake_http.get(URL, status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    result = await check_status(URL)

    assert result["error"] == "unsafe_url"
    assert fake_http.requested("GET") == [URL]


async def test_check_status_gives_up_after_5_redirects(fake_http):
    fake_http.get(URL, status=302, headers={"Location": URL}, repeat=True)

    result = await check_status(URL)

    assert result["error"] == "too_many_redirects"
    assert len(fake_http.calls) == 5


async def test_check_status_redirect_without_location_header_ends_as_too_many_redirects(fake_http):
    fake_http.get(URL, status=301, repeat=True)

    assert (await check_status(URL))["error"] == "too_many_redirects"
