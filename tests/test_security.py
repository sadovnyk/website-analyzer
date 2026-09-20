import socket
import ssl
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
import pytest

from core.security import (
    _is_public_ip,
    certificate,
    get_domain,
    get_geolocation,
    get_ip,
    get_normalized_url,
    is_safe_host,
)

from core.security import _resolve_all_ips as real_resolve_all_ips


def test_get_domain_returns_netloc():
    assert get_domain("https://example.com/path") == "example.com"


def test_get_domain_keeps_port_in_netloc():
    assert get_domain("https://example.com:8080/x") == "example.com:8080"


def test_get_domain_returns_none_on_value_error():
    assert get_domain("http://[::1") is None  # "Invalid IPv6 URL"


def test_get_ip_returns_none_for_invalid_url():
    assert get_ip(" fghfugh ") is None


def test_get_ip_returns_none_for_empty_string():
    assert get_ip("") is None


@patch("core.security.socket.gethostbyname")
def test_get_ip_success_returns_resolved_ip(mock_gethostbyname):
    mock_gethostbyname.return_value = "142.250.74.68"

    assert get_ip("https://www.google.com") == "142.250.74.68"
    mock_gethostbyname.assert_called_once_with("www.google.com")


@pytest.mark.parametrize("exception_to_raise", [ValueError, UnicodeError, socket.gaierror])
@patch("core.security.socket.gethostbyname")
def test_get_exception_returns_error_for_get_ip(mock_gethostbyname, exception_to_raise):
    mock_gethostbyname.side_effect = exception_to_raise
    assert get_ip("https://www.google.com") is None


def test_resolve_all_ips_returns_unique_addresses():
    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("93.184.216.34", 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1::1", 0, 0, 0)),
    ]
    with patch("core.security.socket.getaddrinfo", return_value=infos):
        assert real_resolve_all_ips("example.com") == {"93.184.216.34", "2606:2800:220:1::1"}


def test_resolve_all_ips_returns_empty_set_when_dns_fails():
    with patch("core.security.socket.getaddrinfo", side_effect=socket.gaierror):
        assert real_resolve_all_ips("does-not-exist.invalid") == set()


@pytest.mark.parametrize("ip", [
    "8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946",
])
def test_is_public_ip_true_for_public_addresses(ip):
    assert _is_public_ip(ip) is True


@pytest.mark.parametrize("ip", [
    "127.0.0.1",              # loopback
    "10.1.2.3",               # private
    "172.16.0.1",             # private
    "192.168.0.1",            # private
    "169.254.169.254",        # link-local (cloud metadata endpoint)
    "0.0.0.0",                # unspecified
    "224.0.0.1",              # multicast
    "240.0.0.1",              # reserved
    "::1",                    # IPv6 loopback
    "fe80::1",                # IPv6 link-local
    "fc00::1",                # IPv6 unique local
    "::ffff:127.0.0.1",       # IPv4-mapped loopback
    "::ffff:10.0.0.1",        # IPv4-mapped private
    "not-an-ip",              # garbage is treated as unsafe
    "",
])
def test_is_public_ip_false_for_non_public_addresses(ip):
    assert _is_public_ip(ip) is False


def test_is_safe_host_true_when_all_ips_are_public(public_dns):
    public_dns.return_value = {"8.8.8.8", "1.1.1.1"}
    assert is_safe_host("https://example.com") is True
    public_dns.assert_called_once_with("example.com")


def test_is_safe_host_false_when_any_ip_is_private(public_dns):
    """One private address among public ones is enough to reject (DNS rebinding trick)."""
    public_dns.return_value = {"8.8.8.8", "10.0.0.5"}
    assert is_safe_host("https://example.com") is False


def test_is_safe_host_false_when_host_does_not_resolve(public_dns):
    public_dns.return_value = set()
    assert is_safe_host("https://nonexistent.example") is False


@pytest.mark.parametrize("url", ["http://", "not a url", "", "/just/a/path"])
def test_is_safe_host_false_when_url_has_no_hostname(url, public_dns):
    assert is_safe_host(url) is False
    public_dns.assert_not_called()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1",
    "http://127.0.0.1:8080/admin",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:7f00:1]/",
])
def test_is_safe_host_blocks_literal_internal_addresses(url, public_dns):
    public_dns.side_effect = lambda host: {host}   # literal IPs resolve to themselves
    assert is_safe_host(url) is False


@pytest.fixture
def tls():
    """Mocks the socket -> TLS-wrapped socket chain used by certificate()."""
    with patch("core.security.ssl.create_default_context") as create_context, \
         patch("core.security.socket.create_connection") as create_connection:
        context = create_context.return_value
        raw_sock = create_connection.return_value.__enter__.return_value
        tls_sock = context.wrap_socket.return_value.__enter__.return_value
        yield SimpleNamespace(
            create_connection=create_connection,
            context=context,
            raw_sock=raw_sock,
            tls_sock=tls_sock,
        )


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2024, 1, 1, tzinfo=timezone.utc)


