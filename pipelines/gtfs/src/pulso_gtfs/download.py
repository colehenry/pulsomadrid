"""Fetch a source ZIP, hash it, and archive the untouched original in GCS."""
from __future__ import annotations

import hashlib
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
from google.cloud import storage

from .config import credentials

log = logging.getLogger(__name__)


def fetch(url: str, dest: Path, timeout: float = 300.0) -> tuple[Path, str]:
    """Download to dest. Returns the path and the SHA256 of the bytes."""
    digest = hashlib.sha256()
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
        r.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                digest.update(chunk)
                fh.write(chunk)
    sha = digest.hexdigest()
    log.info("downloaded %s -> %s (%.1f MB, sha256 %s)", url, dest, dest.stat().st_size / 1e6, sha[:12])
    return dest, sha


def archive(local: Path, bucket: str, source: str, when: datetime | None = None) -> str:
    """Copy the original file to GCS, unmodified. This is the reprocessing layer."""
    when = when or datetime.now(UTC)
    key = f"{source}/{when:%Y/%m/%d}/{when:%Y-%m-%dT%H%M%SZ}{''.join(local.suffixes)}"
    blob = storage.Client(credentials=credentials()).bucket(bucket).blob(key)
    blob.upload_from_filename(local)
    uri = f"gs://{bucket}/{key}"
    log.info("archived %s", uri)
    return uri


def extract(zip_path: Path, dest_dir: Path) -> list[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest_dir)
        return z.namelist()
