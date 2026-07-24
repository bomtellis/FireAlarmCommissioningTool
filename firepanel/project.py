from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .models import Change, ParsedNcf
from .ncf import parse_ncf


SCHEMA_VERSION = 2


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    versions_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS panels (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    node INTEGER NOT NULL,
    name TEXT NOT NULL,
    pcf_bytes INTEGER NOT NULL,
    loops_json TEXT NOT NULL,
    point_table_offset INTEGER,
    point_sub_records INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, node)
);

CREATE TABLE IF NOT EXISTS devices (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    node INTEGER NOT NULL,
    panel TEXT NOT NULL,
    loop INTEGER NOT NULL,
    address INTEGER NOT NULL,
    sub_address INTEGER NOT NULL,
    zone INTEGER NOT NULL,
    text TEXT NOT NULL,
    product_code INTEGER NOT NULL,
    observed_type TEXT,
    output_group INTEGER,
    record_offset INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, stable_key)
);

CREATE INDEX IF NOT EXISTS ix_devices_snapshot_zone
    ON devices(snapshot_id, zone);
CREATE INDEX IF NOT EXISTS ix_devices_snapshot_node_loop
    ON devices(snapshot_id, node, loop);

CREATE TABLE IF NOT EXISTS zones (
    number INTEGER PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    floor_id INTEGER REFERENCES floors(id),
    clinical_area INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS floors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    level_order INTEGER NOT NULL,
    dxf_path TEXT,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS zone_geometry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone INTEGER NOT NULL REFERENCES zones(number) ON DELETE CASCADE,
    floor_id INTEGER NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
    geometry_json TEXT NOT NULL,
    source_layer TEXT,
    UNIQUE(zone, floor_id)
);

CREATE TABLE IF NOT EXISTS map_assets (
    entity_kind TEXT NOT NULL CHECK(entity_kind IN ('device', 'panel')),
    entity_key TEXT NOT NULL,
    floor_id INTEGER NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
    x REAL NOT NULL,
    y REAL NOT NULL,
    symbol_type TEXT NOT NULL,
    PRIMARY KEY (entity_kind, entity_key)
);

CREATE INDEX IF NOT EXISTS ix_map_assets_floor
    ON map_assets(floor_id);

CREATE TABLE IF NOT EXISTS output_groups (
    number INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cause_effect_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    trigger_zone INTEGER,
    relation TEXT NOT NULL DEFAULT 'exact',
    target_zone INTEGER,
    target_node INTEGER,
    output_group INTEGER,
    action TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'custom',
    enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS node_power (
    node INTEGER PRIMARY KEY,
    battery_ah REAL,
    standby_hours REAL NOT NULL DEFAULT 24,
    alarm_minutes REAL NOT NULL DEFAULT 30,
    safety_factor REAL NOT NULL DEFAULT 1.25
);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    entity TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    change_type TEXT NOT NULL,
    field TEXT,
    old_value TEXT,
    new_value TEXT
);

