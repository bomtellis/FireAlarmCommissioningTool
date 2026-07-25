from pathlib import Path

import ezdxf

from firepanel.dxf import read_closed_shapes, read_layers, read_linework, read_text


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
    modelspace.add_lwpolyline(
        [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)],
        close=False,
        dxfattribs={"layer": "ZONES_UNFLAGGED"},
    )
    modelspace.add_circle((5, 5), 2, dxfattribs={"layer": "DEVICES"})
    modelspace.add_leader(
        [(0, 20), (5, 25)],
        dxfattribs={"layer": "ANNOTATIONS"},
    )
    block = document.blocks.new("BAD_SCALE_ARROW")
    block.add_line((0, 0), (5, 0))
    modelspace.add_blockref(
        "BAD_SCALE_ARROW",
        (40, 40),
        dxfattribs={
            "layer": "ANNOTATIONS",
            "xscale": 0,
            "yscale": 1,
        },
    )
    modelspace.add_text(
        "Ward 1",
        height=2.5,
        rotation=30,
        dxfattribs={"layer": "LABELS"},
    ).set_placement((2, 3))
    document.saveas(path)

    linework = read_linework(path)
    assert {item.entity_type for item in linework} == {
        "LINE",
        "LWPOLYLINE",
        "CIRCLE",
        "SOLID",
    }
    shapes = read_closed_shapes(path)
    assert len(shapes) == 2
    assert shapes[0].layer == "ZONES"
    assert shapes[1].layer == "ZONES_UNFLAGGED"
    labels = read_text(path)
    assert len(labels) == 1
    assert labels[0].text == "Ward 1"
    assert (labels[0].x, labels[0].y) == (2.0, 3.0)
    assert labels[0].rotation == 30.0
    assert read_layers(path) == [
        "ANNOTATIONS",
        "DEVICES",
        "LABELS",
        "WALLS",
        "ZONES",
        "ZONES_UNFLAGGED",
    ]


def test_dxf_text_uses_effective_rotation_and_text_style(tmp_path: Path) -> None:
    path = tmp_path / "styled-text.dxf"
    document = ezdxf.new()
    style = document.styles.add(
        "ARCHITECTURAL",
        font="missing-architectural-font.shx",
        dxfattribs={
            "width": 0.8,
            "oblique": 12,
        },
    )
    style.set_extended_font_data(
        "Unavailable Architectural Font",
        italic=True,
        bold=True,
    )
    label = document.modelspace().add_mtext(
        "Rotated room",
        dxfattribs={
            "layer": "LABELS",
            "style": "ARCHITECTURAL",
            "char_height": 4,
        },
    )
    label.dxf.insert = (25, 40)
    label.dxf.text_direction = (0, 1, 0)
    document.saveas(path)

    parsed = read_text(path)

    assert len(parsed) == 1
    assert parsed[0].rotation == 90
    assert parsed[0].style_name == "ARCHITECTURAL"
    assert parsed[0].font_family == "Unavailable Architectural Font"
    assert parsed[0].font_file == "missing-architectural-font.shx"
    assert parsed[0].bold
    assert parsed[0].italic
    assert parsed[0].width_factor == 0.8
    assert parsed[0].oblique == 12
