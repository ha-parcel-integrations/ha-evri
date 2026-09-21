# Evri Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-evri.svg)](https://github.com/ha-parcel-integrations/ha-evri/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-evri/total.svg)](https://github.com/ha-parcel-integrations/ha-evri/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks UK parcels and packages from [Evri](https://www.evri.com/). No account is needed: create a hub for each delivery postcode and add the tracking codes you want to follow.

Part of the [ha-parcel-integrations](https://ha-parcel-integrations.github.io/) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Create multiple independent postcode hubs (for example home and work)
- Track incoming and outgoing Evri parcels separately — no account needed
- Per-parcel sensor with the canonical status and an official tracking deep-link
- Summary sensors for incoming, outgoing, delivered and parcels ready for pickup
- Read-only **Deliveries** calendar with the expected delivery windows
- `evri.track_parcel` / `evri.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.12 or newer
- An Evri tracking code and the UK delivery postcode — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-evri` as an **Integration**.
3. Install **Evri** and restart Home Assistant.

### Manual

Copy `custom_components/evri` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Evri** and enter the UK delivery postcode. Add the integration again for each other postcode you want to keep separate.

Then add parcels via the integration's **Configure** dialog, the [`evri.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Incoming parcels | Tracking codes | — | Parcels you expect to receive. Changes apply immediately. |
| Outgoing parcels | Tracking codes | — | Parcels you sent. Filing a code here moves it out of the incoming list. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule (quiet overnight window, faster when a parcel is out
for delivery, stopped entirely once nothing is left to track) with nothing to
configure. See [CLAUDE.md](CLAUDE.md) for the details.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Evri → ⋮ → Delete**. Nothing is stored on Evri's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.evri_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.evri_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.evri_ready_for_pickup` | Incoming parcels waiting at a pickup point |
| `sensor.evri_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.evri_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.evri_outgoing_parcels` | Active outgoing parcels |
| `sensor.evri_outgoing_delivered_parcels` | Recently delivered outgoing parcels |
| `sensor.evri_last_successful_update` | Diagnostic: when Evri was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Announced / received by Evri |
| `in_transit` | In the sorting network |
| `out_for_delivery` | With the courier today |
| `at_pickup_point` | Waiting for you at a pickup location |
| `delivered` | Delivered |
| `returning` | Going back to the sender |
| `problem` | Evri reports an exception |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

`raw_status` contains Evri's machine stage code. Unrecognised stages remain `unknown` and emit a one-time warning asking for a report.

## Events

The integration fires these on the event bus (also available as device triggers on the Evri device):

| Event | When |
|---|---|
| `evri_parcel_registered` | A new parcel appears in the active list |
| `evri_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `evri_parcel_delivered` | A parcel is delivered |
| `evri_parcel_delivery_time_changed` | The expected delivery window changes |
| `evri_outgoing_parcel_status_changed` | An outgoing parcel's canonical status changes |
| `evri_outgoing_parcel_delivered` | An outgoing parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `evri.track_parcel` | `tracking_code`, optional `postal_code`, optional `direction` | Start tracking a parcel; postcode is required when several hubs exist |
| `evri.untrack_parcel` | `tracking_code`, optional `postal_code` | Stop tracking a parcel; postcode is required when several hubs exist |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.evri: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Evri may not have scanned it yet, the code may be wrong, or Evri returned a stage not mapped yet.
- **All updates suddenly fail** — Evri controls the public website credential used by its tracker and may rotate it. That requires an integration update; there is no user credential to reauthenticate.
- **A status logs "Unrecognised Evri status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-evri/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Evri, Home Assistant, or any other third party referenced in this project. Please don't contact Evri for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to Evri's terms. Data is sent only to Evri's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses the same public tracking service as the Evri consumer website.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
