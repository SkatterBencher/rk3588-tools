# rk3588-tools
Tools for tuning the Rockchip RK3588
- RK3588 OC Tool > for configuring the clocks
- RK3588 Telemetry > for monitoring & logging

# RK3588 OC Tool
sudo python3 rk3588-octool.py

# RK3588 OC Tool
This tool provides real-time telemetry monitoring for the RK3588 SoC (tested on Orange Pi 5 Max). It can run in two modes: TUI mode (interactive curses dashboard) or CLI mode (regular console output). It also supports CSV logging for post-analysis.

sudo python3 telemetry.py

If no options are provided, all metrics are shown automatically in the TUI mode.

Enable individual metrics with flags: e.g. python3 telemetry.py -f -t -v
- -f → Show frequencies
- -af → Show advanced frequencies (per-core, GPU, NPU, DSU clocks)
- -v → Show regulator voltages
- -l → Show CPU + system loads
- -t → Show temperatures
- -g → Show performance governors
- -s → Show SAR-ADC readings
- -i <sec> → Refresh interval (default: 2.0)

Enable logging with -log

This will create a timestamped CSV file (e.g., telemetry-20250822-153000.csv) containing all selected metrics.

[![License: CC BY-NC 4.0](https://licensebuttons.net/l/by-nc/4.0/88x31.png)](https://creativecommons.org/licenses/by-nc/4.0/)
