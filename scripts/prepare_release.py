"""Package signed release assets from existing macOS and Windows builds."""

import argparse
import base64
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE_NAME = "谷歌羊毛管理器.app"
BUNDLE_ID = "com.heige.google-manager"
ZIP_NAME = "GooglePerksManager-macOS-arm64.zip"
MANIFEST_NAME = "GooglePerksManager-update.json"
SIGNATURE_NAME = "GooglePerksManager-update.sig"
WINDOWS_NAME = "GooglePerksManager-Windows-x64-Setup.exe"
WINDOWS_MANIFEST = "GooglePerksManager-Windows-x64-update.json"
WINDOWS_SIGNATURE = "GooglePerksManager-Windows-x64-update.sig"
ED25519_PREFIX = bytes.fromhex("302a300506032b6570032100")
PRIVATE_FILES = {
    "wechat-contact.png", "agent-connection.json", "accounts.json", "token.json",
    "oauth.json", "release-ed25519.pem",
}


def command(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def bundle_version(path, trusted_key):
    if path.name != BUNDLE_NAME:
        raise ValueError(f"Expected a bundle named {BUNDLE_NAME}.")
    with (path / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    version = info.get("CFBundleShortVersionString", "")
    if info.get("CFBundleIdentifier") != BUNDLE_ID or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("The app identifier or release version is invalid.")
    if info.get("GooglePerksUpdatePublicKey") != base64.b64encode(trusted_key).decode("ascii"):
        raise ValueError("The app bundle does not embed the upstream update key.")
    command("/usr/bin/codesign", "--verify", "--deep", "--strict", path)
    return version


def verify_archive(archive):
    with zipfile.ZipFile(archive) as content:
        for entry in content.namelist():
            parts = Path(entry).parts
            if entry.startswith("/") or ".." in parts:
                raise ValueError("The release archive contains an invalid path.")
            if PRIVATE_FILES.intersection(parts) or any(part.startswith("client_secret") for part in parts):
                raise ValueError("The release archive contains a private app resource.")
        if content.testzip() is not None:
            raise ValueError("The release archive failed its ZIP integrity check.")


def sign_asset(args, asset, version, manifest, signature, public_key, platform=None):
    with asset.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    record = {"version": version, "asset": asset.name, "sha256": digest,
              "size": asset.stat().st_size, "bundle_id": BUNDLE_ID}
    if platform is not None:
        record["platform"] = platform
    manifest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    command(args.openssl, "pkeyutl", "-sign", "-inkey", args.signing_key, "-rawin",
            "-in", manifest, "-out", signature)
    command(args.openssl, "pkeyutl", "-verify", "-pubin", "-keyform", "DER", "-inkey", public_key,
            "-rawin", "-in", manifest, "-sigfile", signature)
    if signature.stat().st_size != 64:
        raise ValueError("Expected a detached 64-byte Ed25519 signature.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=ROOT / "dist" / BUNDLE_NAME)
    parser.add_argument("--output", type=Path, required=True, help="New output folder for this version")
    parser.add_argument("--signing-key", type=Path, required=True, help="Local Ed25519 signing key outside the source tree")
    parser.add_argument("--windows-installer", type=Path, help="Optional verified Windows x64 installer for this version")
    parser.add_argument("--openssl", default="openssl", help="OpenSSL 3 executable")
    args = parser.parse_args()

    bundle = args.app.resolve(strict=True)
    signer = args.signing_key.expanduser().resolve(strict=True)
    if signer.is_relative_to(ROOT) or signer.stat().st_uid != os.getuid() or signer.stat().st_mode & 0o077:
        parser.error("Use an Ed25519 key owned by this user, outside the source tree, with mode 600.")
    trusted_key = base64.b64decode((ROOT / "macos/UpdatePublicKey.txt").read_text().strip(), validate=True)
    public_der = command(args.openssl, "pkey", "-in", signer, "-pubout", "-outform", "DER",
                         capture_output=True).stdout
    if len(trusted_key) != 32 or public_der != ED25519_PREFIX + trusted_key:
        parser.error("The release key does not match macos/UpdatePublicKey.txt.")
    args.signing_key = signer
    version = bundle_version(bundle, trusted_key)
    output = args.output.resolve()

    with tempfile.TemporaryDirectory() as scratch:
        folder = Path(scratch)
        archive = folder / ZIP_NAME
        manifest = folder / MANIFEST_NAME
        signature = folder / SIGNATURE_NAME
        public_key = folder / "public.der"
        command("/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", bundle, archive)
        verify_archive(archive)
        public_key.write_bytes(public_der)
        sign_asset(args, archive, version, manifest, signature, public_key)
        assets = [archive, manifest, signature]
        if args.windows_installer is not None:
            installer = args.windows_installer.resolve(strict=True)
            with installer.open("rb") as source:
                valid = source.read(2) == b"MZ"
            if installer.name != WINDOWS_NAME or not valid or installer.stat().st_size > 512 * 1024 * 1024:
                parser.error(f"Expected {WINDOWS_NAME}, a Windows installer up to 512 MiB.")
            win_manifest = folder / WINDOWS_MANIFEST
            win_signature = folder / WINDOWS_SIGNATURE
            sign_asset(args, installer, version, win_manifest, win_signature, public_key, platform="windows-x64")
            assets.extend((installer, win_manifest, win_signature))
        output.mkdir(parents=True, exist_ok=False)
        for item in assets:
            shutil.copyfile(item, output / item.name)
    print(f"Prepared v{version} at {output}.")


if __name__ == "__main__":
    main()
