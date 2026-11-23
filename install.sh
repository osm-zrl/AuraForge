#!/usr/bin/env bash
# AuraForge installer for Linux (Fedora, Ubuntu/Debian, Arch, openSUSE).
#
#   1. installs build + runtime dependencies (dnf, apt, pacman or zypper)
#   2. clones, builds and installs rogauracore (github.com/wroberts/rogauracore)
#   3. writes /etc/udev/rules.d/99-rogauracore.rules so no sudo is needed
#   4. installs the GTK4/Libadwaita app, launcher, .desktop entry and icon
#
# Usage: ./install.sh [--skip-deps] [--skip-build] [--rebuild] [--no-udev] [-y]
#        Run as your normal user; the script calls sudo where needed.

set -Eeuo pipefail

APP_ID="io.github.osm_zrl.AuraForge"
REPO_URL="https://github.com/wroberts/rogauracore.git"
PREFIX="/usr/local"
APP_DIR="/opt/auraforge"
BIN_LINK="$PREFIX/bin/auraforge"
DESKTOP_DIR="$PREFIX/share/applications"
ICON_ROOT="$PREFIX/share/icons/hicolor"
UDEV_RULE="/etc/udev/rules.d/99-rogauracore.rules"
ASUS_VID="0b05"
DEFAULT_PIDS=(1854 1866 1869 19b6 1a30)

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_DEPS=0 SKIP_BUILD=0 REBUILD=0 NO_UDEV=0 ASSUME_YES=0

# ---------------------------------------------------------------- helpers --
if [[ -t 1 ]]; then
    B=$'\e[1m' R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' C=$'\e[36m' N=$'\e[0m'
else
    B="" R="" G="" Y="" C="" N=""
fi
step()  { printf '\n%s==>%s %s%s%s\n' "$C" "$N" "$B" "$*" "$N"; }
info()  { printf '    %s\n' "$*"; }
ok()    { printf '    %s✔%s %s\n' "$G" "$N" "$*"; }
warn()  { printf '    %s!%s %s\n' "$Y" "$N" "$*" >&2; }
die()   { printf '%sError:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
trap 'die "command failed at line $LINENO: $BASH_COMMAND"' ERR

usage() {
    sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<EOF

Options:
  --skip-deps   do not install packages (install them yourself first)
  --skip-build  do not build rogauracore (use the one already installed)
  --rebuild     rebuild rogauracore even if it is already installed
  --no-udev     do not write the udev rule
  -y, --yes     do not ask for confirmation
  -h, --help    show this help
EOF
}

for arg in "$@"; do
    case "$arg" in
        --skip-deps)  SKIP_DEPS=1 ;;
        --skip-build) SKIP_BUILD=1 ;;
        --rebuild)    REBUILD=1 ;;
        --no-udev)    NO_UDEV=1 ;;
        -y|--yes)     ASSUME_YES=1 ;;
        -h|--help)    usage; exit 0 ;;
        *) usage; die "unknown option: $arg" ;;
    esac
done

if [[ $EUID -eq 0 ]]; then
    SUDO=()
    [[ -n "${SUDO_USER:-}" ]] || warn "Running as root: per-user settings will belong to root."
else
    command -v sudo >/dev/null || die "sudo is required."
    SUDO=(sudo)
fi

# ----------------------------------------------------------- sanity checks --
step "Checking system"
OS_ID="" OS_LIKE="" OS_NAME="unknown"
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-}" OS_LIKE="${ID_LIKE:-}" OS_NAME="${PRETTY_NAME:-${NAME:-unknown}}"
fi

# Pick the package manager from os-release (ID, then ID_LIKE), falling back to what exists.
detect_pm() {
    local id
    for id in $OS_ID $OS_LIKE; do
        case "$id" in
            fedora|rhel|centos|rocky|almalinux|nobara|ultramarine) echo dnf; return ;;
            debian|ubuntu|linuxmint|pop|elementary|zorin|neon)      echo apt; return ;;
            arch|manjaro|endeavouros|garuda|cachyos|artix)           echo pacman; return ;;
            opensuse*|suse|sles|sled)                                 echo zypper; return ;;
        esac
    done
    local pm
    for pm in dnf apt-get pacman zypper; do
        command -v "$pm" >/dev/null 2>&1 && { [[ $pm == apt-get ]] && echo apt || echo "$pm"; return; }
    done
    echo unknown
}
PM="$(detect_pm)"
ok "Detected $OS_NAME (package manager: $PM)"
[[ -f "$SRC_DIR/auraforge/application.py" ]] || die "Run this script from the project directory."
if [[ $SKIP_DEPS -eq 0 && $PM == unknown ]]; then
    die "Unsupported distribution. Install the dependencies listed in README.md, then run: ./install.sh --skip-deps"
