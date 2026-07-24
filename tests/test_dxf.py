from pathlib import Path

import ezdxf

from firepanel.dxf import read_closed_shapes, read_linework


def test_dxf_linework_and_closed_zone_shapes(tmp_path: Path) -> None:
    path = tmp_path / "floor.dxf"
    document = ezdxf.new()
    modelspace = document.modelspace()
    modelspace.add_line((0, 0), (10, 0), dxfattribs={"layer": "WALLS"})
    modelspace.add_lwpolyline(
        [(0, 0), (10, 0), (10, 10), (0, 10)],
        close=True,
        dxfattribs={"layer": "ZONES"},
    )
    modelspace.add_circle((5, 5), 2, dxfattribs={"layer": "DEVICES"})
    document.saveas(path)

    linework = read_linework(path)
    assert {item.entity_type for item in linework} == {"LINE", "LWPOLYLINE", "CIRCLE"}
    shapes = read_closed_shapes(path)
    assert len(shapes) == 1
    assert shapes[0].layer == "ZONES"
