# Live Battery Monitor for Termux

A small Python dashboard that reads live Android battery telemetry through Termux:API. It needs no root access.

## Requirements

Install **Termux** and the **Termux:API Android companion app** from the same source before running the command below. F-Droid builds are available here:

- [Termux](https://f-droid.org/packages/com.termux/)
- [Termux:API](https://f-droid.org/packages/com.termux.api/)

The installer installs the Termux:API command-line package, but it cannot install the Android companion app for you.

## Install and run with one command

Open Termux and paste this entire line:

```sh
pkg install -y git && git clone https://github.com/Hassan786gg/battery-monitor-termux.git "$HOME/battery-monitor-termux" && bash "$HOME/battery-monitor-termux/install.sh" && battery-monitor
```

No manual script download or copying is needed. This installs Git, downloads this repository, installs Python and the Termux:API command-line package, installs `battery-monitor`, and starts it immediately. Review the source before running if you prefer.

The clone command is for first-time installation. If `$HOME/battery-monitor-termux` already exists from a previous installation, run `battery-monitor` instead, or follow the update instructions below.

## Run again

```sh
battery-monitor
```

The dashboard refreshes every two seconds. Press `Ctrl+C` to stop.

## Update

```sh
cd "$HOME/battery-monitor-termux" && git pull --ff-only && bash install.sh && battery-monitor
```

## What it shows

Charge percentage, charging state, Android's system health flag, temperature, voltage, current, charge-counter and cycle-count fields when the phone reports them. Some fields are unavailable on some devices.

Android's standard battery API does not provide a dependable battery-wear or remaining-capacity percentage. The `GOOD` health flag is the system-reported status, not a precise measure of battery degradation.

## Troubleshooting

If the battery API times out, check that the Termux:API Android companion app is installed from the same source as Termux. Follow any Android permission prompts. Installing only `pkg install termux-api` is not enough: the Android companion app is also required.

## Uninstall

```sh
rm "$PREFIX/bin/battery-monitor"
```

Optionally remove the downloaded repository too:

```sh
rm -rf "$HOME/battery-monitor-termux"
```
