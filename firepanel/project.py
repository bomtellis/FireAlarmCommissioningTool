from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from openpyxl.utils import get_column_letter

from .cause_effect import (
    CauseEffectWorkbook,
    normalise_zone_key,
    read_cause_effect_workbook,
)
from .models import Change, ParsedNcf
from .ncf import parse_configuration, parse_ncf


SCHEMA_VERSION = 6


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
    output_group_name TEXT,
    ringing_style TEXT,
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

CREATE TABLE IF NOT EXISTS cause_effect_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    activation_count INTEGER NOT NULL,
    reference_count INTEGER NOT NULL,
    matched_count INTEGER NOT NULL,
    matrix_only_count INTEGER NOT NULL,
    reference_only_count INTEGER NOT NULL,
    activation_codes_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cause_effect_output_groups (
    import_id INTEGER NOT NULL REFERENCES cause_effect_imports(id) ON DELETE CASCADE,
    target_node INTEGER NOT NULL,
    target_node_name TEXT NOT NULL DEFAULT '',
    output_group INTEGER NOT NULL,
    output_group_name TEXT NOT NULL DEFAULT '',
    source_column TEXT NOT NULL,
    PRIMARY KEY (import_id, target_node, output_group)
);

CREATE TABLE IF NOT EXISTS cause_effect_activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL REFERENCES cause_effect_imports(id) ON DELETE CASCADE,
    trigger_zone TEXT NOT NULL,
    trigger_zone_name TEXT NOT NULL DEFAULT '',
    target_node INTEGER NOT NULL,
    target_node_name TEXT NOT NULL DEFAULT '',
    output_group INTEGER NOT NULL,
    output_group_name TEXT NOT NULL DEFAULT '',
    output_zone_name TEXT NOT NULL DEFAULT '',
    ringing_style TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    source_column TEXT NOT NULL,
    reference_status TEXT NOT NULL DEFAULT 'matrix_only',
    comments TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_cause_effect_activations_import_zone
    ON cause_effect_activations(import_id, trigger_zone);
CREATE INDEX IF NOT EXISTS ix_cause_effect_activations_import_output
    ON cause_effect_activations(import_id, target_node, output_group);

CREATE TABLE IF NOT EXISTS cause_effect_reference_only (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL REFERENCES cause_effect_imports(id) ON DELETE CASCADE,
    trigger_zone TEXT NOT NULL,
    target_node INTEGER NOT NULL,
    target_node_name TEXT NOT NULL DEFAULT '',
    output_group INTEGER NOT NULL,
    output_group_name TEXT NOT NULL DEFAULT '',
    output_zone_name TEXT NOT NULL DEFAULT '',
    ringing_style TEXT NOT NULL,
    source_row INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cause_effect_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL REFERENCES cause_effect_imports(id) ON DELETE CASCADE,
    entity TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    change_type TEXT NOT NULL,
    field TEXT,
    old_value TEXT,
    new_value TEXT
);

