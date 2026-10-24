# Changelog

## Unreleased

### Breaking changes

- **Battery states are now lowercase.** The states of `bmsChgDsgSta`,
  `bmsRunSta` and `bpSysState` change, for example from `PB_CHG_STATE` to
  `pb_chg_state`, so Home Assistant can show them translated ("Charging",
  "Laden"). Update automations and templates that compare these states with
  the old uppercase values. The history keeps the old values up to the
  update.
- **Wallbox `orderStartTimestamp` is a timestamp.** It was shown as a current
  in A by mistake. Home Assistant reports under *Settings → Repairs* that
  the stored statistics no longer match and offers to delete them.
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
  total.
- **Time of the last values** per battery pack and per report of the
  inverter (diagnostic). Shows when the data from the EcoFlow cloud is out
  of date.
- **New devices and datapoints appear without a restart**, e.g. a battery
  pack that was offline when Home Assistant started.
- **Translated states** (English, German) for operating mode, inverter and
  battery states and on/off values. Unknown values are shown as they are.
- **Link to the EcoFlow web portal** on every device page.
- **Readable entity names** in English and German, e.g. "State of charge"
  instead of `bpSoc`. Entity IDs stay the same, also for new installations,
  and names you set yourself are kept. Values whose meaning is unknown keep
  their key as name.
- **Devices that are gone can be deleted**, e.g. a replaced battery pack:
  *device page → ⋮ → Delete*. Only possible while the device sends no
  values. If it returns, it is added again.

### Changed

- **Fewer diagnostic sensors.** Rarely needed sensors are disabled by default
  (connection codes, error and warning codes of inverter and PV strings,
  protection counters, states without known meaning, internal battery
  temperatures, settings that hardly ever change, reactive and apparent power
  per phase, PV strings the system does not have). You can enable them under
  *Settings → Entities*.
  - **If you update:** sensors you already have stay as they are, nothing
    disappears. Sensors added with this version arrive disabled.
  - **New installations:** all of these sensors start disabled.
- Percentages are shown without decimals, insulation resistance with one.
  Stored values keep their full precision.
- Battery state of charge shows the battery level icon.
- Sensors without a current value, e.g. of a battery pack that is offline,
  are shown as *unavailable* instead of *unknown*.
