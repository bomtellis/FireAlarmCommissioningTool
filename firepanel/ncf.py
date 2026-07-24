from __future__ import annotations

import hashlib
import re
import struct
import zipfile
from collections import OrderedDict
from pathlib import Path

from .device_catalog import (
    CONFIRMED_GENERIC_TYPES,
    KNOWN_CATALOGUE_CODES,
    protocols_for_code,
)
from .models import Device, DeviceChannel, Panel, ParsedNcf, Zone


POINT_RECORD_SIZE = 224
SITE_RECORD_OFFSET = 112
SITE_RECORD_SIZE = 112

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


def parse_ncf(path: str | Path) -> ParsedNcf:
    source = Path(path).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
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
