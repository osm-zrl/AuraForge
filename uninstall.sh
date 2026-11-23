#!/usr/bin/env bash
# Remove AuraForge.  Usage: ./uninstall.sh [--purge]
#   --purge  also remove rogauracore, the udev rule and your saved profiles
set -Eeuo pipefail

APP_ID="io.github.osm_zrl.AuraForge"
PREFIX="/usr/local"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1
SUDO=(sudo); [[ $EUID -eq 0 ]] && SUDO=()

echo "Removing application files…"
"${SUDO[@]}" rm -rf /opt/auraforge
"${SUDO[@]}" rm -f "$PREFIX/bin/auraforge" \
    "$PREFIX/share/applications/$APP_ID.desktop" \
    "$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg" \
    "$PREFIX"/share/icons/hicolor/*/apps/"$APP_ID".png
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/$APP_ID.restore.desktop"
# Leftovers from the app's old name ("ROG Aura Control")
LEGACY_ID="io.github.rogauracontrol.RogAuraControl"
"${SUDO[@]}" rm -rf /opt/rog-aura-control
"${SUDO[@]}" rm -f "$PREFIX/bin/rog-aura-control" "$PREFIX/share/applications/$LEGACY_ID.desktop" \
    "$PREFIX/share/icons/hicolor/scalable/apps/$LEGACY_ID.svg" \
    "$PREFIX"/share/icons/hicolor/*/apps/"$LEGACY_ID".png
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/$LEGACY_ID.restore.desktop"
command -v update-desktop-database >/dev/null && "${SUDO[@]}" update-desktop-database -q "$PREFIX/share/applications" || true

if [[ $PURGE -eq 1 ]]; then
    echo "Purging rogauracore, udev rules and settings…"
    "${SUDO[@]}" rm -f "$PREFIX/bin/rogauracore" /etc/udev/rules.d/99-rogauracore.rules \
        /lib/udev/rules.d/90-rogauracore.rules
    "${SUDO[@]}" udevadm control --reload-rules || true
    rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/auraforge"
    rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/rog-aura-control"
fi
echo "Done."
