#!/bin/sh
# Builds the plugin for this machine and zips it the way OpenDeck installs it.
set -eu
cd "$(dirname "$0")"
ID=org.tessera-deck.opendeck.sdPlugin
case "$(uname -s)" in
    Linux) OUT=opendeck-tessera-linux; BIN=opendeck-tessera ;;
    Darwin) OUT=opendeck-tessera-mac; BIN=opendeck-tessera ;;
    *) OUT=opendeck-tessera-win.exe; BIN=opendeck-tessera.exe ;;
esac
cargo build --release
rm -rf "build/$ID"
mkdir -p "build/$ID"
cp -r assets manifest.json "build/$ID/"
cp "target/release/$BIN" "build/$ID/$OUT"
(cd build && rm -f opendeck-tessera.plugin.zip && zip -qr opendeck-tessera.plugin.zip "$ID")
echo "wrote $(pwd)/build/opendeck-tessera.plugin.zip"
