from __future__ import annotations

import hashlib
import json
import re
import struct
import zipfile
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .device_catalog import (
    CONFIRMED_GENERIC_TYPES,
    KNOWN_CATALOGUE_CODES,
    protocols_for_code,
)
from .models import Device, DeviceChannel, Panel, ParsedNcf, Zone


POINT_RECORD_SIZE = 224
SITE_RECORD_OFFSET = 112
SITE_RECORD_SIZE = 112
SKF_REQUIRED_TABLES = {
    "tblNode.json",
    "tblPoint.json",
    "tblZone.json",
}

# SKF point records expose the point measurement separately from the product
# identifier. These values are confirmed by matching the same Leighton devices
# in the legacy NCF and the supplied SKF export.
SKF_MEASUREMENT_TYPES = {
    1: "Ionisation Smoke",
    2: "Optical Smoke",
    3: "Multisensor",
    4: "Heat Detector",
    6: "Call Point",
    11: "Input Module",
    12: "Sounder",
    14: "Relay",
    21: "Input Module",
    27: "Visual Alarm Device",
}


@dataclass(frozen=True, slots=True)
class ConfigurationCauseEffectOutput:
    target_node: int
    target_node_name: str
    output_group: int
    output_group_name: str


@dataclass(frozen=True, slots=True)
class ConfigurationCauseEffectActivation:
    trigger_zone: str
    target_node: int
    target_node_name: str
    output_group: int
    output_group_name: str
    ringing_style: str
    ringing_style_name: str
    zone_qualifiers: int
    source_row: int


