"""Geometry-only zone membership using explicitly supplied polygon data."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class ZoneMembership:
    zone_id: str
    state: str
    inside: bool | None
    reason: str | None = None


def validate_polygon(polygon: Any) -> tuple[tuple[float, float], ...] | None:
    if not isinstance(polygon, (list, tuple)) or len(polygon) < 3:
        return None
    points: list[tuple[float, float]] = []
    for point in polygon:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        x, y = point
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or not isfinite(x) or not isfinite(y):
            return None
        points.append((float(x), float(y)))
    if len(set(points)) < 3:
        return None
    return tuple(points)


def point_in_polygon(point: tuple[float, float], polygon: Any) -> bool | None:
    points = validate_polygon(polygon)
    if points is None:
        return None
    x, y = point
    if not isfinite(x) or not isfinite(y):
        return None
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        crosses = (y1 > y) != (y2 > y)
        if crosses:
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def evaluate_zone_membership(zone_id: str, geometry: Any, point: tuple[float, float] | None) -> ZoneMembership:
    if not geometry:
        return ZoneMembership(zone_id, "NOT_CONFIGURED", None, "Zone polygon is not configured")
    if point is None:
        return ZoneMembership(zone_id, "NOT_ASSESSABLE", None, "Object position is unavailable")
    inside = point_in_polygon(point, geometry)
    if inside is None:
        return ZoneMembership(zone_id, "NOT_ASSESSABLE", None, "Zone polygon or point is invalid")
    return ZoneMembership(zone_id, "ASSESSED", inside)