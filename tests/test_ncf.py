import json
import struct
import zipfile
from pathlib import Path

from firepanel.device_catalog import (
    CATALOGUE_CODES_BY_PROTOCOL,
    KNOWN_CATALOGUE_CODES,
    catalogue_display_name,
    protocols_for_code,
)
from firepanel.ncf import (
    parse_configuration,
    parse_ncf,
    read_configuration_cause_effect,
)
from firepanel.project import ProjectRepository


ROOT = Path(__file__).resolve().parents[1]


def _write_skf_table(
    archive: zipfile.ZipFile,
    name: str,
    rows: list[dict],
    *,
    current: dict[int, dict] | None = None,
) -> None:
    row_list = []
    for row_id, row in enumerate(rows):
        stored = {"RowID": row_id, "Original": row}
        if current and row_id in current:
            stored["Current"] = current[row_id]
        row_list.append(stored)
    document = {
        "FDBS": {
            "Version": 16,
            "Manager": {
                "UpdatesRegistry": True,
                "TableList": [
                    {
                        "class": "Table",
                        "Name": Path(name).stem,
                        "ColumnList": [],
                        "RowList": row_list,
                    }
                ],
                "RelationList": [],
            },
        }
    }
    archive.writestr(name, json.dumps(document))


def test_legacy_ncf_container_still_uses_binary_parser(tmp_path: Path) -> None:
    path = tmp_path / "legacy-format.ncf"
    site = bytearray(336)
    panel_name = b"Panel A"
    site[112 + 8] = 0x12
    site[112 + 9] = len(panel_name)
    site[112 + 10 : 112 + 10 + len(panel_name)] = panel_name
    site[112 + 44] = 5
    struct.pack_into("<i", site, 224 + 8, 3)
    struct.pack_into("<i", site, 224 + 12, 7)
    zone_name = b"Legacy zone"
    site[224 + 16] = len(zone_name)
    site[224 + 17 : 224 + 17 + len(zone_name)] = zone_name

    point = bytearray(224)
    struct.pack_into("<i", point, 8, 2)
    struct.pack_into("<i", point, 12, 45)
    point[16] = 1
    point[17] = 4
    point[18] = 2
    location = b"Legacy detector"
    point[20] = len(location)
    point[21 : 21 + len(location)] = location
    struct.pack_into("<i", point, 48, 7)

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SITE", site)
        archive.writestr("Panel A.pcf", point)

    parsed = parse_configuration(path)
    assert len(parsed.panels) == 1
    assert parsed.panels[0].node == 5
    assert parsed.panels[0].name == "Panel A"
    assert parsed.panels[0].point_sub_records == 1
    assert len(parsed.devices) == 1
    assert parsed.devices[0].stable_key == "5/2/4/1"
    assert parsed.devices[0].text == "Legacy detector"
    assert parsed.devices[0].observed_type == "Optical Smoke"
    assert parsed.zones[0].description == "Legacy zone"