CREATE INDEX IF NOT EXISTS ix_cause_effect_changes_import
    ON cause_effect_changes(import_id, id);

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
        repository.import_configuration(ncf_path)
        return repository

    def _initialise(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            device_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(devices)")
            }
            if "output_group_name" not in device_columns:
                connection.execute("ALTER TABLE devices ADD COLUMN output_group_name TEXT")
            if "ringing_style" not in device_columns:
                connection.execute("ALTER TABLE devices ADD COLUMN ringing_style TEXT")
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        self._backfill_zone_descriptions()
        self._backfill_output_groups()
        self._backfill_cause_effect_output_groups()

    def _backfill_zone_descriptions(self) -> None:
        """Populate projects created before SITE zone names were decoded."""
        with self.connection() as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN TRIM(description) <> '' THEN 1 ELSE 0 END) AS named
                FROM zones
                """
            ).fetchone()
            snapshot = connection.execute(
                "SELECT source_path FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if (
            not counts
            or not counts["total"]
            or counts["named"]
            or not snapshot
            or not Path(snapshot["source_path"]).exists()
        ):
            return
        try:
            parsed = parse_ncf(snapshot["source_path"])
        except (OSError, ValueError, zipfile.BadZipFile):
            return
        with self.connection() as connection:
            self._upsert_zones(connection, parsed)

    def _backfill_output_groups(self) -> None:
        """Populate native group data in projects imported by older parsers."""
        with self.connection() as connection:
            snapshot = connection.execute(
                "SELECT id, source_path FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            populated = connection.execute(
                "SELECT 1 FROM devices WHERE output_group IS NOT NULL LIMIT 1"
            ).fetchone()
        if (
            populated
            or not snapshot
            or not Path(snapshot["source_path"]).exists()
        ):
            return
        try:
            parsed = parse_ncf(snapshot["source_path"])
        except (OSError, ValueError, zipfile.BadZipFile):
            return
        rows = []
        for panel in parsed.panels:
            for device in panel.devices:
                for channel in device.channels:
                    rows.append(
                        (
                            channel.output_group,
                            channel.output_group_name,
                            channel.ringing_style,
                            snapshot["id"],
                            f"{device.node}/{device.loop}/{device.address}/{channel.sub_address}",
                        )
                    )
        with self.connection() as connection:
            connection.executemany(
                """
                UPDATE devices
                SET output_group = ?, output_group_name = ?, ringing_style = ?
                WHERE snapshot_id = ? AND stable_key = ?
                """,
                rows,
            )

    def _backfill_cause_effect_output_groups(self) -> None:
        """Retain empty matrix output columns in projects imported before v6."""
        with self.connection() as connection:
            latest = connection.execute(
                """
                SELECT id, source_path
                FROM cause_effect_imports
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if latest is None:
                return
            populated = connection.execute(
                """
                SELECT 1
                FROM cause_effect_output_groups
                WHERE import_id = ?
                LIMIT 1
                """,
                (latest["id"],),
            ).fetchone()
        source = Path(latest["source_path"])
        if populated or not source.exists():
            return
        try:
            parsed = read_cause_effect_workbook(source)
        except (OSError, ValueError, zipfile.BadZipFile):
            return
        with self.connection() as connection:
            self._insert_cause_effect_output_groups(
                connection,
                int(latest["id"]),
                parsed,
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

    def import_configuration(
        self, configuration_path: str | Path
    ) -> tuple[int, list[Change]]:
        parsed = parse_configuration(configuration_path)
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT id FROM snapshots WHERE sha256 = ?", (parsed.sha256,)
            ).fetchone()
            if existing:
                self._upsert_zones(connection, parsed)
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

    def import_ncf(self, ncf_path: str | Path) -> tuple[int, list[Change]]:
        """Backward-compatible alias for legacy callers."""
        return self.import_configuration(ncf_path)

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
                            channel.output_group_name,
                            channel.ringing_style,
                            channel.record_offset,
                        )
                    )
        connection.executemany(
            """
            INSERT INTO devices(
                snapshot_id, stable_key, node, panel, loop, address, sub_address,
                zone, text, product_code, observed_type, output_group,
                output_group_name, ringing_style, record_offset
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            device_rows,
        )
        ProjectRepository._upsert_zones(connection, parsed)

    @staticmethod
    def _upsert_zones(
        connection: sqlite3.Connection, parsed: ParsedNcf
    ) -> None:
        connection.executemany(
            """
            INSERT INTO zones(number, description) VALUES (?, ?)
            ON CONFLICT(number) DO UPDATE SET
                description = CASE
                    WHEN TRIM(zones.description) = '' THEN excluded.description
                    ELSE zones.description
                END
            """,
            [(zone.number, zone.description) for zone in parsed.zones],
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

    def fetch_output_groups(self, snapshot_id: int | None = None) -> list[sqlite3.Row]:
        snapshot_id = snapshot_id or self.latest_snapshot_id()
        if snapshot_id is None:
            return []
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT node, panel, output_group,
                           MAX(COALESCE(output_group_name, '')) AS group_name,
                           COUNT(DISTINCT loop || '/' || address) AS device_count,
                           GROUP_CONCAT(DISTINCT ringing_style) AS ringing_styles
                    FROM devices
                    WHERE snapshot_id = ?
                      AND output_group IS NOT NULL
                      AND output_group > 0
                    GROUP BY node, panel, output_group
                    ORDER BY node, output_group
                    """,
                    (snapshot_id,),
                )
            )

    def fetch_output_group_devices(
        self, node: int, output_group: int, snapshot_id: int | None = None
    ) -> list[sqlite3.Row]:
        snapshot_id = snapshot_id or self.latest_snapshot_id()
        if snapshot_id is None:
            return []
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM devices
                    WHERE snapshot_id = ? AND node = ? AND output_group = ?
                    ORDER BY loop, address, sub_address
                    """,
                    (snapshot_id, node, output_group),
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
        requested_snapshot = snapshot_id
        snapshot_id = snapshot_id or self.latest_snapshot_id()
        with self.connection() as connection:
            changes = (
                list(
                    connection.execute(
                        "SELECT * FROM changes WHERE snapshot_id = ? ORDER BY id",
                        (snapshot_id,),
                    )
                )
                if snapshot_id is not None
                else []
            )
            if requested_snapshot is not None:
                return changes
            latest_import = connection.execute(
                "SELECT MAX(id) AS id FROM cause_effect_imports"
            ).fetchone()["id"]
            if latest_import is not None:
                changes.extend(
                    connection.execute(
                        """
                        SELECT id, NULL AS snapshot_id, import_id,
                               entity, stable_key, change_type,
                               field, old_value, new_value
                        FROM cause_effect_changes
                        WHERE import_id = ?
                        ORDER BY id
                        """,
                        (latest_import,),
                    )
                )
            return changes

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

    def set_floor_dxf(self, floor_id: int, dxf_path: str | None) -> None:
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE floors SET dxf_path = ? WHERE id = ?",
                (
                    str(Path(dxf_path).resolve()) if dxf_path else None,
                    int(floor_id),
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError("The selected floor no longer exists.")

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

    def update_zone_geometry(
        self,
        geometry_id: int,
        points: Iterable[tuple[float, float]],
    ) -> None:
        geometry_json = json.dumps([[float(x), float(y)] for x, y in points])
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE zone_geometry
                SET geometry_json = ?
                WHERE id = ?
                """,
                (geometry_json, int(geometry_id)),
            )

    def reassign_zone_geometry(self, geometry_id: int, zone: int) -> None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT zone, floor_id FROM zone_geometry WHERE id = ?",
                (int(geometry_id),),
            ).fetchone()
            if row is None:
                raise ValueError("The selected zone geometry no longer exists.")
            existing = connection.execute(
                """
                SELECT id FROM zone_geometry
                WHERE zone = ? AND floor_id = ? AND id <> ?
                """,
                (int(zone), int(row["floor_id"]), int(geometry_id)),
            ).fetchone()
            if existing is not None:
                raise ValueError(
                    "That zone already has an assigned polygon on this floor."
                )
            connection.execute(
                "UPDATE zone_geometry SET zone = ? WHERE id = ?",
                (int(zone), int(geometry_id)),
            )
            connection.execute(
                "UPDATE zones SET floor_id = ? WHERE number = ?",
                (int(row["floor_id"]), int(zone)),
            )
            old_zone_remaining = connection.execute(
                "SELECT floor_id FROM zone_geometry WHERE zone = ? LIMIT 1",
                (int(row["zone"]),),
            ).fetchone()
            connection.execute(
                "UPDATE zones SET floor_id = ? WHERE number = ?",
                (
                    (
                        int(old_zone_remaining["floor_id"])
                        if old_zone_remaining is not None
                        else None
                    ),
                    int(row["zone"]),
                ),
            )

    def remove_zone_geometry(self, geometry_id: int) -> None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT zone, floor_id FROM zone_geometry WHERE id = ?",
                (int(geometry_id),),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                "DELETE FROM zone_geometry WHERE id = ?",
                (int(geometry_id),),
            )
            remaining = connection.execute(
                "SELECT 1 FROM zone_geometry WHERE zone = ? LIMIT 1",
                (int(row["zone"]),),
            ).fetchone()
            if remaining is None:
                connection.execute(
                    "UPDATE zones SET floor_id = NULL WHERE number = ?",
                    (int(row["zone"]),),
                )

    def import_cause_effect(
        self, workbook_path: str | Path
    ) -> tuple[int, CauseEffectWorkbook]:
        parsed = read_cause_effect_workbook(workbook_path)
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT id FROM cause_effect_imports WHERE sha256 = ?",
                (parsed.sha256,),
            ).fetchone()
            if existing:
                return int(existing["id"]), parsed

            previous_comments: dict[tuple[str, int, int, str], str] = {}
            latest = connection.execute(
                "SELECT MAX(id) AS id FROM cause_effect_imports"
            ).fetchone()["id"]
            if latest is not None:
                for row in connection.execute(
                    """
                    SELECT trigger_zone, target_node, output_group, ringing_style,
                           comments
                    FROM cause_effect_activations
                    WHERE import_id = ? AND TRIM(comments) <> ''
                    """,
                    (latest,),
                ):
                    previous_comments[
                        (
                            row["trigger_zone"],
                            row["target_node"],
                            row["output_group"],
                            row["ringing_style"],
                        )
                    ] = row["comments"]

            cursor = connection.execute(
                """
                INSERT INTO cause_effect_imports(
                    imported_at, source_name, source_path, sha256,
                    activation_count, reference_count, matched_count,
                    matrix_only_count, reference_only_count,
                    activation_codes_json, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    parsed.source.name,
                    str(parsed.source),
                    parsed.sha256,
                    len(parsed.activations),
                    parsed.reference_count,
                    parsed.matched_count,
                    parsed.matrix_only_count,
                    parsed.reference_only_count,
                    json.dumps(parsed.activation_codes),
                    json.dumps(parsed.warnings),
                ),
            )
            import_id = int(cursor.lastrowid)
            self._insert_cause_effect_output_groups(
                connection,
                import_id,
                parsed,
            )
            connection.executemany(
                """
                INSERT INTO cause_effect_activations(
                    import_id, trigger_zone, trigger_zone_name,
                    target_node, target_node_name, output_group,
                    output_group_name, output_zone_name, ringing_style,
                    source_row, source_column, reference_status, comments
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
                        activation.trigger_zone,
                        activation.trigger_zone_name,
                        activation.target_node,
                        activation.target_node_name,
                        activation.output_group,
                        activation.output_group_name,
                        activation.output_zone_name,
                        activation.activation_code,
                        activation.source_row,
                        activation.source_column,
                        activation.reference_status,
                        previous_comments.get(
                            (
                                activation.trigger_zone,
                                activation.target_node,
                                activation.output_group,
                                activation.activation_code,
                            ),
                            "",
                        ),
                    )
                    for activation in parsed.activations
                ],
            )
            connection.executemany(
                """
                INSERT INTO cause_effect_reference_only(
                    import_id, trigger_zone, target_node, target_node_name,
                    output_group, output_group_name, output_zone_name,
                    ringing_style, source_row
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
                        reference.trigger_zone,
                        reference.target_node,
                        reference.target_node_name,
                        reference.output_group,
                        reference.output_group_name,
                        reference.output_zone_name,
                        reference.activation_code,
                        reference.source_row,
                    )
                    for reference in parsed.reference_only
                ],
            )
            changes = self._calculate_cause_effect_changes(
                connection,
                latest,
                import_id,
            )
            connection.executemany(
                """
                INSERT INTO cause_effect_changes(
                    import_id, entity, stable_key, change_type,
                    field, old_value, new_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
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
        return import_id, parsed

    @staticmethod
    def _insert_cause_effect_output_groups(
        connection: sqlite3.Connection,
        import_id: int,
        parsed: CauseEffectWorkbook,
    ) -> None:
        connection.executemany(
            """
            INSERT OR REPLACE INTO cause_effect_output_groups(
                import_id, target_node, target_node_name,
                output_group, output_group_name, source_column
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    import_id,
                    output.target_node,
                    output.target_node_name,
                    output.output_group,
                    output.output_group_name,
                    get_column_letter(output.index + 1),
                )
                for output in parsed.output_groups
            ],
        )

    @staticmethod
    def _calculate_cause_effect_changes(
        connection: sqlite3.Connection,
        previous_import: int | None,
        import_id: int,
    ) -> list[Change]:
        if previous_import is None:
            count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM cause_effect_activations
                WHERE import_id = ?
                """,
                (import_id,),
            ).fetchone()["count"]
            return [
                Change(
                    entity="cause_effect_matrix",
                    stable_key=str(import_id),
                    change_type="initial",
                    field="activations",
                    old_value=None,
                    new_value=str(count),
                )
            ]

        old_rows = {
            _cause_effect_stable_key(row): row
            for row in connection.execute(
                """
                SELECT * FROM cause_effect_activations
                WHERE import_id = ?
                """,
                (previous_import,),
            )
        }
        new_rows = {
            _cause_effect_stable_key(row): row
            for row in connection.execute(
                """
                SELECT * FROM cause_effect_activations
                WHERE import_id = ?
                """,
                (import_id,),
            )
        }
        old_outputs = {
            _cause_effect_output_key(row): row
            for row in connection.execute(
                """
                SELECT * FROM cause_effect_output_groups
                WHERE import_id = ?
                """,
                (previous_import,),
            )
        }
        new_outputs = {
            _cause_effect_output_key(row): row
            for row in connection.execute(
                """
                SELECT * FROM cause_effect_output_groups
                WHERE import_id = ?
                """,
                (import_id,),
            )
        }
        changes: list[Change] = []
        for key in sorted(new_rows.keys() - old_rows.keys()):
            changes.append(
                Change(
                    "cause_effect_activation",
                    key,
                    "added",
                    None,
                    None,
                    _cause_effect_activation_summary(new_rows[key]),
                )
            )
        for key in sorted(old_rows.keys() - new_rows.keys()):
            changes.append(
                Change(
                    "cause_effect_activation",
                    key,
                    "removed",
                    None,
                    _cause_effect_activation_summary(old_rows[key]),
                    None,
                )
            )
        for key in sorted(old_rows.keys() & new_rows.keys()):
            old_value = old_rows[key]["ringing_style"]
            new_value = new_rows[key]["ringing_style"]
            if old_value != new_value:
                changes.append(
                    Change(
                        "cause_effect_activation",
                        key,
                        "modified",
                        "ringing_style",
                        _stringify(old_value),
                        _stringify(new_value),
                    )
                )

        for key in sorted(new_outputs.keys() - old_outputs.keys()):
            changes.append(
                Change(
                    "cause_effect_output_group",
                    key,
                    "added",
                    None,
                    None,
                    _cause_effect_output_summary(new_outputs[key]),
                )
            )
        for key in sorted(old_outputs.keys() - new_outputs.keys()):
            changes.append(
                Change(
                    "cause_effect_output_group",
                    key,
                    "removed",
                    None,
                    _cause_effect_output_summary(old_outputs[key]),
                    None,
                )
            )
        for key in sorted(old_outputs.keys() & new_outputs.keys()):
            old_name = old_outputs[key]["output_group_name"]
            new_name = new_outputs[key]["output_group_name"]
            if old_name != new_name:
                changes.append(
                    Change(
                        "cause_effect_output_group",
                        key,
                        "modified",
                        "output_group_name",
                        _stringify(old_name),
                        _stringify(new_name),
                    )
                )

        label_specs = (
            (
                "cause_effect_zone",
                "trigger_zone_name",
                old_rows.values(),
                new_rows.values(),
                lambda row: f"zone {row['trigger_zone']}",
            ),
            (
                "cause_effect_node",
                "target_node_name",
                old_outputs.values(),
                new_outputs.values(),
                lambda row: f"node {row['target_node']}",
            ),
        )
        for entity, field, old_values, new_values, key_for_row in label_specs:
            old_labels = {key_for_row(row): row[field] for row in old_values}
            new_labels = {key_for_row(row): row[field] for row in new_values}
            for key in sorted(old_labels.keys() & new_labels.keys()):
                if old_labels[key] != new_labels[key]:
                    changes.append(
                        Change(
                            entity,
                            key,
                            "modified",
                            field,
                            _stringify(old_labels[key]),
                            _stringify(new_labels[key]),
                        )
                    )
        return changes

    def fetch_cause_effect_changes(
        self,
        import_id: int | None = None,
    ) -> list[sqlite3.Row]:
        with self.connection() as connection:
            if import_id is None:
                import_id = connection.execute(
                    "SELECT MAX(id) AS id FROM cause_effect_imports"
                ).fetchone()["id"]
            if import_id is None:
                return []
            return list(
                connection.execute(
                    """
                    SELECT * FROM cause_effect_changes
                    WHERE import_id = ?
                    ORDER BY id
                    """,
                    (import_id,),
                )
            )

    def latest_cause_effect_import(self) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM cause_effect_imports ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def fetch_cause_effect_output_groups(self) -> list[sqlite3.Row]:
        latest = self.latest_cause_effect_import()
        if latest is None:
            return []
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM cause_effect_output_groups
                    WHERE import_id = ?
                    ORDER BY target_node, output_group
                    """,
                    (latest["id"],),
                )
            )

    def fetch_cause_effect_activations(
        self,
        trigger_zone: object | None = None,
        scope_node: int | None = None,
    ) -> list[sqlite3.Row]:
        latest = self.latest_cause_effect_import()
        if latest is None:
            return []
        clauses = ["import_id = ?"]
        parameters: list[object] = [latest["id"]]
        if trigger_zone is not None:
            clauses.append("trigger_zone = ?")
            parameters.append(normalise_zone_key(trigger_zone))
        if scope_node is not None:
            clauses.append("target_node = ?")
            parameters.append(int(scope_node))
        with self.connection() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT * FROM cause_effect_activations
                    WHERE {' AND '.join(clauses)}
                    ORDER BY CASE
                                 WHEN trigger_zone GLOB '[0-9]*' THEN 0
                                 ELSE 1
                             END,
                             CAST(trigger_zone AS REAL), trigger_zone,
                             target_node, output_group, id
                    """,
                    parameters,
                )
            )

    def fetch_cause_effect_trigger_zones(self) -> list[sqlite3.Row]:
        latest = self.latest_cause_effect_import()
        if latest is None:
            return []
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT trigger_zone,
                           MAX(trigger_zone_name) AS trigger_zone_name,
                           COUNT(*) AS activation_count
                    FROM cause_effect_activations
                    WHERE import_id = ?
                    GROUP BY trigger_zone
                    ORDER BY CASE
                                 WHEN trigger_zone GLOB '[0-9]*' THEN 0
                                 ELSE 1
                             END,
                             CAST(trigger_zone AS REAL), trigger_zone
                    """,
                    (latest["id"],),
                )
            )

    def fetch_cause_effect_reference_only(self) -> list[sqlite3.Row]:
        latest = self.latest_cause_effect_import()
        if latest is None:
            return []
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM cause_effect_reference_only
                    WHERE import_id = ?
                    ORDER BY source_row, id
                    """,
                    (latest["id"],),
                )
            )

    def update_cause_effect_comment(
        self,
        activation_id: int,
        comments: str,
    ) -> None:
        latest = self.latest_cause_effect_import()
        if latest is None:
            return
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE cause_effect_activations
                SET comments = ?
                WHERE id = ? AND import_id = ?
                """,
                (comments.strip(), int(activation_id), latest["id"]),
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
        trigger_zone: int | float | str,
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

    def import_test_sessions(self, sessions) -> tuple[int, int]:
        """Append spreadsheet test sessions and their output-group results."""
        session_count = 0
        result_count = 0
        with self.connection() as connection:
            for session in sessions:
                notes = str(session.notes or "").strip()
                audit_note = f"Imported workbook session: {session.session_key}"
                notes = f"{notes}\n{audit_note}".strip()
                cursor = connection.execute(
                    """
                    INSERT INTO test_sessions(
                        started_at, completed_at, engineer, scope_node, notes
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        _now(),
                        _now(),
                        session.engineer,
                        session.scope_node,
                        notes,
                    ),
                )
                session_id = int(cursor.lastrowid)
                rows = [
                    (
                        session_id,
                        session.trigger_zone,
                        stable_key,
                        expected_state,
                        actual_state,
                        result,
                        comments,
                        tested_at or _now(),
                    )
                    for (
                        stable_key,
                        expected_state,
                        actual_state,
                        result,
                        comments,
                        tested_at,
                    ) in session.results
                ]
                connection.executemany(
                    """
                    INSERT INTO test_results(
                        session_id, trigger_zone, stable_key, expected_state,
                        actual_state, result, comments, tested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                session_count += 1
                result_count += len(rows)
        return session_count, result_count

    def fetch_test_results(self, session_id: int | None = None) -> list[sqlite3.Row]:
        with self.connection() as connection:
            if session_id is None:
                return list(
                    connection.execute(
                        "SELECT * FROM test_results ORDER BY session_id, id"
                    )
                )
            return list(
                connection.execute(
                    """
                    SELECT * FROM test_results
                    WHERE session_id = ?
                    ORDER BY id
                    """,
                    (int(session_id),),
                )
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stringify(value: object) -> str | None:
    return None if value is None else str(value)


def _cause_effect_stable_key(row: sqlite3.Row) -> str:
    return (
        f"zone {row['trigger_zone']} -> "
        f"node {row['target_node']}/group {row['output_group']}"
    )


def _cause_effect_activation_summary(row: sqlite3.Row) -> str:
    ringing_style = str(row["ringing_style"] or "Not specified")
    output_name = str(row["output_group_name"] or "").strip()
    return (
        f"{ringing_style}"
        f"{' - ' + output_name if output_name else ''}"
    )


def _cause_effect_output_key(row: sqlite3.Row) -> str:
    return f"node {row['target_node']}/group {row['output_group']}"


def _cause_effect_output_summary(row: sqlite3.Row) -> str:
    return str(row["output_group_name"] or "Unnamed output group")


def _normalise_test_result(value: str) -> str:
    normalised = value.strip().casefold()
    if normalised in {"pass", "passed", "ok", "correct"}:
        return "pass"
    if normalised in {"fail", "failed", "incorrect"}:
        return "fail"
    return "not-tested" if not normalised else "observation"
