from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DeviceChannel:
    sub_address: int
    text: str
    zone: int
    product_code: int
    observed_type: str | None
    output_group: int | None = None
    output_group_name: str | None = None
    ringing_style: str | None = None
    record_offset: int = 0


@dataclass(slots=True)
class Device:
    node: int
    panel: str
    loop: int
    address: int
    sub_address: int
    text: str
    zone: int
    product_code: int
    observed_type: str | None
    output_group: int | None = None
    output_group_name: str | None = None
    ringing_style: str | None = None
    channels: list[DeviceChannel] = field(default_factory=list)

    @property
    def stable_key(self) -> str:
        return f"{self.node}/{self.loop}/{self.address}/{self.sub_address}"


@dataclass(slots=True)
class Panel:
    node: int
    name: str
    pcf_bytes: int
    loops: list[int] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    point_table_offset: int | None = None
    point_sub_records: int = 0


@dataclass(slots=True)
class Zone:
    number: int
    description: str = ""
    floor_id: int | None = None


@dataclass(slots=True)
class ParsedNcf:
    source: Path
    sha256: str
    versions: list[str]
    panels: list[Panel]
    zones: list[Zone]
    archive_entries: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)

    @property
    def devices(self) -> list[Device]:
        return [device for panel in self.panels for device in panel.devices]


@dataclass(slots=True)
class Change:
    entity: str
    stable_key: str
    change_type: str
    field: str | None
    old_value: str | None
    new_value: str | None


@dataclass(slots=True)
class ZoneEffect:
    zone: int
    state: str
    reason: str


@dataclass(slots=True)
class TriggerResult:
    origin_zone: int
    effects: list[ZoneEffect]
    devices: list[Device]
    output_rules: list[dict[str, Any]]
