# IGL Configuration Boundary

The platform supports the existing Plant → Area → Zone → Camera domain model, polygon geometry storage, equipment records, and zone-linked PPE rules. These structures are generic capabilities only; there are no authorized IGL site/area/zone/camera/equipment/SOP values in this repository.

Do not copy test fixture names or coordinates into operational configuration. Authorized IGL personnel must provide reviewed site hierarchy, camera placement, calibrated image-space polygons, equipment inventory, applicable SOP/PPE requirements, threshold sources, escalation owners, and retention rules before those capabilities can be configured.

Current state: `IGL_CONFIGURATION_STATUS = NOT_CONFIGURED`; `IGL_VALIDATED = false`. No IGL rules or thresholds are inferred from generic examples.

Empty schema-shaped templates are under `config/igl/`. They contain no example operating values and are not loaded as live database configuration. Authorized personnel must submit records through the authenticated configuration API or an explicitly implemented import workflow.