fi

if [[ $ASSUME_YES -eq 0 && -t 0 ]]; then
    if [[ $SKIP_DEPS -eq 0 ]]; then
        info "This will install packages with $PM, build rogauracore into $PREFIX,"
    else
        info "This will build rogauracore into $PREFIX,"
    fi
    info "write $UDEV_RULE and install the app into $APP_DIR."
    read -r -p "    Continue? [Y/n] " reply
    [[ -z "$reply" || "$reply" =~ ^[Yy] ]] || { info "Aborted."; exit 0; }
fi

# Ask for the sudo password once, up front.
[[ ${#SUDO[@]} -eq 0 ]] || sudo -v

# ------------------------------------------------------------ dependencies --
# Each list: rogauracore build deps | GUI runtime | installer/diagnostic tools.
PKGS_DNF=(gcc gcc-c++ make autoconf automake libusb1-devel git
          python3 python3-gobject python3-cairo gtk4 libadwaita
          usbutils polkit desktop-file-utils)
PKGS_APT=(build-essential autoconf automake libusb-1.0-0-dev git
          python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1
          usbutils desktop-file-utils)
PKGS_PACMAN=(base-devel autoconf automake libusb git
             python python-gobject python-cairo gtk4 libadwaita
             usbutils polkit desktop-file-utils)
PKGS_ZYPPER=(gcc gcc-c++ make autoconf automake libusb-1_0-devel git
             python3 python3-gobject python3-gobject-cairo typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1
             usbutils polkit desktop-file-utils)

install_deps() {
    case "$PM" in
        dnf)
            "${SUDO[@]}" dnf install -y "${PKGS_DNF[@]}" ;;
        apt)
            "${SUDO[@]}" apt-get update
            # pkexec was split out of policykit-1 in Ubuntu 24.04 / Debian 13.
            local polkit_pkg=policykit-1
            apt-cache show pkexec >/dev/null 2>&1 && polkit_pkg=pkexec
            "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y "${PKGS_APT[@]}" "$polkit_pkg" ;;
        pacman)
            "${SUDO[@]}" pacman -S --needed --noconfirm "${PKGS_PACMAN[@]}" ;;
        zypper)
            "${SUDO[@]}" zypper --non-interactive install "${PKGS_ZYPPER[@]}" ;;
    esac
}

if [[ $SKIP_DEPS -eq 0 ]]; then
    step "Installing dependencies with $PM"
    install_deps
    ok "Dependencies installed"
else
    info "Skipping package installation (--skip-deps)"
fi

# AuraForge needs GTK >= 4.10 (Gtk.ColorDialogButton) and libadwaita >= 1.5 (Adw.AlertDialog).
step "Checking GTK 4 / libadwaita"
if ! toolkit_report="$(python3 - <<'PY' 2>&1
import sys
try:
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw
except Exception as exc:
    sys.exit(f"PyGObject with GTK 4 + libadwaita is not available: {exc}")
gtk = (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
adw = (Adw.get_major_version(), Adw.get_minor_version(), Adw.get_micro_version())
print("GTK %d.%d.%d, libadwaita %d.%d.%d" % (gtk + adw))
if gtk < (4, 10, 0) or adw < (1, 5, 0):
    sys.exit("too old: AuraForge needs GTK 4.10+ and libadwaita 1.5+")
PY
)"; then
    die "$toolkit_report
       Your distribution's GTK/libadwaita is too old or missing. Supported: Fedora 40+,
       Ubuntu 24.04+, Debian 13+, Arch, openSUSE Tumbleweed."
fi
ok "$toolkit_report"