@dataclass(slots=True)
class ConfigurationCauseEffect:
    source: Path
    format_name: str
    output_groups: list[ConfigurationCauseEffectOutput] = field(default_factory=list)
    activations: list[ConfigurationCauseEffectActivation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _ascii_slot(data: bytes, length_offset: int, maximum: int) -> str:
    if length_offset < 0 or length_offset >= len(data):
        return ""
    length = data[length_offset]
    if length > maximum or length_offset + 1 + length > len(data):
        return ""
    value = data[length_offset + 1 : length_offset + 1 + length]
    if not all(32 <= byte <= 126 for byte in value):
        return ""
    return value.decode("ascii", errors="replace").strip()


def _parse_output_groups(data: bytes) -> tuple[dict[int, str], dict[int, str]]:
    """Decode ConfigTool ringing styles and output-rule group references."""
    style_names: dict[int, str] = {}
    style_signature = struct.pack("<i", 33)
    cursor = 0
    while True:
        marker = data.find(style_signature, cursor)
        if marker < 0:
            break
        cursor = marker + 1
        start = marker - 8
        if start < 26 or start + 16 > len(data):
            continue
        # Ringing-style records are 224 bytes apart. The style label occupies
        # the final 26-byte string slot immediately before its record header.
        neighbouring_record = (
            (start >= 224 and _i32(data, start - 224 + 8) == 33)
            or (start + 224 + 12 <= len(data) and _i32(data, start + 224 + 8) == 33)
        )
        style_number = _i32(data, start + 12)
        name = _ascii_slot(data, start - 26, 24)
        if neighbouring_record and 0 <= style_number <= 255 and name:
            style_names[style_number] = name

    group_names: dict[int, str] = {}
    group_style_numbers: dict[int, set[int]] = {}
    rule_signature = struct.pack("<i", 9)
    cursor = 0
    while True:
        marker = data.find(rule_signature, cursor)
        if marker < 0:
            break
        cursor = marker + 1
        start = marker - 8
        if start < 0 or start + 112 > len(data):
            continue
        neighbouring_record = (
            (start >= 112 and _i32(data, start - 112 + 8) == 9)
            or (start + 112 + 12 <= len(data) and _i32(data, start + 112 + 8) == 9)
        )
        if not neighbouring_record:
            continue
        group = _i32(data, start + 12)
        style_number = _i32(data, start + 24)
        name = _ascii_slot(data, start + 71, 39)
        if not (0 < group <= 65535):
            continue
        if name and group not in group_names:
            group_names[group] = name
        if style_number in style_names:
            group_style_numbers.setdefault(group, set()).add(style_number)

    group_styles = {
        group: ", ".join(style_names[number] for number in sorted(numbers))
        for group, numbers in group_style_numbers.items()
    }
    return group_names, group_styles


def _is_point_record(data: bytes, offset: int) -> bool:
    if offset < 0 or offset + POINT_RECORD_SIZE > len(data):
        return False
    if _i32(data, offset + 8) != 2:
        return False

    channel = data[offset + 16]
    address = data[offset + 17]
    loop = data[offset + 18]
    text_length = data[offset + 20]
    if not (1 <= channel <= 16):
        return False
    if not (1 <= address <= 126):
        return False
    if not (1 <= loop <= 200):
        return False
    if text_length > 27:
        return False
    return all(32 <= value <= 126 for value in data[offset + 21 : offset + 21 + text_length])


def _find_point_table(data: bytes) -> tuple[int | None, int]:
    """Locate the longest consecutive run of known 224-byte point records."""
    signature = struct.pack("<i", 2)
    best_offset: int | None = None
    best_count = 0
    cursor = 0
    visited: set[int] = set()

    while True:
        marker = data.find(signature, cursor)
        if marker < 0:
            break
        cursor = marker + 1
        offset = marker - 8
        if offset in visited or not _is_point_record(data, offset):
            continue

        count = 0
        while _is_point_record(data, offset + count * POINT_RECORD_SIZE):
            visited.add(offset + count * POINT_RECORD_SIZE)
            count += 1
        if count > best_count:
            best_offset = offset
            best_count = count

    return best_offset, best_count


def _parse_site(data: bytes) -> tuple[list[tuple[int, str]], dict[int, str]]:
    nodes: list[tuple[int, str]] = []
    index = 0
    while True:
        offset = SITE_RECORD_OFFSET + index * SITE_RECORD_SIZE
        if offset + SITE_RECORD_SIZE > len(data):
            break
        if data[offset + 8] != 0x12:
            break
        name_length = data[offset + 9]
        if name_length > 32:
            break
        name = data[offset + 10 : offset + 10 + name_length].decode("ascii", errors="replace")
        node = data[offset + 44]
        nodes.append((node, name))
        index += 1

    # ConfigTool stores the network zone-name table immediately after the
    # consecutive node records. It uses the same 112-byte envelope: record
    # class 3 at +8, signed zone number at +12, then a length-prefixed ASCII
    # description at +16/+17.
    zone_names: dict[int, str] = {}
    offset = SITE_RECORD_OFFSET + len(nodes) * SITE_RECORD_SIZE
    while offset + SITE_RECORD_SIZE <= len(data):
        if _i32(data, offset + 8) != 3:
            break
        zone_number = _i32(data, offset + 12)
        name_length = data[offset + 16]
        if zone_number <= 0 or name_length > 80:
            break
        name = data[offset + 17 : offset + 17 + name_length].decode(
            "ascii", errors="replace"
        ).strip()
        zone_names[zone_number] = name
        offset += SITE_RECORD_SIZE
    return nodes, zone_names


def _parse_panel(node: int, name: str, data: bytes) -> Panel:
    table_offset, record_count = _find_point_table(data)
    group_names, group_styles = _parse_output_groups(data)
    device_map: OrderedDict[tuple[int, int], Device] = OrderedDict()

    if table_offset is not None:
        for index in range(record_count):
            offset = table_offset + index * POINT_RECORD_SIZE
            sub_address = data[offset + 16]
            address = data[offset + 17]
            loop = data[offset + 18]
            text_length = data[offset + 20]
            text = data[offset + 21 : offset + 21 + text_length].decode("ascii", errors="replace").rstrip()
            zone = _i32(data, offset + 48)
            product_code = _i32(data, offset + 12)
            observed_type = CONFIRMED_GENERIC_TYPES.get(product_code)
            output_group = _u16(data, offset + 60) or None
            channel = DeviceChannel(
                sub_address=sub_address,
                text=text,
                zone=zone,
                product_code=product_code,
                observed_type=observed_type,
                output_group=output_group,
                output_group_name=group_names.get(output_group),
                ringing_style=group_styles.get(output_group),
                record_offset=offset,
            )

            key = (loop, address)
            if key not in device_map:
                device_map[key] = Device(
                    node=node,
                    panel=name,
                    loop=loop,
                    address=address,
                    sub_address=sub_address,
                    text=text,
                    zone=zone,
                    product_code=product_code,
                    observed_type=observed_type,
                    output_group=output_group,
                    output_group_name=group_names.get(output_group),
                    ringing_style=group_styles.get(output_group),
                )
            device_map[key].channels.append(channel)

    return Panel(
        node=node,
        name=name,
        pcf_bytes=len(data),
        loops=sorted({device.loop for device in device_map.values()}),
        devices=list(device_map.values()),
        point_table_offset=table_offset,
        point_sub_records=record_count,
    )


def _parse_legacy_ncf(source: Path, digest: str) -> ParsedNcf:
    warnings: list[str] = []

    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if "SITE" not in names:
            raise ValueError("This archive does not contain the expected SITE record.")
        site_data = archive.read("SITE")
        versions = list(dict.fromkeys(re.findall(r"\d+\.\d+", site_data.decode("ascii", errors="ignore"))))
        site_nodes, site_zone_names = _parse_site(site_data)

        pcf_names = {
            Path(name).stem.casefold(): name
            for name in names
            if name.lower().endswith(".pcf")
        }
        panels: list[Panel] = []
        for node, panel_name in site_nodes:
            entry_name = pcf_names.get(panel_name.casefold())
            if entry_name is None:
                warnings.append(f"Node {node} ({panel_name}) has no matching PCF entry.")
                panels.append(Panel(node=node, name=panel_name, pcf_bytes=0))
                continue
            panels.append(_parse_panel(node, panel_name, archive.read(entry_name)))

        zone_numbers = sorted({device.zone for panel in panels for device in panel.devices if device.zone > 0})
        zones = [
            Zone(number=number, description=site_zone_names.get(number, ""))
            for number in zone_numbers
        ]
        entries = [
            {
                "name": info.filename,
                "uncompressed_bytes": info.file_size,
                "compressed_bytes": info.compress_size,
            }
            for info in archive.infolist()
        ]

    unlabelled_catalogue_codes = sorted(
        {
            device.product_code
            for panel in panels
            for device in panel.devices
            if device.observed_type is None
            and device.product_code in KNOWN_CATALOGUE_CODES
        }
    )
    unknown_codes = sorted(
        {
            device.product_code
            for panel in panels
            for device in panel.devices
            if device.observed_type is None
            and device.product_code not in KNOWN_CATALOGUE_CODES
        }
    )
    if unlabelled_catalogue_codes:
        details = ", ".join(
            f"{code} ({'/'.join(protocols_for_code(code))})"
            for code in unlabelled_catalogue_codes
        )
        warnings.append(
            "Recognised ConfigTool catalogue codes awaiting protocol-specific "
            f"model labels: {details}"
        )
    if unknown_codes:
        warnings.append(
            "Product codes not present in the supplied catalogues: "
            + ", ".join(str(value) for value in unknown_codes)
        )
    warnings.append(
        "Native point output groups and configured ringing styles were decoded. "
        "Cause/effect logic remains editable project data pending full rule decoding."
    )
    return ParsedNcf(
        source=source,
        sha256=digest,
        versions=versions,
        panels=panels,
        zones=zones,
        archive_entries=entries,
        warnings=warnings,
    )


def _skf_table(
    archive: zipfile.ZipFile, entry_name: str
) -> tuple[int | None, list[tuple[int, dict[str, Any]]]]:
    """Read one FireDAC JSON table from an SKF archive."""
    try:
        document = json.loads(archive.read(entry_name).decode("utf-8-sig"))
        fdbs = document["FDBS"]
        tables = fdbs["Manager"]["TableList"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{entry_name} is not a valid SKF FireDAC table.") from error

    expected_name = Path(entry_name).stem.casefold()
    table = next(
        (
            candidate
            for candidate in tables
            if str(candidate.get("Name", "")).casefold() == expected_name
        ),
        None,
    )
    if table is None:
        raise ValueError(f"{entry_name} does not contain the expected {Path(entry_name).stem} table.")

    rows: list[tuple[int, dict[str, Any]]] = []
    for index, stored_row in enumerate(table.get("RowList", [])):
        if not isinstance(stored_row, dict):
            continue
        state = str(stored_row.get("RowState", "")).casefold()
        if state in {"deleted", "delete"}:
            continue
        row: dict[str, Any] = {}
        original = stored_row.get("Original")
        current = stored_row.get("Current")
        if isinstance(original, dict):
            row.update(original)
        if isinstance(current, dict):
            row.update(current)
        if row:
            rows.append((int(stored_row.get("RowID", index)), row))
    version = fdbs.get("Version")
    return (int(version) if isinstance(version, int) else None), rows


def _skf_integer(row: dict[str, Any], key: str, default: int = 0) -> int:
    value = row.get(key, default)
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def read_configuration_cause_effect(
    path: str | Path,
    trigger_zones: Iterable[object] = (),
) -> ConfigurationCauseEffect:
    """Read zone-based output rules without changing the source configuration."""
    source = Path(path).resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
        if "SITE" in names:
            return ConfigurationCauseEffect(
                source=source,
                format_name="Legacy NCF",
                warnings=[
                    "Legacy NCF cause/effect rule records are not yet decoded. "
                    "Known configured output groups are included without inferred "
                    "trigger relationships."
                ],
            )
        required = {
            "tblNode.json",
            "tblOutputGroup.json",
            "tblOutputGroupLine.json",
            "tblRingingStyle.json",
        }
        missing = sorted(required - names)
        if missing:
            return ConfigurationCauseEffect(
                source=source,
                format_name="SKF",
                warnings=[
                    "Configuration cause/effect export is missing required SKF "
                    "tables: " + ", ".join(missing)
                ],
            )

        _, node_rows = _skf_table(archive, "tblNode.json")
        _, output_rows = _skf_table(archive, "tblOutputGroup.json")
        _, line_rows = _skf_table(archive, "tblOutputGroupLine.json")
        _, style_rows = _skf_table(archive, "tblRingingStyle.json")

    node_names = {
        _skf_integer(row, "NetworkAddress"): str(
            row.get("NodeName") or ""
        ).strip()
        for _, row in node_rows
        if _skf_integer(row, "NetworkAddress") > 0
    }
    output_names = {
        (
            _skf_integer(row, "NetworkAddress"),
            _skf_integer(row, "GroupNo"),
        ): str(row.get("GroupText") or "").strip()
        for _, row in output_rows
        if _skf_integer(row, "NetworkAddress") > 0
        and _skf_integer(row, "GroupNo") > 0
    }
    style_names = {
        (
            _skf_integer(row, "NetworkAddress"),
            _skf_integer(row, "StyleNumber"),
        ): str(row.get("Description") or "").strip()
        for _, row in style_rows
        if _skf_integer(row, "NetworkAddress") > 0
    }
    outputs = [
        ConfigurationCauseEffectOutput(
            target_node=node,
            target_node_name=node_names.get(node, ""),
            output_group=group,
            output_group_name=name,
        )
        for (node, group), name in sorted(output_names.items())
    ]

    numeric_zones: set[int] = set()
    for value in trigger_zones:
        try:
            parsed = float(str(value).strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed.is_integer():
            numeric_zones.add(int(parsed))

    activations: list[ConfigurationCauseEffectActivation] = []
    ignored_lines = 0
    for source_row, row in line_rows:
        node = _skf_integer(row, "NetworkAddress")
        group = _skf_integer(row, "GroupNo")
        zone_from = _skf_integer(row, "ZoneFrom")
        zone_to = _skf_integer(row, "ZoneTo")
        operation = _skf_integer(row, "Operation", -1)
        if (
            node <= 0
            or group <= 0
            or operation != 0
            or zone_from <= 0
            or zone_to < zone_from
        ):
            ignored_lines += 1
            continue
        style_number = _skf_integer(row, "OutputStyleNo", -1)
        style_name = style_names.get(
            (node, style_number),
            f"Style {style_number}",
        )
        style_code = _ringing_style_code(style_name)
        for zone in sorted(
            value for value in numeric_zones if zone_from <= value <= zone_to
        ):
            activations.append(
                ConfigurationCauseEffectActivation(
                    trigger_zone=str(zone),
                    target_node=node,
                    target_node_name=node_names.get(node, ""),
                    output_group=group,
                    output_group_name=output_names.get((node, group), ""),
                    ringing_style=style_code,
                    ringing_style_name=style_name,
                    zone_qualifiers=_skf_integer(row, "ZoneQualifiers"),
                    source_row=source_row,
                )
            )

    warnings = []
    if ignored_lines:
        warnings.append(
            f"{ignored_lines:,} non-zone or unsupported SKF output-group lines "
            "were excluded from the matrix comparison."
        )
    return ConfigurationCauseEffect(
        source=source,
        format_name="SKF",
        output_groups=outputs,
        activations=activations,
        warnings=warnings,
    )


def _ringing_style_code(description: str) -> str:
    normalised = " ".join(description.casefold().split())
    if "timed" in normalised and "alert" in normalised:
        return "TA"
    if "timed" in normalised and "evacuate" in normalised:
        return "TE"
    if "evacuate" in normalised:
        return "E"
    if "alert" in normalised:
        return "A"
    return description.strip() or "Not specified"


def _parse_skf(source: Path, digest: str) -> ParsedNcf:
    warnings: list[str] = []
    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
        missing = sorted(SKF_REQUIRED_TABLES - names)
        if missing:
            raise ValueError(
                "This SKF archive is missing required tables: " + ", ".join(missing)
            )

        table_version, node_rows = _skf_table(archive, "tblNode.json")
        _, point_rows = _skf_table(archive, "tblPoint.json")
        _, zone_rows = _skf_table(archive, "tblZone.json")

        output_group_rows: list[tuple[int, dict[str, Any]]] = []
        output_line_rows: list[tuple[int, dict[str, Any]]] = []
        ringing_style_rows: list[tuple[int, dict[str, Any]]] = []
        if "tblOutputGroup.json" in names:
            _, output_group_rows = _skf_table(archive, "tblOutputGroup.json")
        if "tblOutputGroupLine.json" in names:
            _, output_line_rows = _skf_table(archive, "tblOutputGroupLine.json")
        if "tblRingingStyle.json" in names:
            _, ringing_style_rows = _skf_table(archive, "tblRingingStyle.json")

        nodes: list[tuple[int, str]] = []
        for _, row in node_rows:
            node = _skf_integer(row, "NetworkAddress")
            if node <= 0:
                continue
            name = str(row.get("NodeName") or f"Node {node}").strip()
            nodes.append((node, name))
        nodes.sort(key=lambda item: item[0])
        node_names = dict(nodes)

        zone_names = {
            _skf_integer(row, "ZoneNumber"): str(row.get("ZoneText") or "").strip()
            for _, row in zone_rows
            if _skf_integer(row, "ZoneNumber") > 0
        }
        group_names = {
            (
                _skf_integer(row, "NetworkAddress"),
                _skf_integer(row, "GroupNo"),
            ): str(row.get("GroupText") or "").strip()
            for _, row in output_group_rows
            if _skf_integer(row, "NetworkAddress") > 0
            and _skf_integer(row, "GroupNo") > 0
        }
        style_names = {
            (
                _skf_integer(row, "NetworkAddress"),
                _skf_integer(row, "StyleNumber"),
            ): str(row.get("Description") or "").strip()
            for _, row in ringing_style_rows
            if _skf_integer(row, "NetworkAddress") > 0
        }
        group_style_names: dict[tuple[int, int], set[str]] = defaultdict(set)
        for _, row in output_line_rows:
            node = _skf_integer(row, "NetworkAddress")
            group = _skf_integer(row, "GroupNo")
            style_number = _skf_integer(row, "OutputStyleNo", -1)
            style = style_names.get((node, style_number))
            if node > 0 and group > 0 and style:
                group_style_names[(node, group)].add(style)

        panel_devices: dict[int, OrderedDict[tuple[int, int], Device]] = {
            node: OrderedDict() for node, _ in nodes
        }
        panel_point_counts: dict[int, int] = defaultdict(int)
        used_zones: set[int] = set()
        orphan_nodes: set[int] = set()

        for row_id, row in point_rows:
            node = _skf_integer(row, "NetworkAddress")
            loop = _skf_integer(row, "LoopNumber")
            address = _skf_integer(row, "LoopAddress")
            sub_address = _skf_integer(row, "SubAddress", 1)
            if loop <= 0 or address <= 0 or sub_address <= 0:
                # Negative loop numbers are panel peripherals, LEDs and other
                # internal points, not addressable loop devices.
                continue
            if node not in panel_devices:
                orphan_nodes.add(node)
                continue

            zone = _skf_integer(row, "ZoneNumber")
            text = str(row.get("Location") or "").rstrip()
            product_code = _skf_integer(row, "DeviceID")
            measurement = _skf_integer(row, "Measurement", -1)
            observed_type = SKF_MEASUREMENT_TYPES.get(measurement)
            output_group = _skf_integer(row, "OutputGroup") or None
            group_key = (node, output_group or 0)
            group_styles = sorted(group_style_names.get(group_key, set()))
            channel = DeviceChannel(
                sub_address=sub_address,
                text=text,
                zone=zone,
                product_code=product_code,
                observed_type=observed_type,
                output_group=output_group,
                output_group_name=group_names.get(group_key) or None,
                ringing_style=", ".join(group_styles) or None,
                record_offset=row_id,
            )

            key = (loop, address)
            device_map = panel_devices[node]
            if key not in device_map:
                device_map[key] = Device(
                    node=node,
                    panel=node_names[node],
                    loop=loop,
                    address=address,
                    sub_address=sub_address,
                    text=text,
                    zone=zone,
                    product_code=product_code,
                    observed_type=observed_type,
                    output_group=output_group,
                    output_group_name=group_names.get(group_key) or None,
                    ringing_style=", ".join(group_styles) or None,
                )
            device_map[key].channels.append(channel)
            panel_point_counts[node] += 1
            if zone > 0:
                used_zones.add(zone)

        if orphan_nodes:
            warnings.append(
                "Addressable points referenced nodes absent from tblNode and were skipped: "
                + ", ".join(str(node) for node in sorted(orphan_nodes))
            )

        panels = [
            Panel(
                node=node,
                name=name,
                pcf_bytes=0,
                loops=sorted({device.loop for device in panel_devices[node].values()}),
                devices=list(panel_devices[node].values()),
                point_table_offset=None,
                point_sub_records=panel_point_counts[node],
            )
            for node, name in nodes
        ]
        zones = [
            Zone(number=number, description=zone_names.get(number, ""))
            for number in sorted(used_zones)
        ]
        entries = [
            {
                "name": info.filename,
                "uncompressed_bytes": info.file_size,
                "compressed_bytes": info.compress_size,
            }
            for info in archive.infolist()
        ]

    versions = [f"SKF FDBS {table_version}"] if table_version is not None else ["SKF"]
    unclassified_ids = sorted(
        {
            channel.product_code
            for panel in panels
            for device in panel.devices
            for channel in device.channels
            if channel.observed_type is None
        }
    )
    if unclassified_ids:
        warnings.append(
            "SKF device IDs without a confirmed generic point type: "
            + ", ".join(str(value) for value in unclassified_ids)
        )
    warnings.append(
        "Imported the SKF JSON table format. Addressable loop points, zones, "
        "native output groups and configured ringing styles were decoded; "
        "panel peripherals on internal negative loop numbers were intentionally excluded."
    )
    warnings.append(
        "Zone-range SKF output-group rules are available to the comparison export. "
        "Other cause/effect operations remain project data pending full translation."
    )
    return ParsedNcf(
        source=source,
        sha256=digest,
        versions=versions,
        panels=panels,
        zones=zones,
        archive_entries=entries,
        warnings=warnings,
    )


def parse_ncf(path: str | Path) -> ParsedNcf:
    """Parse either a legacy NCF or a newer SKF network configuration."""
    source = Path(path).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
    if "SITE" in names:
        return _parse_legacy_ncf(source, digest)
    if SKF_REQUIRED_TABLES <= names:
        return _parse_skf(source, digest)
    raise ValueError(
        "Unsupported network configuration archive. Expected a legacy NCF "
        "SITE entry or the newer SKF JSON tables."
    )


# Clearer name for new code while retaining the established public API.
parse_configuration = parse_ncf
