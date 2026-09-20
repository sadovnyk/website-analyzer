import pymysql
import pytest
from unittest.mock import patch, MagicMock

from core import database


def test_get_connection_uses_env_vars(monkeypatch):
    monkeypatch.setenv("DB_PORT", "3306")
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_USER", "root")
    monkeypatch.setenv("DB_PASSWORD", "pass")
    monkeypatch.setenv("DB_NAME", "testdb")

    with patch("core.database.pymysql.connect") as mock_connect:
        database.get_connection()
        mock_connect.assert_called_once_with(
            port=3306,
            host="localhost",
            user="root",
            password="pass",
            database="testdb",
            charset="utf8mb4",
            connect_timeout=5,
        )

def _set_db_env(monkeypatch):
    for key, value in {"DB_PORT": "3306", "DB_HOST": "localhost", "DB_USER": "u",
                       "DB_PASSWORD": "p", "DB_NAME": "db"}.items():
        monkeypatch.setenv(key, value)


def test_get_connection_retries_on_operational_error(monkeypatch):
    _set_db_env(monkeypatch)
    connection = MagicMock()
    errors = [pymysql.err.OperationalError(2003, "down"), pymysql.err.OperationalError(2003, "down")]

    with patch("core.database.pymysql.connect", side_effect=[*errors, connection]) as mock_connect, \
         patch.object(database.get_connection.retry, "sleep"):   # don't really wait 1s + 2s
        result = database.get_connection()

    assert result is connection
    assert mock_connect.call_count == 3


def test_get_connection_gives_up_after_3_attempts_and_reraises(monkeypatch):
    _set_db_env(monkeypatch)

    with patch("core.database.pymysql.connect",
               side_effect=pymysql.err.OperationalError(2003, "down")) as mock_connect, \
         patch.object(database.get_connection.retry, "sleep"):
        with pytest.raises(pymysql.err.OperationalError):
            database.get_connection()

    assert mock_connect.call_count == 3


def test_get_connection_does_not_retry_other_errors(monkeypatch):
    _set_db_env(monkeypatch)

    with patch("core.database.pymysql.connect", side_effect=pymysql.err.ProgrammingError("bad")) as mock_connect:
        with pytest.raises(pymysql.err.ProgrammingError):
            database.get_connection()

    assert mock_connect.call_count == 1


def make_scan_data(**overrides):
    data = {
        "status": {
            "status_code": 200,
            "title": "Title",
            "description": "Desc",
            "duration_ms": 100,
            "error": None,
        },
        "ip": "1.2.3.4",
        "geo": {"country": "UA", "city": "Kyiv", "org": "Org", "error": None},
        "cert": {"ssl_days_left": 90, "valid": True, "error": None},
        "number_of_links": 3,
        "links": {"error": None},
    }
    data.update(overrides)
    return data


def test_save_scan_success(mock_connection, mock_cursor):
    mock_cursor.lastrowid = 42
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.save_scan("https://example.com", make_scan_data(), site_id=1)

    assert result["success"] is True
    assert result["scan_id"] == 42
    assert result["error"] is None
    mock_connection.commit.assert_called_once()
    mock_connection.close.assert_called_once()


def test_save_scan_db_error_rolls_back(mock_connection, mock_cursor):
    mock_cursor.execute.side_effect = Exception("db error")
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.save_scan("https://example.com", make_scan_data(), site_id=1)

    assert result["success"] is False
    assert result["error"] == "db error"
    mock_connection.rollback.assert_called_once()
    mock_connection.commit.assert_not_called()
    mock_connection.close.assert_called_once()


def test_save_scan_missing_keys_in_scan_data_returns_error_and_rolls_back(mock_connection):
    broken = make_scan_data()
    del broken["cert"]
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.save_scan("https://example.com", broken, site_id=1)

    assert result["success"] is False
    assert "cert" in result["error"]
    mock_connection.rollback.assert_called_once()
    mock_connection.close.assert_called_once()


def test_save_scan_inserts_site_id_and_url(mock_connection, mock_cursor):
    with patch("core.database.get_connection", return_value=mock_connection):
        database.save_scan("https://example.com", make_scan_data(), site_id=9)

    _, values = mock_cursor.execute.call_args[0]
    assert values[0] == "https://example.com"
    assert values[-1] == 9