def test_get_certificate_returns_error_for_invalid_url():
    result = certificate("not a url")
    assert result["error"] == "invalid_url"


def test_certificate_not_found_when_peercert_is_none(tls):
    tls.tls_sock.getpeercert.return_value = None

    result = certificate("https://www.google.com")

    assert result["error"] == "certificate_not_found"
    assert result["valid"] is None
    assert result["ssl_days_left"] is None


def test_certificate_not_found_when_notAfter_is_not_a_string(tls):
    tls.tls_sock.getpeercert.return_value = {"notAfter": 1234567890}

    result = certificate("https://www.google.com")

    assert result["error"] == "certificate_not_found"
    assert result["valid"] is None
    assert result["ssl_days_left"] is None


def test_certificate_not_found_when_notAfter_missing(tls):
    tls.tls_sock.getpeercert.return_value = {"subject": ()}

    assert certificate("https://www.google.com")["error"] == "certificate_not_found"


@pytest.mark.parametrize("cert_date, expected_valid", [
    ("Sep 10 12:00:00 2035 GMT", True),
    ("Sep 10 12:00:00 2021 GMT", False),
])
def test_certificate_validity_dates(tls, cert_date, expected_valid):
    tls.tls_sock.getpeercert.return_value = {"notAfter": cert_date}

    result = certificate("https://www.google.com")

    assert result["error"] is None
    assert result["valid"] is expected_valid


@patch("core.security.datetime", FrozenDateTime)
def test_certificate_calculates_exact_days_left(tls):
    tls.tls_sock.getpeercert.return_value = {"notAfter": "Jan 11 00:00:00 2024 GMT"}

    result = certificate("https://www.google.com")

    assert result["error"] is None
    assert result["valid"] is True
    assert result["ssl_days_left"] == 10


@patch("core.security.datetime", FrozenDateTime)
def test_certificate_expired_yesterday_is_invalid_with_negative_days(tls):
    tls.tls_sock.getpeercert.return_value = {"notAfter": "Dec 31 00:00:00 2023 GMT"}

    result = certificate("https://www.google.com")

    assert result["error"] is None
    assert result["valid"] is False
    assert result["ssl_days_left"] < 0


def test_certificate_calls_wrap_socket_with_correct_hostname(tls):
    tls.tls_sock.getpeercert.return_value = {"notAfter": "Sep 10 12:00:00 2035 GMT"}

    certificate("https://www.google.com")

    tls.create_connection.assert_called_once_with(("www.google.com", 443), timeout=10)
    tls.context.wrap_socket.assert_called_once_with(tls.raw_sock, server_hostname="www.google.com")


@pytest.mark.parametrize("exception_to_raise, expected_error", [
    (ssl.SSLError, "ssl_error"),
    (OSError, "connection_error"),
    (socket.timeout, "connection_error"),
    (UnicodeError, "connection_error"),
    (KeyError, "certificate_not_found"),
    (ValueError, "certificate_not_found"),
])
def test_certificate_maps_exceptions_to_error_codes(tls, exception_to_raise, expected_error):
    tls.tls_sock.getpeercert.side_effect = exception_to_raise

    result = certificate("https://www.google.com")

    assert result["error"] == expected_error
    assert result["valid"] is None
    assert result["ssl_days_left"] is None


def test_certificate_connection_refused_is_connection_error(tls):
    tls.create_connection.side_effect = ConnectionRefusedError

    assert certificate("https://www.google.com")["error"] == "connection_error"

def test_certificate_uses_connection_timeout(tls):
    tls.tls_sock.getpeercert.return_value = {"notAfter": "Sep 10 12:00:00 2035 GMT"}

    certificate("https://www.google.com")

    _, kwargs = tls.create_connection.call_args
    assert kwargs.get("timeout")

@pytest.mark.parametrize("exception_to_raise", [aiohttp.ClientError, ValueError])
async def test_get_geolocation_returns_error_on_exception(fake_http, exception_to_raise):
    fake_http.get("https://ipinfo.io/8.8.8.8/json", exception=exception_to_raise)

    result = await get_geolocation("8.8.8.8")

    assert result["error"] == "failed_to_fetch_geolocation"
    assert result["country"] is None


async def test_get_geolocation_returns_error_on_api_failure(fake_http):
    fake_http.get("https://ipinfo.io/8.8.8.8/json", status=404, body="Not Found")

    result = await get_geolocation("8.8.8.8")

    assert result["error"] == "failed_to_fetch_geolocation"
    assert result["country"] is None
    assert result["city"] is None
    assert result["org"] is None


async def test_get_geolocation_returns_error_for_none_ip(fake_http):
    result = await get_geolocation(None)

    assert result["error"] == "invalid_ip"
    assert fake_http.calls == []


