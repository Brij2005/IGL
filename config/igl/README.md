# IGL Configuration Templates

These files are schema-shaped placeholders only. Empty `records` collections mean no operational IGL configuration is supplied. Do not populate from assumptions. Authorized IGL personnel must approve values and sources before importing or activating configuration.

| File | Covers |
| --- | --- |
| `plant.yaml` | Plant identity and location |
| `areas.yaml` | Areas within a plant |
| `zones.yaml` | Zones, hazard descriptions, geometry, PPE requirements |
| `cameras.yaml` | Cameras, source type, capture backend, safety scope |
| `equipment.yaml` | Equipment register |
| `safety_rules.yaml` | Detector configuration, thresholds, debounce, cooldown, evidence policy |
| `ppe_rules.yaml` | PPE mandates per zone |
| `escalation_rules.yaml` | Escalation tiers plus the alarm-policy reference block |
| `notification_recipients.yaml` | Approved notification recipients plus the evidence-retention reference block |

## Status vocabulary

Every file keeps `configuration_status: NOT_CONFIGURED` until an authorized
operator supplies reviewed values. The runtime reports
`IGL_VALIDATION_NOT_CONFIGURED` / `igl_configuration_status: NOT_CONFIGURED`
while that is true, and `CONFIGURED_NOT_VALIDATED` once a plant record exists.
Neither state means the configuration has been validated against IGL footage.

## Live safety-path settings

Alarm policy (`ALARM_*`) and physical actuator settings (`PHYSICAL_ALARM_*`) are
read from the backend environment because they govern the live safety path;
their operator-facing summary is recorded in `escalation_rules.yaml`. Evidence
retention (`EVIDENCE_*`) is likewise environment-driven and summarised in
`notification_recipients.yaml`. These reference blocks are for review and
approval only; the runtime does not read them.
