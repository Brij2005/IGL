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
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            return None
        points.append((float(x), float(y)))
    if len(points) > 3 and points[0] == points[-1]:
        points.pop()
    if len(set(points)) < 3:
        return None
    if any(points[index] == points[(index + 1) % len(points)] for index in range(len(points))):
        return None

    area_twice = sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    )
    if abs(area_twice) <= 1e-12:
        return None

    def orientation(first, second, third):
        cross = (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])
        if abs(cross) <= 1e-12:
            return 0
        return 1 if cross > 0 else -1

    def on_segment(first, point, second):
        return (
            min(first[0], second[0]) - 1e-12 <= point[0] <= max(first[0], second[0]) + 1e-12
            and min(first[1], second[1]) - 1e-12 <= point[1] <= max(first[1], second[1]) + 1e-12
        )

    def segments_intersect(first, second, third, fourth):
        o1 = orientation(first, second, third)
        o2 = orientation(first, second, fourth)
        o3 = orientation(third, fourth, first)
        o4 = orientation(third, fourth, second)
        if o1 != o2 and o3 != o4:
            return True
        return (
            (o1 == 0 and on_segment(first, third, second))
            or (o2 == 0 and on_segment(first, fourth, second))
            or (o3 == 0 and on_segment(third, first, fourth))
            or (o4 == 0 and on_segment(third, second, fourth))
        )

    point_count = len(points)
    for first_index in range(point_count):
        first = points[first_index]
        second = points[(first_index + 1) % point_count]
        for other_index in range(first_index + 1, point_count):
            if other_index == first_index or other_index == (first_index + 1) % point_count:
                continue
            if (other_index + 1) % point_count == first_index:
                continue
            third = points[other_index]
            fourth = points[(other_index + 1) % point_count]
            if segments_intersect(first, second, third, fourth):
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