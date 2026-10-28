# Changelog

## Unreleased

### New

- **LED brightness of the inverter (with the MQTT option):** a slider from
  0 to 100 % in steps of 10, as in the EcoFlow app. The system confirms
  each change; without confirmation Home Assistant shows an error.
- **Backup reserve (with the MQTT option):** a slider from 0 to 100 % in
  steps of 1, as in the EcoFlow app (`sysBatDsgDownLimit`). The app's
  command also carries the charge limit and `devSoc`; the integration sends
  their current values unchanged and does not send at all while it does not
  know them.

## 2026.10.28

### Breaking changes

- **Battery firmware is shown on the device page.** Each battery pack shows
  its firmware version under *Firmware* on its device page, updated after a
  firmware update without a restart. The diagnostic sensor "Firmware
  version" (`moduleAplSwVer`) is removed automatically.
- **No serial number sensors.** The serial number of each battery pack,
  heating rod, wallbox and inverter is shown in its device info. The
  sensors `bpSn`, `hrSn`, `evSn` and `devSn` repeated it and are removed
  automatically.
- **Battery and wallbox states are now lowercase.** The states of
  `bmsChgDsgSta`, `bmsRunSta` and `bpSysState` change, for example from
  `PB_CHG_STATE` to `pb_chg_state`, and the charging status of the
  PowerPulse from `EV_CHG_STS_AVAILABLE` to `ev_chg_sts_available`, so Home
  Assistant can show them translated ("Charging", "Laden"; "Available",
  "Verfügbar"). Update automations and templates that compare these states
  with the old uppercase values. The history keeps the old values up to the
  update.
- **Wallbox `orderStartTimestamp` is a timestamp.** It was shown as a current
  in A by mistake. Home Assistant reports under *Settings → Repairs* that
  the stored statistics no longer match and offers to delete them.
- **Battery `bpRemainWatth` ("Remaining energy") is a stored energy.** It
  is a fill level that rises and falls like the state of charge, not an
  energy meter. Its long-term statistics now keep mean, minimum and maximum
  instead of a sum, and the energy dashboard no longer offers it. Home
  Assistant reports under *Developer tools → Statistics* that the state
  class changed and offers to fix the old statistics.
- **Some sensors moved to the "Diagnostic" section** of their device page:
  values per phase, voltage and current of the PV strings, battery voltage,
  current and temperatures, and values that are also shown as calculated
  energy flow (`sysLoadPwr`, `sysGridPwr`, `mpptPwr`, `pcsMeterPower`, …).
  Entity IDs, history, statistics, automations and your own dashboards are
  not affected. Only the automatically generated default dashboard does not
  show diagnostic entities.

### New

- **More datapoints:** operating mode, inverter state, grid frequency, current
  power limits, feed-in limit, backup reserve, charge and discharge limit,
  §14a EnWG, SG Ready, connection state, error and warning codes, counters of
  grid protection trips, load management of the house connection, and per
  battery pack the highest and lowest cell voltage.
- **Smart meter** as its own device with the power of each phase and the
  total (three-phase meters of type 11 and 14).