def test_save_scan_connection_always_closed_even_on_connect_failure():
    with patch("core.database.get_connection", side_effect=Exception("no connection")):
        result = database.save_scan("https://example.com", make_scan_data(), site_id=1)

    assert result["success"] is False
    assert result["error"] == "no connection"

def test_save_links_success(mock_mongo_collection, monkeypatch):
    client, collection = mock_mongo_collection
    monkeypatch.setenv("MONGO_DB", "mydb")
    monkeypatch.setenv("MONGO_CL", "mycol")

    with patch("core.database.pymongo.MongoClient", return_value=client):
        result = database.save_links(42, {"broken_links": ["http://broken.com"]})

    assert result["success"] is True
    assert result["scan_id"] == 42
    collection.insert_one.assert_called_once_with({
        "my_sql_scan_id": 42,
        "broken_links": ["http://broken.com"],
    })


def test_save_links_connects_with_mongo_uri(mock_mongo_collection, monkeypatch):
    client, _ = mock_mongo_collection
    monkeypatch.setenv("MONGO_URI", "mongodb://user:pw@localhost:27018")
    monkeypatch.setenv("MONGO_DB", "mydb")
    monkeypatch.setenv("MONGO_CL", "mycol")

    with patch("core.database.pymongo.MongoClient", return_value=client) as mock_client_cls:
        database.save_links(1, {"broken_links": []})

    mock_client_cls.assert_called_once_with("mongodb://user:pw@localhost:27018", serverSelectionTimeoutMS=5000)


def test_save_links_mongo_error(monkeypatch):
    monkeypatch.setenv("MONGO_DB", "mydb")
    monkeypatch.setenv("MONGO_CL", "mycol")

    with patch("core.database.pymongo.MongoClient", side_effect=Exception("mongo down")):
        result = database.save_links(42, {"broken_links": []})

    assert result["success"] is False
    assert result["error"] == "db_insert_failed"

def test_get_sites_returns_mapped_rows(mock_connection, mock_cursor):
    mock_cursor.fetchall.return_value = [
        (1, "https://a.com", True, True, 200, "A"),
        (2, "https://b.com", False, False, None, None),
    ]
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.get_sites()

    assert result["success"] is True
    assert result["result"] == [
        {"id": 1, "url": "https://a.com", "is_active": True,"is_pinned": True, "status_code": 200, "title": "A", "domain": "a.com"},
        {"id": 2, "url": "https://b.com", "is_active": False,"is_pinned": False, "status_code": None, "title": None, "domain": "b.com"}
    ]


def test_get_sites_handles_null_url(mock_connection, mock_cursor):
    mock_cursor.fetchall.return_value = [(3, None, True, False, None, None)]
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.get_sites()

    assert result["success"] is True
    assert result["result"][0]["domain"] == ""


def test_get_sites_without_filter_returns_inactive_sites_too(mock_connection, mock_cursor):
    mock_cursor.fetchall.return_value = []
    with patch("core.database.get_connection", return_value=mock_connection):
        database.get_sites()

    assert "WHERE sts.is_active" not in mock_cursor.execute.call_args[0][0]


def test_get_sites_always_closes_connection(mock_connection, mock_cursor):
    mock_cursor.execute.side_effect = Exception("boom")
    with patch("core.database.get_connection", return_value=mock_connection):
        database.get_sites()

    mock_connection.close.assert_called_once()


def test_get_sites_only_active_adds_where_clause(mock_connection, mock_cursor):
    mock_cursor.fetchall.return_value = []
    with patch("core.database.get_connection", return_value=mock_connection):
        database.get_sites(only_active=True)

    executed_query = mock_cursor.execute.call_args[0][0]
    assert "WHERE sts.is_active = TRUE" in executed_query


def test_get_sites_db_error(mock_connection, mock_cursor):
    mock_cursor.execute.side_effect = Exception("boom")
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.get_sites()

    assert result["success"] is False
    assert result["error"] == "db_select_failed"
    assert result["result"] == []

def test_add_site_db_success(mock_connection, mock_cursor):
    mock_cursor.lastrowid = 7
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.add_site_db("https://new.com")

    assert result["success"] is True
    assert result["last_id"] == 7
    mock_cursor.execute.assert_called_once_with(
        "INSERT INTO sites (url) values (%s)", ("https://new.com",)
    )
    mock_connection.commit.assert_called_once()


