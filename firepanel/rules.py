from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from shapely.geometry import Polygon

from .models import ZoneEffect
from .project import ProjectRepository


HTM_SOURCE = "HTM 05-03 Figure 2"
DOOR_SOURCE = "Door drawing suggestion"


@dataclass(slots=True)
class ZoneShape:
    zone: int
    floor_id: int
    level_order: int
    polygon: Polygon


def load_zone_shapes(repository: ProjectRepository) -> list[ZoneShape]:
    shapes: list[ZoneShape] = []
    for row in repository.fetch_zone_geometry():
        points = json.loads(row["geometry_json"])
        polygon = Polygon(points)
        if polygon.is_valid and not polygon.is_empty and polygon.area > 0:
            shapes.append(
                ZoneShape(
                    zone=row["zone"],
                    floor_id=row["floor_id"],
                    level_order=row["level_order"],
                    polygon=polygon,
                )
            )
    return shapes


def find_adjacencies(
    shapes: Iterable[ZoneShape],
    horizontal_tolerance: float = 250.0,
    vertical_overlap_ratio: float = 0.15,
) -> dict[int, dict[int, str]]:
    """
    Return direct same-floor and directly-above/below neighbours.

    DXF units vary by site, so horizontal tolerance is configurable. Vertical
    adjacency requires polygon overlap and consecutive floor ordering.
    """
    items = list(shapes)
    adjacency: dict[int, dict[int, str]] = {shape.zone: {} for shape in items}
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            if left.zone == right.zone:
                continue
            if left.floor_id == right.floor_id:
                if left.polygon.touches(right.polygon) or left.polygon.distance(right.polygon) <= horizontal_tolerance:
                    adjacency[left.zone][right.zone] = "adjacent on same floor"
                    adjacency[right.zone][left.zone] = "adjacent on same floor"
                continue
            if abs(left.level_order - right.level_order) != 1:
                continue
            overlap = left.polygon.intersection(right.polygon).area
            smaller_area = min(left.polygon.area, right.polygon.area)
            if smaller_area and overlap / smaller_area >= vertical_overlap_ratio:
                relation = "directly above/below"
                adjacency[left.zone][right.zone] = relation
                adjacency[right.zone][left.zone] = relation
    return adjacency


def generate_htm_rules(
    repository: ProjectRepository,
    horizontal_tolerance: float = 250.0,
    vertical_overlap_ratio: float = 0.15,
) -> int:
    shapes = load_zone_shapes(repository)
    adjacency = find_adjacencies(shapes, horizontal_tolerance, vertical_overlap_ratio)
    rows: list[tuple] = []
    for shape in shapes:
        rows.append(
            (
                f"Zone {shape.zone} evacuate",
                shape.zone,
                "exact",
                shape.zone,
                None,
                None,
                "EVACUATE / continuous",
                HTM_SOURCE,
                1,
                "Origin zone. Verify against the approved site fire strategy.",
            )
        )
        for target, relation in sorted(adjacency[shape.zone].items()):
            rows.append(
                (
                    f"Zone {shape.zone} alerts zone {target}",
                    shape.zone,
                    relation,
                    target,
                    None,
                    None,
                    "ALERT / intermittent",
                    HTM_SOURCE,
                    1,
                    "Suggested from drawing geometry; competent-person approval required.",
                )
            )
    repository.replace_suggested_rules(rows)
    return len(rows)


def generate_door_rules(repository: ProjectRepository) -> int:
    devices = {
        str(row["stable_key"]): row
        for row in repository.fetch_devices()
    }
    rows: list[tuple] = []
    for door in repository.fetch_doors():
        zone_a = int(door["zone_a"])
        zone_b = int(door["zone_b"])
        zone_context = (
            f"within zone {zone_a}"
            if zone_a == zone_b
            else f"between zones {zone_a} and {zone_b}"
        )
        for capability, device_key, action in (
            (
                "access release",
                door["access_device_key"],
                "UNLOCK DOOR",
            ),
            (
                "hold-open release",
                door["hold_open_device_key"],
                "CLOSE FIRE DOOR",
            ),
        ):
            enabled = (
                door["has_access_control"]
                if capability == "access release"
                else door["has_hold_open"]
            )
            if not enabled:
                continue
            device = devices.get(str(device_key))
            if (
                device is None
                or device["output_group"] is None
                or int(device["output_group"]) <= 0
            ):
                continue
            for trigger_zone in sorted(
                {int(door["zone_a"]), int(door["zone_b"])}
            ):
                rows.append(
                    (
                        f"{door['name']} — {capability}",
                        trigger_zone,
                        "door side",
                        None,
                        int(device["node"]),
                        int(device["output_group"]),
                        action,
                        DOOR_SOURCE,
                        1,
                        (
                            f"Suggested from door drawing {zone_context}; verify "
                            "against the approved fire strategy."
                        ),
                    )
                )
    repository.replace_door_suggested_rules(rows)
    return len(rows)


def evaluate_zone(repository: ProjectRepository, trigger_zone: int) -> list[ZoneEffect]:
    effects: dict[int, ZoneEffect] = {
        trigger_zone: ZoneEffect(
            zone=trigger_zone,
            state="EVACUATE",
            reason="Origin zone",
        )
    }
    for rule in repository.fetch_rules():
        if not rule["enabled"] or rule["trigger_zone"] != trigger_zone or rule["target_zone"] is None:
            continue
        state = "EVACUATE" if str(rule["action"]).upper().startswith("EVACUATE") else "ALERT"
        target = int(rule["target_zone"])
        existing = effects.get(target)
        if existing is None or (existing.state == "ALERT" and state == "EVACUATE"):
            effects[target] = ZoneEffect(target, state, rule["name"])
    return sorted(effects.values(), key=lambda effect: (effect.state != "EVACUATE", effect.zone))