def test_skf_json_tables_are_parsed_without_changing_legacy_api(tmp_path: Path) -> None:
    path = tmp_path / "new-format.skf"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_skf_table(
            archive,
            "tblNode.json",
            [
                {"NetworkAddress": 52, "NodeName": "Old panel name"},
                {"NetworkAddress": 96, "NodeName": "Control repeater"},
            ],
            current={0: {"NodeName": "Main Entrance Panel 1"}},
        )
        _write_skf_table(
            archive,
            "tblPoint.json",
            [
                {
                    "NetworkAddress": 52,
                    "LoopNumber": 1,
                    "LoopAddress": 1,
                    "SubAddress": 1,
                    "ZoneNumber": 179,
                    "DeviceID": 39,
                    "Measurement": 2,
                    "OutputGroup": 0,
                    "Location": "DUCT                ROOM 4",
                },
                {
                    "NetworkAddress": 52,
                    "LoopNumber": 1,
                    "LoopAddress": 41,
                    "SubAddress": 1,
                    "ZoneNumber": 178,
                    "DeviceID": 26,
                    "Measurement": 11,
                    "OutputGroup": 0,
                    "Location": "Spare Input",
                },
                {
                    "NetworkAddress": 52,
                    "LoopNumber": 1,
                    "LoopAddress": 41,
                    "SubAddress": 3,
                    "ZoneNumber": 178,
                    "DeviceID": 26,
                    "Measurement": 14,
                    "OutputGroup": 50,
                    "Location": "DOOR ACCESS RMO STAIRCASE",
                },
                {
                    "NetworkAddress": 52,
                    "LoopNumber": -90,
                    "LoopAddress": 1,
                    "SubAddress": 1,
                    "ZoneNumber": 179,
                    "DeviceID": 12,
                    "Measurement": 12,
                    "OutputGroup": 0,
                    "Location": "Internal panel sounder",
                },
            ],
        )
        _write_skf_table(
            archive,
            "tblZone.json",
            [
                {"ZoneNumber": 178, "ZoneText": "PATH LAB GROUND FLOOR"},
                {"ZoneNumber": 179, "ZoneText": "PATH LAB FIRST FLOOR"},
            ],
        )
        _write_skf_table(
            archive,
            "tblOutputGroup.json",
            [
                {
                    "NetworkAddress": 52,
                    "GroupNo": 50,
                    "GroupText": "PATHOLOGY ACCESS DOORS",
                },
                {
                    "NetworkAddress": 52,
                    "GroupNo": 51,
                    "GroupText": "PANEL SOUNDERS",
                },
            ],
        )
        _write_skf_table(
            archive,
            "tblOutputGroupLine.json",
            [
                {
                    "NetworkAddress": 52,
                    "GroupNo": 50,
                    "Operation": 0,
                    "OutputStyleNo": 0,
                    "ZoneFrom": 179,
                    "ZoneTo": 179,
                    "ZoneQualifiers": 2049,
                },
                {
                    "NetworkAddress": 52,
                    "GroupNo": 51,
                    "Operation": 0,
                    "OutputStyleNo": 1,
                    "ZoneFrom": 178,
                    "ZoneTo": 179,
                    "ZoneQualifiers": 1024,
                },
            ],
        )
        _write_skf_table(
            archive,
            "tblRingingStyle.json",
            [
                {
                    "NetworkAddress": 52,
                    "StyleNumber": 0,
                    "Description": "Evacuate",
                },
                {
                    "NetworkAddress": 52,
                    "StyleNumber": 1,
                    "Description": "Alert",
                },
            ],
        )

    parsed = parse_configuration(path)
    assert parse_ncf(path).sha256 == parsed.sha256
    assert parsed.versions == ["SKF FDBS 16"]
    assert len(parsed.panels) == 2
    assert len(parsed.devices) == 2
    assert sum(panel.point_sub_records for panel in parsed.panels) == 3

    node_52 = next(panel for panel in parsed.panels if panel.node == 52)
    assert node_52.name == "Main Entrance Panel 1"
    assert node_52.loops == [1]
    detector = next(device for device in node_52.devices if device.address == 1)
    assert detector.text == "DUCT                ROOM 4"
    assert detector.zone == 179
    assert detector.observed_type == "Optical Smoke"

    module = next(device for device in node_52.devices if device.address == 41)
    assert [channel.sub_address for channel in module.channels] == [1, 3]
    output = module.channels[1]
    assert output.output_group == 50
    assert output.output_group_name == "PATHOLOGY ACCESS DOORS"
    assert output.ringing_style == "Evacuate"
    assert output.observed_type == "Relay"

    zone_179 = next(zone for zone in parsed.zones if zone.number == 179)
    assert zone_179.description == "PATH LAB FIRST FLOOR"
    assert [
        (
            line.output_group,
            line.zone_from,
            line.zone_to,
            line.ringing_style,
            line.ringing_style_name,
            line.zone_qualifiers,
        )
        for line in parsed.output_group_lines
    ] == [
        (50, 179, 179, "E", "Evacuate", 2049),
        (51, 178, 179, "A", "Alert", 1024),
    ]

    cause_effect = read_configuration_cause_effect(path, [178, 179])
    assert [(row.target_node, row.output_group) for row in cause_effect.output_groups] == [
        (52, 50),
        (52, 51),
    ]
    assert len(cause_effect.activations) == 3
    activation = next(
        row
        for row in cause_effect.activations
        if row.output_group == 50
    )
    assert activation.trigger_zone == "179"
    assert activation.ringing_style == "E"
    assert activation.ringing_style_name == "Evacuate"
    assert activation.zone_qualifiers == 2049
    assert {
        (row.trigger_zone, row.output_group, row.ringing_style)
        for row in cause_effect.activations
    } == {
        ("178", 51, "A"),
        ("179", 50, "E"),
        ("179", 51, "A"),
    }

    repository = ProjectRepository.create(
        tmp_path / "skf-project.fcp",
        "SKF project",
        path,
    )
    persisted_lines = repository.fetch_configuration_output_group_lines()
    assert [
        (
            row["output_group"],
            row["zone_from"],
            row["zone_to"],
            row["ringing_style"],
        )
        for row in persisted_lines
    ] == [
        (50, 179, 179, "E"),
        (51, 178, 179, "A"),
    ]
    assert {
        (
            row["trigger_zone"],
            row["output_group"],
            row["ringing_style"],
        )
        for row in repository.fetch_cause_effect_activations(178)
    } == {("178", 51, "A")}
    panel_group = next(
        row
        for row in repository.fetch_output_groups()
        if row["node"] == 52 and row["output_group"] == 51
    )
    assert panel_group["device_count"] == 0
    assert panel_group["group_name"] == "PANEL SOUNDERS"
    repository.replace_output_group_zone_assignments(
        52,
        51,
        [(178, "SOUNDER"), (179, "BEACON")],
    )
    assert [
        (row["zone"], row["output_kind"])
        for row in repository.fetch_output_group_zone_assignments(52, 51)
    ] == [(178, "SOUNDER"), (179, "BEACON")]


