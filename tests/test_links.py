import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp
import pytest
from core import links
from core.links import broken_links, check_link, check_links, extract_links

URL = "https://www.google.com"


def html_with_links(*hrefs):
    return "<html><body>" + "".join(f'<a href="{h}">x</a>' for h in hrefs) + "</body></html>"


@pytest.mark.parametrize("exception_to_raise, expected_error", [
    (aiohttp.InvalidURL(url="hdfhdfhu"), "invalid_url"),
    (ValueError, "invalid_url"),
    (aiohttp.ClientConnectorError(connection_key=MagicMock(), os_error=OSError()), "connection_failed"),
    (asyncio.TimeoutError, "timeout"),
    (aiohttp.ClientError, "client_error"),
])
async def test_get_exception_returns_error_for_extract_links(fake_http, exception_to_raise, expected_error):
    fake_http.get(URL, exception=exception_to_raise)

    result = await extract_links(URL)

    assert result["error"] == expected_error
    assert result["links"] is None
    assert result["status"] is None


async def test_extract_links_handles_non_200_status(fake_http):
    fake_http.get(URL, status=404)

    result = await extract_links(URL)

    assert result["status"] == 404
    assert result["links"] is None
    assert result["error"] is None


async def test_extract_links_success_returns_set_of_hrefs(fake_http):
    fake_http.get(URL, html=html_with_links("https://www.google.com/a", "/relative-b"))

    result = await extract_links(URL)

    assert result["status"] == 200
    assert result["error"] is None
    assert isinstance(result["links"], set)
    assert result["links"] == {"https://www.google.com/a", "/relative-b"}


async def test_extract_links_deduplicates_repeated_hrefs(fake_http):
    fake_http.get(URL, html=html_with_links("https://www.google.com/a", "https://www.google.com/a"))

    assert (await extract_links(URL))["links"] == {"https://www.google.com/a"}


async def test_extract_links_no_anchors_returns_empty_set(fake_http):
    fake_http.get(URL, html="<html><body>No links here</body></html>")

    result = await extract_links(URL)

    assert result["status"] == 200
    assert result["error"] is None
    assert result["links"] == set()


async def test_extract_links_ignores_anchors_without_href(fake_http):
    fake_http.get(URL, html='<html><body><a name="anchor">No href</a></body></html>')

    assert (await extract_links(URL))["links"] == set()


async def test_extract_links_strips_whitespace_from_href(fake_http):
    fake_http.get(URL, html='<html><body><a href="  https://www.google.com/a  ">A</a></body></html>')

    assert (await extract_links(URL))["links"] == {"https://www.google.com/a"}


async def test_extract_links_non_html_page_returns_empty_set(fake_http):
    fake_http.get(URL, body=b"{}", headers={"Content-Type": "application/json"})

    result = await extract_links(URL)

    assert result["status"] == 200
    assert result["links"] == set()


async def test_extract_links_follows_redirect(fake_http):
    fake_http.get(URL, status=301, headers={"Location": "/new"})
    fake_http.get(f"{URL}/new", html=html_with_links("/found"))

    result = await extract_links(URL)

    assert result["links"] == {"/found"}
    assert fake_http.requested("GET") == [URL, f"{URL}/new"]


async def test_extract_links_disables_automatic_redirects(fake_http):
    fake_http.get(URL, html=html_with_links())

    await extract_links(URL)

    assert fake_http.calls[0][2] == {"allow_redirects": False}


async def test_extract_links_blocks_initial_unsafe_host(fake_http, public_dns):
    public_dns.return_value = {"127.0.0.1"}

    result = await extract_links(URL)

    assert result["error"] == "unsafe_url"
    assert result["links"] is None
    assert fake_http.calls == []


async def test_extract_links_blocks_redirect_to_unsafe_host(fake_http, public_dns):
    public_dns.side_effect = [{"93.184.216.34"}, {"192.168.0.1"}]
    fake_http.get(URL, status=302, headers={"Location": "http://192.168.0.1/router"})

    result = await extract_links(URL)

    assert result["error"] == "unsafe_url"
    assert fake_http.requested("GET") == [URL]


