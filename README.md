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
curl -fsSL https://tinyurl.com/2bzlqrpb | bash
```

This installs Python and the Termux:API command-line package, downloads `battery-monitor`, and starts it immediately. No Git or repository clone is needed. Curl is included in standard Termux installations; if yours lacks it, run `pkg install curl` first.

The command downloads and runs the public [installer](https://github.com/Hassan786gg/battery-monitor-termux/blob/main/install.sh). Review it first if you prefer. You can safely repeat the same command to reinstall.

## Run again

```sh
battery-monitor
```

The dashboard refreshes every two seconds. Press `Ctrl+C` to stop.

### Responsive terminal layout

The dashboard uses a titled rounded frame, a charge gauge, section dividers, and aligned telemetry rows with restrained status colors. It recalculates its layout on each refresh, so rotating the phone or resizing Termux does not require a restart.

Interactive terminals also show a small decorative activity indicator at up to four frames per second. Only changed rows are repainted between samples to reduce flicker; the indicator does not imply a new measurement or animate the reported charge percentage. Battery readings still refresh every two seconds, and the updated timestamp belongs to the sample, not the animation.

For a static dashboard, use either option:

```sh
battery-monitor --no-animation
BATTERY_MONITOR_NO_ANIMATION=1 battery-monitor
```

Redirected output and `TERM=dumb` do not animate or emit cursor-control sequences. `NO_COLOR=1` disables colors independently; use `--no-animation` as well if you want to disable motion.

On short screens, charge and charging state take priority over secondary readings. The frame stays closed, and a footer reports hidden rows and suggests resizing. Extremely small terminals use a borderless summary. Long telemetry values are clipped with `…` instead of wrapping and pushing the bottom of the frame off-screen; wide characters and combining marks are measured in terminal cells. One column and one row are reserved to avoid terminal autowrap and scrolling.

Increase the terminal height (or reduce Termux's font size) to see more readings. Output redirected to a file keeps all telemetry rows rather than applying the interactive height limit. `NO_COLOR=1` disables color without disabling the responsive layout.

## Update

```sh
battery-monitor update
```

This downloads and validates the latest monitor, replaces the installed command only after validation, and exits without opening the dashboard. A failed download leaves the existing installation unchanged. Then run `battery-monitor` to start it.

**Already installed an older version?** Press `Ctrl+C` and run the install command once more to get support for `battery-monitor update`. Older versions do not understand the new command.

Use `battery-monitor --version` to check the installed version, or `battery-monitor --help` for command help.

## What it shows

Charge percentage, charging state, Android's system health flag, temperature, voltage, current, estimated battery-side power in watts, charge-counter and cycle-count fields when the phone reports them. Some fields are unavailable on some devices.

Android's standard battery API does not provide a dependable battery-wear or remaining-capacity percentage. The `GOOD` health flag is the system-reported status, not a precise measure of battery degradation.

## Power in watts

`BATTERY POWER (EST)` shows the **magnitude of net battery-side power**, calculated from the phone's instantaneous battery voltage and current:

```text
watts = |voltage_mV × current_microamps| / 1,000,000,000
```

For the reported example, **4.224 V × 2.472 A = 10.44 W** of estimated battery-side power magnitude. This works while charging or discharging, preserves the signed current in its own row, and does not infer power-flow direction from charging status. If voltage or current is unavailable or invalid, power shows `n/a`. A valid zero-current reading gives `0.00 W`.

**This is not the charger's delivered power, wall-socket consumption, total phone power use, or the phone's maximum supported charging wattage.** Charger input also supplies the phone's running load and conversion losses; battery voltage is not USB/charger voltage. `DEVICE MAX W` remains `n/a (not exposed)` rather than being guessed. The [standard Termux battery-status implementation](https://github.com/termux/termux-api/blob/master/app/src/main/java/com/termux/api/apis/BatteryStatusAPI.java) does not expose charger-input measurements; optional read-only kernel telemetry is used separately below.

Check the manufacturer's specifications for the phone's supported maximum charging wattage. Measuring actual charger input requires suitable device telemetry or an external USB power meter. A supported maximum is a rating, not the power the phone draws continuously.

## Device input and output

Version 1.3.0 adds an automatic, **read-only** probe of `/sys/class/power_supply` and `/sys/class/typec`. It never requests root, writes kernel settings, changes charging limits, or swaps USB roles. Android may restrict access, and some drivers do not expose the measurements at all, so this feature cannot work on every phone.

- `DEVICE INPUT W` means electrical power entering the phone through an identified USB sink port.
- `DEVICE OUTPUT W` means power supplied by the phone through an identified USB source port, such as USB OTG or reverse-wired charging. **It is not the phone's internal power consumption.**
- Input/output values use the driver's live `power_now`, or a clearly labelled `V*I estimate` from `voltage_now` and `current_now` on the **same supply**. Voltage, current and the telemetry source are shown alongside the result. The kernel fields use micro-units, unlike Termux's millivolt battery-voltage field.

To avoid attaching a reading to the wrong port, automatic classification requires exactly one online external supply of USB type, exactly one connected Type-C partner, and a readable unambiguous current `sink` or `source` role. Multiple supplies/ports, missing roles, older connectors, negative or invalid port measurements, disconnected devices, or permission restrictions leave input/output as `n/a (not measured)`. This is not a zero-power reading. If a supply measurement is readable but direction cannot be established, it appears separately as `PORT ... (direction unknown)` rather than being guessed as input or output. Supplies are never summed, since some are aliases for the same physical path.

A readable `input_power_limit` is shown as `LIMIT ... (not live draw)`. Limits, maximum voltage/current settings, battery power, and USB role alone are never substituted for measured input/output watts. The unmeasured opposite direction also remains `n/a`, not an invented zero. Sources: [Linux power-supply units and properties](https://docs.kernel.org/power/power_supply_class.html), [power-supply ABI](https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-class-power), and [USB Type-C role ABI](https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-class-typec).

If input/output stays unavailable, stop the dashboard and run:

```sh
battery-monitor diagnose
```

This prints local read-only diagnostics: accessible supply measurements, current USB roles, and an availability explanation. It does not upload anything or read serial numbers. Share that output when requesting device-specific support. These measurements are driver-reported samples, not a guaranteed calibrated USB-meter reading or wall-socket measurement.

## Current and average current

`CURRENT (RAW)` preserves the signed reading supplied by the phone. Seeing `CHARGING` and `-2472 mA` together does not mean the dashboard calculated the sign incorrectly: charging status and current are separate phone-reported values. Android documents positive current as entering the battery and negative current as leaving it, but a sign/status mismatch alone does not establish its cause. The dashboard flags the mismatch rather than silently flipping the sign. See the [Android current-property reference](https://developer.android.com/reference/android/os/BatteryManager#BATTERY_PROPERTY_CURRENT_NOW).

`AVG (PHONE/RAW)` uses the phone's average-current reading when available, with its device-defined averaging period. If that reading is missing or invalid, `AVG (APP/RAW, N)` shows an **app-calculated sample mean** of valid raw current readings from the last **30 seconds**; `N` is the sample count. It starts with the first valid sample, so the first estimate is not yet a full 30-second average. It is not a hardware measurement or charger-output rating.

The app average resets when charging status or power source changes, after an API error, or when instantaneous current becomes unavailable. It does not reuse samples older than 30 seconds. If neither a native average nor usable current samples exist, the average remains `n/a`. Zero is a valid reported reading and is not treated as missing.

## Troubleshooting

If the battery API times out, check that the Termux:API Android companion app is installed from the same source as Termux. Follow any Android permission prompts. Installing only `pkg install termux-api` is not enough: the Android companion app is also required.

Missing or invalid numeric readings show `n/a`, not a health diagnosis. The dashboard includes `Developer: Hassan Raza` and adapts its lines to the terminal width.

## Development tests

```sh
python3 -m unittest discover -v
python3 -m py_compile live_battery_monitor.py
bash -n install.sh
```

The tests include narrow/short terminal sizes, Unicode cell widths, API errors, color output, resize transitions, redirected logs, animation controls, and the existing telemetry and update regressions. No Android device or root access is needed to run them; actual phone telemetry still requires Termux and its API companion app.

## Uninstall

```sh
rm "$PREFIX/bin/battery-monitor"
```

Optionally remove the downloaded repository too:

```sh
rm -rf "$HOME/battery-monitor-termux"
```
