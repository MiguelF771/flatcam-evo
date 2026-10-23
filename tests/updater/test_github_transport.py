"""Focused, network-free tests for the GitHub Releases update transport."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.updater.checker import UpdateChecker
from services.updater.transport import (
    DigiPublicShareTransport,
    DownloadCancelled,
    GitHubReleasesTransport,
    TransportError,
    github_asset_name,
    parse_github_repo_url,
)


REPO_URL = "https://github.com/MiguelF771/flatcam-evo"
API_URL = "https://api.github.com/repos/MiguelF771/flatcam-evo/releases/latest"
ARCHIVE_URL = "https://github.com/MiguelF771/flatcam-evo/releases/download/v1.0.0/flatcam-windows.zip"
MANIFEST_URL = "https://github.com/MiguelF771/flatcam-evo/releases/download/v1.0.0/manifest-windows.json"
ARCHIVE = b"archive-bytes"
MANIFEST = b'{"version": "1.0.0"}'


class FakeResponse:
    def __init__(self, body=b"", headers=None, status=200):
        self._body = body
        self._position = 0
        self.headers = headers or {}
        self.status = status

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._body) - self._position
        end = min(self._position + size, len(self._body))
        result = self._body[self._position:end]
        self._position = end
        return result


def _release(assets=None):
    if assets is None:
        assets = [
            {
                "name": "flatcam-windows.zip",
                "size": len(ARCHIVE),
                "browser_download_url": ARCHIVE_URL,
                "digest": "sha256:" + hashlib.sha256(ARCHIVE).hexdigest(),
            },
            {
                "name": "manifest-windows.json",
                "size": len(MANIFEST),
                "browser_download_url": MANIFEST_URL,
            },
            {
                "name": "manifest-linux.json",
                "size": 2,
                "browser_download_url": MANIFEST_URL.replace("windows", "linux"),
            },
        ]
    return {"tag_name": "v1.0.0", "assets": assets}


class GitHubOpener:
    def __init__(self, release=None, fail=None):
        self.release = _release() if release is None else release
        self.fail = fail or {}
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        url = request.full_url
        if url in self.fail:
            raise urllib.error.HTTPError(url, self.fail[url], "error", {}, None)
        if url == API_URL:
            return FakeResponse(json.dumps(self.release).encode())
        if url == ARCHIVE_URL:
            return FakeResponse(ARCHIVE, headers={"Content-Length": str(len(ARCHIVE))})
        if url == MANIFEST_URL:
            return FakeResponse(MANIFEST)
        raise AssertionError(f"unexpected URL: {url}")

    def urls(self):
        return [request.full_url for request, _ in self.requests]


class TestRepoUrl(unittest.TestCase):
    def test_accepted_spellings_resolve_owner_and_repo(self):
        for url in (
            REPO_URL,
            REPO_URL + "/",
            REPO_URL + ".git",
            REPO_URL + "/releases",
            "https://www.github.com/MiguelF771/flatcam-evo/releases/",
        ):
            with self.subTest(url=url):
                self.assertEqual(("MiguelF771", "flatcam-evo"), parse_github_repo_url(url))

    def test_non_github_or_incomplete_urls_are_rejected(self):
        for url in (
            "https://s.go.ro/zihniipc",
            "http://github.com/a/b",
            "https://github.com/onlyowner",
            "https://github.com/a/b/issues",
            "https://evilgithub.com/a/b",
            "",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    GitHubReleasesTransport(url)


class TestAssetNames(unittest.TestCase):
    def test_manifest_is_channel_suffixed_and_other_files_unchanged(self):
        self.assertEqual("manifest-windows.json", github_asset_name("windows", "manifest.json"))
        self.assertEqual("manifest-linux.json", github_asset_name("linux", "manifest.json"))
        self.assertEqual("flatcam-windows.zip", github_asset_name("windows", "flatcam-windows.zip"))


class TestGitHubTransport(unittest.TestCase):
    def _transport(self, **kwargs):
        opener = GitHubOpener(**kwargs)
        return GitHubReleasesTransport(REPO_URL, opener=opener, timeout=7), opener

    def test_manifest_downloads_from_latest_release_with_required_headers(self):
        transport, opener = self._transport()

        data = transport.download_bytes("windows", "manifest.json")

        self.assertEqual(MANIFEST, data)
        self.assertEqual([API_URL, MANIFEST_URL], opener.urls())
        api_request, timeout = opener.requests[0]
        self.assertEqual(7, timeout)
        self.assertEqual("application/vnd.github+json", api_request.get_header("Accept"))
        self.assertTrue(api_request.get_header("User-agent"))

    def test_release_metadata_is_cached_per_transport(self):
        transport, opener = self._transport()

        transport.download_bytes("windows", "manifest.json")
        transport.check_file("windows", "flatcam-windows.zip")

        self.assertEqual(1, opener.urls().count(API_URL))

    def test_archive_download_verifies_sha256_and_reports_progress(self):
        transport, _ = self._transport()
        progress = []
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "stage" / "flatcam-windows.zip"

            result = transport.download_archive(
                "windows",
                "flatcam-windows.zip",
                destination,
                expected_size=len(ARCHIVE),
                expected_sha256=hashlib.sha256(ARCHIVE).hexdigest(),
                progress_cb=lambda written, total: progress.append((written, total)),
            )

            self.assertEqual(destination, result)
            self.assertEqual(ARCHIVE, destination.read_bytes())
            self.assertFalse(Path(f"{destination}.part").exists())
        self.assertEqual((len(ARCHIVE), len(ARCHIVE)), progress[-1])

    def test_archive_checksum_mismatch_leaves_no_files(self):
        transport, _ = self._transport()
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "flatcam-windows.zip"

            with self.assertRaises(TransportError):
                transport.download_archive(
                    "windows", "flatcam-windows.zip", destination, expected_sha256="0" * 64
                )

            self.assertFalse(destination.exists())
            self.assertFalse(Path(f"{destination}.part").exists())

    def test_cancel_stops_archive_download(self):
        transport, _ = self._transport()
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "flatcam-windows.zip"

            with self.assertRaises(DownloadCancelled):
                transport.download_archive(
                    "windows", "flatcam-windows.zip", destination, cancel_cb=lambda: True
                )

            self.assertFalse(destination.exists())

    def test_missing_asset_is_none_for_check_and_error_for_download(self):
        transport, _ = self._transport(release=_release(assets=[]))

        self.assertIsNone(transport.check_file("windows", "flatcam-windows.zip"))
        with self.assertRaises(TransportError):
            transport.download_bytes("windows", "manifest.json")

    def test_check_file_and_listing_use_channel_names(self):
        transport, _ = self._transport()

        remote = transport.check_file("windows", "manifest.json")
        listing = {item.name: item for item in transport.list_files("windows")}

        self.assertEqual("manifest.json", remote.name)
        self.assertEqual(len(MANIFEST), remote.size)
        self.assertEqual({"manifest.json", "flatcam-windows.zip"}, set(listing))
        self.assertEqual(hashlib.sha256(ARCHIVE).hexdigest(), listing["flatcam-windows.zip"].sha256)

    def test_http_errors_map_to_transport_error(self):
        for status in (404, 403, 500):
            with self.subTest(status=status):
                transport, _ = self._transport(fail={API_URL: status})
                with self.assertRaisesRegex(TransportError, str(status)):
                    transport.download_bytes("windows", "manifest.json")

    def test_asset_download_http_error_maps_to_transport_error(self):
        transport, _ = self._transport(fail={ARCHIVE_URL: 404})
        with TemporaryDirectory() as directory:
            with self.assertRaises(TransportError):
                transport.download_archive("windows", "flatcam-windows.zip", Path(directory) / "a.zip")

    def test_invalid_release_json_is_rejected(self):
        transport, _ = self._transport(release={"tag_name": "v1"})
        with self.assertRaises(TransportError):
            transport.latest_release()


class TestCheckerTransportSelection(unittest.TestCase):
    def test_github_url_builds_github_transport(self):
        transport = UpdateChecker(share_url=REPO_URL).create_transport()
        self.assertIsInstance(transport, GitHubReleasesTransport)

    def test_other_urls_keep_digi_transport(self):
        transport = UpdateChecker().create_transport(share_url="https://s.go.ro/zihniipc")
        self.assertIsInstance(transport, DigiPublicShareTransport)

    def test_transport_factory_still_overrides_selection(self):
        sentinel = object()
        checker = UpdateChecker(share_url=REPO_URL, transport_factory=lambda app: sentinel)
        self.assertIs(sentinel, checker.create_transport())


if __name__ == "__main__":
    unittest.main()
