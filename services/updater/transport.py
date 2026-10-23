"""Anonymous Digi public-share transport."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .manifest import ManifestError, select_channel, validate_relative_path


PUBLIC_HOST = "storage.rcs-rds.ro"
_LINK_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")


class TransportError(RuntimeError):
    """Raised for deterministic public-share transport failures."""


class DownloadCancelled(TransportError):
    """Raised when a caller cancels a download."""


@dataclass(frozen=True)
class ShareMetadata:
    link_id: str
    canonical_url: str

    @property
    def origin_url(self) -> str:
        """Return the validated HTTPS origin separate from the share path."""
        parsed = urllib.parse.urlsplit(self.canonical_url)
        return urllib.parse.urlunsplit(("https", parsed.netloc, "", "", ""))


@dataclass(frozen=True)
class RemoteFile:
    name: str
    size: int | None = None
    sha256: str | None = None


class _HttpTransportBase:
    """Shared response, streaming and verification helpers for release transports."""

    label = "Digi"
    chunk_size = 1024 * 1024

    @staticmethod
    def _close(response) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _status(response) -> int:
        return int(getattr(response, "status", getattr(response, "code", 200)) or 200)

    @staticmethod
    def _response_total(response, expected_size=None) -> int | None:
        total = expected_size
        if total is None:
            header = response.headers.get("Content-Length") if hasattr(response, "headers") else None
            if header is None:
                getheader = getattr(response, "getheader", None)
                if callable(getheader):
                    header = getheader("Content-Length")
            try:
                total = int(header) if header is not None else None
            except (TypeError, ValueError):
                total = None
        return total

    def _read_response(self, response, expected_size=None, progress_cb=None, cancel_cb=None) -> bytes:
        total = self._response_total(response, expected_size)
        output = io.BytesIO()
        written = 0
        while True:
            if cancel_cb is not None and cancel_cb():
                raise DownloadCancelled(f"{self.label} download cancelled.")
            chunk = response.read(self.chunk_size)
            if not chunk:
                break
            output.write(chunk)
            written += len(chunk)
            if progress_cb is not None:
                progress_cb(written, total)
        if cancel_cb is not None and cancel_cb():
            raise DownloadCancelled(f"{self.label} download cancelled.")
        return output.getvalue()

    def _stream_response(
        self,
        response,
        destination: Path,
        expected_size=None,
        expected_sha256=None,
        progress_cb=None,
        cancel_cb=None,
    ) -> None:
        total = self._response_total(response, expected_size)
        digest = hashlib.sha256()
        written = 0
        with Path(destination).open("wb") as output:
            while True:
                if cancel_cb is not None and cancel_cb():
                    raise DownloadCancelled(f"{self.label} download cancelled.")
                chunk = response.read(self.chunk_size)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if progress_cb is not None:
                    progress_cb(written, total)
            output.flush()
            os.fsync(output.fileno())
        if cancel_cb is not None and cancel_cb():
            raise DownloadCancelled(f"{self.label} download cancelled.")
        if expected_size is not None and written != expected_size:
            raise TransportError(f"{self.label} download size did not match the expected size.")
        if expected_sha256 is not None and digest.hexdigest() != expected_sha256.lower():
            raise TransportError(f"{self.label} download SHA-256 did not match the expected digest.")

    def _verify(self, data: bytes, expected_size=None, expected_sha256=None) -> None:
        if expected_size is not None and len(data) != expected_size:
            raise TransportError(f"{self.label} download size did not match the expected size.")
        if expected_sha256 is not None:
            actual = hashlib.sha256(data).hexdigest()
            if actual.lower() != expected_sha256.lower():
                raise TransportError(f"{self.label} download SHA-256 did not match the expected digest.")


class DigiPublicShareTransport(_HttpTransportBase):
    """Resolve and access the configured Digi public share without credentials."""

    def __init__(
        self,
        short_url: str,
        *,
        expected_link_id: str | None = None,
        opener=None,
        timeout: float = 30.0,
        chunk_size: int = 1024 * 1024,
        canonical_host: str = PUBLIC_HOST,
    ) -> None:
        self.short_url = short_url
        self.expected_link_id = expected_link_id
        self._opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.chunk_size = chunk_size
        self.canonical_host = canonical_host.lower()
        self._metadata: ShareMetadata | None = None

    def _open(self, url: str, referer: str | None = None):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise TransportError("Digi transport requires HTTPS URLs.")
        headers = {"Referer": referer} if referer else {}
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            response = self._opener(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise TransportError(f"Digi request returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError(f"Digi request failed: {type(exc).__name__}.") from exc
        if self._status(response) >= 400:
            self._close(response)
            raise TransportError(f"Digi request returned HTTP {self._status(response)}.")
        return response

    def _canonical_parts(self, url: str) -> tuple[str, str]:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != self.canonical_host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise TransportError("Digi redirect did not resolve to the canonical HTTPS host.")
        path = parsed.path.rstrip("/")
        pieces = path.split("/")
        if len(pieces) != 3 or pieces[1] != "links" or not _LINK_ID_RE.fullmatch(pieces[2]):
            raise TransportError("Digi redirect has an invalid public-link path.")
        if self.expected_link_id and pieces[2] != self.expected_link_id:
            raise TransportError("Digi redirect resolved to an unexpected public link.")
        canonical = urllib.parse.urlunsplit(("https", parsed.netloc, path, "", ""))
        return canonical, pieces[2]

    def resolve_share(self) -> ShareMetadata:
        """Resolve the short URL and validate the anonymous canonical link metadata."""
        if self._metadata is not None:
            return self._metadata
        response = self._open(self.short_url)
        try:
            redirect_url = response.geturl() or getattr(response, "url", "")
        finally:
            self._close(response)
        canonical_url, link_id = self._canonical_parts(redirect_url)
        origin_url = urllib.parse.urlunsplit(
            ("https", urllib.parse.urlsplit(canonical_url).netloc, "", "", "")
        )
        metadata_url = f"{origin_url}/api/v2/public/links/{link_id}"
        response = self._open(metadata_url, referer=canonical_url)
        try:
            try:
                data = json.loads(response.read())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransportError("Digi metadata response was not valid JSON.") from exc
        finally:
            self._close(response)
        if not isinstance(data, dict):
            raise TransportError("Digi metadata response was not an object.")
        metadata_id = data.get("id", data.get("link_id", data.get("uuid")))
        if str(metadata_id) != link_id:
            raise TransportError("Digi metadata did not match the canonical public link.")
        for key in ("public", "is_public", "isPublic"):
            if key in data and data[key] is not True:
                raise TransportError("Digi link is not public.")
        for key in ("password_protected", "passwordProtected", "hasPassword"):
            if key in data and data[key] is True:
                raise TransportError("Digi link requires a password.")
        self._metadata = ShareMetadata(link_id=link_id, canonical_url=canonical_url)
        return self._metadata

    def _channel(self, channel: str) -> str:
        if channel in ("windows", "linux"):
            return channel
        return select_channel(channel)

    def _listing_url(self, channel: str) -> str:
        metadata = self.resolve_share()
        encoded_path = urllib.parse.quote(f"/{channel}", safe="")
        return f"{metadata.origin_url}/api/v2/public/links/{metadata.link_id}/bundle?path={encoded_path}"

    def _content_url(self, channel: str, filename: str) -> str:
        metadata = self.resolve_share()
        filename = validate_relative_path(filename)
        remote_path = f"/{channel}/{filename}"
        encoded_name = urllib.parse.quote(filename, safe="")
        encoded_path = urllib.parse.quote(remote_path, safe="")
        return (
            f"{metadata.origin_url}/content/links/{metadata.link_id}/files/get/"
            f"{encoded_name}?path={encoded_path}"
        )

    def list_files(self, channel: str) -> tuple[RemoteFile, ...]:
        """List files in a platform folder using the Digi bundle endpoint."""
        channel = self._channel(channel)
        metadata = self.resolve_share()
        response = self._open(self._listing_url(channel), referer=metadata.canonical_url)
        try:
            try:
                data = json.loads(response.read())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransportError("Digi listing response was not valid JSON.") from exc
        finally:
            self._close(response)
        if isinstance(data, list):
            raw_files = data
        elif isinstance(data, dict):
            raw_files = data.get("files", data.get("items", data.get("data", [])))
        else:
            raise TransportError("Digi listing response was not a file list.")
        if not isinstance(raw_files, list):
            raise TransportError("Digi listing response was not a file list.")
        result = []
        for item in raw_files:
            if not isinstance(item, dict):
                raise TransportError("Digi listing contained an invalid file entry.")
            raw_name = item.get("name", item.get("path", item.get("filename")))
            if not isinstance(raw_name, str):
                raise TransportError("Digi listing entry has no file name.")
            prefix = f"/{channel}/"
            alternate_prefix = f"{channel}/"
            if raw_name.startswith(prefix):
                raw_name = raw_name[len(prefix):]
            elif raw_name.startswith(alternate_prefix):
                raw_name = raw_name[len(alternate_prefix):]
            name = validate_relative_path(raw_name)
            size = item.get("size")
            if size is not None:
                try:
                    size = int(size)
                except (TypeError, ValueError) as exc:
                    raise TransportError("Digi listing entry has an invalid size.") from exc
                if size < 0:
                    raise TransportError("Digi listing entry has an invalid size.")
            digest = item.get("sha256")
            result.append(RemoteFile(name=name, size=size, sha256=digest))
        return tuple(result)

    def check_file(self, channel: str, filename: str) -> RemoteFile | None:
        """Return matching remote metadata, or None when the file is absent."""
        filename = validate_relative_path(filename)
        for remote in self.list_files(channel):
            if remote.name == filename:
                return remote
        return None

    def download_bytes(
        self,
        channel: str,
        filename: str,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        progress_cb=None,
        cancel_cb=None,
    ) -> bytes:
        """Download one public-share file into memory without credentials."""
        channel = self._channel(channel)
        metadata = self.resolve_share()
        response = self._open(self._content_url(channel, filename), referer=metadata.canonical_url)
        try:
            data = self._read_response(response, expected_size, progress_cb, cancel_cb)
        finally:
            self._close(response)
        self._verify(data, expected_size, expected_sha256)
        return data

    def download_archive(
        self,
        channel: str,
        filename: str,
        destination: Path,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        progress_cb=None,
        cancel_cb=None,
    ) -> Path:
        """Stream an archive to a temporary file and atomically replace destination."""
        channel = self._channel(channel)
        metadata = self.resolve_share()
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(f"{destination}.part")
        temporary.unlink(missing_ok=True)
        response = None
        try:
            response = self._open(self._content_url(channel, filename), referer=metadata.canonical_url)
            self._stream_response(
                response,
                temporary,
                expected_size,
                expected_sha256,
                progress_cb,
                cancel_cb,
            )
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            if response is not None:
                self._close(response)
        return destination
