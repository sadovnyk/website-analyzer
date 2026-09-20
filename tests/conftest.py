import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import aiohttp
import pytest
from multidict import CIMultiDict

PUBLIC_IP = "93.184.216.34"

@pytest.fixture(autouse=True)
def public_dns():
    with patch("core.security._resolve_all_ips", return_value={PUBLIC_IP}) as mock:
        yield mock

class FakeResponse:

    def __init__(self, status=200, headers=None, body=b"", chunk_size=None):
        self.status = status
        self.headers = CIMultiDict(headers or {})
        self._body = body
        self._pos = 0
        self._chunk_size = chunk_size
        self.content = SimpleNamespace(read=self._read, iter_chunked=self._iter_chunked)

    async def _read(self, n=-1):
        size = len(self._body) - self._pos
        if n is not None and n >= 0:
            size = min(size, n)
        if self._chunk_size:
            size = min(size, self._chunk_size)
        data = self._body[self._pos:self._pos + size]
        self._pos += size
        return data

    async def _iter_chunked(self, n):
        while True:
            chunk = await self._read(n)
            if not chunk:
                return
            yield chunk

    async def json(self, **kwargs):
        return json.loads(self._body)


class _RequestContext:
    def __init__(self, http, method, url, kwargs):
        self._http, self._method, self._url, self._kwargs = http, method, url, kwargs

    async def __aenter__(self):
        return self._http.respond(self._method, self._url, self._kwargs)

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, http, *args, **kwargs):
        self._http = http
        self.init_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return _RequestContext(self._http, "GET", str(url), kwargs)

    def head(self, url, **kwargs):
        return _RequestContext(self._http, "HEAD", str(url), kwargs)


class FakeHTTP:

    def __init__(self):
        self._routes = {}
        self.calls = []            # [(method, url, kwargs)]
        self.session_class = None  # the patched aiohttp.ClientSession mock

    def _add(self, method, url, *, status=200, headers=None, body=b"",
             html=None, json_data=None, exception=None, repeat=False, chunk_size=None):
        headers = dict(headers or {})
        if html is not None:
            body = html
            headers.setdefault("Content-Type", "text/html; charset=utf-8")
        if json_data is not None:
            body = json.dumps(json_data)
            headers.setdefault("Content-Type", "application/json")
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._routes.setdefault((method, url), []).append({
            "response": FakeResponse(status, headers, body, chunk_size),
            "exception": exception,
            "repeat": repeat,
        })

    def get(self, url, **kw):
        self._add("GET", url, **kw)

    def head(self, url, **kw):
        self._add("HEAD", url, **kw)

    def respond(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        queue = self._routes.get((method, url))
        if not queue:
            raise aiohttp.ClientConnectionError(f"No fake route for {method} {url}")
        route = queue[0]
        if not route["repeat"]:
            queue.pop(0)
        if route["exception"] is not None:
            raise route["exception"]
        return route["response"]

    def requested(self, method=None):
        """URLs that were requested, optionally filtered by method."""
        return [u for m, u, _ in self.calls if method in (None, m)]


@pytest.fixture
def fake_http():
    """Patches aiohttp.ClientSession globally with a session backed by FakeHTTP."""
    http = FakeHTTP()
    with patch("aiohttp.ClientSession",
               side_effect=lambda *a, **kw: FakeSession(http, *a, **kw)) as session_cls:
        http.session_class = session_cls
        yield http


@pytest.fixture
def fake_session(fake_http):
    """A session to pass directly into functions like check_link(session, ...)."""
    return FakeSession(fake_http)


@pytest.fixture
def logger_module(tmp_path, monkeypatch):
    import core.logger as logger_module

    fake_log_file = tmp_path / "cron.log"
    monkeypatch.setattr(logger_module, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(logger_module, "LOG_FILE", str(fake_log_file))

    yield logger_module

    for name in list(logging.Logger.manager.loggerDict.keys()):
        if name.startswith("test_logger"):
            lg = logging.getLogger(name)
            for h in lg.handlers[:]:
                h.close()
                lg.removeHandler(h)


@pytest.fixture
def mock_cursor():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    return cursor


@pytest.fixture
def mock_connection(mock_cursor):
    conn = MagicMock()
    conn.cursor.return_value = mock_cursor
    return conn


@pytest.fixture
def mock_mongo_collection():
    collection = MagicMock()
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.__getitem__.return_value.__getitem__.return_value = collection
    return client, collection