CREATE TABLE IF NOT EXISTS test_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    engineer TEXT NOT NULL DEFAULT '',
    scope_node INTEGER,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES test_sessions(id) ON DELETE CASCADE,
    trigger_zone INTEGER NOT NULL,
    stable_key TEXT,
    expected_state TEXT NOT NULL,
    actual_state TEXT,
    result TEXT NOT NULL DEFAULT 'not-tested',
    comments TEXT NOT NULL DEFAULT '',
    tested_at TEXT
);
"""


class ProjectRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self._initialise()

    @classmethod
    def create(cls, path: str | Path, name: str, ncf_path: str | Path) -> "ProjectRepository":
        project_path = Path(path).resolve()
        project_path.parent.mkdir(parents=True, exist_ok=True)
        if project_path.exists():
            raise FileExistsError(project_path)
        sqlite3.connect(project_path).close()
        repository = cls(project_path)
        repository.set_metadata("project_name", name)
        repository.set_metadata("created_at", _now())
        repository.import_ncf(ncf_path)
        return repository

    def _initialise(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def metadata(self, key: str, default: str = "") -> str:
        with self.connection() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_metadata(self, key: str, value: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
                (key, value),
            )

    @property
    def name(self) -> str:
        return self.metadata("project_name", self.path.stem)

    def save_as(self, target: str | Path) -> "ProjectRepository":
        target_path = Path(target).resolve()
        if target_path.exists():
            raise FileExistsError(target_path)
        with self.connection() as connection:
            connection.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(self.path, target_path)
        return ProjectRepository(target_path)

    def latest_snapshot_id(self) -> int | None:
        with self.connection() as connection:
            row = connection.execute("SELECT MAX(id) AS id FROM snapshots").fetchone()
        return int(row["id"]) if row and row["id"] is not None else None

    def import_ncf(self, ncf_path: str | Path) -> tuple[int, list[Change]]:
        parsed = parse_ncf(ncf_path)
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT id FROM snapshots WHERE sha256 = ?", (parsed.sha256,)
            ).fetchone()
            if existing:
                return int(existing["id"]), []

            previous_snapshot = connection.execute("SELECT MAX(id) AS id FROM snapshots").fetchone()["id"]
            cursor = connection.execute(
                """
                INSERT INTO snapshots(
                    imported_at, source_name, source_path, sha256, versions_json, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    parsed.source.name,
                    str(parsed.source),
                    parsed.sha256,
                    json.dumps(parsed.versions),
                    json.dumps(parsed.warnings),
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            self._insert_parsed(connection, snapshot_id, parsed)
            changes = self._calculate_changes(connection, previous_snapshot, snapshot_id)
            connection.executemany(
                """
                INSERT INTO changes(
                    snapshot_id, entity, stable_key, change_type, field, old_value, new_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        change.entity,
                        change.stable_key,
                        change.change_type,
                        change.field,
                        change.old_value,
                        change.new_value,
                    )
                    for change in changes
                ],
            )
        return snapshot_id, changes

    @staticmethod
    def _insert_parsed(
        connection: sqlite3.Connection, snapshot_id: int, parsed: ParsedNcf
    ) -> None:
        connection.executemany(
            """
            INSERT INTO panels(
                snapshot_id, node, name, pcf_bytes, loops_json,
                point_table_offset, point_sub_records
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot_id,
                    panel.node,
                    panel.name,
                    panel.pcf_bytes,
                    json.dumps(panel.loops),
                    panel.point_table_offset,
                    panel.point_sub_records,
                )
                for panel in parsed.panels
            ],
        )

        device_rows = []
        for panel in parsed.panels:
            for device in panel.devices:
                for channel in device.channels:
                    stable_key = f"{device.node}/{device.loop}/{device.address}/{channel.sub_address}"
                    device_rows.append(
                        (
                            snapshot_id,
                            stable_key,
                            device.node,
                            device.panel,
                            device.loop,
                            device.address,
                            channel.sub_address,
                            channel.zone,
                            channel.text,
                            channel.product_code,
                            channel.observed_type,
                            channel.output_group,
                            channel.record_offset,
                        )
                    )
        connection.executemany(
            """
            INSERT INTO devices(
                snapshot_id, stable_key, node, panel, loop, address, sub_address,
                zone, text, product_code, observed_type, output_group, record_offset
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            device_rows,
        )
        connection.executemany(
            "INSERT OR IGNORE INTO zones(number) VALUES (?)",
            [(zone.number,) for zone in parsed.zones],
        )

    @staticmethod
    def _calculate_changes(
        connection: sqlite3.Connection, previous_snapshot: int | None, snapshot_id: int
    ) -> list[Change]:
        if previous_snapshot is None:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM devices WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()["count"]
            return [
                Change(
                    entity="snapshot",
                    stable_key=str(snapshot_id),
                    change_type="initial",
                    field="devices",
                    old_value=None,
                    new_value=str(count),
                )
            ]

        fields = ("panel", "zone", "text", "product_code", "observed_type", "output_group")
        old_rows = {
            row["stable_key"]: row
            for row in connection.execute(
                "SELECT * FROM devices WHERE snapshot_id = ?", (previous_snapshot,)
            )
        }
        new_rows = {
            row["stable_key"]: row
            for row in connection.execute(
                "SELECT * FROM devices WHERE snapshot_id = ?", (snapshot_id,)
            )
        }
        changes: list[Change] = []
        for key in sorted(new_rows.keys() - old_rows.keys()):
            changes.append(Change("device", key, "added", None, None, new_rows[key]["text"]))
        for key in sorted(old_rows.keys() - new_rows.keys()):
            changes.append(Change("device", key, "removed", None, old_rows[key]["text"], None))
        for key in sorted(old_rows.keys() & new_rows.keys()):
            for field in fields:
                old_value = old_rows[key][field]
                new_value = new_rows[key][field]
                if old_value != new_value:
                    changes.append(
                        Change(
                            "device",
                            key,
                            "modified",
                            field,
                            _stringify(old_value),
                            _stringify(new_value),
                        )
                    )
        return changes

    def fetch_devices(self, snapshot_id: int | None = None) -> list[sqlite3.Row]:
        snapshot_id = snapshot_id or self.latest_snapshot_id()
        if snapshot_id is None:
            return []
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM devices
                    WHERE snapshot_id = ?
                    ORDER BY node, loop, address, sub_address
                    """,
                    (snapshot_id,),
                )
            )

    def fetch_panels(self, snapshot_id: int | None = None) -> list[sqlite3.Row]:
        snapshot_id = snapshot_id or self.latest_snapshot_id()
        if snapshot_id is None:
            return []
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT p.*,
                           COUNT(DISTINCT d.loop || '/' || d.address) AS device_count
                    FROM panels p
                    LEFT JOIN devices d
                      ON d.snapshot_id = p.snapshot_id AND d.node = p.node
                    WHERE p.snapshot_id = ?
                    GROUP BY p.snapshot_id, p.node
                    ORDER BY p.node
                    """,
                    (snapshot_id,),
                )
            )

    def fetch_zones(self) -> list[sqlite3.Row]:
        snapshot_id = self.latest_snapshot_id()
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT z.*, f.name AS floor_name, f.level_order,
                           COUNT(DISTINCT d.node || '/' || d.loop || '/' || d.address) AS device_count
                    FROM zones z
                    LEFT JOIN floors f ON f.id = z.floor_id
                    LEFT JOIN devices d ON d.snapshot_id = ? AND d.zone = z.number
                    GROUP BY z.number
                    ORDER BY z.number
                    """,
                    (snapshot_id,),
                )
            )

    def fetch_changes(self, snapshot_id: int | None = None) -> list[sqlite3.Row]:
        snapshot_id = snapshot_id or self.latest_snapshot_id()
        if snapshot_id is None:
            return []
        with self.connection() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM changes WHERE snapshot_id = ? ORDER BY id", (snapshot_id,)
                )
            )

    def fetch_snapshots(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return list(connection.execute("SELECT * FROM snapshots ORDER BY id DESC"))

    def add_floor(self, name: str, level_order: int, dxf_path: str | None = None) -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO floors(name, level_order, dxf_path) VALUES (?, ?, ?)",
                (name, level_order, dxf_path),
            )
            return int(cursor.lastrowid)

    def fetch_floors(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return list(connection.execute("SELECT * FROM floors ORDER BY level_order DESC"))

    def assign_zone_geometry(
        self,
        zone: int,
        floor_id: int,
        points: Iterable[tuple[float, float]],
        source_layer: str = "",
    ) -> None:
        geometry_json = json.dumps([[float(x), float(y)] for x, y in points])
        with self.connection() as connection:
            connection.execute(
                "UPDATE zones SET floor_id = ? WHERE number = ?",
                (floor_id, zone),
            )
            connection.execute(
                """
                INSERT INTO zone_geometry(zone, floor_id, geometry_json, source_layer)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(zone, floor_id)
                DO UPDATE SET geometry_json=excluded.geometry_json,
                              source_layer=excluded.source_layer
                """,
                (zone, floor_id, geometry_json, source_layer),
            )

    def fetch_zone_geometry(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT g.*, f.name AS floor_name, f.level_order, z.description
                    FROM zone_geometry g
                    JOIN floors f ON f.id = g.floor_id
                    JOIN zones z ON z.number = g.zone
                    ORDER BY f.level_order DESC, g.zone
                    """
                )
            )

    def place_map_asset(
        self,
        entity_kind: str,
        entity_key: str,
        floor_id: int,
        x: float,
        y: float,
        symbol_type: str,
    ) -> None:
        if entity_kind not in {"device", "panel"}:
            raise ValueError(f"Unsupported map entity: {entity_kind}")
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO map_assets(entity_kind, entity_key, floor_id, x, y, symbol_type)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_kind, entity_key) DO UPDATE SET
                    floor_id=excluded.floor_id,
                    x=excluded.x,
                    y=excluded.y,
                    symbol_type=excluded.symbol_type
                """,
                (entity_kind, entity_key, floor_id, float(x), float(y), symbol_type),
            )

    def remove_map_asset(self, entity_kind: str, entity_key: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM map_assets WHERE entity_kind = ? AND entity_key = ?",
                (entity_kind, entity_key),
            )

    def fetch_map_assets(self, floor_id: int | None = None) -> list[sqlite3.Row]:
        with self.connection() as connection:
            if floor_id is None:
                return list(
                    connection.execute(
                        "SELECT * FROM map_assets ORDER BY floor_id, entity_kind, entity_key"
                    )
                )
            return list(
                connection.execute(
                    """
                    SELECT * FROM map_assets
                    WHERE floor_id = ?
                    ORDER BY entity_kind, entity_key
                    """,
                    (floor_id,),
                )
            )

    def set_output_group_name(self, number: int, name: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO output_groups(number, name) VALUES (?, ?)
                ON CONFLICT(number) DO UPDATE SET name=excluded.name
                """,
                (number, name.strip()),
            )

    def output_group_details(self, number: int) -> tuple[str, list[int]]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT name FROM output_groups WHERE number = ?", (number,)
            ).fetchone()
            rule_names = [
                item["name"]
                for item in connection.execute(
                    """
                    SELECT DISTINCT name
                    FROM cause_effect_rules
                    WHERE enabled = 1 AND output_group = ?
                    ORDER BY name
                    """,
                    (number,),
                )
            ]
            zones = [
                int(item["trigger_zone"])
                for item in connection.execute(
                    """
                    SELECT DISTINCT trigger_zone
                    FROM cause_effect_rules
                    WHERE enabled = 1 AND output_group = ? AND trigger_zone IS NOT NULL
                    ORDER BY trigger_zone
                    """,
                    (number,),
                )
            ]
        name = row["name"] if row and row["name"] else " / ".join(rule_names)
        return (name, zones)

    def replace_suggested_rules(self, rows: Iterable[tuple]) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM cause_effect_rules WHERE source = 'HTM 05-03 Figure 2'")
            connection.executemany(
                """
                INSERT INTO cause_effect_rules(
                    name, trigger_zone, relation, target_zone, target_node,
                    output_group, action, source, enabled, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                list(rows),
            )

    def fetch_rules(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM cause_effect_rules ORDER BY trigger_zone, action, target_zone, id"
                )
            )

    def add_rule(
        self,
        name: str,
        trigger_zone: int,
        relation: str,
        target_zone: int | None,
        target_node: int | None,
        output_group: int | None,
        action: str,
        notes: str = "",
    ) -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO cause_effect_rules(
                    name, trigger_zone, relation, target_zone, target_node,
                    output_group, action, source, enabled, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'custom', 1, ?)
                """,
                (
                    name,
                    trigger_zone,
                    relation,
                    target_zone,
                    target_node,
                    output_group,
                    action,
                    notes,
                ),
            )
            return int(cursor.lastrowid)

    def set_node_power(
        self,
        node: int,
        battery_ah: float | None,
        standby_hours: float,
        alarm_minutes: float,
        safety_factor: float = 1.25,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO node_power(node, battery_ah, standby_hours, alarm_minutes, safety_factor)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(node) DO UPDATE SET
                    battery_ah=excluded.battery_ah,
                    standby_hours=excluded.standby_hours,
                    alarm_minutes=excluded.alarm_minutes,
                    safety_factor=excluded.safety_factor
                """,
                (node, battery_ah, standby_hours, alarm_minutes, safety_factor),
            )

    def fetch_node_power(self) -> dict[int, sqlite3.Row]:
        with self.connection() as connection:
            return {
                int(row["node"]): row
                for row in connection.execute("SELECT * FROM node_power")
            }

    def create_test_session(
        self,
        engineer: str,
        scope_node: int | None,
        trigger_zone: int,
        results: Iterable[tuple[str | None, str, str, str]],
        notes: str = "",
    ) -> int:
        """Persist the current simulation/check as a commissioning test session."""
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO test_sessions(started_at, completed_at, engineer, scope_node, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (_now(), _now(), engineer, scope_node, notes),
            )
            session_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO test_results(
                    session_id, trigger_zone, stable_key, expected_state,
                    actual_state, result, comments, tested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        trigger_zone,
                        stable_key,
                        expected_state,
                        result,
                        _normalise_test_result(result),
                        comments,
                        _now(),
                    )
                    for stable_key, expected_state, result, comments in results
                ],
            )
            return session_id

    def fetch_test_sessions(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT s.*, COUNT(r.id) AS result_count
                    FROM test_sessions s
                    LEFT JOIN test_results r ON r.session_id = s.id
                    GROUP BY s.id
                    ORDER BY s.id DESC
                    """
                )
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stringify(value: object) -> str | None:
    return None if value is None else str(value)


def _normalise_test_result(value: str) -> str:
    normalised = value.strip().casefold()
    if normalised in {"pass", "passed", "ok", "correct"}:
        return "pass"
    if normalised in {"fail", "failed", "incorrect"}:
        return "fail"
    return "not-tested" if not normalised else "observation"