- **PowerPulse 2 wallbox:** charging current settings (custom, maximum,
  solar minimum) in A, energy and duration of the last charging session,
  phase setting. These values were in the response but not read before.
  Value mappings and units from the diagnostics and the
  [PowerGlow](https://github.com/Xygen/EcoFlow-PowerGlow-for-Home-Assistant)
  and
  [PowerPulse 2](https://github.com/Xygen/EcoFlow-PowerPulse-2-for-Home-Assistant)
  integrations by Xygen (discussion #132) – thanks!
- **Energy management per device:** wallbox and heating rod each show their
  maximum power, priority and allocated power, and their error and warning
  code of the energy management ("(energy management)"). Before, only the
  first device got these values. The values of the whole system from the
  same report (`socCur`, `feedPwrCap`, `pclPwrBase`, `startState`) moved
  from the wallbox to the inverter, disabled by default; the old wallbox
  entities are removed automatically.
- **Time of the last values** per battery pack and per report of the
  inverter (diagnostic). Shows when the data from the EcoFlow cloud is out
  of date.
- **New devices and datapoints appear without a restart**, e.g. a battery
  pack that was offline when Home Assistant started.
- **Translated states** (English, German) for operating mode, feed-in mode,
  grid state (on-grid/off-grid), inverter and battery states, on/off
  values, mode and state of the PowerGlow heating rod, charging mode and
  charging status of the PowerPulse wallbox, as far as possible with the
  texts of the EcoFlow web portal. The operating mode also knows AI mode,
  backup, schedule, battery calibration and charging from grid, the feed-in
  mode "off" (thanks to shuette42/ecoflow-energy-ha for the value lists).
  Unknown values are shown as they are.
- **EMS work state** (diagnostic, disabled by default): what the system is
  doing right now – idle, starting (battery, solar, grid), running, stopped,
  maintenance.
- **Firmware of the inverter and the smart meter (optional, MQTT).** The new
  option *Extended connection (MQTT)*, off by default, connects like the
  EcoFlow app and shows the firmware of the energy management on the
  inverter and the firmware of the smart meter on their device pages. It
  updates after a firmware update. All other modules are in the
  diagnostics. The values still come from the regular polling.
- **Link to the EcoFlow web portal** on every device page.
- **Readable entity names** in English and German, e.g. "State of charge"
  instead of `bpSoc`. Entity IDs stay the same, also for new installations,
  and names you set yourself are kept. Values whose meaning is unknown keep
  their key as name. Values the cloud only refreshes while the EcoFlow app
  (or a PowerInsight display) is open end in "(cloud)"; use the calculated
  values without suffix instead. In dual systems, the values per inverter
  from the parallel report end in "(energy flow)". This includes the
  PowerGlow heating rod and the PowerPulse wallbox; heating rod values from
  its energy stream report end in "(cloud)" as well.
- **Devices that are gone can be deleted**, e.g. a replaced battery pack:
  *device page → ⋮ → Delete*. Only possible while the device sends no
  values. If it returns, it is added again.

### Changed

- **Backup reserve has the right name.** The backup reserve you set in the
  EcoFlow app is `sysBatDsgDownLimit`: the sensor "Discharge limit" is now
  called "Backup reserve (discharge limit)". The sensor that was called
  "Backup reserve" (`sysBatBackupRatio`, disabled by default) does not follow
  the app setting and is now called "Backup ratio (not in the app)". Entity
  IDs stay the same; names you changed yourself are kept.
- **Fewer diagnostic sensors.** Rarely needed sensors are disabled by default
  (connection codes, error and warning codes of inverter and PV strings,
  protection counters, states without known meaning, internal battery
  temperatures, settings that hardly ever change, reactive and apparent power
  per phase, PV strings the system does not have). You can enable them under
  *Settings → Entities*.
  - **If you update:** sensors you already have stay as they are, nothing
    disappears. Sensors added with this version arrive disabled.
  - **New installations:** all of these sensors start disabled.
- Percentages and litres are shown without decimals, currents and
  insulation resistance with one; the duration of the last charging session
  of the PowerPulse in minutes. Stored values keep their full precision.
- **Heating rod and wallbox have one name each:** "PowerGlow" and
  "PowerPulse". Before, depending on which report arrived first, they were
  also called "Heating Rod" or "Charger". Device pages may show the new
  name; entity IDs you already have stay as they are.
- **Units for heating rod and wallbox:** the charging current setting of the
  PowerPulse is shown in A (6.0 A instead of 60), the energy target of smart
  charging in Wh, the planned departure as a time (unknown if none is set).
  The self-check of the PowerGlow no longer shows a battery icon.
- Battery state of charge shows the battery level icon.
- Sensors without a current value, e.g. of a battery pack that is offline,
  are shown as *unavailable* instead of *unknown*.
- The debug log no longer contains the complete response of the EcoFlow
  cloud. It is in the diagnostics, with serial numbers replaced.
