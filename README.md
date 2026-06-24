# P2000 — Home Assistant Integration

Monitor Dutch P2000 emergency pager messages (fire, police, ambulance, coastguard) directly in Home Assistant.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

## Features

- Real-time P2000 alerts as a sensor entity
- Filter by municipality, city/village, capcode, region, and emergency service
- Priority 1 only and lifeliner options
- Automatic fallback to backup RSS feed if the primary API is unavailable
- Configurable via the Home Assistant UI — no YAML required

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** → click the three dots → **Custom repositories**
3. Add `https://github.com/leeuwte/p2000` as an **Integration**
4. Search for **P2000** and install it
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/p2000` folder to your HA `custom_components` directory
2. Restart Home Assistant

## Configuration

Go to **Settings → Devices & Services → Add Integration** and search for **P2000**.

| Field | Description |
|---|---|
| Name | Sensor name (used as entity ID) |
| Icon | MDI icon, e.g. `mdi:fire-truck` |
| Municipalities | Comma-separated gemeente names, e.g. `Amsterdam, Utrecht` |
| Cities / villages | Comma-separated woonplaatsen, e.g. `Amstelveen, Diemen` |
| Capcodes | Comma-separated capcodes to monitor |
| Regions | Comma-separated regio names |
| Emergency services | Select one or more: Politie, Brandweer, Ambulance, Kustwacht |
| Priority 1 only | Only show prio 1 alerts |
| Lifeliners | Include lifeliner alerts |

All filter fields are optional. Leaving them empty returns all messages from the primary feed.

You can update the filters at any time via **Settings → Devices & Services → P2000 → Configure**.

## Sensor attributes

The sensor state holds the latest alert message. The following attributes are available:

| Attribute | Description |
|---|---|
| `melding` | Full alert text |
| `tijd` | Time of the alert |
| `datum` | Date of the alert |
| `capcode` | Capcode of the unit |
| `dienst` | Emergency service (Brandweer, Ambu, etc.) |
| `prio` | Priority level |
| `latitude` | Latitude (if available) |
| `longitude` | Longitude (if available) |

## Example automation

```yaml
automation:
  - alias: "Notify on fire truck near me"
    trigger:
      - platform: state
        entity_id: sensor.p2000
    condition:
      - condition: template
        value_template: "{{ 'Brandweer' in state_attr('sensor.p2000', 'dienst') }}"
    action:
      - service: notify.mobile_app
        data:
          title: "P2000 Brandweer"
          message: "{{ states('sensor.p2000') }}"
```

## Data sources

- **Primary**: [AlarmeringDroid API](https://beta.alarmeringdroid.nl)
- **Backup**: RSS feed via brandweer-berkel-enschot.nl

## Credits

Original integration by [@leeuwte](https://github.com/leeuwte).
