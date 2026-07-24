from pathlib import Path

from firepanel.device_catalog import (
    CATALOGUE_CODES_BY_PROTOCOL,
    KNOWN_CATALOGUE_CODES,
    catalogue_display_name,
    protocols_for_code,
)
from firepanel.ncf import parse_ncf


ROOT = Path(__file__).resolve().parents[1]


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
