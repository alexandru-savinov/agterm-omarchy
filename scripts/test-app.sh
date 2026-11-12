#!/usr/bin/env bash
# Run the application-hosted AppKit unit tests with scheme launch isolation.
set -euo pipefail
CDPATH='' cd -- "$(dirname "$0")/.."

./scripts/setup.sh
xcodegen generate
xcodebuild test \
  -project agterm.xcodeproj \
  -scheme agtermTests \
  -destination 'platform=macOS' \
  -derivedDataPath build/DerivedData