# ------------------------------------------------------------- rogauracore --
find_rogauracore() {
    command -v rogauracore 2>/dev/null || { [[ -x "$PREFIX/bin/rogauracore" ]] && echo "$PREFIX/bin/rogauracore"; } || true
}

step "rogauracore"
EXISTING="$(find_rogauracore)"
if [[ $SKIP_BUILD -eq 1 ]]; then
    info "Skipping build (--skip-build)"
elif [[ -n "$EXISTING" && $REBUILD -eq 0 ]]; then
    ok "Already installed at $EXISTING (use --rebuild to rebuild)"
else
    BUILD_DIR="$(mktemp -d -t rogauracore-build.XXXXXX)"
    trap 'rm -rf "$BUILD_DIR"' EXIT
    info "Cloning $REPO_URL"
    git clone --depth 1 "$REPO_URL" "$BUILD_DIR/rogauracore"
    (
        cd "$BUILD_DIR/rogauracore"
        info "autoreconf -i"
        autoreconf -i
        info "./configure"
        ./configure --prefix="$PREFIX"
        info "make"
        make -j"$(nproc)"
        info "sudo make install"
        "${SUDO[@]}" make install
    )
    ok "rogauracore installed to $PREFIX/bin/rogauracore"
fi
[[ -n "$(find_rogauracore)" ]] || warn "rogauracore is not on PATH; the app will show an error until it is installed."

# --------------------------------------------------------------- udev rule --
if [[ $NO_UDEV -eq 0 ]]; then
    step "Configuring udev rule ($UDEV_RULE)"
    declare -A SEEN=()
    PIDS=()
    for pid in "${DEFAULT_PIDS[@]}"; do SEEN[$pid]=1; PIDS+=("$pid"); done

    # Add any other ASUS keyboard-like device found by lsusb (e.g. newer N-KEY PIDs).
    if command -v lsusb >/dev/null; then
        while IFS= read -r line; do
            pid="$(sed -nE "s/.*ID ${ASUS_VID}:([0-9a-fA-F]{4}).*/\1/p" <<<"$line" | tr 'A-F' 'a-f')"
            [[ -n "$pid" ]] || continue
            info "Found: ${line#*ID }"
            if [[ -z "${SEEN[$pid]:-}" ]] && grep -qiE 'n-key|keyboard|aura|kbd|rog' <<<"$line"; then
                SEEN[$pid]=1; PIDS+=("$pid")
                info "  -> adding extra keyboard PID $pid"
            fi
        done < <(lsusb | grep -i "ID ${ASUS_VID}:" || true)
    else
        warn "lsusb not found; using the default product ID list only"
    fi

    TMP_RULE="$(mktemp)"
    {
        echo "# Generated by AuraForge install.sh on $(date -I)"
        echo "# Gives the logged-in (and any local) user read/write access to ASUS ROG Aura"
        echo "# keyboard USB devices so rogauracore runs without sudo."
        for pid in "${PIDS[@]}"; do
            printf 'ACTION!="remove", SUBSYSTEM=="usb", ATTRS{idVendor}=="%s", ATTRS{idProduct}=="%s", MODE="0666", TAG+="uaccess"\n' \
                "$ASUS_VID" "$pid"
        done
    } >"$TMP_RULE"
    "${SUDO[@]}" install -D -m 0644 "$TMP_RULE" "$UDEV_RULE"
    rm -f "$TMP_RULE"
    ok "Wrote rule for PIDs: ${PIDS[*]}"

    "${SUDO[@]}" udevadm control --reload-rules
    "${SUDO[@]}" udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor="$ASUS_VID" || true
    "${SUDO[@]}" udevadm settle || true

    # Verify that a supported keyboard node is now writable by this user.
    access_ok=0 found=0
    for dev in /sys/bus/usb/devices/*; do
        [[ "$(cat "$dev/idVendor" 2>/dev/null)" == "$ASUS_VID" ]] || continue
        pid="$(cat "$dev/idProduct" 2>/dev/null)"
        [[ -n "${SEEN[$pid]:-}" ]] || continue
        found=1
        busnum="$(cat "$dev/busnum")" devnum="$(cat "$dev/devnum")"
        node="$(printf '/dev/bus/usb/%03d/%03d' "$((10#$busnum))" "$((10#$devnum))")"
        if [[ -r "$node" && -w "$node" ]]; then
            ok "$ASUS_VID:$pid at $node is accessible without sudo"
            access_ok=1
        fi
    done
    if [[ $found -eq 0 ]]; then
        warn "No supported ASUS Aura keyboard detected right now."
    elif [[ $access_ok -eq 0 ]]; then
        warn "Keyboard found but not yet writable - reboot if it stays that way."
    fi
else
    info "Skipping udev rule (--no-udev)"
fi

# ---------------------------------------------------------------- the app --
step "Installing AuraForge"
# Remove the previous install under the app's old name ("ROG Aura Control").
LEGACY_ID="io.github.rogauracontrol.RogAuraControl"
"${SUDO[@]}" rm -rf /opt/rog-aura-control
"${SUDO[@]}" rm -f "$PREFIX/bin/rog-aura-control" "$DESKTOP_DIR/$LEGACY_ID.desktop" \
    "$ICON_ROOT/scalable/apps/$LEGACY_ID.svg" "$ICON_ROOT"/*/apps/"$LEGACY_ID".png