def test_supplied_ncf_inventory() -> None:
    parsed = parse_ncf(ROOT / "Leighton-Site.NCF")
    assert len(parsed.panels) == 61
    assert sum(len(panel.devices) for panel in parsed.panels) == 7777
    assert sum(panel.point_sub_records for panel in parsed.panels) == 9965

    node_52 = next(panel for panel in parsed.panels if panel.node == 52)
    assert node_52.name == "Main Entrance Panel 1"
    assert len(node_52.devices) == 248
    first = next(device for device in node_52.devices if device.loop == 1 and device.address == 1)
    assert first.zone == 179
    assert first.text == "DUCT                ROOM 4"
    assert first.observed_type == "Optical Smoke"
    zone_179 = next(zone for zone in parsed.zones if zone.number == 179)
    assert zone_179.description == "PATH LAB FIRST FLOOR"


def test_multi_channel_module_is_one_physical_device() -> None:
    parsed = parse_ncf(ROOT / "Leighton-Site.NCF")
    node_52 = next(panel for panel in parsed.panels if panel.node == 52)
    module = next(device for device in node_52.devices if device.loop == 1 and device.address == 41)
    assert len(module.channels) == 3
    assert [channel.sub_address for channel in module.channels] == [1, 2, 3]
    output = module.channels[2]
    assert output.output_group == 50
    assert output.output_group_name == "PATHOLOGY ACCESS DOORS"
    assert output.ringing_style == "Evacuate"


def test_supplied_protocol_catalogue_code_inventory() -> None:
    assert {name: len(codes) for name, codes in CATALOGUE_CODES_BY_PROTOCOL.items()} == {
        "Apollo": 63,
        "Hochiki": 29,
        "Argus/Vega": 22,
        "Nittan": 18,
    }
    assert len(KNOWN_CATALOGUE_CODES) == 101
    assert protocols_for_code(366) == ("Apollo",)
    assert protocols_for_code(154) == ("Apollo", "Hochiki", "Argus/Vega", "Nittan")
    assert catalogue_display_name(18) == "Apollo XP95 Ionisation Smoke Detector"
    assert catalogue_display_name(4) == "ConfigTool catalogue code 4"
