#!/bin/bash
# Build an iOS XCFramework from the Anki C-ABI bridge crate (rslib core).
# Adapted from AMGI (github.com/antigluten/amgi/scripts/build-xcframework.sh).
#
# Usage:
#   ./build-xcframework.sh            # build sim + device, package xcframework
#   SIM_ONLY=1 ./build-xcframework.sh # build simulator target only (faster proof)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BRIDGE_ROOT="$(dirname "$SCRIPT_DIR")"             # .../speedrun/bridge
CRATE_DIR="$BRIDGE_ROOT/anki-bridge-rs"
HEADER_DIR="$CRATE_DIR/include"
OUTPUT_DIR="$BRIDGE_ROOT/AnkiRust.xcframework"
LIB_NAME="libanki_bridge.a"                        # [lib] name = "anki_bridge"

# Toolchain pinned to match upstream rslib (rust-toolchain.toml: 1.92.0).
TOOLCHAIN="1.92.0-aarch64-apple-darwin"

export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:$PATH"
export PROTOC="${PROTOC:-$(command -v protoc || echo /opt/homebrew/bin/protoc)}"
export IPHONEOS_DEPLOYMENT_TARGET="17.0"

echo "==> protoc:            $PROTOC ($("$PROTOC" --version))"
echo "==> toolchain:         $TOOLCHAIN"
echo "==> deployment target: iOS $IPHONEOS_DEPLOYMENT_TARGET"
echo "==> crate:             $CRATE_DIR"

build_target() {
    local target="$1"
    echo "==> Building for $target ..."
    cargo "+$TOOLCHAIN" build \
        --manifest-path "$CRATE_DIR/Cargo.toml" \
        --target "$target" \
        --release
}

# 1. Regenerate the C header with cbindgen (header drives the Swift module map).
echo "==> Generating C header via cbindgen ..."
cbindgen --config "$CRATE_DIR/cbindgen.toml" \
         --crate anki-bridge-ios \
         --output "$HEADER_DIR/anki_bridge.h" \
         "$CRATE_DIR"

# 2. Cross-compile. NOTE: cargo locks target/, so targets are built sequentially.
SIM_ONLY="${SIM_ONLY:-0}"
build_target "aarch64-apple-ios-sim"
SIM_LIB="$CRATE_DIR/target/aarch64-apple-ios-sim/release/$LIB_NAME"
[ -f "$SIM_LIB" ] || { echo "ERROR: simulator lib not found at $SIM_LIB"; exit 1; }
echo "==> Simulator lib: $(du -h "$SIM_LIB" | cut -f1)  $SIM_LIB"

XCARGS=(-library "$SIM_LIB" -headers "$HEADER_DIR")

if [ "$SIM_ONLY" != "1" ]; then
    build_target "aarch64-apple-ios"
    DEVICE_LIB="$CRATE_DIR/target/aarch64-apple-ios/release/$LIB_NAME"
    [ -f "$DEVICE_LIB" ] || { echo "ERROR: device lib not found at $DEVICE_LIB"; exit 1; }
    echo "==> Device lib:    $(du -h "$DEVICE_LIB" | cut -f1)  $DEVICE_LIB"
    XCARGS+=(-library "$DEVICE_LIB" -headers "$HEADER_DIR")
fi

# 3. Package the XCFramework.
echo "==> Packaging XCFramework ..."
rm -rf "$OUTPUT_DIR"
xcodebuild -create-xcframework "${XCARGS[@]}" -output "$OUTPUT_DIR"

# 4. Add a module map to each slice so Swift can `import AnkiRustLib`.
echo "==> Adding module maps ..."
for HEADERS in "$OUTPUT_DIR"/*/Headers; do
    cat > "$HEADERS/module.modulemap" <<'MODULEMAP'
module AnkiRustLib {
    header "anki_bridge.h"
    export *
}
MODULEMAP
done

echo "==> Done. XCFramework at: $OUTPUT_DIR"
find "$OUTPUT_DIR" -type f | sort