# Move a "restore on login" entry from the old name to the new one.
TARGET_HOME="$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)"
OLD_AUTOSTART="${TARGET_HOME:-$HOME}/.config/autostart/$LEGACY_ID.restore.desktop"
if [[ -f "$OLD_AUTOSTART" ]]; then
    sed -e "s/$LEGACY_ID/$APP_ID/g" -e 's/rog-aura-control/auraforge/g' -e 's/ROG Aura Control/AuraForge/g' \
        "$OLD_AUTOSTART" >"${OLD_AUTOSTART%/*}/$APP_ID.restore.desktop"
    rm -f "$OLD_AUTOSTART"
    ok "Moved the login restore entry to the new name"
fi
"${SUDO[@]}" rm -rf "$APP_DIR"
"${SUDO[@]}" install -d -m 0755 "$APP_DIR/auraforge" "$APP_DIR/bin"
"${SUDO[@]}" install -m 0644 "$SRC_DIR"/auraforge/*.py "$APP_DIR/auraforge/"
"${SUDO[@]}" install -m 0755 "$SRC_DIR/bin/auraforge" "$APP_DIR/bin/auraforge"
"${SUDO[@]}" python3 -m compileall -q "$APP_DIR/auraforge" || true
"${SUDO[@]}" install -d "$PREFIX/bin"
"${SUDO[@]}" ln -sfn "$APP_DIR/bin/auraforge" "$BIN_LINK"
ok "Launcher: $BIN_LINK"

"${SUDO[@]}" install -D -m 0644 "$SRC_DIR/data/$APP_ID.desktop" "$DESKTOP_DIR/$APP_ID.desktop"
# Remove the old scalable icon from earlier versions so it can't win over the PNGs.
"${SUDO[@]}" rm -f "$ICON_ROOT/scalable/apps/$APP_ID.svg"
for png in "$SRC_DIR"/data/icons/hicolor/*/apps/"$APP_ID".png; do
    size_dir="$(basename "$(dirname "$(dirname "$png")")")"
    "${SUDO[@]}" install -D -m 0644 "$png" "$ICON_ROOT/$size_dir/apps/$APP_ID.png"
done
if command -v desktop-file-validate >/dev/null; then
    desktop-file-validate "$SRC_DIR/data/$APP_ID.desktop" || warn "desktop-file-validate reported issues"
fi
command -v update-desktop-database >/dev/null && "${SUDO[@]}" update-desktop-database -q "$DESKTOP_DIR" || true
command -v gtk-update-icon-cache >/dev/null && \
    "${SUDO[@]}" gtk-update-icon-cache -q -f -t "$ICON_ROOT" 2>/dev/null || true
ok "Desktop entry and icon installed"

# --------------------------------------------------------------- warnings --
if systemctl is-active --quiet asusd.service 2>/dev/null; then
    warn "asusd (asusctl) is running and also controls the keyboard LEDs."
    warn "It may override rogauracore. Consider: sudo systemctl disable --now asusd"
fi

step "Done"
info "Launch “AuraForge” from your app menu, or run: auraforge"
info "Quick test from a terminal:  rogauracore single_static ff0000"
info "If Fn brightness keys stop working: use “Fix Brightness Keys” in the app."