def test_add_site_db_failure(mock_connection, mock_cursor):
    mock_cursor.execute.side_effect = Exception("insert failed")
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.add_site_db("https://new.com")

    assert result["success"] is False
    assert result["error"] == "db_insert_failed"
    assert result["last_id"] is None

def test_get_site_details_success(mock_connection, mock_cursor, mock_mongo_collection, monkeypatch):
    monkeypatch.setenv("MONGO_DB", "mydb")
    monkeypatch.setenv("MONGO_CL", "mycol")

    mock_cursor.fetchall.return_value = [
        {
            "site_id": 1, "url": "https://a.com", "is_active": True, "added_at": "2024-01-01",
            "scan_id": 100, "status_code": 200, "title": "T", "description": "D",
            "duration_ms": 50, "status_error": None, "ip": "1.1.1.1", "country": "UA",
            "city": "Kyiv", "org": "Org", "geo_error": None, "ssl_days_left": 30,
            "valid": True, "cert_error": None, "total_links": 5, "links_error": None,
            "scanned_at": "2024-01-02",
        }
    ]

    client, collection = mock_mongo_collection
    collection.find.return_value = [{"my_sql_scan_id": 100, "broken_links": ["http://x.com"]}]

    with patch("core.database.get_connection", return_value=mock_connection), \
         patch("core.database.pymongo.MongoClient", return_value=client):
        result = database.get_site_details(1)

    assert result["success"] is True
    assert result["site"]["id"] == 1
    assert len(result["scans"]) == 1
    assert result["scans"][0]["scan_id"] == 100
    assert result["scans"][0]["broken_links"] == ["http://x.com"]
    collection.find.assert_called_once_with({"my_sql_scan_id": {"$in": [100]}})
    mock_connection.close.assert_called_once()


def test_get_site_details_site_not_found(mock_connection, mock_cursor):
    mock_cursor.fetchall.return_value = []
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.get_site_details(999)

    assert result["success"] is False
    assert result["error"] == "site_not_found"
    assert result["site"] is None


def test_get_site_details_no_broken_links_defaults_to_empty_list(
    mock_connection, mock_cursor, mock_mongo_collection, monkeypatch
):
    monkeypatch.setenv("MONGO_DB", "mydb")
    monkeypatch.setenv("MONGO_CL", "mycol")

    mock_cursor.fetchall.return_value = [
        {
            "site_id": 1, "url": "https://a.com", "is_active": True, "added_at": "2024-01-01",
            "scan_id": 100, "status_code": 200, "title": "T", "description": "D",
            "duration_ms": 50, "status_error": None, "ip": "1.1.1.1", "country": "UA",
            "city": "Kyiv", "org": "Org", "geo_error": None, "ssl_days_left": 30,
            "valid": True, "cert_error": None, "total_links": 5, "links_error": None,
            "scanned_at": "2024-01-02",
        }
    ]
    client, collection = mock_mongo_collection
    collection.find.return_value = []   # Mongo has no document for this scan

    with patch("core.database.get_connection", return_value=mock_connection), \
         patch("core.database.pymongo.MongoClient", return_value=client):
        result = database.get_site_details(1)

    assert result["scans"][0]["broken_links"] == []


def _scan_row(scan_id, **overrides):
    row = {
        "site_id": 1, "url": "https://a.com", "is_active": True, "added_at": "2024-01-01",
        "scan_id": scan_id, "status_code": 200, "title": "T", "description": "D",
        "duration_ms": 50, "status_error": None, "ip": "1.1.1.1", "country": "UA",
        "city": "Kyiv", "org": "Org", "geo_error": None, "ssl_days_left": 30,
        "valid": True, "cert_error": None, "total_links": 5, "links_error": None,
        "scanned_at": "2024-01-02",
    }
    row.update(overrides)
    return row


