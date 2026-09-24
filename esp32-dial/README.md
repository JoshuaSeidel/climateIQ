# ClimateIQ Rotary Dial

Physical desk/wall dial for ClimateIQ, built on the **GrowCube 1.28" ESP32-S3 round
knob display** (an Elecrow CrowPanel 1.28" HMI rebrand — GC9A01 240×240 IPS, CST816
capacitive touch, aluminum rotary encoder ring with push button, 16MB flash + octal
PSRAM). Firmware is ESPHome + LVGL, with a face modeled on
[climate-cluster-card](https://github.com/rickyfont94/climate-cluster-card): dark
instrument-cluster look, outer scale ring, mode-colored target arc, big center current
temperature with a `Set` readout.

## Controls

| Input | Action |
|---|---|
| Rotate | Adjust target temperature (auto-sends 2 s after you stop turning) |
| Short press | Send the pending target immediately (or force a refresh) |
| Long press (1 s) | Cancel hold — resume the ClimateIQ schedule |

Arc/status colors: orange = heating, blue = cooling, amber = while dialing, gray = idle,
red `OFFLINE` when the backend is unreachable.

## How it talks to ClimateIQ

The dial calls the backend REST API directly over Wi-Fi (no Home Assistant hop):

- `GET /api/v1/system/override` — polled every 10 s for current/target/schedule temps,
  HVAC mode/action, and hold state. Temperatures arrive already in your display unit.
- `POST /api/v1/system/override` `{"temperature": X}` — set a manual hold.
- `POST /api/v1/system/quick-action` `{"action": "resume"}` — return to schedule.

If the backend runs standalone with `CLIMATEIQ_API_KEY` set, put that key in the
`climateiq_api_key` substitution (sent as `Authorization: Bearer`). In HA add-on mode
direct LAN access needs no auth.

## Flashing

1. Install ESPHome (`brew install esphome` or `pip install esphome`).
2. `cp secrets.example.yaml secrets.yaml` and fill in your Wi-Fi credentials.
3. Edit the `substitutions:` block in `climateiq-dial.yaml`:
   - `climateiq_base_url` — e.g. `http://homeassistant.local:8420`
   - `temp_min` / `temp_max` / `temp_step` — dial range in **your display unit**
     (defaults are °F; use e.g. 15/28/0.5 for °C)
4. First flash over USB-C: `esphome run climateiq-dial.yaml`
5. Later updates go over the air (OTA) automatically.

The device also joins Home Assistant via the native ESPHome API (backlight control
entity), and falls back to a `ClimateIQ-Dial` setup AP if Wi-Fi is unconfigured.

## Pinout (GrowCube / CrowPanel 1.28" ESP32-S3)

GC9A01 SPI: CLK 10, MOSI 11, CS 9, DC 3, RST 14 · backlight PWM 46 ·
encoder A/B 45/42 · button 41 (active low) · CST816 touch: I2C SDA 6 / SCL 7,
INT 5, RST 13.
