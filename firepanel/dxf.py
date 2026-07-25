from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path

import ezdxf


@lru_cache(maxsize=12)
def _cached_document(path_text: str, modified_ns: int):
    del modified_ns
    return ezdxf.readfile(path_text)


def _document(path: str | Path):
    source = Path(path).resolve()
    return _cached_document(str(source), source.stat().st_mtime_ns)


def _expanded_entities(entity, depth: int = 0):
    """Expand compound annotation/block entities into drawable primitives."""
    entity_type = entity.dxftype()
    if depth < 4 and entity_type in {
        "LEADER",
        "MLEADER",
        "MULTILEADER",
        "INSERT",
        "DIMENSION",
    }:
        try:
            virtual = list(entity.virtual_entities())
        except (
            ArithmeticError,
            AttributeError,
            NotImplementedError,
            TypeError,
            ValueError,
        ):
            virtual = []
        if virtual:
            for child in virtual:
                yield from _expanded_entities(child, depth + 1)
            return
    yield entity


def _geometrically_closed(
    points: list[tuple[float, float]],
    flagged_closed: bool,
) -> bool:
    if flagged_closed:
        return True
    if len(points) < 3:
        return False
    x_span = max(point[0] for point in points) - min(point[0] for point in points)
    y_span = max(point[1] for point in points) - min(point[1] for point in points)
    tolerance = max(1e-6, math.hypot(x_span, y_span) * 1e-5)
    return math.dist(points[0], points[-1]) <= tolerance


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


@dataclass(slots=True)
class DxfText:
    layer: str
    text: str
    x: float
    y: float
    height: float
    rotation: float


def read_text(path: str | Path) -> list[DxfText]:
    """Read model-space TEXT and MTEXT labels from a DXF drawing."""
    document = _document(path)
    result: list[DxfText] = []
    for source_entity in document.modelspace():
        for entity in _expanded_entities(source_entity):
            entity_type = entity.dxftype()
            if entity_type not in {"TEXT", "MTEXT"}:
                continue
            insert = entity.dxf.insert
            if entity_type == "MTEXT":
                text = entity.plain_text()
                height = float(entity.dxf.char_height or 2.5)
            else:
                text = str(entity.dxf.text or "")
                height = float(entity.dxf.height or 2.5)
            if not text.strip():
                continue
            result.append(
                DxfText(
                    layer=str(entity.dxf.layer),
                    text=text,
                    x=float(insert.x),
                    y=float(insert.y),
                    height=max(height, 0.01),
                    rotation=float(entity.dxf.rotation or 0.0),
                )
            )
    return result


def read_layers(path: str | Path) -> list[str]:
    """Return model-space layers that contain renderable geometry or text."""
    document = _document(path)
    supported = {
        "LINE",
        "LWPOLYLINE",
        "POLYLINE",
        "CIRCLE",
        "ARC",
        "TEXT",
        "MTEXT",
        "LEADER",
        "MLEADER",
        "MULTILEADER",
        "INSERT",
        "DIMENSION",
        "SOLID",
        "TRACE",
    }
    return sorted(
        {
            str(entity.dxf.layer)
            for entity in document.modelspace()
            if entity.dxftype() in supported
        },
        key=str.casefold,
    )


def read_linework(path: str | Path) -> list[DxfLinework]:
    """Read common 2D DXF entities for use as a map underlay."""
    document = _document(path)
    result: list[DxfLinework] = []
    for source_entity in document.modelspace():
        for entity in _expanded_entities(source_entity):
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
                closed = _geometrically_closed(points, bool(entity.closed))
            elif entity_type == "POLYLINE":
                points = [
                    (float(vertex.dxf.location.x), float(vertex.dxf.location.y))
                    for vertex in entity.vertices
                ]
                closed = _geometrically_closed(points, bool(entity.is_closed))
            elif entity_type in {"SOLID", "TRACE", "3DFACE"}:
                points = [
                    (float(getattr(entity.dxf, name).x), float(getattr(entity.dxf, name).y))
                    for name in ("vtx0", "vtx1", "vtx2", "vtx3")
                    if entity.dxf.hasattr(name)
                ]
                closed = len(points) >= 3
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
    document = _document(path)
    modelspace = document.modelspace()
    shapes: list[DxfShape] = []

    for entity in modelspace:
        entity_type = entity.dxftype()
        points: list[tuple[float, float]] = []
        closed = False
        if entity_type == "LWPOLYLINE":
            points = [(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
            closed = _geometrically_closed(points, bool(entity.closed))
        elif entity_type == "POLYLINE":
            points = [
                (float(vertex.dxf.location.x), float(vertex.dxf.location.y))
                for vertex in entity.vertices
            ]
            closed = _geometrically_closed(points, bool(entity.is_closed))
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
