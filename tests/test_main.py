import pytest
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient
from urllib.parse import quote
from web.main import app, add_site_post, max_active_scan_links, max_total_scan_links

from starlette.requests import Request
client = TestClient(app)

NO_SITES = {"success": True, "error": None, "result": []}


@pytest.fixture
def no_sites():

    with patch("web.main.get_sites", return_value=NO_SITES) as mock:
        yield mock


@pytest.fixture
def mock_template_response():
    from starlette.responses import Response

    def fake_template_response(request=None, name=None, context=None, **kwargs):
        resp = Response(content=f"rendered:{name}", status_code=200)
        resp.template_name = name
        resp.template_context = context
        return resp

    with patch("web.main.templates.TemplateResponse", side_effect=fake_template_response) as mock_tr:
        yield mock_tr


def test_root_success_renders_index_with_sites(mock_template_response):
    sites = [{"id": 1, "url": "https://a.com", "is_active": True, "status_code": 200, "title": "A"}]
    with patch("web.main.get_sites", return_value={"success": True, "error": None, "result": sites}):
        response = client.get("/")

    assert response.status_code == 200
    call_kwargs = mock_template_response.call_args.kwargs
    assert call_kwargs["name"] == "index.html"
    assert call_kwargs["context"]["sites"] == sites
    assert call_kwargs["context"]["error"] is None


def test_root_db_failure_renders_index_with_error(mock_template_response):
    with patch("web.main.get_sites", return_value={"success": False, "error": "db_select_failed", "result": []}):
        response = client.get("/")

    assert response.status_code == 200
    call_kwargs = mock_template_response.call_args.kwargs
    assert call_kwargs["context"]["sites"] == []
    assert call_kwargs["context"]["error"] == "Temporary database issues. Please refresh the page."

def test_add_site_get_renders_form(mock_template_response):
    response = client.get("/add-site")

    assert response.status_code == 200
    call_kwargs = mock_template_response.call_args.kwargs
    assert call_kwargs["name"] == "add_site.html"
    assert call_kwargs["context"]["error"] is None


def test_add_site_post_failure_renders_form_with_error(mock_template_response, no_sites):
    with patch("web.main.add_site_db", return_value={"success": False, "error": "db_insert_failed", "last_id": None}), \
         patch("web.main.scan_one_site") as mock_scan_one_site:

        response = client.post("/add-site", data={"url": "https://bad.com"})

    assert response.status_code == 200
    call_kwargs = mock_template_response.call_args.kwargs
    assert call_kwargs["name"] == "add_site.html"
    assert call_kwargs["context"]["error"] == "The data could not be saved. Please try again in a few minutes."
    mock_scan_one_site.assert_not_called()


def test_add_site_post_missing_url_returns_422():
    response = client.post("/add-site", data={})
    assert response.status_code == 422


def test_add_site_post_url_is_properly_quoted_in_redirect(mock_template_response, no_sites):
    test_url = "https://example.com/path?query=1&other=2"
    with patch("web.main.add_site_db", return_value={"success": True, "error": None, "last_id": 1}), \
         patch("web.main.scan_one_site"):

        response = client.post("/add-site", data={"url": test_url}, follow_redirects=False)

    expected_location = f"/?scanning=1&url={quote(test_url)}"
    assert response.headers["location"] == expected_location


def test_site_detail_success_renders_with_reversed_chart_data(mock_template_response):
    scans = [{"scan_id": 1}, {"scan_id": 2}, {"scan_id": 3}]
    result = {
        "success": True,
        "error": None,
        "site": {"id": 5, "url": "https://a.com"},
        "scans": scans,
    }
    with patch("web.main.get_site_details", return_value=result):
        response = client.get("/site/5")

    assert response.status_code == 200
    call_kwargs = mock_template_response.call_args.kwargs
    assert call_kwargs["name"] == "site_detail.html"
    assert call_kwargs["context"]["site"] == {"id": 5, "url": "https://a.com"}
    assert call_kwargs["context"]["scans"] == scans
    assert call_kwargs["context"]["chart_scans"] == list(reversed(scans))