def test_get_site_details_maps_broken_links_to_the_right_scan(
    mock_connection, mock_cursor, mock_mongo_collection, monkeypatch
):
    monkeypatch.setenv("MONGO_DB", "mydb")
    monkeypatch.setenv("MONGO_CL", "mycol")
    mock_cursor.fetchall.return_value = [_scan_row(102), _scan_row(101), _scan_row(100)]
    client, collection = mock_mongo_collection
    collection.find.return_value = [
        {"my_sql_scan_id": 101, "broken_links": [{"url": "http://b.com", "status": 404}]},
        {"my_sql_scan_id": 100},   # document without "broken_links"
    ]

    with patch("core.database.get_connection", return_value=mock_connection), \
         patch("core.database.pymongo.MongoClient", return_value=client):
        result = database.get_site_details(1)

    assert [s["scan_id"] for s in result["scans"]] == [102, 101, 100]    # order from SQL is kept
    assert result["scans"][0]["broken_links"] == []                      # no Mongo doc
    assert result["scans"][1]["broken_links"] == [{"url": "http://b.com", "status": 404}]
    assert result["scans"][2]["broken_links"] == []                      # doc without the key


def test_get_site_details_site_without_scans_skips_mongo_query(
    mock_connection, mock_cursor, mock_mongo_collection
):
    """LEFT JOIN gives one row with scan_id=None for a site that was never scanned."""
    mock_cursor.fetchall.return_value = [_scan_row(None)]
    client, collection = mock_mongo_collection

    with patch("core.database.get_connection", return_value=mock_connection), \
         patch("core.database.pymongo.MongoClient", return_value=client):
        result = database.get_site_details(1)

    assert result["success"] is True
    assert result["site"]["url"] == "https://a.com"
    assert result["scans"] == []
    collection.find.assert_not_called()


def test_get_site_details_db_error(mock_connection, mock_cursor):
    mock_cursor.execute.side_effect = Exception("boom")
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.get_site_details(1)

    assert result["success"] is False
    assert result["error"] == "db_select_failed"
    mock_connection.close.assert_called_once()


def test_get_site_details_site_not_found_closes_connection(mock_connection, mock_cursor):
    mock_cursor.fetchall.return_value = []
    with patch("core.database.get_connection", return_value=mock_connection):
        database.get_site_details(999)

    mock_connection.close.assert_called_once()


def test_delete_site_success(mock_connection, mock_cursor):
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.delete_site(1)

    assert result["success"] is True
    mock_cursor.execute.assert_called_once_with(
        "DELETE FROM sites WHERE sites.id = %s", (1,)
    )
    mock_connection.commit.assert_called_once()
    mock_connection.close.assert_called_once()


def test_delete_site_failure(mock_connection, mock_cursor):
    mock_cursor.execute.side_effect = Exception("fail")
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.delete_site(1)

    assert result["success"] is False
    assert result["error"] == "db_delete_failed"
    mock_connection.commit.assert_not_called()
    mock_connection.close.assert_called_once()


def test_toggle_site_active_failure(mock_connection, mock_cursor):
    mock_cursor.execute.side_effect = Exception("fail")
    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.toggle_site_active(1)

    assert result["success"] is False
    assert result["error"] == "db_update_failed"


def test_check_mysql_connection_success(mock_connection):
    with patch("core.database.get_connection", return_value=mock_connection):
        assert database.check_mysql_connection() is True
    mock_connection.ping.assert_called_once_with(reconnect=True)
    mock_connection.close.assert_called_once()


def test_check_mysql_connection_failure():
    with patch("core.database.get_connection", side_effect=Exception("down")):
        assert database.check_mysql_connection() is False

def test_check_mongo_connection_success():
    mock_client = MagicMock()
    with patch("core.database.pymongo.MongoClient", return_value=mock_client):
        assert database.check_mongo_connection() is True
    mock_client.admin.command.assert_called_once_with("ping")
    mock_client.close.assert_called_once()


def test_check_mongo_connection_failure():
    with patch("core.database.pymongo.MongoClient", side_effect=Exception("down")):
        assert database.check_mongo_connection() is False

def test_toggle_site_active_success_when_deactivating(mock_connection, mock_cursor):
    mock_cursor.fetchone.return_value = (True,)

    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.toggle_site_active(1)

    assert result["success"] is True
    assert mock_cursor.execute.call_count == 2
    mock_cursor.execute.assert_any_call("SELECT is_active FROM sites WHERE id = %s", (1,))
    mock_cursor.execute.assert_any_call(
        "UPDATE sites SET is_active = NOT is_active WHERE sites.id = %s", (1,)
    )
    mock_connection.commit.assert_called_once()