async def test_extract_links_gives_up_after_5_redirects(fake_http):
    fake_http.get(URL, status=302, headers={"Location": URL}, repeat=True)

    result = await extract_links(URL)

    assert result["error"] == "too_many_redirects"
    assert len(fake_http.calls) == 5

@pytest.mark.parametrize("exception_to_raise, expected_error", [
    (aiohttp.ClientConnectorError(connection_key=None, os_error=OSError()), "connection_failed"),
    (asyncio.TimeoutError(), "timeout"),
    (aiohttp.ClientError(), "client_error"),
])
async def test_check_link_returns_errors(fake_http, fake_session, exception_to_raise, expected_error):
    fake_http.head(URL, exception=exception_to_raise)

    response = await check_link(fake_session, URL, asyncio.Semaphore(5))

    assert response["error"] == expected_error
    assert response["status"] is None
    assert response["url"] == URL


async def test_check_link_success_returns_200(fake_http, fake_session):
    fake_http.head(URL, status=200)

    response = await check_link(fake_session, URL, asyncio.Semaphore(5))

    assert response == {"url": URL, "status": 200, "error": None}


@pytest.mark.parametrize("status", [404, 500])
async def test_check_link_reports_error_status_without_error_field(fake_http, fake_session, status):
    fake_http.head(URL, status=status)

    response = await check_link(fake_session, URL, asyncio.Semaphore(5))

    assert response["status"] == status
    assert response["error"] is None


async def test_check_link_uses_head_with_3s_timeout_and_no_auto_redirects(fake_http, fake_session):
    fake_http.head(URL, status=200)

    await check_link(fake_session, URL, asyncio.Semaphore(5))

    method, url, kwargs = fake_http.calls[0]
    assert (method, url) == ("HEAD", URL)
    assert kwargs["timeout"].total == 3
    assert kwargs["allow_redirects"] is False


async def test_check_link_follows_redirect_and_reports_final_status(fake_http, fake_session):
    fake_http.head(URL, status=301, headers={"Location": f"{URL}/moved"})
    fake_http.head(f"{URL}/moved", status=404)

    response = await check_link(fake_session, URL, asyncio.Semaphore(5))

    assert response["status"] == 404
    assert response["url"] == URL


async def test_check_link_gives_up_after_3_redirects(fake_http, fake_session):
    fake_http.head(URL, status=302, headers={"Location": URL}, repeat=True)

    response = await check_link(fake_session, URL, asyncio.Semaphore(5))

    assert response["error"] == "too_many_redirects"
    assert response["status"] is None
    assert len(fake_http.calls) == 3


async def test_check_link_blocks_unsafe_host_without_sending_request(fake_http, fake_session, public_dns):
    public_dns.return_value = {"10.1.1.1"}

    response = await check_link(fake_session, "http://intranet.example.com", asyncio.Semaphore(5))

    assert response["error"] == "unsafe_url"
    assert response["status"] is None
    assert fake_http.calls == []


async def test_check_link_blocks_redirect_to_unsafe_host(fake_http, fake_session, public_dns):
    public_dns.side_effect = [{"93.184.216.34"}, {"127.0.0.1"}]
    fake_http.head(URL, status=302, headers={"Location": "http://localhost:8080/admin"})

    response = await check_link(fake_session, URL, asyncio.Semaphore(5))

    assert response["error"] == "unsafe_url"
    assert fake_http.requested("HEAD") == [URL]

async def test_check_links_returns_list_of_results(fake_http):
    fake_http.head(URL, status=200)

    results = await check_links([URL])

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["status"] == 200


async def test_check_links_multiple_urls_all_processed(fake_http):
    fake_http.head(f"{URL}/a", status=200)
    fake_http.head(f"{URL}/b", status=404)
    fake_http.head(f"{URL}/c", status=500)

    results = await check_links([f"{URL}/a", f"{URL}/b", f"{URL}/c"])

    assert len(results) == 3
    assert sorted(r["status"] for r in results) == [200, 404, 500]


