"""Simple API client for the Chuck Norris Jokes API.

Functions are small, well-documented, and raise APIError on failure.
Network calls are designed to be mockable in tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import requests

BASE_URL = "https://api.chucknorris.io"


class APIError(Exception):
    """Raised when an API request fails or returns an invalid response."""


def _get_json(path: str, params: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Any:
    """Internal helper to perform a GET request and return parsed JSON.

    Args:
        path: Relative API path starting with '/'.
        params: Optional query parameters.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON (list or dict).

    Raises:
        APIError: On non-2xx status codes or JSON parsing issues.
    """
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, params=params or {}, timeout=timeout)
        if not (200 <= resp.status_code < 300):
            raise APIError(f"HTTP {resp.status_code} for {url}")
        return resp.json()
    except requests.RequestException as e:
        raise APIError(f"Request failed for {url}: {e}") from e
    except ValueError as e:
        raise APIError(f"Invalid JSON from {url}: {e}") from e


def get_random_joke() -> str:
    """Fetch a random Chuck Norris joke.

    Returns:
        The joke text.
    """
    data = _get_json("/jokes/random")
    return str(data.get("value", ""))


def get_categories() -> List[str]:
    """Fetch all available joke categories.

    Returns:
        List of category names.
    """
    data = _get_json("/jokes/categories")
    if isinstance(data, list):
        return [str(x) for x in data]
    raise APIError("Unexpected categories payload")


def search_jokes(query: str, limit: int = 20) -> List[str]:
    """Search for jokes containing the query string.

    Args:
        query: Search term.
        limit: Maximum number of jokes to return.

    Returns:
        List of joke texts (up to limit size).
    """
    query = (query or "").strip()
    if not query:
        return []
    data = _get_json("/jokes/search", params={"query": query})
    items = data.get("result", []) if isinstance(data, dict) else []
    out = []
    for it in items:
        val = str(it.get("value", ""))
        if val:
            out.append(val)
        if len(out) >= max(1, int(limit)):
            break
    return out

