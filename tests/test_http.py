"""The transport has one job the rest of the system depends on: send the
header names exactly as given. Plane rejects a normalised X-API-Key."""

import pytest

from catalogbot.http import JsonClient


def test_header_case_is_preserved():
    client = JsonClient("https://api.plane.so", headers={"X-API-Key": "secret"})
    assert "X-API-Key" in client._headers
    assert "X-Api-Key" not in client._headers


def test_base_url_path_prefix_is_kept():
    client = JsonClient("https://openrouter.ai/api/v1")
    assert client._host == "openrouter.ai"
    assert client._prefix == "/api/v1"


def test_trailing_slash_does_not_double_up():
    client = JsonClient("https://api.plane.so/")
    assert client._prefix == ""


def test_non_http_scheme_is_rejected():
    with pytest.raises(ValueError):
        JsonClient("ftp://example.com")