def test_toggle_site_active_success_when_activating_below_limit(mock_connection, mock_cursor):
    mock_cursor.fetchone.return_value = (False,)

    with patch("core.database.get_connection", return_value=mock_connection), \
         patch("core.database.get_sites", return_value={"success": True, "result": [], "error": None}):

        result = database.toggle_site_active(1)

    assert result["success"] is True
    mock_connection.commit.assert_called_once()


def test_toggle_site_active_blocks_activation_when_limit_reached(mock_connection, mock_cursor):
    mock_cursor.fetchone.return_value = (False,)

    with patch("core.database.get_connection", return_value=mock_connection), \
         patch("core.database.get_sites", return_value={
             "success": True, "result": [{"id": i} for i in range(5)], "error": None
         }):

        result = database.toggle_site_active(1)

    assert result["success"] is False
    assert result["error"] == "total_active_links_reached"
    mock_connection.commit.assert_not_called()
    assert mock_cursor.execute.call_count == 1


def test_toggle_site_active_deactivates_even_when_limit_reached(mock_connection, mock_cursor):
    mock_cursor.fetchone.return_value = (True,)

    with patch("core.database.get_connection", return_value=mock_connection), \
         patch("core.database.get_sites") as mock_get_sites:

        result = database.toggle_site_active(1)

    assert result["success"] is True
    mock_connection.commit.assert_called_once()
    mock_get_sites.assert_not_called()


def test_toggle_site_active_site_not_found(mock_connection, mock_cursor):
    mock_cursor.fetchone.return_value = None

    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.toggle_site_active(999)

    assert result["success"] is False
    assert result["error"] == "site_not_found"
    mock_connection.commit.assert_not_called()
    assert mock_cursor.execute.call_count == 1


def test_toggle_site_active_db_error_on_select(mock_connection, mock_cursor):
    mock_cursor.execute.side_effect = Exception("db down")

    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.toggle_site_active(1)

    assert result["success"] is False
    assert result["error"] == "db_update_failed"


def test_toggle_site_active_db_error_on_update(mock_connection, mock_cursor):
    mock_cursor.fetchone.return_value = (True,)
    mock_cursor.execute.side_effect = [None, Exception("update failed")]

    with patch("core.database.get_connection", return_value=mock_connection):
        result = database.toggle_site_active(1)

    assert result["success"] is False
    assert result["error"] == "db_update_failed"

@patch("core.database.get_connection")
def test_toggle_site_pin_success(mock_get_connection):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (0,)  # is_pinned = False
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_get_connection.return_value = mock_conn

    result = database.toggle_site_pin(1)

    assert result["success"] is True
    assert result["error"] is None
    mock_conn.commit.assert_called_once()
    mock_conn.close.assert_called_once()

@patch("core.database.get_connection")
def test_toggle_site_pin_executes_update_query(mock_get_connection):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (1,)
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_get_connection.return_value = mock_conn

    database.toggle_site_pin(4)

    mock_cursor.execute.assert_any_call("UPDATE sites SET is_pinned = NOT is_pinned WHERE sites.id = %s", (4,))

@patch("core.database.get_connection")
def test_toggle_site_pin_site_not_found(mock_get_connection):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None  # сайту не існує
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_get_connection.return_value = mock_conn

    result = database.toggle_site_pin(999)

    assert result["success"] is False
    assert result["error"] == "site_not_found"
    mock_conn.commit.assert_not_called()

@patch("core.database.get_connection")
def test_toggle_site_pin_db_error(mock_get_connection):
    mock_get_connection.side_effect = Exception("connection refused")

    result = database.toggle_site_pin(1)

    assert result["success"] is False
    assert result["error"] == "db_update_failed"

@patch("core.database.get_connection")
def test_get_sites_includes_is_pinned_field(mock_get_connection):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        (1, "https://example.com", True, True, 200, "Example"),
    ]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_get_connection.return_value = mock_conn

    result = database.get_sites()

    assert result["success"] is True
    site = result["result"][0]
    assert "is_pinned" in site
    assert site["is_pinned"] is True


@patch("core.database.get_connection")
def test_get_sites_is_pinned_false_by_default(mock_get_connection):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        (2, "https://another.com", True, False, 404, "Another"),
    ]
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_get_connection.return_value = mock_conn

    result = database.get_sites()

    assert result["result"][0]["is_pinned"] is False