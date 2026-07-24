from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import ezdxf


@dataclass(slots=True)
class DxfShape:
    layer: str
    entity_type: str
    points: list[tuple[float, float]]


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