def test_site_detail_not_found_renders_empty_state(mock_template_response):
    result = {"success": False, "error": "site_not_found", "site": None, "scans": []}
    with patch("web.main.get_site_details", return_value=result):
        response = client.get("/site/999")

    assert response.status_code == 200
    call_kwargs = mock_template_response.call_args.kwargs
    assert call_kwargs["context"]["site"] is None
    assert call_kwargs["context"]["error"] == "That page was not found."
    assert call_kwargs["context"]["scans"] == []
    assert call_kwargs["context"]["chart_scans"] == []


def test_site_detail_invalid_id_type_returns_422():
    response = client.get("/site/not-a-number")
    assert response.status_code == 422

def test_delete_site_success_redirects_home():
    with patch("web.main.delete_site", return_value={"success": True, "error": None}):
        response = client.post("/site/5/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_delete_site_failure_renders_index_with_error(mock_template_response):
    sites = [{"id": 1, "url": "https://a.com"}]
    with patch("web.main.delete_site", return_value={"success": False, "error": "db_delete_failed"}), \
         patch("web.main.get_sites", return_value={"success": True, "error": None, "result": sites}):

        response = client.post("/site/5/delete")

    assert response.status_code == 200
    call_kwargs = mock_template_response.call_args.kwargs
    assert call_kwargs["name"] == "index.html"
    assert call_kwargs["context"]["error"] == "The site could not be deleted. Please try again."
    assert call_kwargs["context"]["sites"] == sites


def test_toggle_site_success_redirects_home():
    with patch("web.main.toggle_site_active", return_value={"success": True, "error": None}):
        response = client.post("/site/5/toggle", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_toggle_site_failure_renders_index_with_error(mock_template_response):
    sites = [{"id": 2, "url": "https://b.com"}]
    with patch("web.main.toggle_site_active", return_value={"success": False, "error": "db_update_failed"}), \
         patch("web.main.get_sites", return_value={"success": True, "error": None, "result": sites}):

        response = client.post("/site/5/toggle")

    assert response.status_code == 200
    call_kwargs = mock_template_response.call_args.kwargs
    assert call_kwargs["context"]["error"] == "The site status could not be updated. Please try again."
    assert call_kwargs["context"]["sites"] == sites


@pytest.mark.asyncio
async def test_add_site_post_returns_template_on_url_error():
    module_path = add_site_post.__module__

    mock_request = MagicMock(spec=Request)
    mock_bg_tasks = MagicMock()
    raw_url = "invalid_url_string"
    expected_error = "invalid_scheme"
    user_error_text = "Not correct protocol of URL"

    with patch(f"{module_path}.get_normalized_url") as mock_normalize, \
            patch(f"{module_path}.get_user_message", return_value=user_error_text) as mock_msg, \
            patch(f"{module_path}.templates.TemplateResponse") as mock_template_response:
        mock_normalize.return_value = {"url": None, "error": expected_error}

        response = await add_site_post(
            request=mock_request,
            background_tasks=mock_bg_tasks,
            url=raw_url,
        )

        mock_normalize.assert_called_once_with(raw_url)
        mock_msg.assert_called_once_with(expected_error)

        mock_template_response.assert_called_once_with(
            request=mock_request,
            name="add_site.html",
            context={"request": mock_request, "error": user_error_text},
        )
        assert response == mock_template_response.return_value

def test_healthz_all_up_returns_200():
    with patch("web.main.check_mysql_connection", return_value=True), \
         patch("web.main.check_mongo_connection", return_value=True):

        response = client.get("/healthz")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "up"
    assert data["components"]["mysql"] == "ok"
    assert data["components"]["mongo"] == "ok"


def test_healthz_mysql_down_returns_503():
    with patch("web.main.check_mysql_connection", return_value=False), \
         patch("web.main.check_mongo_connection", return_value=True):

        response = client.get("/healthz")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "down"
    assert data["components"]["mysql"] == "down"
    assert data["components"]["mongo"] == "ok"


def test_healthz_mongo_down_returns_503():
    with patch("web.main.check_mysql_connection", return_value=True), \
         patch("web.main.check_mongo_connection", return_value=False):

        response = client.get("/healthz")

    assert response.status_code == 503
    data = response.json()
    assert data["components"]["mysql"] == "ok"
    assert data["components"]["mongo"] == "down"


def test_healthz_both_down_returns_503():
    with patch("web.main.check_mysql_connection", return_value=False), \
         patch("web.main.check_mongo_connection", return_value=False):

        response = client.get("/healthz")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "down"
    assert data["components"]["mysql"] == "down"
    assert data["components"]["mongo"] == "down"

@patch("web.main.toggle_site_pin")
def test_pin_route_success_redirects_home(mock_toggle):
    mock_toggle.return_value = {"success": True, "error": None}

    response = client.post("/site/1/pin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    mock_toggle.assert_called_once_with(1)


@patch("web.main.get_sites")
@patch("web.main.toggle_site_pin")
def test_pin_route_failure_renders_index_with_error(mock_toggle, mock_get_sites):
    mock_toggle.return_value = {"success": False, "error": "site_not_found"}
    mock_get_sites.return_value = {"success": True, "result": []}

    response = client.post("/site/999/pin", follow_redirects=False)

    assert response.status_code == 200
    assert "Error" in response.text
    assert "That page was not found." in response.text


@patch("web.main.toggle_site_pin")
def test_pin_route_toggles_twice(mock_toggle):
    mock_toggle.return_value = {"success": True, "error": None}

    response1 = client.post("/site/5/pin", follow_redirects=False)
    assert response1.status_code == 303

    response2 = client.post("/site/5/pin", follow_redirects=False)
    assert response2.status_code == 303

    assert mock_toggle.call_count == 2

def test_add_site_post_success_saves_normalized_url_and_schedules_scan(no_sites):
    with patch("web.main.add_site_db", return_value={"success": True, "error": None, "last_id": 7}) as mock_add, \
         patch("web.main.scan_one_site") as mock_scan:

        response = client.post("/add-site", data={"url": "  example.com/page "}, follow_redirects=False)

    assert response.status_code == 303
    mock_add.assert_called_once_with("https://example.com/page")
    mock_scan.assert_awaited_once_with("https://example.com/page", 7)


@pytest.mark.parametrize("url, expected_message", [
    ("   ", "Please enter a website address."),
    ("ftp://files.example.com", "Invalid website scheme."),
    ("localhost", "Invalid website address."),
    ("a" * 2100, "The website address is too long."),
])
def test_add_site_post_invalid_input_shows_message_and_saves_nothing(
    mock_template_response, no_sites, url, expected_message
):
    with patch("web.main.add_site_db") as mock_add, patch("web.main.scan_one_site") as mock_scan:
        response = client.post("/add-site", data={"url": url})

    assert response.status_code == 200
    assert mock_template_response.call_args.kwargs["context"]["error"] == expected_message
    mock_add.assert_not_called()
    mock_scan.assert_not_called()


def test_add_site_post_completely_empty_field_is_rejected_by_fastapi_with_422():
    """FastAPI treats an empty form value as missing (the browser's `required` normally prevents it),
    so the friendly 'empty_url' message is only reachable for whitespace-only input."""
    assert client.post("/add-site", data={"url": ""}).status_code == 422


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/admin",
    "http://10.0.0.1",
    "http://169.254.169.254/latest/meta-data/",
])
def test_add_site_post_rejects_internal_addresses(mock_template_response, no_sites, public_dns, url):
    public_dns.side_effect = lambda host: {host}

    with patch("web.main.add_site_db") as mock_add, patch("web.main.scan_one_site") as mock_scan:
        response = client.post("/add-site", data={"url": url})

    assert response.status_code == 200
    assert mock_template_response.call_args.kwargs["context"]["error"] == "This website address cannot be scanned."
    mock_add.assert_not_called()
    mock_scan.assert_not_called()


def test_add_site_post_rejects_hostname_that_resolves_to_private_ip(mock_template_response, no_sites, public_dns):
    public_dns.return_value = {"192.168.0.20"}

    with patch("web.main.add_site_db") as mock_add:
        client.post("/add-site", data={"url": "https://looks-public.example.com"})

    assert mock_template_response.call_args.kwargs["context"]["error"] == "This website address cannot be scanned."
    mock_add.assert_not_called()


def test_add_site_post_blocks_when_total_site_limit_reached(mock_template_response):
    all_sites = {"success": True, "error": None, "result": [{"id": i} for i in range(max_total_scan_links)]}

    with patch("web.main.get_sites", return_value=all_sites), \
         patch("web.main.add_site_db") as mock_add:
        response = client.post("/add-site", data={"url": "https://example.com"})

    assert response.status_code == 200
    assert mock_template_response.call_args.kwargs["context"]["error"] == "The limit of 50 sites has been reached."
    mock_add.assert_not_called()


def test_add_site_post_blocks_when_active_site_limit_reached(mock_template_response):
    def fake_get_sites(only_active=False):
        count = max_active_scan_links if only_active else max_active_scan_links + 3
        return {"success": True, "error": None, "result": [{"id": i} for i in range(count)]}

    with patch("web.main.get_sites", side_effect=fake_get_sites), \
         patch("web.main.add_site_db") as mock_add:
        response = client.post("/add-site", data={"url": "https://example.com"})

    assert response.status_code == 200
    assert mock_template_response.call_args.kwargs["context"]["error"] == \
        "No more than 5 websites can be active at the same time."
    mock_add.assert_not_called()


def test_add_site_post_allows_adding_below_limits(no_sites):
    with patch("web.main.add_site_db", return_value={"success": True, "error": None, "last_id": 1}), \
         patch("web.main.scan_one_site"):
        response = client.post("/add-site", data={"url": "https://example.com"}, follow_redirects=False)

    assert response.status_code == 303


def test_toggle_site_active_limit_error_is_shown_to_user(mock_template_response):
    with patch("web.main.toggle_site_active",
               return_value={"success": False, "error": "total_active_links_reached"}), \
         patch("web.main.get_sites", return_value=NO_SITES):
        response = client.post("/site/5/toggle")

    assert response.status_code == 200
    assert mock_template_response.call_args.kwargs["context"]["error"] == \
        "No more than 5 websites can be active at the same time."


def test_delete_site_non_numeric_id_returns_422():
    assert client.post("/site/abc/delete").status_code == 422

def _site(**kw):
    base = {"id": 1, "url": "https://a.com", "is_active": True, "is_pinned": False,
            "status_code": None, "title": None, "domain": "a.com"}
    base.update(kw)
    return base


def test_index_renders_sites_with_null_scan_data():
    sites = [
        _site(id=1),                                               # never scanned
        _site(id=2, url="https://b.com", status_code=200, title="B", domain="b.com"),
        _site(id=3, url="https://c.com", status_code=503, title="C", domain="c.com", is_active=False),
        _site(id=4, url="https://d.com", is_pinned=True, domain="d.com"),
    ]
    with patch("web.main.get_sites", return_value={"success": True, "error": None, "result": sites}):
        response = client.get("/")

    assert response.status_code == 200
    assert "no scans yet" in response.text
    assert "https://c.com" in response.text
    assert "Paused" in response.text


def test_index_renders_empty_state():
    with patch("web.main.get_sites", return_value=NO_SITES):
        response = client.get("/")

    assert response.status_code == 200
    assert "No sites yet" in response.text


def test_index_escapes_html_in_page_title():
    evil = _site(status_code=200, title="<script>alert(1)</script>")
    with patch("web.main.get_sites", return_value={"success": True, "error": None, "result": [evil]}):
        response = client.get("/")

    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def _scan(scan_id, **kw):
    base = {
        "scan_id": scan_id, "status_code": None, "title": None, "description": None,
        "duration_ms": None, "status_error": None, "ip": None, "country": None, "city": None,
        "org": None, "geo_error": None, "ssl_days_left": None, "valid": None, "cert_error": None,
        "total_links": None, "links_error": None, "scanned_at": "2024-01-02 10:00:00",
        "broken_links": [],
    }
    base.update(kw)
    return base


def _detail(scans):
    return {
        "success": True, "error": None, "scans": scans,
        "site": {"id": 5, "url": "https://a.com", "is_active": True, "added_at": "2024-01-01"},
    }


def test_site_detail_renders_scan_with_all_null_fields():
    """A scan that failed completely (no status, no cert, no geo) must still render."""
    with patch("web.main.get_site_details", return_value=_detail([_scan(1, status_error="timeout")])):
        response = client.get("/site/5")

    assert response.status_code == 200
    assert "Unreachable" in response.text
    assert "No cert" in response.text


def test_site_detail_renders_history_charts_and_broken_links():
    scans = [
        _scan(3, status_code=200, duration_ms=120, ssl_days_left=5, valid=True, total_links=4,
              ip="1.2.3.4", country="UA", city="Kyiv", org="Org", title="Home",
              broken_links=[{"url": "https://x.com/gone", "status": 404, "error": None},
                            {"url": "https://y.com", "status": None, "error": "timeout"}]),
        _scan(2, status_code=500, duration_ms=None),
        _scan(1, status_code=200, duration_ms=90, ssl_days_left=60, valid=True),
    ]
    with patch("web.main.get_site_details", return_value=_detail(scans)):
        response = client.get("/site/5")

    assert response.status_code == 200
    assert "SITE_DETAIL_CHART_DATA" in response.text
    assert "2 broken links" in response.text
    assert "https://x.com/gone" in response.text
    assert "timeout" in response.text


def test_site_detail_single_scan_shows_not_enough_data_note():
    with patch("web.main.get_site_details", return_value=_detail([_scan(1, status_code=200, duration_ms=5)])):
        response = client.get("/site/5")

    assert "Not enough data yet" in response.text
    assert "SITE_DETAIL_CHART_DATA" not in response.text.split("<body")[1].split("turbo:load")[0]


def test_site_detail_site_without_scans_shows_empty_state():
    with patch("web.main.get_site_details", return_value=_detail([])):
        response = client.get("/site/5")

    assert response.status_code == 200
    assert "No scans yet" in response.text


def test_site_detail_escapes_html_in_broken_link_url():
    scans = [_scan(1, status_code=200, broken_links=[{"url": '"><img src=x onerror=alert(1)>', "status": 404}])]
    with patch("web.main.get_site_details", return_value=_detail(scans)):
        response = client.get("/site/5")

    assert "<img src=x onerror=alert(1)>" not in response.text


def test_site_detail_not_found_renders_error_banner():
    result = {"success": False, "error": "site_not_found", "site": None, "scans": []}
    with patch("web.main.get_site_details", return_value=result):
        response = client.get("/site/999")

    assert response.status_code == 200
    assert "That page was not found." in response.text


def test_add_site_page_renders_error_message():
    with patch("web.main.get_normalized_url", return_value={"url": None, "error": "invalid_url"}):
        response = client.post("/add-site", data={"url": "x"})

    assert response.status_code == 200
    assert "Invalid website address." in response.text

