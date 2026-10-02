# IGL Configuration Boundary

Current state: `igl_configuration_status = NOT_CONFIGURED`; `validation_status = NOT_VALIDATED`; `igl_validated = false`. Every file under `config/igl/` carries `configuration_status: NOT_CONFIGURED` and `records: []`. Nothing has been populated from an assumption, and no import workflow exists.

## What The Platform Supports

The Plant -> Area -> Zone -> Camera domain model, polygon geometry storage, equipment records, and zone-linked PPE rules are generic capabilities only. There are no authorised IGL site, area, zone, camera, equipment or SOP values in this repository.

Do not copy test fixture names or coordinates into operational configuration. Authorised IGL personnel must provide a reviewed site hierarchy, camera placement, calibrated image-space polygons, equipment inventory, applicable SOP/PPE requirements, threshold sources, escalation owners, and retention rules before those capabilities can be configured.

Runtime reporting changes with data, not with intent: `NOT_CONFIGURED` while no plant record exists, `CONFIGURED_NOT_VALIDATED` once a plant record exists, and `UNKNOWN_DATABASE_UNAVAILABLE` when the database cannot be read. None of those states means the configuration has been validated against IGL footage.

## Template Files And Schemas

Empty schema-shaped templates are under `config/igl/`. They are not loaded as live database configuration. Authorised personnel must submit records through the operator configuration API, using authenticated ADMIN access outside localhost development.

| File | Covers |
| --- | --- |
| `plant.yaml` | Plant identity and location |
| `areas.yaml` | Areas within a plant |
| `zones.yaml` | Zones, hazard descriptions, geometry, PPE requirements, approval |
| `cameras.yaml` | Cameras, source type, capture backend, safety scope |
| `equipment.yaml` | Equipment register |
| `safety_rules.yaml` | Detector configuration, thresholds, debounce, cooldown, evidence policy |
| `ppe_rules.yaml` | PPE mandates per zone |
| `escalation_rules.yaml` | Escalation tiers plus the alarm-policy reference block |
| `notification_recipients.yaml` | Approved recipients plus the evidence-retention reference block |

### zones.yaml

Added fields, all `null` until an authorised operator supplies them:

- `hazard_description`: free-text hazard description supplied by the plant. Never invented here.
- `ppe_requirements`: PPE mandates that apply inside the zone, by PPE item code.
- `requires_ppe`: retained boolean alongside the item list.
- `approved_by` / `approved_at`: author, date and approval for the record. Empty until an operator signs it off.

The existing fields remain: `id`, `area_id`, `name`, `code`, `zone_type` (`WORK_AREA`, `RESTRICTED`, `HAZARDOUS`, `PPE_MANDATORY` are the kinds this build understands; no IGL-specific classification is assumed), `polygon_coordinates` in normalised frame coordinates, `coordinate_reference` (drawing, survey, SOP, or `NOT_CONFIGURED`), `is_active` and `source_reference`.

### escalation_rules.yaml

Added `alarm_policy_reference`, a review block for approval only. It carries its own `configuration_status: NOT_CONFIGURED`, an `intended_values` field, `physical_actuator: NOT_CONFIGURED`, and `approved_by` / `approved_at`.

The runtime does not read this block. The authoritative alarm values are `ALARM_ENABLED`, `ALARM_MIN_SEVERITY`, `ALARM_COOLDOWN_SECONDS`, `ALARM_MAX_REPEATS_PER_WINDOW`, `ALARM_REPEAT_WINDOW_SECONDS`, `ALARM_AUTO_EXPIRE_SECONDS`, `ALARM_AUDIBLE_BROWSER` and `ALARM_PHYSICAL_ACTUATION` in the backend environment, because they govern the live safety path. The block records the plant's intended policy for review and approval only.

The escalation `record_schema` itself is unchanged: `event_type` (null means every event class), `severity`, response tiers `from_role` / `to_role` / `channel` / `escalation_level` / `escalate_after_seconds`, `dedup_window_seconds`, `is_active`, `source_reference`, `approved_by`, `approved_at`.

### notification_recipients.yaml

New file. It holds the recipient schema and an evidence-retention reference block.

Recipient `record_schema`: `id`, `name`, `channel` (`EMAIL`, `WHATSAPP`, `DASHBOARD`; a channel with no configured transport stays `NOT_CONFIGURED` and is never reported as delivered), `recipient` or `recipient_role` (both null falls back to the transport's configured default recipient list), optional `event_type` / `severity` / `zone_id` scoping, `dedup_window_seconds`, `is_enabled`, `template_kind` (`INCIDENT`, `ESCALATION`, or `TEST`), `source_reference`, `approved_by`, `approved_at`.

`evidence_retention_reference` is a review block, also `configuration_status: NOT_CONFIGURED`, with `retention_days`, `evidence_dir`, `retain_identifiable_frames` (left null because whether frames containing identifiable people may be retained, and for how long, is a plant decision and not a software default), `deletion_authority`, `approved_by` and `approved_at`. The runtime does not read it; retention is applied from `EVIDENCE_RETENTION_DAYS` and `EVIDENCE_DIR`.

## Live Settings That Are Not In These Files

Alarm policy and physical actuator settings are environment-driven, not YAML-driven, and are summarised for review in `escalation_rules.yaml`. Evidence retention is environment-driven and summarised in `notification_recipients.yaml`. Model weights, notification transports and database configuration are environment settings only. The full list is in [setup.md](setup.md).

## What Authorised Personnel Must Supply

- The site hierarchy: plant, areas, zones, cameras, equipment.
- Calibrated zone geometry in normalised frame coordinates, with a documented coordinate reference (drawing, survey or SOP) for each polygon.
- Zone hazard descriptions and the PPE mandates that apply inside each zone.
- The cameras, their source types, and their capture backends where the platform has to choose one.
- For each detector: the confidence floor, minimum observations, minimum duration, maximum observation gap, debounce, cooldown, severity, zone scoping, evidence policy and correlation window, each with a `source_reference` naming the SOP or survey it came from.
- Escalation tiers with named roles and channels, and the delay before each tier fires.
- Approved notification recipients by channel, role and event scope, with a template kind.
- The intended alarm policy, physical actuator identity, and evidence retention rules, for approval.
- Model checkpoint authorisation: which checkpoint, which version, and who approved it.

Anything left at a generic value is reported as `ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION`, never as an IGL-validated operating value. That distinction is enforced in the effective rule configuration exposed by `GET /api/v1/system/detectors`.

## Validation Boundary

`igl_validated` is `false` and `validation_status` is `NOT_VALIDATED` in every code path in this repository. Raising either requires authorised IGL footage, labelled evaluation data, documented plant and camera conditions, approved SOP rules, and a reviewed independent evaluation. None of that material is present in this workspace, and no substitute data has been created. See [ai_validation.md](ai_validation.md) and [validation.md](validation.md).
