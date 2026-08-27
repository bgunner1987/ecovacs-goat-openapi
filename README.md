# Ecovacs GOAT Open API – Simple Mode

Minimal Home Assistant custom integration for an Ecovacs GOAT mower via the Ecovacs Open API / MCP endpoints.

This version is intentionally conservative:

- Start mowing (`Clean` / `s` or resume `r`) and return to base (`Charge` / `go-start`)
- Working-status polling (`GetWorkState`)
- One problem binary sensor for dashboard alerts
- No experimental commands; return-to-base uses the documented Open API command
- No external Python requirements
- Cloud polling only

## Entities

- `lawn_mower.<nickname>` with only `START_MOWING` supported
- `binary_sensor.<nickname>_fehler`
- `sensor.<nickname>_fehlergrund`
- `sensor.<nickname>_mahstatus`
- `sensor.<nickname>_ladestatus`
- diagnostic API/raw sensors

## Service

```yaml
action: ecovacs_goat_openapi.start_mowing
```

You can also use the standard Home Assistant lawn mower service:

```yaml
action: lawn_mower.start_mowing
target:
  entity_id: lawn_mower.<nickname>
```
