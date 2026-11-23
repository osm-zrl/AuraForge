# AuraForge

A native GTK4 + Libadwaita app for Linux that controls the RGB keyboard lighting on ASUS ROG
laptops. It runs on Fedora, Ubuntu/Debian, Arch and openSUSE, under any desktop (GNOME, KDE,
Cinnamon…) on Wayland or X11. It was built for the **ROG Strix G513QM**, whose keyboard
shows up as USB device `0b05:1866`.

The app doesn't include its own USB code. Every hardware action runs the open-source
[`rogauracore`](https://github.com/wroberts/rogauracore) command-line tool.

## Tested on

| | |
|---|---|
| **Laptop** | ASUS ROG Strix G15 **G513QM** (`ROG Strix G513QM_G513QM`, BIOS `G513QM.331`) |
| **CPU / GPU** | AMD Ryzen 9 5900HX with Radeon Vega graphics + NVIDIA GeForce RTX 3060 Mobile |
| **Keyboard controller** | USB `0b05:1866` (ASUSTek N-KEY Device) |
| **OS** | Fedora Linux 42 (Workstation Edition) |
| **Kernel** | 6.19.14-108.fc42.x86_64 |
| **Desktop** | GNOME Shell 48.8 on Wayland |
| **Python** | 3.13.13, PyGObject 3.50.2, pycairo 1.25.1 |
| **Toolkit** | GTK 4.18.6, libadwaita 1.7.12 |
| **Backend** | rogauracore 1.6, built with GCC 15.2.1 against libusb 1.0.29 |

Other ROG laptops whose keyboard uses one of rogauracore's product IDs
(`1854 1866 1869 19b6 1a30`) should work too, but haven't been tested. Other distributions are
supported by the installer but have only been checked against their package versions, not run.

## Project layout

```
RogColor/
├── install.sh                 # installer: packages (dnf/apt/pacman/zypper), rogauracore, udev rule, app
├── uninstall.sh               # removes the app (--purge also removes rogauracore, rule, settings)
├── bin/auraforge              # launcher (works from the checkout or once installed)
├── data/
│   ├── io.github.osm_zrl.AuraForge.desktop
│   └── icons/hicolor/<size>/apps/io.github.osm_zrl.AuraForge.png   (16–512 px)
└── auraforge/
    ├── __init__.py            # app id, name, version
    ├── __main__.py            # `python3 -m auraforge`
    ├── application.py         # Adw.Application + CLI (--restore, --apply-profile, --list-profiles)
    ├── window.py              # main window (header bar, controls, profiles, diagnostics)
    ├── preview.py             # animated keyboard + light bar preview, colour swatches
    ├── backend.py             # rogauracore wrapper, error handling, background runner, USB checks
    ├── modes.py               # mode list, LightingState, command-line arguments
    ├── storage.py             # profiles.json / state.json / settings.json (atomic writes)
    └── autostart.py           # "restore on login" XDG autostart entry
```

## Supported distributions

AuraForge needs **GTK 4.10+**, **libadwaita 1.5+** and **Python 3.9+** with PyGObject. In
practice the libadwaita version decides which releases work.

| Distribution | Works? | Package manager used by `install.sh` |
|---|---|---|
| Fedora 40+ (also Nobara, Ultramarine) | ✅ tested on Fedora 42 | `dnf` |
| Ubuntu 24.04+, Linux Mint 22+, Pop!_OS 24.04+, Zorin 18+ | ✅ | `apt` |
| Debian 13 (trixie) and newer | ✅ | `apt` |
| Arch, Manjaro, EndeavourOS, CachyOS | ✅ | `pacman` |
| openSUSE Tumbleweed | ✅ | `zypper` |
| Ubuntu 22.04, Debian 12, openSUSE Leap 15 | ❌ libadwaita too old | — |

The installer reads `/etc/os-release` to pick the package manager. After installing packages it
checks the GTK and libadwaita versions and stops with a clear message if they are too old.

Whether your **keyboard** works depends on rogauracore, not on the distribution. See
[Troubleshooting](#troubleshooting).

## Install

```bash
git clone https://github.com/osm-zrl/AuraForge.git
```

```bash
cd AuraForge
```

```bash
./install.sh
```

Run it as your normal user. It asks for your sudo password once, then:

1. Installs the build tools and runtime packages with your distribution's package manager
   (listed below).
2. Clones `rogauracore`, runs `autoreconf -i && ./configure && make && sudo make install`, and
   installs it to `/usr/local/bin/rogauracore`.
3. Writes `/etc/udev/rules.d/99-rogauracore.rules` for the ASUS Aura product IDs
   (`1854 1866 1869 19b6 1a30`, plus any ASUS keyboard `lsusb` finds), reloads udev and checks
   that the keyboard can be opened without sudo.
4. Installs the app to `/opt/auraforge`, the `auraforge` launcher in `/usr/local/bin`, and the
   `.desktop` file and icons (16–512 px PNG) in `/usr/local/share`.

### Packages installed

These are the packages `install.sh` installs. On another distribution, install the equivalents
yourself and run `./install.sh --skip-deps`.

Fedora:

```bash
sudo dnf install gcc gcc-c++ make autoconf automake libusb1-devel git python3 python3-gobject python3-cairo gtk4 libadwaita usbutils polkit desktop-file-utils
```

Ubuntu / Debian / Linux Mint / Pop!_OS (on Ubuntu 22.04 or Debian 12, use `policykit-1`
instead of `pkexec`; the GUI won't run there anyway):

```bash
sudo apt install build-essential autoconf automake libusb-1.0-0-dev git python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 usbutils desktop-file-utils pkexec
```

Arch / Manjaro / EndeavourOS:

```bash
sudo pacman -S --needed base-devel autoconf automake libusb git python python-gobject python-cairo gtk4 libadwaita usbutils polkit desktop-file-utils
```

openSUSE Tumbleweed:

```bash
sudo zypper install gcc gcc-c++ make autoconf automake libusb-1_0-devel git python3 python3-gobject python3-gobject-cairo typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 usbutils polkit desktop-file-utils
```

Options: `--skip-deps`, `--skip-build`, `--rebuild` (rebuild rogauracore), `--no-udev`, `-y`.

**Upgrading from "ROG Aura Control"** (the old name): just run `./install.sh` again. It removes
the old `rog-aura-control` install and moves the login-restore entry. Your profiles move from
`~/.config/rog-aura-control` to `~/.config/auraforge` the first time AuraForge starts.

## Launch

- Open **AuraForge** from your app menu (the GNOME app grid, KDE launcher, etc.), or
- run `auraforge` in a terminal.

To try it without installing (rogauracore and the udev rule still have to be installed):

```bash
./bin/auraforge
```

### Command-line use

```bash
auraforge --restore             # re-apply the last state, no window
```

```bash
auraforge --apply-profile "Night"
```

```bash
auraforge --list-profiles
```

## Using the app

<img width="832" height="1088" alt="Screenshot From 2026-11-23 21-22-05" src="https://github.com/user-attachments/assets/54d73d15-ee13-45bf-97ac-34cb27c2fc67" />


| Area | What it does |
|---|---|
| **Initialize** (header) | Runs `rogauracore initialize_keyboard`, then re-applies the current lighting. |
| **Power switch** (header) | Off runs `brightness 0`. On restores the brightness you had before. |
| **Effect** | Static, Breathing (2 colours + speed), Color Cycle (speed), 4-Zone Static, 4-Zone Breathing (speed), Rainbow Wave (`rainbow_cycle`, speed), Rainbow (static zones), and the 9 preset colours. Colour pickers appear only for the effects that use them. |
| **Speed** | Slow, Medium or Fast (1–3). Only enabled for animated effects. |
| **Brightness** | Off, Low, Medium or High. Sent right away with `rogauracore brightness N`. |
| **Apply** / *Apply changes instantly* | Changes are sent automatically after a short pause. Turn instant apply off to send them only when you press Apply (Ctrl+Enter). |
| **Profiles** | Save Current (Ctrl+S). Click a profile to apply it. The ⋮ menu has Overwrite, Rename and Delete. Stored in `~/.config/auraforge/profiles.json`. |
| **Restore last lighting profile on login** | Creates `~/.config/autostart/io.github.osm_zrl.AuraForge.restore.desktop`, which runs `auraforge --restore` at login. |
| **Status bar** | Shows the last command that ran and its exit status. Click the icon to open the details. |
| **Diagnostics** | Shows the rogauracore path, the detected keyboard, USB access, the udev rule, conflicting services (asusd), and the last command with its exit code and output. Also has a command log and the **Fix Brightness Keys** button (`pkexec systemctl restart upower.service`). |

The last state that was applied successfully is saved in `~/.config/auraforge/state.json`.

## Troubleshooting

- **"No permission to access the keyboard"**: run `./install.sh` again, or just its udev step
  with `./install.sh --skip-deps --skip-build`, then reboot.
- **"No supported ROG Aura keyboard"**: run `lsusb | grep 0b05`. rogauracore only knows the
  product IDs `1854 1866 1869 19b6 1a30`.
- **Lighting keeps getting reset**: `asusd` (from asusctl) also controls the LEDs. Stop it with
  `sudo systemctl disable --now asusd`.
- **Fn brightness keys stop working after a change**: this is a known rogauracore/UPower
  issue. Click **Fix Brightness Keys**.
- **Keyboard goes dark after suspend**: click **Initialize**.
- **The light bar doesn't change**: rogauracore sends commands to the keyboard controller.
  On some Strix models the light bar follows the keyboard; on others rogauracore can't control it.
- **Only one colour shows in 4-zone modes**: some G513 models have a single-zone keyboard.

## Uninstall

```bash
./uninstall.sh            # app, launcher, desktop entry, autostart entry
```

```bash
./uninstall.sh --purge    # also rogauracore, the udev rules and saved profiles
```

## Credits

- **Osmz** ([github.com/osm-zrl](https://github.com/osm-zrl)): author and maintainer; design, testing on real hardware.
- **Claude** (Anthropic), via [Claude Code](https://claude.com/claude-code): co-developer of the app code, installer and docs.
- **Will Roberts**: [rogauracore](https://github.com/wroberts/rogauracore), the command-line tool that talks to the keyboard.

## License

[MIT](LICENSE) © 2026 Osmz. rogauracore is a separate project with its own license; install.sh downloads and builds it from its repository.
