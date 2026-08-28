# Ecovacs GOAT Open API – Simple Mode

Minimal Home Assistant custom integration for an Ecovacs GOAT mower via the Ecovacs Open API / MCP endpoints.

This version is intentionally conservative:

- Start, resume, pause, and return-to-base controls
- Working-status polling (`GetWorkState`)
- One problem binary sensor for dashboard alerts
- No experimental commands; return-to-base uses the documented Open API command
- No external Python requirements
- Cloud polling only

## Entities

- `lawn_mower.<nickname>` supporting `START_MOWING`, `PAUSE`, and `DOCK`
- `binary_sensor.<nickname>_fehler`
- `sensor.<nickname>_fehlergrund`
- `sensor.<nickname>_mahstatus`
- `sensor.<nickname>_ladestatus`
- diagnostic API/raw sensors

## Service

Command mapping:

- Start: `Clean` / `s`
- Resume: `Clean` / `r`
- Pause: `Clean` / `p`
- Return to base: `Charge` / `go-start`

```yaml
action: ecovacs_goat_openapi.start_mowing
```

The equivalent domain services `pause_mowing` and `return_to_base` are also available.

You can also use the standard Home Assistant lawn mower service:

```yaml
action: lawn_mower.start_mowing
target:
  entity_id: lawn_mower.<nickname>
```

Pause and dock use the standard `lawn_mower.pause` and `lawn_mower.dock` actions in the same way.
