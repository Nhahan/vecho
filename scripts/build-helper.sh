#!/bin/sh
# Rebuilds the prebuilt macOS programs shipped with vecho, so people without the Xcode
# command line tools can use it: the system audio helper and the vecho.app launcher.
# Run after changing system_audio.swift or launcher.swift.
set -eu
cd "$(dirname "$0")/../src/vecho/resources"
out=bin
mkdir -p "$out"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
build() {  # build <source.swift> <output name> <minimum macOS>
    for arch in arm64 x86_64; do
        swiftc -O -target "$arch-apple-macos$3" "$1" -o "$tmp/$arch"
    done
    lipo -create "$tmp/arm64" "$tmp/x86_64" -output "$out/$2"
    codesign --force --sign - "$out/$2"
    shasum -a 256 "$1" | cut -c1-12 > "$out/$2.digest"
    echo "built $out/$2 ($(cat "$out/$2.digest"))"
}
build system_audio.swift system-audio 14.2
build launcher.swift launcher 13.0
