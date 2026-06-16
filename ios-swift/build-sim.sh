#!/bin/bash
set -euo pipefail

SIM_UDID="${SIM_UDID:-6B43D628-B5FA-436B-9DEF-6137E2F95936}"
SCHEME="BricksDispatch"
BUNDLE_ID="com.bricks.go"

cd "$(dirname "$0")"

xcodegen generate -q 2>/dev/null || xcodegen generate

xcodebuild \
  -project "BricksDispatch.xcodeproj" \
  -scheme "$SCHEME" \
  -destination "id=$SIM_UDID" \
  -configuration Debug \
  build

APP_PATH="$(find /Users/akasm/Library/Developer/Xcode/DerivedData -path "*/Build/Products/Debug-iphonesimulator/${SCHEME}.app" -type d | head -1)"
if [ -z "$APP_PATH" ]; then
  echo "App not found after build"
  exit 1
fi

xcrun simctl install "$SIM_UDID" "$APP_PATH"
xcrun simctl launch --terminate-running-process "$SIM_UDID" "$BUNDLE_ID"
echo "✓ $SCHEME installed and launched on simulator"