async def test_get_geolocation_success_returns_country_city_org(fake_http):
    fake_http.get("https://ipinfo.io/8.8.8.8/json",
                  json_data={"country": "US", "city": "Mountain View", "org": "Google LLC"})

    result = await get_geolocation("8.8.8.8")

    assert result == {"country": "US", "city": "Mountain View", "org": "Google LLC", "error": None}


async def test_get_geolocation_missing_fields_default_to_none(fake_http):
    fake_http.get("https://ipinfo.io/8.8.8.8/json", json_data={"country": "US"})

    result = await get_geolocation("8.8.8.8")

    assert result["error"] is None
    assert result["country"] == "US"
    assert result["city"] is None
    assert result["org"] is None


async def test_get_geolocation_empty_response_returns_all_none(fake_http):
    fake_http.get("https://ipinfo.io/8.8.8.8/json", json_data={})

    result = await get_geolocation("8.8.8.8")

    assert result == {"country": None, "city": None, "org": None, "error": None}


async def test_get_geolocation_uses_correct_url_for_given_ip(fake_http):
    fake_http.get("https://ipinfo.io/1.1.1.1/json", json_data={"country": "AU"})

    result = await get_geolocation("1.1.1.1")

    assert result["country"] == "AU"
    assert fake_http.requested("GET") == ["https://ipinfo.io/1.1.1.1/json"]


@pytest.mark.parametrize("raw_url, expected_url", [
    ("https://example.com", "https://example.com"),
    ("http://example.com", "http://example.com"),
    ("example.com", "https://example.com"),
    ("sub.domain.com", "https://sub.domain.com"),
    ("   https://example.com   ", "https://example.com"),
    ("   example.com/test   ", "https://example.com/test"),
    ("https://example.com/path/to/page", "https://example.com/path/to/page"),
    ("example.com/search?q=pytest&lang=ua", "https://example.com/search?q=pytest&lang=ua"),
    ("example.com#section", "https://example.com#section"),
    ("https://example.com:8080/api", "https://example.com:8080/api"),
])
def test_valid_urls_return_success(raw_url, expected_url):
    assert get_normalized_url(raw_url) == {"url": expected_url, "error": None}


def test_max_length_boundary_255_chars():
    base = "https://example.com/"
    valid_url = base + ("a" * (255 - len(base)))

    assert get_normalized_url(valid_url) == {"url": valid_url, "error": None}


@pytest.mark.parametrize("invalid_input", [None, 123, 12.34, [], {}, True, b"https://example.com"])
def test_non_string_input_returns_invalid_url(invalid_input):
    assert get_normalized_url(invalid_input) == {"url": None, "error": "invalid_url"}


@pytest.mark.parametrize("empty_input", ["", "   ", "\t\n"])
def test_empty_string_returns_empty_url(empty_input):
    assert get_normalized_url(empty_input) == {"url": None, "error": "empty_url"}


def test_url_exceeding_max_length_returns_error():
    base = "https://example.com/"
    too_long_url = base + ("a" * (256 - len(base)))

    assert get_normalized_url(too_long_url) == {"url": None, "error": "url_too_long"}


@pytest.mark.parametrize("unsupported_scheme_url", [
    "ftp://files.example.com",
    "mailto:user@example.com",
    "ssh://git@github.com",
    "ws://example.com/socket",
    "javascript:alert(1)",
    "file:///etc/passwd",
])
def test_invalid_scheme_returns_error(unsupported_scheme_url):
    assert get_normalized_url(unsupported_scheme_url) == {"url": None, "error": "invalid_scheme"}


@pytest.mark.parametrize("invalid_netloc_url", [
    "localhost",
    "http://localhost",
    "http://mycomputer/path",
    "someinternalname",
    "https://",
    "http://",
])
def test_missing_dot_or_empty_netloc_returns_invalid_url(invalid_netloc_url):
    assert get_normalized_url(invalid_netloc_url) == {"url": None, "error": "invalid_url"}


def test_urlparse_value_error_handling():
    target_path = f"{get_normalized_url.__module__}.urlparse"

    with patch(target_path, side_effect=ValueError("Test error")):
        result = get_normalized_url("https://example.com")

    assert result == {"url": None, "error": "invalid_url"}


@pytest.mark.parametrize("url", [
    "http://127.0.0.1",
    "127.0.0.1:8080/admin",
    "http://10.0.0.1/",
    "192.168.1.1",
    "http://169.254.169.254/latest/meta-data/",
])
def test_internal_ip_literals_are_rejected_as_unsafe(url, public_dns):
    public_dns.side_effect = lambda host: {host}

    assert get_normalized_url(url) == {"url": None, "error": "unsafe_url"}


def test_hostname_resolving_to_private_ip_is_rejected(public_dns):
    public_dns.return_value = {"10.0.0.5"}

    assert get_normalized_url("https://intranet.example.com") == {"url": None, "error": "unsafe_url"}


def test_unresolvable_hostname_is_rejected(public_dns):
    public_dns.return_value = set()

    assert get_normalized_url("https://no-such-host.example.com") == {"url": None, "error": "unsafe_url"}
