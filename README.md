<!-- prettier-ignore -->
# Ecoflow PowerOcean
[![GitHub release](https://img.shields.io/github/release/niltrip/powerocean?include_prereleases=&sort=semver&color=blue)](https://github.com/niltrip/powerocean/releases/)
[![issues - powerocean](https://img.shields.io/github/issues/niltrip/powerocean)](https://github.com/niltrip/powerocean/issues)
[![GH-code-size](https://img.shields.io/github/languages/code-size/niltrip/powerocean?color=red)](https://github.com/niltrip/powerocean)
[![GH-last-commit](https://img.shields.io/github/last-commit/niltrip/powerocean?style=flat-square)](https://github.com/niltrip/powerocean/commits/main)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![HACS validation](https://github.com/niltrip/powerocean/workflows/Validate/badge.svg)](https://github.com/niltrip/powerocean/actions?query=workflow:"Validate")
![installation_badge](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.powerocean.total)


[Home Assistant](https://home-assistant.io/) custom component to get access to my EcoFlow PowerOcean system.

This component was inspired by [tolwi/hassio-ecoflow-cloud](https://github.com/tolwi/hassio-ecoflow-cloud) and is a copy of [evercape/hass-resol-KM2](https://github.com/evercape/hass-resol-KM2).

## Disclaimers

⚠️ Temporary quick solution to my problem ("Nothing is more persistent than a makeshift solution.").
I hope of an integrate PowerOcean into [tolwi/hassio-ecoflow-cloud](https://github.com/tolwi/hassio-ecoflow-cloud) with an official API.

Nevertheless, I hope that it works for others as well.

## Prerequisites

I have tested the component with my System.
I use the Android App and the Webportal at https://portal.ecoflow.com/user/eu/de/login

You need the S/N number of your inverter and your credentinals

## Installation

- Install as a custom repository via HACS
- Manually download and extract to the custom_components directory

Once installed, use Add Integration -> Ecoflow PowerOcean.

## Configuration

Follow the flow.

![step 1](documentation/setup_step_01.png)
![step 2](documentation/setup_step_02.png)
![step 3](documentation/setup_step_03.png)



### Sensors
Sensors are registered to device as `sensor.{device_name}_{sensor_name}` with an friendly name of `sensor_name`.
Additional attributes are presented on each sensor:
- Product Description, Destination Name, Source Name: internal names
- Internal Unique ID: `{serial}_{sensor_name}` or `{serial}_{report}_{sensor_name}`
- Device Name: `{serial}`
- Vendor Product Serial: serial number of the PowerOcean inverter
- Vendor Firmware Version: 5.1.27
- Vendor Product Build: 28

The versions are from my system.

### Which values update, and which do not

The EcoFlow cloud does not refresh all values at the same rate. Some of them keep returning the same number for hours. The integration reports what the API hands it, so a flat line here is not a bug in this component.

Measured on my own system over 72 hours (three-phase PowerOcean, one battery, release 2026.03.04):

| sensor | report | distinct values in 72 h |
|---|---|---|
| `mpptPwr`, `bpPwr` | `data` | 1 (always 0 W) |
| `pv1Pwr`, `pv2Pwr`, `pvInvPwr` | `ENERGY_STREAM_REPORT` | 1 (always 0 W) |
| `sysGridPwr`, `sysLoadPwr` | `data` | 4 |
| `todayElectricityGeneration` | `data` | about one per hour |
| `pcsMeterPower`, `pcsActPwr`, `mpptPv_pwrTotal` | `EMS_HEARTBEAT` | more than 2100 |

On 2026-08-21 at 13:16 `mpptPwr` read 0 W while `mpptPv_pwrTotal` read 4238 W. Both describe the same solar power, and both came out of the same response.

Others report in [#34](https://github.com/niltrip/powerocean/issues/34) and [#28](https://github.com/niltrip/powerocean/issues/28) that these values start moving again while the app or the web portal is open. I cannot confirm that part myself.

For a dashboard, take the values from `EMS_HEARTBEAT`:

- grid: `pcsMeterPower` (positive = import, negative = export) rather than `sysGridPwr`
- solar: `mpptPv_pwrTotal` rather than `mpptPwr`, `pv1Pwr` or `pvInvPwr`
- battery: `emsBpPower` rather than `bpPwr`
- house: `housePower`, which is calculated from the three values above

### Neuer Sensor (berechnet aus einzelnen Strings)

![sensor](documentation/mpptPv_pwrTotal.PNG)

### Darstellung einzelner Geräte über SN
![integration](documentation/integration.PNG)
![powerpulse](documentation/powerpulse.png)
![powerglow](documentation/powerglow.png)


### Energiedashboard
![dashboard](documentation/dashboard.PNG)


## Troubleshooting
Please set your logging for the this custom component to debug during initial setup phase. If everything works well, you are safe to remove the debug logging:

```yaml
logger:
  default: warn
  logs:
    custom_components.powerocean: debug
```

## Credits

Thanks to my kollege David for giving me a start point.

And also thanks for the great work of the team from homeassistant and the great community.