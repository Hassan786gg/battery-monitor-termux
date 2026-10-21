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
curl -fsSL https://hassan786gg.github.io/battery-monitor-termux/install.sh | bash
```

This installs Python and the Termux:API command-line package, downloads `battery-monitor`, and starts it immediately. No Git or repository clone is needed. Curl is included in standard Termux installations; if yours lacks it, run `pkg install curl` first.

The command downloads and runs the public [installer](https://github.com/Hassan786gg/battery-monitor-termux/blob/main/install.sh). Review it first if you prefer. You can safely repeat the same command to reinstall or update.

## Run again

```sh
battery-monitor
```

The dashboard refreshes every two seconds. Press `Ctrl+C` to stop.

## Update

```sh
curl -fsSL https://hassan786gg.github.io/battery-monitor-termux/install.sh | bash
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
