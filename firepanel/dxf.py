from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import ezdxf


@dataclass(slots=True)
class DxfShape:
    layer: str
    entity_type: str
    points: list[tuple[float, float]]


@dataclass(slots=True)
class DxfLinework:
    layer: str
    entity_type: str
    points: list[tuple[float, float]]
    closed: bool = False


def read_linework(path: str | Path) -> list[DxfLinework]:
    """Read common 2D DXF entities for use as a map underlay."""
    document = ezdxf.readfile(Path(path))
    result: list[DxfLinework] = []
    for entity in document.modelspace():
        entity_type = entity.dxftype()
        points: list[tuple[float, float]] = []
        closed = False
        if entity_type == "LINE":
            points = [
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            ]
        elif entity_type == "LWPOLYLINE":
            points = [(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
            closed = bool(entity.closed)
        elif entity_type == "POLYLINE":
            points = [
                (float(vertex.dxf.location.x), float(vertex.dxf.location.y))
                for vertex in entity.vertices
            ]
            closed = bool(entity.is_closed)
        elif entity_type in {"CIRCLE", "ARC"}:
            centre = entity.dxf.center
            radius = float(entity.dxf.radius)
            start = 0.0 if entity_type == "CIRCLE" else float(entity.dxf.start_angle)
            end = 360.0 if entity_type == "CIRCLE" else float(entity.dxf.end_angle)
            if end <= start:
                end += 360.0
            steps = max(12, int((end - start) / 7.5))
            points = [
                (
                    float(centre.x) + radius * math.cos(math.radians(start + (end - start) * i / steps)),
                    float(centre.y) + radius * math.sin(math.radians(start + (end - start) * i / steps)),
                )
                for i in range(steps + 1)
            ]
            closed = entity_type == "CIRCLE"
        if len(points) >= 2:
            if closed and points[0] != points[-1]:
                points.append(points[0])
            result.append(
                DxfLinework(str(entity.dxf.layer), entity_type, points, closed)
            )
    return result


def read_closed_shapes(path: str | Path) -> list[DxfShape]:
    document = ezdxf.readfile(Path(path))
    modelspace = document.modelspace()
    shapes: list[DxfShape] = []

    for entity in modelspace:
        entity_type = entity.dxftype()
        points: list[tuple[float, float]] = []
        closed = False
        if entity_type == "LWPOLYLINE":
            points = [(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
            closed = bool(entity.closed)
        elif entity_type == "POLYLINE":
            points = [
                (float(vertex.dxf.location.x), float(vertex.dxf.location.y))
                for vertex in entity.vertices
            ]
            closed = bool(entity.is_closed)
        else:
            continue

        if closed and len(points) >= 3:
            if points[0] != points[-1]:
                points.append(points[0])
            shapes.append(
                DxfShape(
                    layer=str(entity.dxf.layer),
                    entity_type=entity_type,
                    points=points,
                )
            )
    return shapes