async def test_check_links_empty_input_returns_empty_list(fake_http):
    assert await check_links([]) == []


async def test_check_links_preserves_order_matching_input_urls(fake_http):
    fake_http.head(f"{URL}/first", status=200)
    fake_http.head(f"{URL}/second", status=404)

    results = await check_links([f"{URL}/first", f"{URL}/second"])

    assert [r["url"] for r in results] == [f"{URL}/first", f"{URL}/second"]


async def test_check_links_turns_unexpected_exception_into_error_result(fake_http):
    async def fake_check_link(session, url, semaphore):
        if url.endswith("/bad"):
            raise RuntimeError("boom")
        return {"url": url, "status": 200, "error": None}

    with patch("core.links.check_link", side_effect=fake_check_link):
        results = await check_links([f"{URL}/ok", f"{URL}/bad"])

    assert results[0] == {"url": f"{URL}/ok", "status": 200, "error": None}
    assert results[1] == {"url": f"{URL}/bad", "status": None, "error": "boom"}

async def test_broken_links_page_not_found(fake_http):
    fake_http.get(URL, status=404)

    results = await broken_links(URL)

    assert results["error"] == "Unable to check links — the page returned status code 404"
    assert results["broken_links"] == []
    assert results["total_links"] is None


async def test_broken_links_finds_404(fake_http):
    mock_extracted = {"status": 200, "error": None, "links": [f"{URL}/bad-link"]}
    fake_http.head(f"{URL}/bad-link", status=404)

    with patch("core.links.extract_links", return_value=mock_extracted):
        results = await broken_links(URL)

    assert results["error"] is None
    assert results["total_links"] == 1
    assert len(results["broken_links"]) == 1
    assert results["broken_links"][0]["status"] == 404


async def test_broken_links_end_to_end_without_patching_extract_links(fake_http):
    fake_http.get(URL, html=html_with_links("/ok", "/gone"))
    fake_http.head(f"{URL}/ok", status=200)
    fake_http.head(f"{URL}/gone", status=410)

    results = await broken_links(URL)

    assert results["error"] is None
    assert results["total_links"] == 2
    assert [b["url"] for b in results["broken_links"]] == [f"{URL}/gone"]


async def test_broken_links_all_healthy_returns_empty_broken_list(fake_http):
    mock_extracted = {"status": 200, "error": None, "links": {f"{URL}/a", f"{URL}/b"}}
    fake_http.head(f"{URL}/a", status=200)
    fake_http.head(f"{URL}/b", status=200)

    with patch("core.links.extract_links", return_value=mock_extracted):
        results = await broken_links(URL)

    assert results["error"] is None
    assert results["total_links"] == 2
    assert results["broken_links"] == []


async def test_broken_links_no_links_found_on_page(fake_http):
    mock_extracted = {"status": 200, "error": None, "links": set()}

    with patch("core.links.extract_links", return_value=mock_extracted):
        results = await broken_links(URL)

    assert results["error"] is None
    assert results["total_links"] == 0
    assert results["broken_links"] == []


async def test_broken_links_mixed_statuses_filters_only_broken_ones(fake_http):
    mock_extracted = {
        "status": 200, "error": None,
        "links": {f"{URL}/ok", f"{URL}/missing", f"{URL}/server-error"},
    }
    fake_http.head(f"{URL}/ok", status=200)
    fake_http.head(f"{URL}/missing", status=404)
    fake_http.head(f"{URL}/server-error", status=500)

    with patch("core.links.extract_links", return_value=mock_extracted):
        results = await broken_links(URL)

    assert results["total_links"] == 3
    assert sorted(item["status"] for item in results["broken_links"]) == [404, 500]


