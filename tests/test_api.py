import pytest
from unittest.mock import patch, MagicMock

from src import api


@patch("src.api.requests.get")
def test_get_random_joke(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"value": "A random joke"}
    mock_get.return_value = resp

    joke = api.get_random_joke()
    assert joke == "A random joke"


@patch("src.api.requests.get")
def test_get_categories(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = ["animal", "career"]
    mock_get.return_value = resp

    cats = api.get_categories()
    assert cats == ["animal", "career"]


@patch("src.api.requests.get")
def test_search_jokes(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "result": [
            {"value": "joke 1"},
            {"value": "joke 2"},
            {"value": "joke 3"},
        ]
    }
    mock_get.return_value = resp

    jokes = api.search_jokes("code", limit=2)
    assert jokes == ["joke 1", "joke 2"]


@patch("src.api.requests.get")
def test_api_error_non_200(mock_get):
    resp = MagicMock()
    resp.status_code = 500
    resp.json.return_value = {}
    mock_get.return_value = resp

    with pytest.raises(api.APIError):
        api.get_random_joke()

