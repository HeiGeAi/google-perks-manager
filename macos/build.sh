#!/bin/bash
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
build="$repo/build/macos"
app="$repo/dist/谷歌羊毛管理器.app"

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    echo "目前支持在 Apple 芯片 Mac 上构建。" >&2
    exit 1
fi
# The unversioned SDK link is paired with the active toolchain even when preview SDKs coexist.
sdk_root="$(dirname "$(xcrun --sdk macosx --show-sdk-path)")/MacOSX.sdk"
[[ -d "$sdk_root" ]] || sdk_root="$(xcrun --sdk macosx --show-sdk-path)"
command -v uv >/dev/null || { echo "构建需要 uv。" >&2; exit 1; }
mkdir -p "$build" "$repo/dist"
if [[ ! -x "$build/venv/bin/python" ]]; then
    uv venv --python 3.13.12 "$build/venv"
fi
uv pip sync --python "$build/venv/bin/python" macos/requirements.txt

stage="$(mktemp -d "$build/package.XXXXXXXX")"
trap 'rm -rf "$stage"' EXIT
# Personal contact assets live in the local data directory, outside the public bundle.
mkdir "$stage/web"
/usr/bin/rsync -a --exclude '/brand/wechat-contact.png' --exclude '.DS_Store' "$repo/web/" "$stage/web/"
"$build/venv/bin/python" -m PyInstaller --noconfirm --log-level WARN --onedir \
    --name GoogleManagerService --paths "$repo" \
    --specpath "$build" --workpath "$build/freeze" --distpath "$build/payload" \
    --hidden-import google_manager.colab_worker \
    --add-data "$repo/benefits.json:." --add-data "$stage/web:web" \
    --add-data "$repo/recipes/run_job.py:recipes" --add-data "$repo/recipes/wan22.json:recipes" \
    --add-data "$repo/recipes/breeze.json:recipes" \
    --exclude-module pyarrow --exclude-module IPython --exclude-module tkinter \
    macos/service_entry.py

# Assemble separately so a failed build cannot damage the previous app.
bundle="$stage/谷歌羊毛管理器.app"
contents="$bundle/Contents"
resources="$contents/Resources"
mkdir -p "$contents/MacOS" "$resources"
cp macos/Info.plist "$contents/Info.plist"
update_public_key="${GOOGLE_PERKS_UPDATE_PUBLIC_KEY:-}"
if [[ -z "$update_public_key" && -f macos/UpdatePublicKey.txt ]]; then
    update_public_key="$(tr -d '[:space:]' < macos/UpdatePublicKey.txt)"
fi
if [[ -n "$update_public_key" ]]; then
    "$build/venv/bin/python" -c 'import base64, sys; assert len(base64.b64decode(sys.argv[1], validate=True)) == 32' "$update_public_key"
    /usr/bin/plutil -insert GooglePerksUpdatePublicKey -string "$update_public_key" "$contents/Info.plist"
fi
for locale in en zh-Hans; do
    ditto "macos/$locale.lproj" "$resources/$locale.lproj"
done
ditto "$build/payload/GoogleManagerService" "$resources/backend"
"$build/venv/bin/python" macos/notices.py "$resources/Notices"
cp LICENSE "$resources/Notices/GooglePerksManager-LICENSE.txt"
xcrun swift -sdk "$sdk_root" macos/icon.swift "$build/AppIcon.iconset" "$repo/macos/AppIcon.png"
iconutil -c icns "$build/AppIcon.iconset" -o "$resources/AppIcon.icns"
xcrun swiftc -sdk "$sdk_root" -O -target arm64-apple-macos14.0 macos/App.swift macos/ReleaseUpdater.swift -o "$contents/MacOS/GoogleManager"
xcrun swiftc -sdk "$sdk_root" -parse-as-library -O -target arm64-apple-macos14.0 macos/Media.swift -o "$resources/GoogleManagerMedia"
codesign --force --sign - --timestamp=none "$bundle"
codesign --verify --deep --strict "$bundle"

# Loading the actual bundled client must reach its local token check before any network call.
worker_result="$(env -i PATH=/usr/bin:/bin "$resources/backend/GoogleManagerService" --colab-worker <<<'{}')"
"$build/venv/bin/python" -c 'import json, sys; assert json.load(sys.stdin).get("status") == 401' <<< "$worker_result"
if [[ -e "$app" ]]; then
    [[ -d "$app" ]] || { echo "目标路径被其他文件占用。" >&2; exit 1; }
    [[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist")" == com.heige.google-manager ]] || {
        echo "目标路径的应用标识与本项目不符。" >&2
        exit 1
    }
    mv "$app" "$stage/previous.app"
fi
if ! mv "$bundle" "$app"; then
    if [[ -d "$stage/previous.app" ]]; then mv "$stage/previous.app" "$app"; fi
    exit 1
fi
echo "$app"
