"""Download the game tables (wago.tools) and the icon list (wowdev community listfile).

Downloads are https only, limited to an allowlist of hosts (redirects included), size-capped,
written to a ``.part`` file and renamed only after the content looks right.
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .assets import load_toml
from .errors import MacroForgeError, ValidationError
from .fsutil import atomic_write_text
from .settings import Settings

BUILD_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{3,7}$")
MAX_TABLE_BYTES = 50_000_000
MAX_LISTFILE_BYTES = 600_000_000
MAX_ICON_LINES = 500_000
MAX_API_BYTES = 5_000_000
MAX_LINE_BYTES = 65536
TABLE_DEADLINE_SECONDS = 600
LISTFILE_DEADLINE_SECONDS = 1800
CHUNK = 65536
ICON_ASSET = "community-listfile-withcapitals.csv"
_ICON_LINE = re.compile(r"^\d+;interface/icons/", re.I)


def installed_build(settings: Settings) -> str:
    """Version string (like ``1.60.1.70291``) of this client's product, from ``.build.info``."""
    try:
        lines = settings.build_info.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValidationError(f"cannot read {settings.build_info}: {exc}") from exc
    columns = {h.split("!")[0]: i for i, h in enumerate(lines[0].split("|"))} if lines else {}
    for row in lines[1:]:
        fields = row.split("|")
        if len(fields) > max(columns.get("Product", 0), columns.get("Version", 0)) \
                and fields[columns["Product"]] == settings.product:
            return fields[columns["Version"]]
    raise ValidationError(f"product {settings.product!r} not found in .build.info; "
                          'set "product" in config.json')


def check_url(settings: Settings, url: str) -> None:
    """Raise unless ``url`` is https (or http when explicitly allowed) on an allowed host."""
    parts = urlparse(url)
    if parts.scheme != "https" and not (settings.fetch_allow_http and parts.scheme == "http"):
        raise ValidationError(f"refusing non-https URL: {url}")
    host = (parts.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in settings.fetch_allowed_hosts):
        raise ValidationError(f"refusing to download from {host!r}")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Apply the same host rules to every redirect."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Refuse redirects that leave the allowed hosts."""
        check_url(self.settings, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(settings: Settings, url: str, timeout: int):
    """Open an allowed URL; network failures become :class:`MacroForgeError`."""
    check_url(settings, url)
    opener = urllib.request.build_opener(_CheckedRedirects(settings))
    request = urllib.request.Request(url, headers={"User-Agent": "macroforge"})
    try:
        return opener.open(request, timeout=timeout)
    except (urllib.error.URLError, OSError) as exc:
        raise MacroForgeError(f"download failed: {exc}") from exc


def _lines(resp, deadline: float):
    """Yield the lines of a response without ever buffering more than ``MAX_LINE_BYTES`` of one."""
    buffer = b""
    while True:
        if time.monotonic() > deadline:
            raise ValidationError("download took too long")
        chunk = resp.read(CHUNK)
        if not chunk:
            break
        buffer += chunk
        *lines, buffer = buffer.split(b"\n")
        yield from lines
        if len(buffer) > MAX_LINE_BYTES:
            raise ValidationError("download contains an over-long line")
    if buffer:
        yield buffer


def download_table(settings: Settings, table: str, build: str, dest: Path) -> None:
    """Download one table to ``dest`` if it is a CSV with the expected columns."""
    required = load_toml("tables.toml")["tables"][table]
    part = dest.with_name(dest.name + ".part")
    try:
        total = 0
        deadline = time.monotonic() + TABLE_DEADLINE_SECONDS
        url = settings.fetch_wago_url.format(table=table, build=build)
        with open_url(settings, url, 120) as resp, open(part, "wb") as out:
            while chunk := resp.read(CHUNK):
                if time.monotonic() > deadline:
                    raise ValidationError(f"{table}: download took too long")
                total += len(chunk)
                if total > MAX_TABLE_BYTES:
                    raise ValidationError(
                        f"{table}: download is larger than {MAX_TABLE_BYTES} bytes")
                out.write(chunk)
        try:
            with open(part, encoding="utf-8-sig", newline="") as handle:
                header = next(csv.reader([handle.readline()]), [])
        except (UnicodeDecodeError, csv.Error):
            raise ValidationError(f"{table}: not a text CSV file") from None
        missing = [c for c in required if c not in header]
        if missing:
            raise ValidationError(f"{table}: not the expected CSV (missing columns {missing})")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


def download_icons(settings: Settings, dest: Path) -> int:
    """Download the community listfile, keeping only ``Interface/ICONS`` lines; returns how many."""
    with open_url(settings, settings.fetch_listfile_api, 60) as resp:
        body = resp.read(MAX_API_BYTES + 1)
    if len(body) > MAX_API_BYTES:
        raise ValidationError("release information is too large")
    try:
        assets = json.loads(body).get("assets", [])
        url = next(a["browser_download_url"] for a in assets if a.get("name") == ICON_ASSET)
    except (ValueError, RecursionError, StopIteration, KeyError, AttributeError, TypeError):
        raise ValidationError(f"release does not contain {ICON_ASSET}") from None
    part = dest.with_name(dest.name + ".part")
    count = read_bytes = 0
    deadline = time.monotonic() + LISTFILE_DEADLINE_SECONDS
    try:
        with open_url(settings, url, 300) as resp, \
                open(part, "w", encoding="utf-8", newline="\n") as out:
            for raw in _lines(resp, deadline):
                read_bytes += len(raw) + 1
                if read_bytes > MAX_LISTFILE_BYTES:
                    raise ValidationError("listfile is larger than expected")
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if _ICON_LINE.match(line):
                    out.write(line + "\n")
                    count += 1
                    if count > MAX_ICON_LINES:
                        raise ValidationError("listfile has too many icon lines")
        if count == 0:
            raise ValidationError("listfile contained no icons")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return count


def fetch_all(settings: Settings, build: str | None = None, no_icons: bool = False,
              out: Callable[[str], None] = print) -> None:
    """Download every table for ``build`` (default: the installed client) and the icon list."""
    build = build or installed_build(settings)
    if not BUILD_RE.match(build):
        raise ValidationError(f"{build!r} is not a build like 1.60.1.70291")
    settings.data.mkdir(exist_ok=True)
    out(f"build {build}")
    for table in load_toml("tables.toml")["tables"]:
        try:
            download_table(settings, table, build, settings.data / f"{table}.csv")
        except MacroForgeError as exc:
            raise ValidationError(
                f"  {table}: failed ({exc}). wago.tools may not have build {build} yet; "
                "try again later or pass --build") from exc
        out(f"  {table}: ok")
    for old in [*settings.data.glob("spells_*.json"), settings.data / "pets.json"]:
        old.unlink(missing_ok=True)  # derived tables are rebuilt from the new data on demand
    atomic_write_text(settings.data / "BUILD", build + "\n")  # client build the tables are for
    icons = settings.data / "icons.csv"
    if no_icons and icons.exists():
        out("icons: kept existing")
        return
    out("icons: downloading the community listfile (~150 MB) and keeping only Interface/ICONS ...")
    out(f"  {download_icons(settings, icons)} icons")
