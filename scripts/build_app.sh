#!/usr/bin/env bash
# Build dist/Rocky.app without py2app: a thin bundle whose launcher runs `python -m rocky.app` from this
# checkout. The icon is the watermelon emoji rendered by rocky.app.emoji_bitmap and packed with iconutil.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/dist/Rocky.app"
VERSION="$(cd "$ROOT" && uv run python -c 'import rocky; print(rocky.__version__)')"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Rocky</string>
  <key>CFBundleDisplayName</key><string>Rocky</string>
  <key>CFBundleIdentifier</key><string>com.yousefqasim.rocky</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>rocky</string>
  <key>CFBundleIconFile</key><string>Rocky</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Rocky listens for the wake word watermelon.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>Rocky turns what you say into commands on this Mac.</string>
  <key>NSAppleEventsUsageDescription</key><string>Rocky controls apps on your behalf when you ask it to.</string>
</dict>
</plist>
PLIST

# The launcher: ROCKY_HOME is baked in at build time. Finder starts apps with a bare PATH, so uv is looked
# up in the usual install locations before falling back to the checkout's own .venv.
cat > "$APP/Contents/MacOS/rocky" <<LAUNCHER
#!/bin/bash
ROCKY_HOME="$ROOT"
export PATH="\$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH"
cd "\$ROCKY_HOME" || exit 1
ENV_FILE=()
[ -f .env ] && ENV_FILE=(--env-file .env)
if command -v uv >/dev/null 2>&1; then
  exec uv run "\${ENV_FILE[@]}" python -m rocky.app
fi
exec .venv/bin/python -m rocky.app
LAUNCHER
chmod +x "$APP/Contents/MacOS/rocky"

ICONSET="$ROOT/dist/Rocky.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
(cd "$ROOT" && uv run python - "$ICONSET" <<'PY'
import sys
from pathlib import Path

from rocky.app import emoji_bitmap, write_png

out = Path(sys.argv[1])
for base in (16, 32, 128, 256, 512):
    write_png(emoji_bitmap(base), out / f"icon_{base}x{base}.png")
    write_png(emoji_bitmap(base * 2), out / f"icon_{base}x{base}@2x.png")
PY
)
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Rocky.icns"
rm -rf "$ICONSET"

echo "$APP"
open -R "$APP"