async def test_broken_links_relative_hrefs_resolved_to_absolute_before_checking(fake_http):
    mock_extracted = {"status": 200, "error": None, "links": {"/relative-path"}}
    fake_http.head(f"{URL}/relative-path", status=200)

    with patch("core.links.extract_links", return_value=mock_extracted):
        results = await broken_links(URL)

    assert results["error"] is None
    assert results["total_links"] == 1
    assert results["broken_links"] == []
    assert fake_http.requested("HEAD") == [f"{URL}/relative-path"]


async def test_broken_links_propagates_error_from_extract_links():
    mock_extracted = {"status": None, "error": "timeout", "links": None}

    with patch("core.links.extract_links", return_value=mock_extracted):
        results = await broken_links(URL)

    assert results["error"] == "timeout"
    assert results["broken_links"] == []
    assert results["total_links"] is None


async def test_broken_links_propagates_unsafe_url_error(fake_http, public_dns):
    public_dns.return_value = {"127.0.0.1"}

    results = await broken_links(URL)

    assert results["error"] == "unsafe_url"
    assert fake_http.calls == []


async def test_broken_links_treats_check_link_exception_as_broken():
    mock_extracted = {"status": 200, "error": None, "links": {f"{URL}/weird"}}

    async def fake_check_links(urls):
        return [{"url": urls[0], "status": None, "error": "unexpected_error"}]

    with patch("core.links.extract_links", return_value=mock_extracted), \
         patch("core.links.check_links", side_effect=fake_check_links):
        results = await broken_links(URL)

    assert results["error"] is None
    assert results["total_links"] == 1
    assert len(results["broken_links"]) == 1
    assert results["broken_links"][0]["error"] == "unexpected_error"


async def test_broken_links_ignores_non_http_schemes():
    mock_extracted = {
        "status": 200, "error": None,
        "links": {"mailto:a@b.com", "javascript:void(0)", "tel:+380501234567", "ftp://x.com/f", f"{URL}/ok"},
    }

    with patch("core.links.extract_links", return_value=mock_extracted), \
         patch("core.links.check_links", return_value=[]) as mock_check_links:
        results = await broken_links(URL)

    assert mock_check_links.call_args.args[0] == [f"{URL}/ok"]
    assert results["total_links"] == 1


async def test_broken_links_caps_number_of_checked_links():
    many = {f"{URL}/page-{i}" for i in range(links.MAX_LINKS_TO_CHECK + 100)}
    mock_extracted = {"status": 200, "error": None, "links": many}

    with patch("core.links.extract_links", return_value=mock_extracted), \
         patch("core.links.check_links", return_value=[]) as mock_check_links:
        results = await broken_links(URL)

    assert len(mock_check_links.call_args.args[0]) == links.MAX_LINKS_TO_CHECK
    assert results["total_links"] == links.MAX_LINKS_TO_CHECK


@pytest.mark.parametrize("status, expected_broken", [(200, False), (399, False), (400, True), (503, True)])
async def test_broken_links_status_threshold_is_400(status, expected_broken):
    mock_extracted = {"status": 200, "error": None, "links": {f"{URL}/x"}}
    checked = [{"url": f"{URL}/x", "status": status, "error": None}]

    with patch("core.links.extract_links", return_value=mock_extracted), \
         patch("core.links.check_links", return_value=checked):
        results = await broken_links(URL)

    assert bool(results["broken_links"]) is expected_broken


async def test_broken_links_result_without_status_and_error_counts_as_broken():
    mock_extracted = {"status": 200, "error": None, "links": {f"{URL}/x"}}
    checked = [{"url": f"{URL}/x", "status": None, "error": None}]

    with patch("core.links.extract_links", return_value=mock_extracted), \
         patch("core.links.check_links", return_value=checked):
        results = await broken_links(URL)

    assert len(results["broken_links"]) == 1

async def test_extract_links_reads_the_whole_page_even_if_it_arrives_in_chunks(fake_http):
    page = html_with_links("/first", "/last")
    fake_http.get(URL, html=page, chunk_size=len(page) // 2)   # body arrives in two chunks

    result = await extract_links(URL)

    assert result["links"] == {"/first", "/last"}
