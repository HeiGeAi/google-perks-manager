"""Validate and cache a signed Windows installer from this project's GitHub releases."""

import argparse
import base64
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__
from .accounts import private_directory
from .server import ROOT, default_data_dir

REPO = "HeiGeAi/google-perks-manager"
RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=12"
INSTALLER = "GooglePerksManager-Windows-x64-Setup.exe"
MANIFEST = "GooglePerksManager-Windows-x64-update.json"
SIGNATURE = "GooglePerksManager-Windows-x64-update.sig"
LIMIT = 512 * 1024 * 1024
VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}")
ASSET_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if (url.scheme != "https" or url.netloc not in ASSET_HOSTS
            or url.username or url.password):
            raise ValueError("Update redirect rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, maximum):
    request = Request(url, headers={"User-Agent": "GooglePerksManager"})
    with build_opener(ReleaseRedirect()).open(request, timeout=30) as response:
        content = response.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("Update metadata too large")
    return content


def parsed_version(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError("Invalid update version")
    return tuple(map(int, value.split(".")))


def asset_url(version, asset):
    parsed_version(version)
    return f"https://github.com/{REPO}/releases/download/v{version}/{asset}"


def verified_manifest(raw, signature, version, key=None):
    if key is None:
        key = base64.b64decode((ROOT / "UpdatePublicKey.txt").read_text().strip(), validate=True)
    Ed25519PublicKey.from_public_bytes(key).verify(signature, raw)
    manifest = json.loads(raw)
    if (manifest.get("version") != version or manifest.get("platform") != "windows-x64"
        or manifest.get("asset") != INSTALLER or manifest.get("bundle_id") != "com.heige.google-manager"
        or not isinstance(manifest.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"])
        or type(manifest.get("size")) is not int or not 0 < manifest["size"] <= LIMIT):
        raise ValueError("Invalid Windows update manifest")
    return manifest


def available():
    releases = json.loads(fetch(RELEASES, 2 * 1024 * 1024))
    versions = []
    for release in releases:
        tag = release.get("tag_name", "")
        if (not tag.startswith("v") or not VERSION.fullmatch(tag[1:])
            or release.get("draft") or release.get("prerelease")):
            continue
        if {INSTALLER, MANIFEST, SIGNATURE} <= {a["name"] for a in release["assets"]}:
            versions.append(tag[1:])
    newest = max(versions, key=parsed_version, default=__version__)
    return newest if parsed_version(newest) > parsed_version(__version__) else None


def check_file(file, manifest):
    if file.stat().st_size != manifest["size"]:
        raise ValueError("Incorrect installer size")
    with file.open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise ValueError("Invalid installer format")
        handle.seek(0)
        if hashlib.file_digest(handle, "sha256").hexdigest() != manifest["sha256"]:
            raise ValueError("Incorrect installer hash")


def update_directory(version):
    parsed_version(version)
    if parsed_version(version) <= parsed_version(__version__):
        raise ValueError("Cannot install an older version")
    root = Path(os.environ.get("GOOGLE_MANAGER_DATA_DIR") or default_data_dir())
    directory = root / "updates" / version
    for folder in (root, directory.parent, directory):
        private_directory(folder)
    return directory


def download(version):
    directory = update_directory(version)
    manifest = fetch(asset_url(version, MANIFEST), 8192)
    signature = fetch(asset_url(version, SIGNATURE), 64)
    verified = verified_manifest(manifest, signature, version)
    request = Request(asset_url(version, INSTALLER), headers={"User-Agent": "GooglePerksManager"})
    with tempfile.TemporaryDirectory(dir=directory) as staging:
        candidate = Path(staging) / INSTALLER
        with build_opener(ReleaseRedirect()).open(request, timeout=45) as response, candidate.open("wb") as output:
            remaining = verified["size"]
            while remaining:
                chunk = response.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                output.write(chunk)
                remaining -= len(chunk)
            if remaining or response.read(1):
                raise ValueError("Incorrect installer size")
        check_file(candidate, verified)
        (Path(staging) / MANIFEST).write_bytes(manifest)
        (Path(staging) / SIGNATURE).write_bytes(signature)
        for name in (INSTALLER, MANIFEST, SIGNATURE):
            (Path(staging) / name).replace(directory / name)
    return {"status": "ready", "version": version}


def verify(version):
    directory = update_directory(version)
    manifest = verified_manifest((directory / MANIFEST).read_bytes(), (directory / SIGNATURE).read_bytes(), version)
    installer = directory / INSTALLER
    check_file(installer, manifest)
    return {"path": str(installer), "version": version}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "download", "verify"))
    parser.add_argument("version", nargs="?")
    args = parser.parse_args(argv)
    try:
        if args.action == "check":
            version = available()
            if version:
                raw = fetch(asset_url(version, MANIFEST), 8192)
                verified_manifest(raw, fetch(asset_url(version, SIGNATURE), 64), version)
            result = {"status": "available" if version else "current", "version": version or __version__}
        else:
            result = download(args.version) if args.action == "download" else verify(args.version)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, InvalidSignature):
        print('{"error":"Could not verify the Windows update."}')
        return 1
