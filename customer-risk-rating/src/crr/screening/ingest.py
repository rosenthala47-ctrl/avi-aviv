"""Fetch or load one source's raw list file and parse it into records.

Two ways in, sharing one parse step:

* :func:`fetch` — a live HTTP GET against the publisher, for an environment
  with outbound access to it (a bank's own network, not necessarily wherever
  this ingestion job happens to run from).
* :func:`load_file` — parse a file already on disk. This is not a fallback
  for when the network is unavailable; it is how a lot of real regulated
  deployments are required to do this regardless of connectivity — a
  controlled process downloads and reviews the publisher's file, and the
  application itself is never allowed to reach outbound government or EU
  infrastructure directly. ``scripts/refresh_watchlists.py`` exposes both.

DEFAULT_URLS point at each publisher's real, current, freely downloadable
endpoint for OFAC and the UN. The EU's Financial Sanctions Files (FSF) list
does not have a stable public URL for the raw XML — the European Commission
serves it through the interactive Sanctions Map UI, which mints a
short-lived per-session download token, so the URL below is a placeholder;
``--url`` (or ``--file`` with a copy obtained through that UI) is required
for that source until/unless a stable feed is configured. This is documented
here rather than silently working for two sources and mysteriously failing
for the third.
"""

from __future__ import annotations

from pathlib import Path

from crr.screening.models import WatchlistRecord
from crr.screening.parsers import parse_source

#: Best-effort defaults for `--source X` with no `--url` override. See the
#: EU note above for why that entry is not a real endpoint.
DEFAULT_URLS: dict[str, str] = {
    "ofac": "https://www.treasury.gov/ofac/downloads/sdn.xml",
    "un": "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
    "eu": "",  # see module docstring — requires a session-specific token
}

#: Refuse to parse a response larger than this — these lists are tens of MB
#: at most; an unbounded read against a URL is exactly the kind of thing that
#: turns a slow feed or a misconfigured redirect into a memory exhaustion.
MAX_RESPONSE_BYTES = 200 * 1024 * 1024

_REQUEST_TIMEOUT = 60.0


def fetch(source: str, url: str | None = None) -> bytes:
    """GET the source's list file and return the raw bytes, unparsed."""
    import requests  # local import: only ingest's live-fetch path needs it

    target = url or DEFAULT_URLS.get(source) or ""
    if not target:
        raise ValueError(f"no default URL for source {source!r} — pass --url explicitly (see module docstring)")
    with requests.get(target, timeout=_REQUEST_TIMEOUT, stream=True) as response:
        response.raise_for_status()
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=1 << 20):
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ValueError(f"{source} response exceeded {MAX_RESPONSE_BYTES} bytes — refusing to parse")
            chunks.append(chunk)
        return b"".join(chunks)


def load_file(path: str | Path) -> bytes:
    p = Path(path)
    data = p.read_bytes()
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError(f"{p} exceeded {MAX_RESPONSE_BYTES} bytes — refusing to parse")
    return data


def fetch_and_parse(source: str, url: str | None = None) -> list[WatchlistRecord]:
    return parse_source(source, fetch(source, url))


def load_and_parse(source: str, path: str | Path) -> list[WatchlistRecord]:
    return parse_source(source, load_file(path))
