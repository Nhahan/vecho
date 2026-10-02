#!/bin/sh
# Rebuilds the prebuilt macOS system audio helper shipped with vecho, so people without the
# Xcode command line tools can record system audio. Run after changing system_audio.swift.
set -eu
cd "$(dirname "$0")/../src/vecho/resources"
out=bin
mkdir -p "$out"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for arch in arm64 x86_64; do
    swiftc -O -target "$arch-apple-macos14.2" system_audio.swift -o "$tmp/$arch"
done
lipo -create "$tmp/arm64" "$tmp/x86_64" -output "$out/system-audio"
codesign --force --sign - "$out/system-audio"
shasum -a 256 system_audio.swift | cut -c1-12 > "$out/system-audio.digest"
echo "built $out/system-audio for $(cat "$out/system-audio.digest")"
