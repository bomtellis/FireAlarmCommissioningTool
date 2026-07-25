from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import qtawesome as qta
from shapely.geometry import Point, Polygon
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsPolygonItem,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .cause_effect import normalise_zone_key
from .device_catalog import catalogue_display_name, device_current_ma
from .dxf import (
    DxfShape,
    DxfText,
    read_closed_shapes,
    read_layers,
    read_linework,
    read_text,
)
from .exports import (
    export_cause_effect_comparison_xlsx,
    export_change_pdf,
    export_devices_xlsx,
)
from .project import ProjectRepository, zone_shape_key
from .rules import evaluate_zone, generate_door_rules, generate_htm_rules
from .styles import APP_STYLESHEET
from .testing_workbook import export_testing_workbook, read_testing_workbook
CONFIGURATION_FILTER = "Network configurations (*.ncf *.NCF *.skf *.SKF)"


@lru_cache(maxsize=1)
def _installed_font_families() -> dict[str, str]:
    return {
        family.casefold(): family
        for family in QFontDatabase.families()
    }


def _normalised_font_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


@lru_cache(maxsize=128)
def _resolve_dxf_font_family(font_family: str, font_file: str) -> str:
    installed = _installed_font_families()
    file_stem = Path(font_file).stem if font_file else ""
    aliases = {
        "arial": "Arial",
        "arialbd": "Arial",
        "arialbi": "Arial",
        "ariali": "Arial",
        "helvetica": "Arial",
        "calibri": "Calibri",
        "times": "Times New Roman",
        "timesnewroman": "Times New Roman",
        "romanc": "Times New Roman",
        "romand": "Times New Roman",
        "romans": "Times New Roman",
        "romant": "Times New Roman",
        "scripts": "Segoe Script",
        "scriptc": "Segoe Script",
    }
    candidates = [font_family, file_stem]
    for value in (font_family, file_stem):
        alias = aliases.get(_normalised_font_name(value))
        if alias:
            candidates.append(alias)
    if font_file.casefold().endswith(".shx"):
        candidates.extend(["Arial", "Liberation Sans", "DejaVu Sans"])
    for candidate in candidates:
        family = installed.get(str(candidate).casefold())
        if family:
            return family
    return QFontDatabase.systemFont(
        QFontDatabase.SystemFont.GeneralFont
    ).family()


@lru_cache(maxsize=256)
def _dxf_font_template(
    family: str,
    bold: bool,
    italic: bool,
    stretch: int,
) -> tuple[QFont, float]:
    font = QFont(family)
    font.setPixelSize(100)
    font.setBold(bold)
    font.setItalic(italic)
    font.setStretch(stretch)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font, max(QFontMetricsF(font).capHeight(), 1.0)


def _dxf_text_font(label: DxfText) -> tuple[QFont, float, str]:
    family = _resolve_dxf_font_family(label.font_family, label.font_file)
    template, cap_height = _dxf_font_template(
        family,
        bool(label.bold),
        bool(label.italic or abs(label.oblique) > 0.01),
        max(1, min(400, round(label.width_factor * 100))),
    )
    return (
        QFont(template),
        max(float(label.height) / cap_height, 0.0001),
        family,
    )


def natural_sort_key(value: object) -> tuple[tuple[int, object], ...]:
    """Return a key that sorts embedded numbers numerically and text naturally."""
    text = "" if value is None else str(value).strip()
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return ((0, float(text)),)
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", text)
        if part
    )


class NaturalSortTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        return natural_sort_key(self.text()) < natural_sort_key(other.text())


class FilterableTableWidget(QTableWidget):
    """QTableWidget with natural sorting and a filter on every column header."""

    def __init__(self, rows: int, columns: int, parent: QWidget | None = None):
        super().__init__(rows, columns, parent)
        self._base_headers: list[str] = []
        self._column_filters: dict[int, str] = {}
        header = self.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_filter_menu)
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        header.setToolTip(
            "Click a column heading to sort. Right-click a heading to filter that column."
        )

    def setHorizontalHeaderLabels(self, labels) -> None:
        self._base_headers = [str(label) for label in labels]
        self._update_header_labels()

    def setItem(self, row: int, column: int, item: QTableWidgetItem) -> None:
        super().setItem(row, column, item)
        if self._column_filters:
            self.setRowHidden(row, not self._row_matches_filters(row))

    def set_column_filter(self, column: int, value: str) -> None:
        value = value.strip()
        if value:
            self._column_filters[column] = value.casefold()
        else:
            self._column_filters.pop(column, None)
        self._update_header_labels()
        self.apply_filters()

    def clear_filters(self) -> None:
        self._column_filters.clear()
        self._update_header_labels()
        self.apply_filters()

    def apply_filters(self) -> None:
        for row in range(self.rowCount()):
            self.setRowHidden(row, not self._row_matches_filters(row))

    def _row_matches_filters(self, row: int) -> bool:
        for column, needle in self._column_filters.items():
            item = self.item(row, column)
            if item is None or needle not in item.text().casefold():
                return False
        return True

    def _update_header_labels(self) -> None:
        labels = [
            f"{label}  [filter]" if column in self._column_filters else f"{label}  \u25be"
            for column, label in enumerate(self._base_headers)
        ]
        super().setHorizontalHeaderLabels(labels)

    def _show_filter_menu(self, position) -> None:
        column = self.horizontalHeader().logicalIndexAt(position)
        if column < 0:
            return
        label = (
            self._base_headers[column]
            if column < len(self._base_headers)
            else f"Column {column + 1}"
        )
        menu = QMenu(self)
        filter_action = menu.addAction(f"Filter {label}\u2026")
        clear_action = menu.addAction(f"Clear {label} filter")
        clear_action.setEnabled(column in self._column_filters)
        clear_all_action = menu.addAction("Clear all column filters")
        clear_all_action.setEnabled(bool(self._column_filters))
        chosen = menu.exec(self.horizontalHeader().mapToGlobal(position))
        if chosen == filter_action:
            value, accepted = QInputDialog.getText(
                self,
                f"Filter {label}",
                "Show rows containing:",
                text=self._column_filters.get(column, ""),
            )
            if accepted:
                self.set_column_filter(column, value)
        elif chosen == clear_action:
            self.set_column_filter(column, "")
        elif chosen == clear_all_action:
            self.clear_filters()


def _item(value: object, alignment: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    result = NaturalSortTableWidgetItem("" if value is None else str(value))
    if alignment is not None:
        result.setTextAlignment(alignment)
    return result


class CauseEffectMatrixWidget(QWidget):
    """Complete imported matrix with an optional target-node column filter."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Show node"))
        self.node_filter = QComboBox()
        self.node_filter.setMinimumWidth(360)
        self.node_filter.currentIndexChanged.connect(self._apply_node_filter)
        controls.addWidget(self.node_filter)
        self.summary = QLabel()
        controls.addWidget(self.summary)
        controls.addStretch()
        layout.addLayout(controls)

        self.zone_table = QTableWidget()
        self.zone_table.setObjectName("causeEffectFrozenZones")
        self.zone_table.setColumnCount(1)
        self.zone_table.setHorizontalHeaderLabels(["Trigger zone"])
        self.zone_table.setAlternatingRowColors(True)
        self.zone_table.setWordWrap(True)
        self.zone_table.setSortingEnabled(False)
        self.zone_table.verticalHeader().setVisible(False)
        self.zone_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.zone_table.horizontalHeader().setMinimumHeight(82)
        self.zone_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.zone_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.zone_table.setColumnWidth(0, 278)

        self.table = QTableWidget()
        self.table.setObjectName("causeEffectMatrix")
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.table.horizontalHeader().setMinimumHeight(82)

        panes = QHBoxLayout()
        panes.setContentsMargins(0, 0, 0, 0)
        panes.setSpacing(0)
        frozen_pane = QWidget()
        frozen_layout = QVBoxLayout(frozen_pane)
        frozen_layout.setContentsMargins(0, 0, 0, 0)
        frozen_layout.setSpacing(0)
        frozen_layout.addWidget(self.zone_table, 1)
        scrollbar_spacer = QWidget()
        scrollbar_spacer.setFixedHeight(
            self.table.horizontalScrollBar().sizeHint().height()
        )
        frozen_layout.addWidget(scrollbar_spacer)
        frozen_pane.setFixedWidth(280)
        panes.addWidget(frozen_pane)
        panes.addWidget(self.table, 1)
        layout.addLayout(panes, 1)

        self.table.verticalScrollBar().valueChanged.connect(
            self.zone_table.verticalScrollBar().setValue
        )
        self.zone_table.verticalScrollBar().valueChanged.connect(
            self.table.verticalScrollBar().setValue
        )
        self.set_activations([])

    def set_activations(self, activations, output_groups=()) -> None:
        rows = list(activations)
        imported_outputs = list(output_groups)
        self.node_filter.blockSignals(True)
        self.node_filter.clear()
        self.node_filter.addItem("All nodes", None)
        self.zone_table.clearContents()
        self.zone_table.setRowCount(0)
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)

        if not rows:
            self.node_filter.setEnabled(False)
            self.summary.setText(
                "Import a Cause & Effect workbook to populate the matrix."
            )
            self.node_filter.blockSignals(False)
            return

        node_names: dict[int, str] = {}
        for output in imported_outputs:
            node = int(output["target_node"])
            name = str(output["target_node_name"] or "").strip()
            if node not in node_names or (name and not node_names[node]):
                node_names[node] = name
        for activation in rows:
            node = int(activation["target_node"])
            name = str(activation["target_node_name"] or "").strip()
            if node not in node_names or (name and not node_names[node]):
                node_names[node] = name
        for node in sorted(node_names):
            label = f"Node {node}"
            if node_names[node]:
                label += f" - {node_names[node]}"
            self.node_filter.addItem(label, node)
        self.node_filter.setEnabled(True)
        self._populate_table(rows, node_names, imported_outputs)
        self.node_filter.setCurrentIndex(0)
        self.node_filter.blockSignals(False)
        self._apply_node_filter()

    def _populate_table(
        self,
        activations,
        node_names: dict[int, str],
        imported_outputs,
    ) -> None:
        output_names: dict[tuple[int, int], str] = {}
        zone_names: dict[str, str] = {}
        cells: dict[tuple[str, int, int], dict[str, object]] = {}

        for output in imported_outputs:
            key = (
                int(output["target_node"]),
                int(output["output_group"]),
            )
            output_names[key] = str(
                output["output_group_name"] or ""
            ).strip()

        for activation in activations:
            node = int(activation["target_node"])
            output_group = int(activation["output_group"])
            output_name = str(activation["output_group_name"] or "").strip()
            output_key = (node, output_group)
            if output_key not in output_names or (
                output_name and not output_names[output_key]
            ):
                output_names[output_key] = output_name

            trigger_zone = str(activation["trigger_zone"] or "").strip()
            trigger_name = str(activation["trigger_zone_name"] or "").strip()
            if trigger_zone not in zone_names or (
                trigger_name and not zone_names[trigger_zone]
            ):
                zone_names[trigger_zone] = trigger_name

            cell = cells.setdefault(
                (trigger_zone, node, output_group),
                {
                    "styles": [],
                    "matrix_only": False,
                    "comments": [],
                },
            )
            ringing_style = str(activation["ringing_style"] or "").strip()
            styles = cell["styles"]
            if ringing_style and ringing_style not in styles:
                styles.append(ringing_style)
            if activation["reference_status"] != "matched":
                cell["matrix_only"] = True
            comments = str(activation["comments"] or "").strip()
            saved_comments = cell["comments"]
            if comments and comments not in saved_comments:
                saved_comments.append(comments)

        output_groups = sorted(output_names)
        trigger_zones = sorted(zone_names, key=natural_sort_key)
        self.zone_table.setRowCount(len(trigger_zones))
        self.table.setRowCount(len(trigger_zones))
        self.table.setColumnCount(len(output_groups))

        headers = []
        for node, output_group in output_groups:
            node_label = f"Node {node}"
            if node_names.get(node):
                node_label += f" - {node_names[node]}"
            output_name = output_names[(node, output_group)]
            header = f"{node_label}\nOutput group {output_group}"
            if output_name:
                header += f"\n{output_name}"
            headers.append(header)
        self.table.setHorizontalHeaderLabels(headers)
        for column, (node, output_group) in enumerate(output_groups):
            header_item = self.table.horizontalHeaderItem(column)
            header_item.setData(
                Qt.ItemDataRole.UserRole,
                node,
            )
            header_item.setData(
                Qt.ItemDataRole.UserRole + 1,
                output_group,
            )

        for column in range(self.table.columnCount()):
            self.table.setColumnWidth(column, 210)

        for row, trigger_zone in enumerate(trigger_zones):
            trigger_name = zone_names[trigger_zone]
            label = f"Zone {trigger_zone}"
            if trigger_name:
                label += f" - {trigger_name}"
            zone_item = _item(label)
            zone_item.setData(Qt.ItemDataRole.UserRole, trigger_zone)
            zone_item.setFlags(zone_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            zone_item.setBackground(QColor("#e9eff7"))
            self.zone_table.setItem(row, 0, zone_item)

            for column, (node, output_group) in enumerate(
                output_groups,
            ):
                cell = cells.get((trigger_zone, node, output_group))
                if not cell:
                    continue
                styles = cell["styles"]
                style_text = " / ".join(styles)
                item = _item(style_text, Qt.AlignmentFlag.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                status = "Matrix only" if cell["matrix_only"] else "Matched"
                tooltip = (
                    f"{label}\n"
                    f"Node {node}"
                    f"{' - ' + node_names[node] if node_names.get(node) else ''}\n"
                    f"Output group {output_group}"
                    f"{' - ' + output_names[(node, output_group)] if output_names[(node, output_group)] else ''}\n"
                    f"Ringing style: {style_text or 'Not specified'}\n"
                    f"Reference check: {status}"
                )
                if cell["comments"]:
                    tooltip += "\nComments: " + " | ".join(cell["comments"])
                item.setToolTip(tooltip)
                if cell["matrix_only"]:
                    item.setBackground(QColor("#fff3cd"))
                self.table.setItem(row, column, item)

        self.zone_table.resizeRowsToContents()
        self.table.resizeRowsToContents()
        for row in range(len(trigger_zones)):
            height = max(
                self.zone_table.rowHeight(row),
                self.table.rowHeight(row),
            )
            self.zone_table.setRowHeight(row, height)
            self.table.setRowHeight(row, height)
        self.summary.setText(
            f"{len(node_names):,} nodes; {len(output_groups):,} output groups"
        )

    def _apply_node_filter(self) -> None:
        selected_node = self.node_filter.currentData()
        visible_outputs = 0
        for column in range(self.table.columnCount()):
            header_item = self.table.horizontalHeaderItem(column)
            column_node = (
                header_item.data(Qt.ItemDataRole.UserRole)
                if header_item is not None
                else None
            )
            hidden = (
                selected_node is not None
                and int(column_node) != int(selected_node)
            )
            self.table.setColumnHidden(column, hidden)
            if not hidden:
                visible_outputs += 1
        if self.node_filter.isEnabled():
            if selected_node is None:
                node_count = self.node_filter.count() - 1
                self.summary.setText(
                    f"{node_count:,} nodes; {visible_outputs:,} output groups"
                )
            else:
                self.summary.setText(
                    f"Node {int(selected_node)}; "
                    f"{visible_outputs:,} output groups"
                )


def node_current_totals(devices) -> tuple[int, float, float]:
    """Count physical devices and total their quiescent/alarm draw in mA."""
    physical_devices: dict[tuple[int, int], list[object]] = defaultdict(list)
    for device in devices:
        physical_devices[(device["loop"], device["address"])].append(device)
    quiescent_ma = 0.0
    alarm_ma = 0.0
    for channels in physical_devices.values():
        draws = [
            device_current_ma(channel["observed_type"])
            for channel in channels
        ]
        # Multi-channel modules are one physical loop device. Use the largest
        # applicable draw rather than counting every channel.
        quiescent_ma += max(draw[0] for draw in draws)
        alarm_ma += max(draw[1] for draw in draws)
    return len(physical_devices), quiescent_ma, alarm_ma


def _zone_label(zone) -> str:
    number = zone["number"]
    description = str(zone["description"] or "").strip()
    return f"Zone {number} — {description}" if description else f"Zone {number}"


def _fetch_doors(
    repository: ProjectRepository | object,
    floor_id: int | None = None,
) -> list:
    fetch = getattr(repository, "fetch_doors", None)
    return list(fetch(floor_id) if fetch is not None else [])


def _fetch_map_assets(
    repository: ProjectRepository | object,
    floor_id: int | None = None,
) -> list:
    fetch = getattr(repository, "fetch_map_assets", None)
    if fetch is None:
        return []
    try:
        rows = list(fetch(floor_id))
    except TypeError:
        rows = list(fetch())
    if floor_id is None:
        return rows
    return [
        row
        for row in rows
        if "floor_id" not in dict(row)
        or int(row["floor_id"]) == int(floor_id)
    ]


def _door_states(
    door,
    fire_active: bool = False,
    activated_device_keys: set[str] | None = None,
) -> tuple[str | None, str | None]:
    access_state = None
    hold_open_state = None
    access_activated = (
        fire_active
        if activated_device_keys is None
        else bool(
            door.get("access_device_key")
            and str(door["access_device_key"]) in activated_device_keys
        )
    )
    hold_open_activated = (
        fire_active
        if activated_device_keys is None
        else bool(
            door.get("hold_open_device_key")
            and str(door["hold_open_device_key"]) in activated_device_keys
        )
    )
    if door["has_access_control"]:
        access_state = (
            "UNLOCKED"
            if access_activated
            else door["access_normal_state"]
        )
    if door["has_hold_open"]:
        hold_open_state = (
            "CLOSED"
            if hold_open_activated
            else door["hold_open_normal_state"]
        )
    return access_state, hold_open_state


def _door_status_text(
    door,
    fire_active: bool = False,
    activated_device_keys: set[str] | None = None,
) -> str:
    access_state, hold_open_state = _door_states(
        door,
        fire_active,
        activated_device_keys,
    )
    states = []
    if access_state:
        states.append(f"Access: {access_state}")
    if hold_open_state:
        states.append(f"Hold-open: {hold_open_state}")
    return " · ".join(states)


def _door_zone_text(door) -> str:
    zone_a = int(door["zone_a"])
    zone_b = int(door["zone_b"])
    return (
        f"Zone {zone_a} (internal)"
        if zone_a == zone_b
        else f"{zone_a} / {zone_b}"
    )


def _nearest_zone_numbers(
    repository: ProjectRepository | object,
    floor_id: int,
    scene_position: tuple[float, float],
    limit: int = 2,
) -> list[int]:
    fetch_geometry = getattr(repository, "fetch_zone_geometry", None)
    if fetch_geometry is None:
        return []
    # Zone polygons retain DXF Y coordinates; placed sprites use scene Y.
    point = Point(float(scene_position[0]), -float(scene_position[1]))
    distances: dict[int, float] = {}
    containing_zones: set[int] = set()
    for row in fetch_geometry():
        if int(row["floor_id"]) != int(floor_id):
            continue
        try:
            polygon = Polygon(json.loads(row["geometry_json"]))
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
        except (TypeError, ValueError):
            continue
        if polygon.is_empty:
            continue
        zone = int(row["zone"])
        if polygon.contains(point):
            containing_zones.add(zone)
        distance = float(polygon.distance(point))
        distances[zone] = min(distance, distances.get(zone, math.inf))
    requested = max(int(limit), 0)
    if len(containing_zones) == 1:
        zone = next(iter(containing_zones))
        return [zone] * min(requested, 2)
    return [
        zone
        for zone, _distance in sorted(
            distances.items(),
            key=lambda item: (item[1], item[0]),
        )[:requested]
    ]


def _add_door_graphics(
    scene: QGraphicsScene,
    door,
    fire_active: bool = False,
    movable: bool = False,
    activated_device_keys: set[str] | None = None,
) -> list[QGraphicsItem]:
    sprite_x = door.get("sprite_x")
    sprite_y = door.get("sprite_y")
    if sprite_x is None or sprite_y is None:
        sprite_x = (float(door["start_x"]) + float(door["end_x"])) / 2.0
        sprite_y = (float(door["start_y"]) + float(door["end_y"])) / 2.0
    centre = QPointF(float(sprite_x), float(sprite_y))
    door_type = str(door.get("door_type") or "SINGLE").upper()
    rotation = float(door.get("rotation_degrees") or 0)
    access_state, hold_open_state = _door_states(
        door,
        fire_active,
        activated_device_keys,
    )
    output_active = (
        fire_active
        if activated_device_keys is None
        else any(
            key
            and str(key) in activated_device_keys
            for key in (
                door.get("access_device_key"),
                door.get("hold_open_device_key"),
            )
        )
    )
    colour = QColor("#198754" if output_active else "#183153")
    pen = QPen(colour, DOOR_PEN_WIDTH)
    path = QPainterPath()
    opening = (
        DOUBLE_DOOR_OPENING
        if door_type == "DOUBLE"
        else SINGLE_DOOR_OPENING
    )
    left = -opening / 2.0
    right = opening / 2.0
    # Short jamb lines make the marker read as a doorway even without a DXF wall.
    path.moveTo(left - DOOR_JAMB_LENGTH, 0)
    path.lineTo(left, 0)
    path.moveTo(right, 0)
    path.lineTo(right + DOOR_JAMB_LENGTH, 0)
    held_open = hold_open_state == "HELD OPEN"
    if door_type == "DOUBLE":
        leaf = opening / 2.0
        if held_open:
            path.moveTo(left, 0)
            path.lineTo(left, -leaf)
            path.arcMoveTo(
                QRectF(left - leaf, -leaf, leaf * 2.0, leaf * 2.0),
                0,
            )
            path.arcTo(
                QRectF(left - leaf, -leaf, leaf * 2.0, leaf * 2.0),
                0,
                90,
            )
            path.moveTo(right, 0)
            path.lineTo(right, -leaf)
            path.arcMoveTo(
                QRectF(right - leaf, -leaf, leaf * 2.0, leaf * 2.0),
                180,
            )
            path.arcTo(
                QRectF(right - leaf, -leaf, leaf * 2.0, leaf * 2.0),
                180,
                -90,
            )
        else:
            path.addRect(
                QRectF(
                    left,
                    -DOOR_LEAF_DEPTH / 2.0,
                    opening / 2.0,
                    DOOR_LEAF_DEPTH,
                )
            )
            path.moveTo(0, -80)
            path.lineTo(0, 80)
            path.addRect(
                QRectF(
                    0,
                    -DOOR_LEAF_DEPTH / 2.0,
                    opening / 2.0,
                    DOOR_LEAF_DEPTH,
                )
            )
            path.addEllipse(QRectF(-150, -55, 55, 55))
            path.addEllipse(QRectF(95, -55, 55, 55))
    else:
        if held_open:
            path.moveTo(left, 0)
            path.lineTo(left, -opening)
            swing_rect = QRectF(
                left - opening,
                -opening,
                opening * 2.0,
                opening * 2.0,
            )
            path.arcMoveTo(swing_rect, 0)
            path.arcTo(swing_rect, 0, 90)
        else:
            path.addRect(
                QRectF(
                    left,
                    -DOOR_LEAF_DEPTH / 2.0,
                    opening,
                    DOOR_LEAF_DEPTH,
                )
            )
            path.addEllipse(QRectF(right - 150, -55, 55, 55))
    door_item = scene.addPath(
        path,
        pen,
        (
            QBrush(Qt.BrushStyle.NoBrush)
            if held_open
            else QBrush(colour)
        ),
    )
    door_item.setPos(centre)
    door_item.setRotation(rotation)
    door_item.setZValue(40)
    door_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
    door_item.setFlag(
        QGraphicsItem.GraphicsItemFlag.ItemIsMovable,
        movable,
    )
    door_item.setData(70, int(door["id"]))
    door_item.setData(72, True)

    graphics: list[QGraphicsItem] = [door_item]
    if access_state:
        locked = access_state == "LOCKED"
        lock_colour = QColor("#dc3545" if locked else "#198754")
        lock_icon = qta.icon(
            "fa5s.lock" if locked else "fa5s.unlock",
            color=lock_colour.name(),
        )
        lock_item = scene.addPixmap(lock_icon.pixmap(32, 32))
        lock_item.setParentItem(door_item)
        lock_item.setScale(DOOR_PADLOCK_SCALE)
        lock_item.setPos(
            QPointF(opening / 2.0 + DOOR_JAMB_LENGTH, -460)
        )
        lock_item.setZValue(1)
        lock_item.setData(70, int(door["id"]))
        lock_item.setData(71, lock_colour.name())
        graphics.append(lock_item)
    tooltip = (
        f"{door['name']} ({door_type.title()} door)\n"
        f"Zones {_door_zone_text(door)}\n"
        f"{_door_status_text(door, fire_active, activated_device_keys)}"
    )
    door_item.setToolTip(tooltip)
    for item in graphics[1:]:
        item.setToolTip(tooltip)
    return graphics


def _device_symbol(row: dict) -> str:
    text = " ".join(
        str(row.get(key) or "")
        for key in (
            "observed_type",
            "text",
            "panel",
            "output_group_name",
        )
    ).casefold()
    if "call point" in text or "mcp" in text:
        return "Call point"
    if any(word in text for word in ("power supply", "psu", "mains unit")):
        return "Power supply"
    if any(word in text for word in ("beacon", "vad", "vid")):
        return "Beacon"
    if "sounder" in text:
        return "Sounder"
    if row.get("output_group") is not None or any(
        word in text
        for word in ("relay", "input", "output", "door holder", "interface")
    ):
        return "Output device"
    if any(word in text for word in ("smoke", "heat", "detector", "sensor", "multi")):
        return "Detector"
    return "Device"


def _sounder_style_zone_state(style: str | None) -> str | None:
    values = {
        value.strip().upper()
        for value in str(style or "").split(",")
        if value.strip()
    }
    if any(
        value in {"E", "TE"} or value.startswith("EVAC")
        for value in values
    ):
        return "EVACUATE"
    if any(
        value in {"A", "TA"} or value.startswith("ALERT")
        for value in values
    ):
        return "ALERT"
    return None


def _physical_device_payloads(rows) -> list[dict]:
    """Collapse address channels into one zone-map payload per physical device."""
    grouped: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    for source_row in rows:
        row = dict(source_row)
        grouped[
            (
                int(row["node"]),
                int(row["loop"]),
                int(row["address"]),
            )
        ].append(row)

    payloads: list[dict] = []
    for channels in grouped.values():
        channels.sort(
            key=lambda row: (
                int(row["sub_address"]) != 0,
                int(row["sub_address"]),
                str(row["stable_key"]),
            )
        )
        representative = channels[0]
        member_keys = tuple(str(row["stable_key"]) for row in channels)
        sub_addresses = tuple(
            sorted({int(row["sub_address"]) for row in channels})
        )
        zones = tuple(sorted({int(row["zone"]) for row in channels}))
        names = []
        for row in channels:
            name = str(row.get("text") or "").strip() or catalogue_display_name(
                row.get("product_code"),
                row.get("observed_type"),
            )
            if name and name not in names:
                names.append(name)
        name = names[0] if names else "Unnamed device"
        if len(names) > 1:
            name += f" (+{len(names) - 1} channels)"
        key = str(representative["stable_key"])
        payloads.append(
            {
                **representative,
                "kind": "device",
                "key": key,
                "stable_key": key,
                "symbol": _device_symbol(representative),
                "name": name,
                "member_keys": member_keys,
                "sub_addresses": sub_addresses,
                "zones": zones,
                "channel_count": len(channels),
                "channel_details": tuple(
                    {
                        "sub_address": int(row["sub_address"]),
                        "name": (
                            str(row.get("text") or "").strip()
                            or catalogue_display_name(
                                row.get("product_code"),
                                row.get("observed_type"),
                            )
                        ),
                    }
                    for row in channels
                ),
            }
        )
    return sorted(
        payloads,
        key=lambda payload: (
            int(payload["node"]),
            int(payload["loop"]),
            int(payload["address"]),
        ),
    )


SYMBOL_COLOURS = {
    "Detector": QColor("#0d6efd"),
    "Call point": QColor("#dc3545"),
    "Sounder": QColor("#fd7e14"),
    "Output device": QColor("#6f42c1"),
    "Power supply": QColor("#198754"),
    "Panel": QColor("#183153"),
    "Device": QColor("#64748b"),
}

# Fire-alarm drawing symbols use real scene dimensions so they scale with the
# architectural underlay. The dimensions are millimetres, matching the DXF.
FIRE_SYMBOL_RED = QColor("#d71920")
DEVICE_SYMBOL_RADIUS = 140.0
DEVICE_SYMBOL_PEN_WIDTH = 16.0
DEVICE_SYMBOL_TEXT_SIZE = 140
DEVICE_ADDRESS_TEXT_SIZE = 80
MAP_POPUP_TEXT_SIZE = 18
MAP_POPUP_PADDING = 16.0


def _add_map_popup(
    scene: QGraphicsScene,
    existing_popup: QGraphicsItem | None,
    content: str,
    anchor_item: QGraphicsItem,
    view: QGraphicsView,
) -> QGraphicsItem:
    """Add a readable screen-sized information card beside a map item."""
    if existing_popup is not None:
        try:
            scene.removeItem(existing_popup)
        except RuntimeError:
            pass
    text = scene.addSimpleText(content)
    font = QFont("Arial")
    font.setPixelSize(MAP_POPUP_TEXT_SIZE)
    text.setFont(font)
    text.setBrush(QBrush(QColor("#172033")))
    bounds = text.boundingRect()
    popup = scene.addRect(
        0,
        0,
        bounds.width() + MAP_POPUP_PADDING * 2.0,
        bounds.height() + MAP_POPUP_PADDING * 2.0,
        QPen(QColor("#183153"), 2),
        QBrush(QColor("#ffffff")),
    )
    popup.setPos(
        anchor_item.sceneBoundingRect().topRight() + QPointF(12, 0)
    )
    popup.setZValue(2000)
    popup.setFlag(
        QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
        True,
    )
    popup.setData(90, "map-popup")
    popup.setData(
        91,
        max(abs(float(view.transform().m11())), 1e-9),
    )
    text.setParentItem(popup)
    text.setPos(MAP_POPUP_PADDING, MAP_POPUP_PADDING)
    text.setZValue(1)
    return popup


def _device_popup_content(
    *,
    name: str,
    node: int,
    zones,
    loop: int,
    address: int,
    status: str,
    channels=(),
) -> str:
    lines = [
        str(name),
        f"Node: {node}",
        f"Zone: {', '.join(map(str, zones))}",
        f"Loop: {loop}",
        f"Address: {address}",
        f"Status: {status}",
    ]
    channel_rows = list(channels)
    if len(channel_rows) > 1:
        lines.append("Sub-addresses:")
        lines.extend(
            f"  {channel['sub_address']}: {channel['name']}"
            for channel in channel_rows
        )
    return "\n".join(lines)


def _device_symbol_code(payload: dict) -> str:
    symbol = str(payload.get("symbol") or "Device")
    text = " ".join(
        str(payload.get(key) or "")
        for key in ("observed_type", "name", "text", "panel")
    ).casefold()
    if symbol == "Panel":
        return "FAP"
    if symbol == "Output device":
        return "I/O"
    if symbol == "Power supply":
        return "PSU"
    if symbol == "Call point":
        return ""
    if "void" in text:
        return "SV"
    if "multi" in text:
        return "M"
    if "heat" in text:
        return "H"
    # Optical detectors deliberately use O rather than the generic smoke S.
    if "optical" in text:
        return "O"
    if "smoke" in text:
        return "S"
    if symbol == "Detector":
        return "D"
    if symbol == "Sounder":
        return "S"
    if symbol == "Beacon":
        return "B"
    return "?"


def _add_fire_alarm_symbol(
    scene: QGraphicsScene,
    payload: dict,
    x: float,
    y: float,
    tooltip: str,
    *,
    selectable: bool = False,
    show_address: bool = False,
    fill_colour: QColor | None = None,
) -> QGraphicsItem:
    """Draw a conventional, scene-scaled fire-alarm plan symbol."""
    symbol = str(payload.get("symbol") or "Device")
    description = " ".join(
        str(payload.get(key) or "")
        for key in ("observed_type", "name", "text")
    ).casefold()
    code = _device_symbol_code(payload)
    pen = QPen(FIRE_SYMBOL_RED, DEVICE_SYMBOL_PEN_WIDTH)
    pen.setCosmetic(False)
    brush = QBrush(fill_colour or QColor("#ffffff"))
    radius = DEVICE_SYMBOL_RADIUS

    detector_base = symbol == "Detector" or (
        symbol == "Sounder"
        and any(
            word in description
            for word in ("detector", "optical", "smoke", "heat", "multi")
        )
    )
    if detector_base or symbol in {"Call point", "Device", "Beacon"}:
        marker = scene.addEllipse(
            -radius,
            -radius,
            radius * 2.0,
            radius * 2.0,
            pen,
            brush,
        )
    else:
        wide = symbol in {"Panel", "Output device", "Power supply"}
        width = 430.0 if wide else 280.0
        height = 240.0 if wide else 280.0
        marker = scene.addRect(
            -width / 2.0,
            -height / 2.0,
            width,
            height,
            pen,
            brush,
        )

    marker.setPos(float(x), float(y))
    marker.setZValue(20)
    marker.setToolTip(tooltip)
    marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, selectable)
    marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, selectable)
    asset_key = (payload.get("kind"), payload.get("key"))
    if all(value is not None for value in asset_key):
        marker.setData(10, asset_key)
        marker.setData(11, True)

    def add_child(item: QGraphicsItem, z_offset: float = 1.0) -> QGraphicsItem:
        item.setParentItem(marker)
        item.setZValue(z_offset)
        item.setToolTip(tooltip)
        if all(value is not None for value in asset_key):
            item.setData(10, asset_key)
        return item

    if symbol == "Call point":
        dot_radius = 42.0
        dot = scene.addEllipse(
            -dot_radius,
            -dot_radius,
            dot_radius * 2.0,
            dot_radius * 2.0,
            QPen(Qt.PenStyle.NoPen),
            QBrush(FIRE_SYMBOL_RED),
        )
        add_child(dot)
    elif code:
        code_item = scene.addSimpleText(code)
        code_font = QFont("Arial")
        code_font.setPixelSize(
            98
            if len(code) >= 3
            else (112 if len(code) == 2 else DEVICE_SYMBOL_TEXT_SIZE)
        )
        code_item.setFont(code_font)
        code_item.setBrush(QBrush(QColor("#111827")))
        bounds = code_item.boundingRect()
        code_item.setPos(-bounds.width() / 2.0, -bounds.height() / 2.0)
        add_child(code_item)

    if symbol == "Sounder":
        right_edge = radius if detector_base else 140.0
        horn = QPainterPath()
        horn.moveTo(right_edge, -66.0)
        horn.lineTo(right_edge + 75.0, -66.0)
        horn.lineTo(right_edge + 155.0, -125.0)
        horn.lineTo(right_edge + 155.0, 125.0)
        horn.lineTo(right_edge + 75.0, 66.0)
        horn.lineTo(right_edge, 66.0)
        add_child(
            scene.addPath(horn, pen, QBrush(Qt.BrushStyle.NoBrush))
        )
        if any(word in description for word in ("beacon", "vad", "vid")):
            rays = QPainterPath()
            for start, end in (
                ((right_edge + 180.0, -105.0), (right_edge + 245.0, -155.0)),
                ((right_edge + 190.0, 0.0), (right_edge + 270.0, 0.0)),
                ((right_edge + 180.0, 105.0), (right_edge + 245.0, 155.0)),
            ):
                rays.moveTo(*start)
                rays.lineTo(*end)
            add_child(scene.addPath(rays, pen))
    elif symbol == "Beacon":
        rays = QPainterPath()
        for start, end in (
            ((-105.0, -155.0), (-155.0, -225.0)),
            ((0.0, -175.0), (0.0, -265.0)),
            ((105.0, -155.0), (155.0, -225.0)),
        ):
            rays.moveTo(*start)
            rays.lineTo(*end)
        add_child(scene.addPath(rays, pen))

    if show_address:
        address = (
            f"N{payload.get('node')}"
            if payload.get("kind") == "panel"
            else f"{payload.get('loop')}/{payload.get('address')}"
        )
        address_item = scene.addSimpleText(address)
        address_font = QFont("Arial")
        address_font.setPixelSize(DEVICE_ADDRESS_TEXT_SIZE)
        address_item.setFont(address_font)
        address_item.setBrush(QBrush(QColor("#7f1d1d")))
        address_item.setPos(
            marker.boundingRect().right() + 55.0,
            -address_item.boundingRect().height() / 2.0,
        )
        add_child(address_item, 2.0)

    return marker

# Fixed floor-plan dimensions in millimetres. These are scene-space sizes, so
# the sprites grow and shrink with the DXF underlay as the camera zooms.
SINGLE_DOOR_OPENING = 900.0
DOUBLE_DOOR_OPENING = 1800.0
DOOR_JAMB_LENGTH = 120.0
DOOR_LEAF_DEPTH = 140.0
DOOR_PEN_WIDTH = 28.0
DOOR_PADLOCK_SCALE = 14.0


class MapGraphicsView(QGraphicsView):
    scene_clicked = Signal(QPointF)
    scene_right_clicked = Signal(object, QPointF)
    scene_double_clicked = Signal(QPointF)
    item_moved = Signal(object)
    drawing_cancelled = Signal()
    drawing_undo_requested = Signal()
    drawing_finish_requested = Signal()
    polygon_selection_requested = Signal(QPointF, bool, object)

    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None):
        super().__init__(scene, parent)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self._pan_button: Qt.MouseButton | None = None
        self._pan_start = None
        self._pan_last = None
        self._pan_dragged = False
        self._press_item = None
        self._selection_before_press: list[QGraphicsItem] = []
        self._moving_item = None
        self._item_drag_start_scene: QPointF | None = None
        self._item_start_pos: QPointF | None = None
        self.draw_mode = False
        self.edit_geometry_mode = False

    @staticmethod
    def _asset_root_item(
        item: QGraphicsItem | None,
    ) -> QGraphicsItem | None:
        if not MapGraphicsView._item_is_valid(item):
            return None
        if item.data(10) is None:
            return None
        if item.data(11):
            return item
        scene = item.scene()
        if scene is None:
            return None
        key = item.data(10)
        return next(
            (
                candidate
                for candidate in scene.items()
                if candidate.data(11)
                and candidate.data(10) == key
            ),
            None,
        )

    @staticmethod
    def _door_root_item(
        item: QGraphicsItem | None,
    ) -> QGraphicsItem | None:
        if not MapGraphicsView._item_is_valid(item):
            return None
        if item.data(70) is None:
            return None
        if item.data(72):
            return item
        scene = item.scene()
        if scene is None:
            return None
        door_id = item.data(70)
        return next(
            (
                candidate
                for candidate in scene.items()
                if candidate.data(72)
                and candidate.data(70) == door_id
            ),
            None,
        )

    @staticmethod
    def _item_is_valid(item: QGraphicsItem | None) -> bool:
        if item is None:
            return False
        try:
            item.scene()
        except RuntimeError:
            return False
        return True

    def cancel_scene_interaction(self) -> None:
        """Drop graphics-item references before the owning scene is rebuilt."""
        self._pan_button = None
        self._pan_start = None
        self._pan_last = None
        self._pan_dragged = False
        self._press_item = None
        self._selection_before_press = []
        self._moving_item = None
        self._item_drag_start_scene = None
        self._item_start_pos = None

    def mousePressEvent(self, event) -> None:
        point = event.position().toPoint()
        if self.draw_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                self.scene_clicked.emit(self.mapToScene(point))
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                self.scene_double_clicked.emit(self.mapToScene(point))
                event.accept()
                return
            if event.button() == Qt.MouseButton.MiddleButton:
                self._pan_button = event.button()
                self._pan_start = point
                self._pan_last = point
                self._pan_dragged = False
                self._press_item = None
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        if event.button() == Qt.MouseButton.RightButton:
            item = self.itemAt(point)
            self.scene_right_clicked.emit(
                self._asset_root_item(item)
                or self._door_root_item(item)
                or item,
                self.mapToScene(point),
            )
            event.accept()
            return
        if event.button() in {
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.MiddleButton,
        }:
            raw_item = self.itemAt(point)
            pressed_item = (
                self._asset_root_item(raw_item)
                or self._door_root_item(raw_item)
                or raw_item
            )
            self._selection_before_press = (
                list(self.scene().selectedItems())
                if self.scene() is not None
                else []
            )
            if (
                event.button() == Qt.MouseButton.LeftButton
                and pressed_item is not None
                and (
                    pressed_item.data(10) is not None
                    or pressed_item.data(70) is not None
                )
                and bool(
                    pressed_item.flags()
                    & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                )
            ):
                if not (
                    event.modifiers()
                    & Qt.KeyboardModifier.ControlModifier
                ):
                    self.scene().clearSelection()
                pressed_item.setSelected(True)
                self._moving_item = pressed_item
                self._item_drag_start_scene = self.mapToScene(point)
                self._item_start_pos = QPointF(pressed_item.pos())
                event.accept()
                return
            if (
                event.button() == Qt.MouseButton.LeftButton
                and pressed_item is not None
                and (
                    pressed_item.data(31) is not None
                    or (
                        self.edit_geometry_mode
                        and pressed_item.data(30) is not None
                    )
                )
            ):
                self._moving_item = pressed_item
                super().mousePressEvent(event)
                return
            self._pan_button = event.button()
            self._pan_start = point
            self._pan_last = point
            self._pan_dragged = False
            self._press_item = pressed_item
            if event.button() == Qt.MouseButton.MiddleButton:
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._moving_item is not None
            and not self._item_is_valid(self._moving_item)
        ):
            self.cancel_scene_interaction()
        if (
            self._moving_item is not None
            and (
                self._moving_item.data(10) is not None
                or self._moving_item.data(70) is not None
            )
            and self._item_drag_start_scene is not None
            and self._item_start_pos is not None
        ):
            delta = (
                self.mapToScene(event.position().toPoint())
                - self._item_drag_start_scene
            )
            self._moving_item.setPos(self._item_start_pos + delta)
            event.accept()
            return
        if (
            self._pan_button is not None
            and self._pan_last is not None
            and (
                not self.draw_mode
                or self._pan_button == Qt.MouseButton.MiddleButton
            )
        ):
            point = event.position().toPoint()
            if (
                not self._pan_dragged
                and self._pan_start is not None
                and (point - self._pan_start).manhattanLength()
                >= QApplication.startDragDistance()
            ):
                self._pan_dragged = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._pan_dragged:
                delta = point - self._pan_last
                self._pan_view_by(delta)
                self._pan_last = point
                event.accept()
                return
        super().mouseMoveEvent(event)

    def _pan_view_by(self, viewport_delta: QPoint) -> None:
        """Pan freely, expanding the scene when the viewport passes its edge."""
        viewport = self.viewport().rect()
        centre = self.mapToScene(viewport.center())
        scene_delta = (
            self.mapToScene(QPoint(0, 0))
            - self.mapToScene(viewport_delta)
        )
        target = centre + scene_delta
        visible = self.mapToScene(viewport).boundingRect()
        required = QRectF(
            target.x() - visible.width() / 2.0,
            target.y() - visible.height() / 2.0,
            visible.width(),
            visible.height(),
        )
        # One extra viewport in every direction keeps the next drag smooth.
        required = required.adjusted(
            -visible.width(),
            -visible.height(),
            visible.width(),
            visible.height(),
        )
        scene = self.scene()
        if scene is not None:
            current = scene.sceneRect()
            scene.setSceneRect(
                current.united(required)
                if current.isValid()
                else required
            )
        self.centerOn(target)

    def mouseReleaseEvent(self, event) -> None:
        if self.draw_mode and event.button() in {
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.RightButton,
        }:
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._moving_item is not None
        ):
            moved_item = self._moving_item
            self._moving_item = None
            if not self._item_is_valid(moved_item):
                self._item_drag_start_scene = None
                self._item_start_pos = None
                event.accept()
                return
            if (
                moved_item.data(10) is not None
                or moved_item.data(70) is not None
            ):
                self._item_drag_start_scene = None
                self._item_start_pos = None
                self.item_moved.emit(moved_item)
                event.accept()
                return
            super().mouseReleaseEvent(event)
            self.item_moved.emit(moved_item)
            return
        if event.button() == self._pan_button:
            point = event.position().toPoint()
            was_dragged = self._pan_dragged
            pressed_item = self._press_item
            selection_before_press = self._selection_before_press
            self._pan_button = None
            self._pan_start = None
            self._pan_last = None
            self._pan_dragged = False
            self._press_item = None
            self._selection_before_press = []
            if self.draw_mode:
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.unsetCursor()
            if was_dragged:
                event.accept()
                return
            if event.button() == Qt.MouseButton.MiddleButton:
                event.accept()
                return
            pressed_item_valid = self._item_is_valid(pressed_item)
            pressed_is_asset = (
                pressed_item_valid
                and pressed_item.data(10) is not None
            )
            polygon_click = (
                pressed_item_valid
                and not pressed_is_asset
            )
            selection_before_press = [
                item
                for item in selection_before_press
                if self._item_is_valid(item)
            ]
            super().mouseReleaseEvent(event)
            if polygon_click:
                self.polygon_selection_requested.emit(
                    self.mapToScene(point),
                    bool(
                        event.modifiers()
                        & Qt.KeyboardModifier.ControlModifier
                    ),
                    selection_before_press,
                )
            self.scene_clicked.emit(self.mapToScene(point))
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.draw_mode and event.button() == Qt.MouseButton.LeftButton:
            self.scene_double_clicked.emit(
                self.mapToScene(event.position().toPoint())
            )
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self.scale(factor, factor)
        event.accept()

    def scale(self, sx: float, sy: float) -> None:
        super().scale(sx, sy)
        self._update_map_popup_scales()

    def _update_map_popup_scales(self) -> None:
        scene = self.scene()
        if scene is None:
            return
        current_scale = max(abs(float(self.transform().m11())), 1e-9)
        for item in scene.items():
            if item.data(90) != "map-popup":
                continue
            baseline = float(item.data(91) or current_scale)
            item.setScale(max(1.0, current_scale / baseline))

    def keyPressEvent(self, event) -> None:
        if self.draw_mode:
            if event.key() in {
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
            }:
                self.drawing_finish_requested.emit()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Escape:
                self.drawing_cancelled.emit()
                event.accept()
                return
            if event.key() in {Qt.Key.Key_Backspace, Qt.Key.Key_Delete}:
                self.drawing_undo_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class Page(QWidget):
    def __init__(self, title: str):
        super().__init__()
        self.repository: ProjectRepository | None = None
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 18, 20, 18)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        self.layout.addWidget(heading)

    def set_repository(self, repository: ProjectRepository | None) -> None:
        self.repository = repository
        self.refresh()

    def refresh(self) -> None:
        pass


class DashboardPage(Page):
    def __init__(self):
        super().__init__("Commissioning overview")
        cards = QHBoxLayout()
        self.values: dict[str, QLabel] = {}
        for key, label in (
            ("panels", "Network nodes"),
            ("devices", "Physical devices"),
            ("zones", "Detection zones"),
            ("changes", "Latest changes"),
        ):
            frame = QFrame()
            frame.setObjectName("card")
            layout = QVBoxLayout(frame)
            caption = QLabel(label)
            value = QLabel("—")
            value.setObjectName("cardValue")
            layout.addWidget(caption)
            layout.addWidget(value)
            cards.addWidget(frame)
            self.values[key] = value
        self.layout.addLayout(cards)
        self.project_label = QLabel("Create or open a commissioning project to begin.")
        self.project_label.setWordWrap(True)
        self.layout.addWidget(self.project_label)
        self.warnings = QTextEdit()
        self.warnings.setReadOnly(True)
        self.warnings.setPlaceholderText("Import warnings and engineering notes appear here.")
        self.layout.addWidget(self.warnings, 1)

    def refresh(self) -> None:
        if not self.repository:
            for value in self.values.values():
                value.setText("—")
            self.project_label.setText("Create or open a commissioning project to begin.")
            self.warnings.clear()
            return
        panels = self.repository.fetch_panels()
        devices = self.repository.fetch_devices()
        zones = self.repository.fetch_zones()
        physical = {(row["node"], row["loop"], row["address"]) for row in devices}
        self.values["panels"].setText(str(len(panels)))
        self.values["devices"].setText(f"{len(physical):,}")
        self.values["zones"].setText(str(len(zones)))
        self.values["changes"].setText(str(len(self.repository.fetch_changes())))
        self.project_label.setText(
            f"<b>{self.repository.name}</b><br>{self.repository.path}<br>"
            "Configuration snapshots are immutable; drawing assignments, test results and "
            "approved custom rules remain project data."
        )
        snapshots = self.repository.fetch_snapshots()
        if snapshots:
            warnings = json.loads(snapshots[0]["warnings_json"])
            self.warnings.setPlainText("\n".join(f"• {warning}" for warning in warnings))


class AboutPage(Page):
    def __init__(self):
        super().__init__("About")
        card = QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(720)
        card_layout = QVBoxLayout(card)

        application_name = QLabel("FirePanel Commissioning")
        application_name.setObjectName("cardValue")
        card_layout.addWidget(application_name)

        self.version_label = QLabel(f"Version {__version__}")
        self.version_label.setObjectName("aboutVersion")
        card_layout.addWidget(self.version_label)

        description = QLabel(
            "Desktop commissioning workspace for fire-alarm configuration, "
            "Cause & Effect, zone drawings, testing, and tracked changes."
        )
        description.setWordWrap(True)
        card_layout.addWidget(description)

        licence = QLabel(
            "Licensed under the GNU Affero General Public License v3.0."
        )
        licence.setWordWrap(True)
        card_layout.addWidget(licence)

        self.layout.addWidget(card)
        self.layout.addStretch()


class DevicesPage(Page):
    def __init__(self):
        super().__init__("Devices")
        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter node, address, zone, text or type…")
        self.search.textChanged.connect(self.refresh)
        controls.addWidget(self.search)
        self.layout.addLayout(controls)
        headers = [
            "Node", "Panel", "Loop", "Address", "Sub address", "Zone",
            "Device text", "Type", "Product", "Output group",
        ]
        self.table = FilterableTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setColumnWidth(1, 240)
        self.table.setColumnWidth(6, 280)
        self.layout.addWidget(self.table, 1)

    def refresh(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if not self.repository:
            return
        needle = self.search.text().casefold()
        for row in self.repository.fetch_devices():
            haystack = " ".join(str(row[key] or "") for key in row.keys()).casefold()
            if needle and needle not in haystack:
                continue
            index = self.table.rowCount()
            self.table.insertRow(index)
            values = [
                row["node"], row["panel"], row["loop"], row["address"], row["sub_address"],
                row["zone"], row["text"],
                catalogue_display_name(row["product_code"], row["observed_type"]),
                row["product_code"], row["output_group"],
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, _item(value))
        self.table.setSortingEnabled(True)


class ZonesPage(Page):
    def __init__(self):
        super().__init__("Zones")
        self.table = FilterableTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Zone number", "Zone description", "Nodes", "Devices"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setColumnWidth(1, 520)
        self.table.setColumnWidth(2, 180)
        self.layout.addWidget(self.table, 1)

    def refresh(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if self.repository:
            for zone in self.repository.fetch_zones():
                row_index = self.table.rowCount()
                self.table.insertRow(row_index)
                for column, value in enumerate(
                    [
                        zone["number"],
                        zone["description"],
                        zone["nodes"] or "",
                        zone["device_count"],
                    ]
                ):
                    self.table.setItem(row_index, column, _item(value))
        self.table.setSortingEnabled(True)
        self.table.apply_filters()


class NodesPage(Page):
    def __init__(self):
        super().__init__("Nodes and power")
        note = QLabel(
            "Loop current and autonomy are engineering estimates until manufacturer current data, "
            "battery capacity and alarm loading are confirmed."
        )
        note.setWordWrap(True)
        self.layout.addWidget(note)
        self.table = FilterableTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            [
                "Node", "Panel", "Loops", "Devices", "Quiescent draw mA",
                "Alarm draw mA", "Battery Ah", "Standby h", "Alarm min",
                "Required Ah", "Autonomy check",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setColumnWidth(1, 280)
        self.layout.addWidget(self.table, 1)
        button = QPushButton("Set selected node battery / autonomy")
        button.clicked.connect(self.set_power)
        self.layout.addWidget(button)

    def refresh(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if not self.repository:
            return
        power = self.repository.fetch_node_power()
        devices_by_node: dict[int, list[object]] = defaultdict(list)
        for device in self.repository.fetch_devices():
            devices_by_node[device["node"]].append(device)
        for panel in self.repository.fetch_panels():
            node = panel["node"]
            device_count, quiescent_ma, alarm_ma = node_current_totals(
                devices_by_node[node]
            )
            settings = power.get(node)
            battery = settings["battery_ah"] if settings else None
            standby = settings["standby_hours"] if settings else 24.0
            alarm_minutes = settings["alarm_minutes"] if settings else 30.0
            factor = settings["safety_factor"] if settings else 1.25
            required = (
                (quiescent_ma / 1000) * standby
                + (alarm_ma / 1000) * (alarm_minutes / 60)
            ) * factor
            status = "Enter battery"
            if battery is not None:
                status = "PASS (estimate)" if battery >= required else "REVIEW"
            row_index = self.table.rowCount()
            self.table.insertRow(row_index)
            values = [
                node, panel["name"], panel["loops_json"], device_count,
                f"{quiescent_ma:.1f}", f"{alarm_ma:.1f}",
                "" if battery is None else f"{battery:.1f}", f"{standby:.1f}",
                f"{alarm_minutes:.0f}", f"{required:.1f}", status,
            ]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, _item(value))
        self.table.setSortingEnabled(True)
        self.table.apply_filters()

    def set_power(self) -> None:
        if not self.repository or self.table.currentRow() < 0:
            return
        node = int(self.table.item(self.table.currentRow(), 0).text())
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Node {node} power assumptions")
        form = QFormLayout(dialog)
        battery = QDoubleSpinBox()
        battery.setRange(0, 1000)
        battery.setSuffix(" Ah")
        standby = QDoubleSpinBox()
        standby.setRange(1, 240)
        standby.setValue(24)
        standby.setSuffix(" h")
        alarm = QDoubleSpinBox()
        alarm.setRange(1, 240)
        alarm.setValue(30)
        alarm.setSuffix(" min")
        form.addRow("Installed battery", battery)
        form.addRow("Standby period", standby)
        form.addRow("Alarm period", alarm)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.repository.set_node_power(node, battery.value(), standby.value(), alarm.value())
            self.refresh()


class OutputGroupDevicesDialog(QDialog):
    def __init__(
        self,
        repository: ProjectRepository,
        node: int,
        panel: str,
        output_group: int,
        group_name: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        label = f"Output group {output_group}"
        if group_name:
            label += f" — {group_name}"
        self.setWindowTitle(f"Node {node}: {label}")
        self.resize(1050, 560)
        layout = QVBoxLayout(self)
        heading = QLabel(f"<b>Node {node} — {panel}</b><br>{label}")
        layout.addWidget(heading)
        rows = repository.fetch_output_group_devices(node, output_group)
        table = FilterableTableWidget(0, 9)
        table.setHorizontalHeaderLabels(
            [
                "Loop", "Address", "Sub address", "Zone", "Device text",
                "Type", "Product", "Ringing style", "Configured group",
            ]
        )
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setColumnWidth(4, 280)
        table.setColumnWidth(5, 190)
        table.setColumnWidth(7, 220)
        for row in rows:
            row_index = table.rowCount()
            table.insertRow(row_index)
            values = [
                row["loop"],
                row["address"],
                row["sub_address"],
                row["zone"],
                row["text"],
                catalogue_display_name(row["product_code"], row["observed_type"]),
                row["product_code"],
                row["ringing_style"] or "Not specified",
                row["output_group_name"] or "",
            ]
            for column, value in enumerate(values):
                table.setItem(row_index, column, _item(value))
        table.setSortingEnabled(True)
        table.apply_filters()
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class OutputGroupZoneAssignmentDialog(QDialog):
    def __init__(
        self,
        repository: ProjectRepository,
        node: int,
        output_group: int,
        group_name: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.repository = repository
        self.node = int(node)
        self.output_group = int(output_group)
        self.setWindowTitle(
            f"Assign node {node} / output group {output_group} to zones"
        )
        self.resize(720, 620)
        layout = QVBoxLayout(self)
        heading = QLabel(
            f"{group_name or 'Unnamed output group'}\n"
            "Tick Sounders and/or Beacons for every zone served by this "
            "output. Use this for panel outputs as well as loop-driven devices."
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter zone number or description…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ["Zone", "Sounders", "Beacons"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(0, 440)
        existing = {
            (int(row["zone"]), str(row["output_kind"]).upper())
            for row in repository.fetch_output_group_zone_assignments(
                self.node,
                self.output_group,
            )
        }
        for zone in repository.fetch_zones():
            row = self.table.rowCount()
            self.table.insertRow(row)
            zone_number = int(zone["number"])
            label = _item(_zone_label(zone))
            label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
            label.setData(Qt.ItemDataRole.UserRole, zone_number)
            self.table.setItem(row, 0, label)
            for column, output_kind in ((1, "SOUNDER"), (2, "BEACON")):
                checkbox = QTableWidgetItem()
                checkbox.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                checkbox.setCheckState(
                    Qt.CheckState.Checked
                    if (zone_number, output_kind) in existing
                    else Qt.CheckState.Unchecked
                )
                self.table.setItem(row, column, checkbox)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(
                row,
                bool(query and query not in self.table.item(row, 0).text().casefold()),
            )

    def assignments(self) -> list[tuple[int, str]]:
        assignments = []
        for row in range(self.table.rowCount()):
            zone = int(
                self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            )
            for column, output_kind in ((1, "SOUNDER"), (2, "BEACON")):
                if (
                    self.table.item(row, column).checkState()
                    == Qt.CheckState.Checked
                ):
                    assignments.append((zone, output_kind))
        return assignments

    def save(self) -> None:
        self.repository.replace_output_group_zone_assignments(
            self.node,
            self.output_group,
            self.assignments(),
        )
        self.accept()


class OutputGroupsPage(Page):
    def __init__(self):
        super().__init__("Output groups")
        note = QLabel(
            "Output groups are shown per panel node, including panel-only "
            "groups imported from Cause & Effect. Double-click a group to see "
            "its loop points, or assign the group directly to sounder and "
            "beacon zones."
        )
        note.setWordWrap(True)
        self.layout.addWidget(note)
        self.table = FilterableTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Node", "Panel", "Output group", "Group name", "Devices",
                "Ringing styles", "Assigned zones",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(3, 280)
        self.table.setColumnWidth(5, 300)
        self.table.setColumnWidth(6, 300)
        self.table.doubleClicked.connect(self.open_group)
        self.layout.addWidget(self.table, 1)
        assign = QPushButton("Assign selected group to sounder/beacon zones")
        assign.setProperty("secondary", True)
        assign.clicked.connect(self.assign_selected_group)
        self.layout.addWidget(assign)

    def refresh(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if not self.repository:
            return
        for row in self.repository.fetch_output_groups():
            assignments = (
                self.repository.fetch_output_group_zone_assignments(
                    int(row["node"]),
                    int(row["output_group"]),
                )
            )
            assigned_text = ", ".join(
                f"{assignment['zone']} "
                f"{str(assignment['output_kind']).title()}"
                for assignment in assignments
            )
            row_index = self.table.rowCount()
            self.table.insertRow(row_index)
            values = [
                row["node"],
                row["panel"],
                row["output_group"],
                row["group_name"],
                row["device_count"],
                row["ringing_styles"] or "Not specified",
                assigned_text or "Not assigned",
            ]
            for column, value in enumerate(values):
                item = _item(value)
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    (
                        int(row["node"]),
                        str(row["panel"]),
                        int(row["output_group"]),
                        str(row["group_name"] or ""),
                    ),
                )
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)

    def open_group(self, index) -> None:
        if not self.repository:
            return
        item = self.table.item(index.row(), 0)
        if item is None:
            return
        node, panel, output_group, group_name = item.data(Qt.ItemDataRole.UserRole)
        OutputGroupDevicesDialog(
            self.repository,
            node,
            panel,
            output_group,
            group_name,
            self,
        ).exec()

    def assign_selected_group(self) -> None:
        if not self.repository or self.table.currentRow() < 0:
            return
        item = self.table.item(self.table.currentRow(), 0)
        if item is None:
            return
        node, _panel, output_group, group_name = item.data(
            Qt.ItemDataRole.UserRole
        )
        dialog = OutputGroupZoneAssignmentDialog(
            self.repository,
            node,
            output_group,
            group_name,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()


class ZoneSelectionDialog(QDialog):
    def __init__(
        self,
        zones: list[tuple[str, object]],
        current_zone: object | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Select zone")
        self.resize(560, 480)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type a zone number or description…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.zone_list = QListWidget()
        for label, value in zones:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.zone_list.addItem(item)
            if current_zone is not None and value == current_zone:
                self.zone_list.setCurrentItem(item)
        self.zone_list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.zone_list, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.setFocus()

    def selected_zone(self):
        item = self.zone_list.currentItem()
        return (
            item.data(Qt.ItemDataRole.UserRole)
            if item is not None
            else None
        )

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        first_visible = None
        current_visible = False
        for index in range(self.zone_list.count()):
            item = self.zone_list.item(index)
            visible = not query or query in item.text().casefold()
            item.setHidden(not visible)
            if visible and first_visible is None:
                first_visible = item
            if visible and item is self.zone_list.currentItem():
                current_visible = True
        if not current_visible:
            self.zone_list.setCurrentItem(first_visible)

    def _accept_selection(self) -> None:
        if self.selected_zone() is None:
            QMessageBox.information(
                self,
                "Select a zone",
                "Select a zone from the filtered list.",
            )
            return
        self.accept()


class PolygonSelectionDialog(QDialog):
    def __init__(
        self,
        polygons: list[tuple[str, QGraphicsPolygonItem]],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Select overlapping polygon")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Multiple zone polygons overlap at this position. Select the one "
            "you want to work with."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.polygon_list = QListWidget()
        for label, polygon in polygons:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, polygon)
            self.polygon_list.addItem(item)
        if self.polygon_list.count():
            self.polygon_list.setCurrentRow(0)
        self.polygon_list.itemDoubleClicked.connect(
            lambda _item: self.accept()
        )
        layout.addWidget(self.polygon_list, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_polygon(self) -> QGraphicsPolygonItem | None:
        item = self.polygon_list.currentItem()
        return (
            item.data(Qt.ItemDataRole.UserRole)
            if item is not None
            else None
        )


class DoorDialog(QDialog):
    def __init__(
        self,
        repository: ProjectRepository,
        floor_id: int,
        start: tuple[float, float],
        end: tuple[float, float],
        door: dict | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.repository = repository
        self.floor_id = int(floor_id)
        self.start = start
        self.end = end
        self.door = door
        self.sprite_position = (
            (
                float(door.get("sprite_x")),
                float(door.get("sprite_y")),
            )
            if (
                door
                and door.get("sprite_x") is not None
                and door.get("sprite_y") is not None
            )
            else (
                (float(start[0]) + float(end[0])) / 2.0,
                (float(start[1]) + float(end[1])) / 2.0,
            )
        )
        self.setWindowTitle("Configure fire door")
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Record the zones on both sides; select the same zone for a door "
            "wholly within one zone. Link each installed function to its "
            "fire-alarm device now or assign it later. Suggested control "
            "devices prioritise outputs in either selected zone."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.name = QLineEdit(
            str(door["name"]) if door else f"Door {len(_fetch_doors(repository)) + 1}"
        )
        form.addRow("Door name", self.name)
        self.door_type = QComboBox()
        self.door_type.addItem("Single door", "SINGLE")
        self.door_type.addItem("Double door", "DOUBLE")
        self.rotation = QDoubleSpinBox()
        self.rotation.setRange(-359.0, 359.0)
        self.rotation.setDecimals(0)
        self.rotation.setSingleStep(15.0)
        self.rotation.setSuffix("°")
        form.addRow("Door sprite", self.door_type)
        form.addRow("Rotation", self.rotation)
        self.zone_a = QComboBox()
        self.zone_b = QComboBox()
        for zone in repository.fetch_zones():
            label = _zone_label(zone)
            self.zone_a.addItem(label, int(zone["number"]))
            self.zone_b.addItem(label, int(zone["number"]))
        form.addRow("Zone on side A", self.zone_a)
        form.addRow("Zone on side B", self.zone_b)
        self.nearest_zone_hint = QLabel(
            "The nearest zones on this floor are selected automatically. "
            "Review or override either side if required."
        )
        self.nearest_zone_hint.setWordWrap(True)
        form.addRow("", self.nearest_zone_hint)

        self.access_enabled = QCheckBox(
            "Door access release — unlock on fire"
        )
        self.access_device = QComboBox()
        self.access_device.setMinimumContentsLength(55)
        self._configure_device_typeahead(self.access_device)
        self.access_state = QComboBox()
        self.access_state.addItems(["LOCKED", "UNLOCKED"])
        form.addRow(self.access_enabled)
        form.addRow("Access fire-alarm device", self.access_device)
        form.addRow("Normal access state", self.access_state)

        self.hold_open_enabled = QCheckBox(
            "Fire hold-open — release and close on fire"
        )
        self.hold_open_device = QComboBox()
        self.hold_open_device.setMinimumContentsLength(55)
        self._configure_device_typeahead(self.hold_open_device)
        self.hold_open_state = QComboBox()
        self.hold_open_state.addItems(["HELD OPEN", "CLOSED"])
        form.addRow(self.hold_open_enabled)
        form.addRow("Hold-open fire-alarm device", self.hold_open_device)
        form.addRow("Normal hold-open state", self.hold_open_state)
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(90)
        form.addRow("Notes", self.notes)
        layout.addLayout(form)

        self.suggestions = QLabel()
        self.suggestions.setWordWrap(True)
        layout.addWidget(self.suggestions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if door:
            self.door_type.setCurrentIndex(
                self.door_type.findData(
                    str(door.get("door_type") or "SINGLE").upper()
                )
            )
            self.rotation.setValue(
                float(door.get("rotation_degrees") or 0)
            )
            self.zone_a.setCurrentIndex(
                self.zone_a.findData(int(door["zone_a"]))
            )
            self.zone_b.setCurrentIndex(
                self.zone_b.findData(int(door["zone_b"]))
            )
            self.access_enabled.setChecked(bool(door["has_access_control"]))
            self.hold_open_enabled.setChecked(bool(door["has_hold_open"]))
            self.access_state.setCurrentText(str(door["access_normal_state"]))
            self.hold_open_state.setCurrentText(
                str(door["hold_open_normal_state"])
            )
            self.notes.setPlainText(str(door["notes"] or ""))
        else:
            nearest = _nearest_zone_numbers(
                repository,
                self.floor_id,
                self.sprite_position,
            )
            if nearest:
                self.zone_a.setCurrentIndex(
                    self.zone_a.findData(nearest[0])
                )
            if len(nearest) > 1:
                self.zone_b.setCurrentIndex(
                    self.zone_b.findData(nearest[1])
                )
                if nearest[0] == nearest[1]:
                    self.nearest_zone_hint.setText(
                        f"Door is within Zone {nearest[0]}; that zone is "
                        "selected on both sides. Review or override if required."
                    )
                else:
                    self.nearest_zone_hint.setText(
                        f"Nearest drawing zones selected: {nearest[0]} and "
                        f"{nearest[1]}. Review or override either side if required."
                    )
            elif len(nearest) == 1:
                self.zone_b.setCurrentIndex(
                    self.zone_b.findData(nearest[0])
                )
                self.nearest_zone_hint.setText(
                    f"Door is within Zone {nearest[0]}; that zone is "
                    "selected on both sides. Review or override if required."
                )
            elif self.zone_b.count() > 1:
                side_a = self.zone_a.currentData()
                fallback = next(
                    (
                        index
                        for index in range(self.zone_b.count())
                        if self.zone_b.itemData(index) != side_a
                    ),
                    0,
                )
                self.zone_b.setCurrentIndex(fallback)
            self.access_enabled.setChecked(True)

        self.zone_a.currentIndexChanged.connect(self._refresh_device_choices)
        self.zone_b.currentIndexChanged.connect(self._refresh_device_choices)
        self.access_enabled.toggled.connect(self._update_enabled_controls)
        self.hold_open_enabled.toggled.connect(self._update_enabled_controls)
        self._refresh_device_choices(
            access_key=(door or {}).get("access_device_key"),
            hold_open_key=(door or {}).get("hold_open_device_key"),
        )
        self._update_enabled_controls()

    @staticmethod
    def _device_label(device, suggested: bool = False) -> str:
        name = str(device["text"] or "").strip() or catalogue_display_name(
            device["product_code"], device["observed_type"]
        )
        output = (
            f" · output group {device['output_group']}"
            if device["output_group"] is not None
            else ""
        )
        prefix = "Suggested · " if suggested else ""
        return (
            f"{prefix}Node {device['node']} · Zone {device['zone']} · "
            f"L{device['loop']}/A{device['address']}/{device['sub_address']} · "
            f"{name}{output}"
        )

    @staticmethod
    def _configure_device_typeahead(combo: QComboBox) -> None:
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.lineEdit().setClearButtonEnabled(True)
        combo.lineEdit().setPlaceholderText(
            "Type node, zone, address, device name or output group…"
        )
        completer = combo.completer()
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion
        )
        completer.setMaxVisibleItems(18)

    def _refresh_device_choices(
        self,
        *_args,
        access_key: str | None = None,
        hold_open_key: str | None = None,
    ) -> None:
        if access_key is None:
            access_key = self.access_device.currentData()
        if hold_open_key is None:
            hold_open_key = self.hold_open_device.currentData()
        zone_a = self.zone_a.currentData()
        zone_b = self.zone_b.currentData()
        if zone_a is None or zone_b is None:
            return
        suggestions = {}
        for capability, combo, current in (
            ("access", self.access_device, access_key),
            ("hold-open", self.hold_open_device, hold_open_key),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Not assigned — select a device now or later", None)
            devices = self.repository.suggest_door_control_devices(
                int(zone_a),
                int(zone_b),
                capability,
            )
            for index, device in enumerate(devices):
                combo.addItem(
                    self._device_label(device, index == 0),
                    str(device["stable_key"]),
                )
            selected = combo.findData(current)
            combo.setCurrentIndex(selected if selected >= 0 else 0)
            combo.blockSignals(False)
            if devices:
                suggestions[capability] = self._device_label(
                    devices[0]
                )
        self.suggestions.setText(
            "Control-device suggestions:\n"
            + "\n".join(
                f"• {capability.title()}: {label}"
                for capability, label in suggestions.items()
            )
        )

    def _update_enabled_controls(self, *_args) -> None:
        access = self.access_enabled.isChecked()
        hold_open = self.hold_open_enabled.isChecked()
        self.access_device.setEnabled(access)
        self.access_state.setEnabled(access)
        self.hold_open_device.setEnabled(hold_open)
        self.hold_open_state.setEnabled(hold_open)

    def values(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "floor_id": self.floor_id,
            "start": self.start,
            "end": self.end,
            "zone_a": int(self.zone_a.currentData()),
            "zone_b": int(self.zone_b.currentData()),
            "has_access_control": self.access_enabled.isChecked(),
            "access_device_key": self.access_device.currentData(),
            "access_normal_state": self.access_state.currentText(),
            "has_hold_open": self.hold_open_enabled.isChecked(),
            "hold_open_device_key": self.hold_open_device.currentData(),
            "hold_open_normal_state": self.hold_open_state.currentText(),
            "notes": self.notes.toPlainText().strip(),
            "door_type": self.door_type.currentData(),
            "sprite_position": self.sprite_position,
            "rotation_degrees": self.rotation.value(),
        }

    def _validate_and_accept(self) -> None:
        if self.zone_a.currentData() is None or self.zone_b.currentData() is None:
            QMessageBox.information(
                self, "Select zones", "Select both zones beside the door."
            )
            return
        try:
            values = self.values()
            self.repository._validated_door_values(**values)
        except ValueError as error:
            QMessageBox.information(self, "Door details incomplete", str(error))
            return
        self.accept()


class DxfManagementDialog(QDialog):
    def __init__(
        self,
        repository: ProjectRepository,
        current_floor_id: int | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.repository = repository
        self.current_floor_id = current_floor_id
        self.changed = False
        self.setWindowTitle("Architectural underlay drawings")
        self.resize(900, 520)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Manage the architectural DXF underlay for each floor. Zone "
            "polygons, doors and placed devices are retained when an underlay "
            "is replaced or removed."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = FilterableTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Floor", "Order", "Architectural DXF", "Status"]
        )
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(2, 480)
        self.table.doubleClicked.connect(
            lambda _index: self.attach_or_replace()
        )
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        add_floor = QPushButton("Add floor with DXF…")
        add_floor.clicked.connect(self.add_floor)
        attach = QPushButton("Attach / replace selected DXF…")
        attach.setProperty("secondary", True)
        attach.clicked.connect(self.attach_or_replace)
        remove = QPushButton("Remove selected underlay")
        remove.setProperty("secondary", True)
        remove.clicked.connect(self.remove_underlay)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        actions.addWidget(add_floor)
        actions.addWidget(attach)
        actions.addWidget(remove)
        actions.addStretch()
        actions.addWidget(close)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        selected = self.selected_floor_id() or self.current_floor_id
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for floor in self.repository.fetch_floors():
            path = str(floor["dxf_path"] or "")
            status = (
                "Not assigned"
                if not path
                else ("Available" if Path(path).exists() else "File missing")
            )
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, value in enumerate(
                [floor["name"], floor["level_order"], path, status]
            ):
                item = _item(value)
                item.setData(Qt.ItemDataRole.UserRole, int(floor["id"]))
                self.table.setItem(row, column, item)
            if selected is not None and int(floor["id"]) == int(selected):
                self.table.selectRow(row)
        self.table.setSortingEnabled(True)
        self.table.apply_filters()
        if not self.table.selectedItems() and self.table.rowCount():
            self.table.selectRow(0)

    def selected_floor_id(self) -> int | None:
        items = self.table.selectedItems()
        if not items:
            return None
        value = items[0].data(Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    def _select_dxf(self, title: str) -> str | None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            "",
            "DXF drawings (*.dxf)",
        )
        if not path:
            return None
        try:
            read_linework(path)
        except Exception as error:
            QMessageBox.critical(
                self,
                "DXF could not be read",
                str(error),
            )
            return None
        return path

    def add_floor(self) -> None:
        path = self._select_dxf("Select architectural underlay")
        if not path:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Floor name",
            "Name",
            text=Path(path).stem,
        )
        if not accepted or not name.strip():
            return
        level, accepted = QInputDialog.getInt(
            self,
            "Floor order",
            "Level order (ground = 0)",
            0,
            -20,
            100,
        )
        if not accepted:
            return
        self.current_floor_id = self.repository.add_floor(
            name.strip(),
            level,
            path,
        )
        self.changed = True
        self.refresh()

    def attach_or_replace(self) -> None:
        floor_id = self.selected_floor_id()
        if floor_id is None:
            QMessageBox.information(
                self,
                "Select a floor",
                "Select the floor whose architectural underlay you want to manage.",
            )
            return
        path = self._select_dxf("Select architectural underlay")
        if not path:
            return
        self.repository.set_floor_dxf(floor_id, path)
        self.current_floor_id = floor_id
        self.changed = True
        self.refresh()

    def remove_underlay(self) -> None:
        floor_id = self.selected_floor_id()
        if floor_id is None:
            return
        floor = next(
            (
                row
                for row in self.repository.fetch_floors()
                if int(row["id"]) == floor_id
            ),
            None,
        )
        if floor is None or not floor["dxf_path"]:
            return
        if (
            QMessageBox.question(
                self,
                "Remove architectural underlay",
                (
                    f"Remove the DXF underlay from {floor['name']}? "
                    "Zone polygons, doors and placed devices will be retained."
                ),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.repository.set_floor_dxf(floor_id, None)
        self.current_floor_id = floor_id
        self.changed = True
        self.refresh()


class ZonesMapPage(Page):
    geometry_changed = Signal()

    def __init__(self):
        super().__init__("Site drawings and zones")
        controls = QHBoxLayout()
        self.floor_combo = QComboBox()
        self.floor_combo.currentIndexChanged.connect(self.floor_changed)
        manage_dxf_button = QPushButton("Manage architectural underlays…")
        manage_dxf_button.setProperty("secondary", True)
        manage_dxf_button.clicked.connect(self.manage_underlays)
        self.show_underlay = QCheckBox("Show architectural underlay")
        self.show_underlay.setChecked(True)
        self.show_underlay.toggled.connect(self.underlay_visibility_changed)
        self.draw_polygon_button = QPushButton("Draw zone polyline")
        self.draw_polygon_button.setCheckable(True)
        self.draw_polygon_button.setProperty("secondary", True)
        self.draw_polygon_button.toggled.connect(self.set_draw_mode)
        self.draw_door_button = QPushButton("Place door")
        self.draw_door_button.setCheckable(True)
        self.draw_door_button.setProperty("secondary", True)
        self.draw_door_button.toggled.connect(self.set_door_draw_mode)
        self.finish_polygon_button = QPushButton("Finish polygon")
        self.finish_polygon_button.setProperty("secondary", True)
        self.finish_polygon_button.setEnabled(False)
        self.finish_polygon_button.clicked.connect(
            lambda: self.finish_drawing()
        )
        self.undo_polygon_button = QPushButton("Undo point")
        self.undo_polygon_button.setProperty("secondary", True)
        self.undo_polygon_button.setEnabled(False)
        self.undo_polygon_button.clicked.connect(self.undo_draw_point)
        self.cancel_polygon_button = QPushButton("Cancel drawing")
        self.cancel_polygon_button.setProperty("secondary", True)
        self.cancel_polygon_button.setEnabled(False)
        self.cancel_polygon_button.clicked.connect(self.cancel_drawing)
        self.move_polygon_button = QPushButton("Move polygons")
        self.move_polygon_button.setCheckable(True)
        self.move_polygon_button.setProperty("secondary", True)
        self.move_polygon_button.toggled.connect(self.set_move_mode)
        self.zone_combo = QComboBox()
        self.zone_combo.setMinimumWidth(330)
        self.zone_combo.view().setMinimumWidth(520)
        assign_button = QPushButton("Assign selected shape to zone")
        assign_button.clicked.connect(self.assign_selected)
        controls.addWidget(QLabel("Floor"))
        controls.addWidget(self.floor_combo)
        controls.addWidget(manage_dxf_button)
        controls.addWidget(self.show_underlay)
        controls.addWidget(self.draw_polygon_button)
        controls.addWidget(self.draw_door_button)
        controls.addWidget(self.finish_polygon_button)
        controls.addWidget(self.undo_polygon_button)
        controls.addWidget(self.cancel_polygon_button)
        controls.addWidget(self.move_polygon_button)
        controls.addStretch()
        controls.addWidget(self.zone_combo)
        controls.addWidget(assign_button)
        self.layout.addLayout(controls)
        navigation_hint = QLabel(
            "Mouse: wheel to zoom · middle-drag or left-drag to pan · "
            "Ctrl-click to select multiple polygons Â· right-click a polygon "
            "to edit points, assign/remove its zone, copy or delete it. "
            "Place a single or double door sprite with one click; "
            "manage per-floor architectural DXFs from the underlay dialog; "
            "click each zone corner "
            "and double-click to finish a polygon."
        )
        navigation_hint.setWordWrap(True)
        self.layout.addWidget(navigation_hint)
        splitter = QSplitter()
        self.scene = QGraphicsScene()
        self.view = MapGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.view.scene_clicked.connect(self.place_selected)
        self.view.scene_right_clicked.connect(self.assign_shape_from_context)
        self.view.scene_double_clicked.connect(self.finish_drawing)
        self.view.item_moved.connect(self.geometry_item_moved)
        self.view.drawing_cancelled.connect(self.cancel_drawing)
        self.view.drawing_undo_requested.connect(self.undo_draw_point)
        self.view.drawing_finish_requested.connect(
            lambda: self.finish_drawing()
        )
        self.view.polygon_selection_requested.connect(
            self.select_polygon_at_point
        )
        self.scene.selectionChanged.connect(self.show_selection_details)

        side_tabs = QTabWidget()
        self.zone_table = FilterableTableWidget(0, 4)
        self.zone_table.setHorizontalHeaderLabels(
            ["Zone", "Description", "Floors", "Devices"]
        )
        side_tabs.addTab(self.zone_table, "Zones")

        doors_page = QWidget()
        doors_layout = QVBoxLayout(doors_page)
        doors_layout.setContentsMargins(6, 6, 6, 6)
        doors_hint = QLabel(
            "Doors can use access release, fire hold-open, or both. "
            "Each function links to a configured fire-alarm device."
        )
        doors_hint.setWordWrap(True)
        doors_layout.addWidget(doors_hint)
        self.door_table = FilterableTableWidget(0, 5)
        self.door_table.setHorizontalHeaderLabels(
            ["Door", "Zones", "Functions", "Linked devices", "Normal state"]
        )
        self.door_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.door_table.setColumnWidth(0, 150)
        self.door_table.setColumnWidth(2, 180)
        self.door_table.setColumnWidth(3, 260)
        self.door_table.doubleClicked.connect(
            lambda _index: self.edit_selected_door()
        )
        doors_layout.addWidget(self.door_table, 1)
        door_buttons = QHBoxLayout()
        edit_door = QPushButton("Edit selected door")
        edit_door.setProperty("secondary", True)
        edit_door.clicked.connect(self.edit_selected_door)
        delete_door = QPushButton("Delete selected door")
        delete_door.setProperty("secondary", True)
        delete_door.clicked.connect(self.delete_selected_door)
        door_buttons.addWidget(edit_door)
        door_buttons.addWidget(delete_door)
        doors_layout.addLayout(door_buttons)
        side_tabs.addTab(doors_page, "Doors")

        layers_page = QWidget()
        layers_layout = QVBoxLayout(layers_page)
        layers_layout.setContentsMargins(6, 6, 6, 6)
        layers_hint = QLabel(
            "Toggle individual architectural DXF geometry and text layers. "
            "Use Show architectural underlay for the whole drawing."
        )
        layers_hint.setWordWrap(True)
        layers_layout.addWidget(layers_hint)
        self.layer_list = QListWidget()
        self.layer_list.itemChanged.connect(self.layer_visibility_changed)
        layers_layout.addWidget(self.layer_list, 1)
        side_tabs.addTab(layers_page, "DXF layers")

        placement = QWidget()
        placement_layout = QVBoxLayout(placement)
        placement_layout.setContentsMargins(6, 6, 6, 6)
        zone_placement_row = QHBoxLayout()
        self.device_zone_combo = QComboBox()
        self.device_zone_combo.setMinimumWidth(220)
        self.device_zone_combo.currentIndexChanged.connect(
            self._device_placement_zone_changed
        )
        self.place_zone_devices_button = QPushButton("Place zone devices")
        self.place_zone_devices_button.setCheckable(True)
        self.place_zone_devices_button.toggled.connect(
            self.set_zone_device_placement
        )
        zone_placement_row.addWidget(QLabel("Zone"))
        zone_placement_row.addWidget(self.device_zone_combo, 1)
        zone_placement_row.addWidget(self.place_zone_devices_button)
        placement_layout.addLayout(zone_placement_row)
        self.zone_placement_status = QLabel(
            "Choose a zone to place its devices in sequence."
        )
        self.zone_placement_status.setWordWrap(True)
        placement_layout.addWidget(self.zone_placement_status)
        self.asset_search = QLineEdit()
        self.asset_search.setPlaceholderText("Filter node, zone, address or device name…")
        self.asset_search.textChanged.connect(self.refresh_asset_list)
        self.asset_category = QComboBox()
        self.asset_category.addItems(
            [
                "All", "Detector", "Call point", "Sounder", "Beacon",
                "Output device", "Power supply", "Panel", "Device",
            ]
        )
        self.asset_category.currentIndexChanged.connect(self.refresh_asset_list)
        placement_layout.addWidget(self.asset_search)
        placement_layout.addWidget(self.asset_category)
        self.asset_list = QListWidget()
        self.asset_list.currentItemChanged.connect(self.asset_chosen)
        placement_layout.addWidget(self.asset_list, 1)
        hint = QLabel("Select an imported device or panel, then click its position on the drawing.")
        hint.setWordWrap(True)
        placement_layout.addWidget(hint)
        self.asset_details = QLabel("No map item selected.")
        self.asset_details.setWordWrap(True)
        self.asset_details.setMinimumHeight(105)
        self.asset_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        placement_layout.addWidget(self.asset_details)
        name_group_button = QPushButton("Name selected output group")
        name_group_button.setProperty("secondary", True)
        name_group_button.clicked.connect(self.name_selected_output_group)
        placement_layout.addWidget(name_group_button)
        remove_button = QPushButton("Remove selected placement")
        remove_button.setProperty("secondary", True)
        remove_button.clicked.connect(self.remove_selected_placement)
        placement_layout.addWidget(remove_button)
        side_tabs.addTab(placement, "Place devices")
        side_tabs.setMinimumWidth(430)
        side_tabs.setMaximumWidth(560)
        splitter.addWidget(self.view)
        splitter.addWidget(side_tabs)
        splitter.setStretchFactor(0, 1)
        self.layout.addWidget(splitter, 1)
        self.pending_shapes: dict[int, list[DxfShape]] = {}
        self.shape_items: dict[QGraphicsPolygonItem, DxfShape] = {}
        self.geometry_items: dict[QGraphicsPolygonItem, dict] = {}
        self.vertex_handles: dict[QGraphicsItem, tuple[QGraphicsPolygonItem, int]] = {}
        self._editing_polygon: QGraphicsPolygonItem | None = None
        self.layer_visibility: dict[int, dict[str, bool]] = {}
        self.drawing_points: list[QPointF] = []
        self.drawing_preview = None
        self.drawing_markers: list[QGraphicsItem] = []
        self.drawing_kind: str | None = None
        self.door_items: dict[QGraphicsItem, dict] = {}
        self.map_popup: QGraphicsItem | None = None
        self.asset_rows: list[dict] = []
        self.asset_by_key: dict[tuple[str, str], dict] = {}
        self.asset_alias_to_key: dict[tuple[str, str], tuple[str, str]] = {}
        self._displayed_floor_id: int | None = None

    def set_repository(self, repository: ProjectRepository | None) -> None:
        if repository is not self.repository:
            self._displayed_floor_id = None
            self.place_zone_devices_button.blockSignals(True)
            self.place_zone_devices_button.setChecked(False)
            self.place_zone_devices_button.blockSignals(False)
            self.zone_placement_status.setText(
                "Choose a zone to place its devices in sequence."
            )
        super().set_repository(repository)

    def refresh(self) -> None:
        self.floor_combo.blockSignals(True)
        self.floor_combo.clear()
        self.zone_combo.clear()
        self.zone_table.setSortingEnabled(False)
        self.zone_table.setRowCount(0)
        if self.repository:
            for floor in self.repository.fetch_floors():
                self.floor_combo.addItem(floor["name"], floor["id"])
            for zone in self.repository.fetch_zones():
                row = self.zone_table.rowCount()
                self.zone_table.insertRow(row)
                for column, value in enumerate(
                    [zone["number"], zone["description"], zone["floor_name"], zone["device_count"]]
                ):
                    self.zone_table.setItem(row, column, _item(value))
        self.zone_table.setSortingEnabled(True)
        self.zone_table.apply_filters()
        self._refresh_door_table()
        self.floor_combo.blockSignals(False)
        self._build_asset_rows()
        self._refresh_device_zone_choices()
        self.refresh_asset_list()
        self.floor_changed()

    def _refresh_door_table(self) -> None:
        self.door_table.setSortingEnabled(False)
        self.door_table.setRowCount(0)
        if not self.repository:
            return
        devices = {
            str(row["stable_key"]): row
            for row in self.repository.fetch_devices()
        }
        for door_row in _fetch_doors(self.repository):
            door = dict(door_row)
            functions = []
            linked = []
            if door["has_access_control"]:
                functions.append("Access release")
                linked.append(
                    "Access: "
                    + self._door_device_summary(
                        devices.get(str(door["access_device_key"]))
                    )
                )
            if door["has_hold_open"]:
                functions.append("Fire hold-open")
                linked.append(
                    "Hold-open: "
                    + self._door_device_summary(
                        devices.get(str(door["hold_open_device_key"]))
                    )
                )
            row = self.door_table.rowCount()
            self.door_table.insertRow(row)
            values = [
                (
                    f"{door['name']} "
                    f"({str(door.get('door_type') or 'SINGLE').title()})"
                ),
                _door_zone_text(door),
                " + ".join(functions),
                "\n".join(linked),
                _door_status_text(door),
            ]
            for column, value in enumerate(values):
                item = _item(value)
                item.setData(Qt.ItemDataRole.UserRole, int(door["id"]))
                self.door_table.setItem(row, column, item)
        self.door_table.setSortingEnabled(True)
        self.door_table.apply_filters()

    @staticmethod
    def _door_device_summary(device) -> str:
        if device is None:
            return "Not assigned"
        name = str(device["text"] or "").strip() or catalogue_display_name(
            device["product_code"], device["observed_type"]
        )
        return (
            f"N{device['node']} L{device['loop']}/A{device['address']}/"
            f"{device['sub_address']} · {name}"
        )

    def _available_zone_choices(
        self,
        include_zone: object | None = None,
    ) -> list[tuple[str, object]]:
        if not self.repository:
            return []
        included = (
            int(include_zone)
            if include_zone is not None
            else None
        )
        floor_id = self.floor_combo.currentData()
        assigned = {
            int(row["zone"])
            for row in self.repository.fetch_zone_geometry()
            if (
                floor_id is not None
                and int(row["floor_id"]) == int(floor_id)
                and (included is None or int(row["zone"]) != included)
            )
        }
        return [
            (_zone_label(row), row["number"])
            for row in self.repository.fetch_zones()
            if int(row["number"]) not in assigned
        ]

    def _refresh_preserving_view(self) -> None:
        floor_id = self.floor_combo.currentData()
        transform = self.view.transform()
        centre = self.view.mapToScene(
            self.view.viewport().rect().center()
        )
        self.refresh()
        if floor_id is not None:
            index = self.floor_combo.findData(floor_id)
            if index >= 0 and index != self.floor_combo.currentIndex():
                self.floor_combo.blockSignals(True)
                self.floor_combo.setCurrentIndex(index)
                self.floor_combo.blockSignals(False)
                self._refresh_zone_combo()
                self._refresh_layer_list()
                self.refresh_scene(False)
        current_floor_id = self.floor_combo.currentData()
        self._displayed_floor_id = (
            int(current_floor_id)
            if current_floor_id is not None
            else None
        )
        self.view.setTransform(transform)
        self.view.centerOn(centre)

    def floor_changed(self) -> None:
        floor_id = self.floor_combo.currentData()
        preserve_position = (
            floor_id is not None
            and self._displayed_floor_id is not None
            and int(floor_id) != self._displayed_floor_id
        )
        if preserve_position:
            transform = self.view.transform()
            centre = self.view.mapToScene(
                self.view.viewport().rect().center()
            )
        self._refresh_zone_combo()
        self._refresh_layer_list()
        self.refresh_scene(fit=not preserve_position)
        if preserve_position:
            self.view.setTransform(transform)
            inverse, invertible = transform.inverted()
            if invertible:
                visible = inverse.mapRect(QRectF(self.view.viewport().rect()))
                required = QRectF(
                    centre.x() - visible.width() / 2.0,
                    centre.y() - visible.height() / 2.0,
                    visible.width(),
                    visible.height(),
                )
                drawing = self.scene.itemsBoundingRect()
                self.scene.setSceneRect(
                    drawing.united(required)
                    if drawing.isValid()
                    else required
                )
            self.view.centerOn(centre)
        self._displayed_floor_id = (
            int(floor_id) if floor_id is not None else None
        )

    def manage_underlays(self) -> None:
        if not self.repository:
            return
        dialog = DxfManagementDialog(
            self.repository,
            self.floor_combo.currentData(),
            self,
        )
        dialog.exec()
        if not dialog.changed:
            return
        self.pending_shapes.clear()
        self.layer_visibility.clear()
        self._refresh_preserving_view()

    def underlay_visibility_changed(self, visible: bool) -> None:
        self.layer_list.setEnabled(bool(visible))
        self.refresh_scene(False)

    def _refresh_zone_combo(self) -> None:
        current_zone = self.zone_combo.currentData()
        self.zone_combo.clear()
        if not self.repository or self.floor_combo.currentData() is None:
            return
        for label, zone in self._available_zone_choices():
            self.zone_combo.addItem(label, zone)
        index = self.zone_combo.findData(current_zone)
        if index >= 0:
            self.zone_combo.setCurrentIndex(index)

    def _refresh_layer_list(self) -> None:
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        self.layer_list.setEnabled(self.show_underlay.isChecked())
        floor_id = self.floor_combo.currentData()
        if not self.repository or floor_id is None:
            self.layer_list.blockSignals(False)
            return
        floor = next(
            (
                row
                for row in self.repository.fetch_floors()
                if row["id"] == floor_id
            ),
            None,
        )
        if floor and floor["dxf_path"] and Path(floor["dxf_path"]).exists():
            visibility = self.layer_visibility.setdefault(int(floor_id), {})
            for layer in read_layers(floor["dxf_path"]):
                item = QListWidgetItem(layer)
                item.setData(Qt.ItemDataRole.UserRole, layer)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if visibility.get(layer, True)
                    else Qt.CheckState.Unchecked
                )
                self.layer_list.addItem(item)
        self.layer_list.blockSignals(False)

    def layer_visibility_changed(self, item: QListWidgetItem) -> None:
        floor_id = self.floor_combo.currentData()
        if floor_id is None:
            return
        layer = str(item.data(Qt.ItemDataRole.UserRole))
        self.layer_visibility.setdefault(int(floor_id), {})[layer] = (
            item.checkState() == Qt.CheckState.Checked
        )
        self.refresh_scene(False)

    def _build_asset_rows(self) -> None:
        self.asset_rows = []
        self.asset_by_key = {}
        self.asset_alias_to_key = {}
        if not self.repository:
            return
        for payload in _physical_device_payloads(
            self.repository.fetch_devices()
        ):
            self.asset_rows.append(payload)
            canonical = ("device", str(payload["key"]))
            for member_key in payload["member_keys"]:
                alias = ("device", str(member_key))
                self.asset_by_key[alias] = payload
                self.asset_alias_to_key[alias] = canonical
        for sqlite_row in self.repository.fetch_panels():
            row = dict(sqlite_row)
            payload = {
                "kind": "panel",
                "key": str(row["node"]),
                "symbol": "Panel",
                "name": row["name"],
                **row,
            }
            self.asset_rows.append(payload)
            key = ("panel", str(row["node"]))
            self.asset_by_key[key] = payload
            self.asset_alias_to_key[key] = key

    def _canonical_asset_key(
        self,
        entity_kind: str,
        entity_key: str,
    ) -> tuple[str, str]:
        key = (str(entity_kind), str(entity_key))
        return self.asset_alias_to_key.get(key, key)

    def _placed_asset_keys(self) -> set[tuple[str, str]]:
        if not self.repository:
            return set()
        return {
            self._canonical_asset_key(
                row["entity_kind"],
                row["entity_key"],
            )
            for row in self.repository.fetch_map_assets()
        }

    def _refresh_device_zone_choices(self) -> None:
        current_zone = self.device_zone_combo.currentData()
        counts: dict[int, int] = defaultdict(int)
        descriptions: dict[int, str] = {}
        for payload in self.asset_rows:
            if payload["kind"] == "device":
                for zone in payload.get("zones", (payload["zone"],)):
                    counts[int(zone)] += 1
        if self.repository:
            descriptions = {
                int(row["number"]): str(row["description"] or "")
                for row in self.repository.fetch_zones()
            }
        self.device_zone_combo.blockSignals(True)
        self.device_zone_combo.clear()
        self.device_zone_combo.addItem("All zones â€” manual placement", None)
        for zone in sorted(counts):
            description = descriptions.get(zone, "").strip()
            label = f"Zone {zone}"
            if description:
                label += f" â€” {description}"
            label += f" ({counts[zone]} devices)"
            self.device_zone_combo.addItem(label, zone)
        index = self.device_zone_combo.findData(current_zone)
        self.device_zone_combo.setCurrentIndex(index if index >= 0 else 0)
        self.device_zone_combo.blockSignals(False)

    def _device_placement_zone_changed(self) -> None:
        self.refresh_asset_list()
        if self.place_zone_devices_button.isChecked():
            if self.device_zone_combo.currentData() is None:
                self.place_zone_devices_button.setChecked(False)
            else:
                self._advance_zone_device()

    def set_zone_device_placement(self, enabled: bool) -> None:
        zone = self.device_zone_combo.currentData()
        if enabled and zone is None:
            QMessageBox.information(
                self,
                "Select a zone",
                "Choose the zone whose devices you want to place.",
            )
            self.place_zone_devices_button.blockSignals(True)
            self.place_zone_devices_button.setChecked(False)
            self.place_zone_devices_button.blockSignals(False)
            return
        if enabled:
            self.draw_polygon_button.setChecked(False)
            self.draw_door_button.setChecked(False)
            self.move_polygon_button.setChecked(False)
            self.asset_search.clear()
            self.asset_category.setCurrentText("All")
            self.view.setCursor(Qt.CursorShape.CrossCursor)
            self.view.setFocus()
            self._advance_zone_device()
        else:
            if not self.view.draw_mode:
                self.view.unsetCursor()
            if zone is None:
                self.zone_placement_status.setText(
                    "Choose a zone to place its devices in sequence."
                )
            elif "complete" not in self.zone_placement_status.text().casefold():
                self.zone_placement_status.setText(
                    f"Zone {zone} placement paused."
                )

    def _zone_device_payloads(self, zone: int) -> list[dict]:
        return sorted(
            [
                payload
                for payload in self.asset_rows
                if payload["kind"] == "device"
                and int(zone)
                in {
                    int(value)
                    for value in payload.get("zones", (payload["zone"],))
                }
            ],
            key=lambda payload: (
                int(payload["node"]),
                int(payload["loop"]),
                int(payload["address"]),
            ),
        )

    def _advance_zone_device(self) -> None:
        if not self.repository:
            return
        zone = self.device_zone_combo.currentData()
        if zone is None:
            return
        payloads = self._zone_device_payloads(int(zone))
        placed = self._placed_asset_keys()
        unplaced = [
            payload
            for payload in payloads
            if (payload["kind"], str(payload["key"])) not in placed
        ]
        if not unplaced:
            self.place_zone_devices_button.blockSignals(True)
            self.place_zone_devices_button.setChecked(False)
            self.place_zone_devices_button.blockSignals(False)
            self.view.unsetCursor()
            self.zone_placement_status.setText(
                f"Zone {zone} placement complete â€” "
                f"{len(payloads)} of {len(payloads)} devices placed."
            )
            return
        current = unplaced[0]
        key = (current["kind"], current["key"])
        for index in range(self.asset_list.count()):
            item = self.asset_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                self.asset_list.setCurrentItem(item)
                break
        placed_count = len(payloads) - len(unplaced)
        self.zone_placement_status.setText(
            (
                f"Current device {placed_count + 1} of {len(payloads)}: "
                f"Node {current['node']} Â· L{current['loop']}/"
                f"A{current['address']} Â· "
                f"{current['name']}. Click its position on the drawing."
            )
        )

    def refresh_asset_list(self) -> None:
        current = None
        if self.asset_list.currentItem():
            current = self.asset_list.currentItem().data(Qt.ItemDataRole.UserRole)
        self.asset_list.clear()
        if not self.repository:
            return
        category = self.asset_category.currentText()
        needle = self.asset_search.text().strip().casefold()
        zone_filter = self.device_zone_combo.currentData()
        placed = self._placed_asset_keys()
        for payload in self.asset_rows:
            if zone_filter is not None and (
                payload["kind"] != "device"
                or int(zone_filter)
                not in {
                    int(value)
                    for value in payload.get("zones", (payload["zone"],))
                }
            ):
                continue
            if category != "All" and payload["symbol"] != category:
                continue
            if payload["kind"] == "device":
                zones = ", ".join(
                    str(value)
                    for value in payload.get("zones", (payload["zone"],))
                )
                identity = (
                    f"Node {payload['node']} · Zone {zones} · "
                    f"L{payload['loop']}/A{payload['address']}"
                )
            else:
                identity = f"Node {payload['node']} · panel"
            label = f"{'✓ ' if (payload['kind'], payload['key']) in placed else ''}{payload['symbol']} · {identity} · {payload['name']}"
            if needle and needle not in label.casefold():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (payload["kind"], payload["key"]))
            item.setToolTip(label)
            self.asset_list.addItem(item)
            if current == (payload["kind"], payload["key"]):
                self.asset_list.setCurrentItem(item)

    def import_dxf(self) -> None:
        if not self.repository:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import floor drawing", "", "DXF drawings (*.dxf)")
        if not path:
            return
        name, ok = QInputDialog.getText(self, "Floor name", "Name", text=Path(path).stem)
        if not ok or not name:
            return
        level, ok = QInputDialog.getInt(self, "Floor order", "Level order (ground = 0)", 0, -20, 100)
        if not ok:
            return
        try:
            shapes = read_closed_shapes(path)
            floor_id = self.repository.add_floor(name, level, path)
            self.pending_shapes[floor_id] = shapes
            self.refresh()
            self.floor_combo.setCurrentIndex(self.floor_combo.findData(floor_id))
            self.refresh_scene()
            QMessageBox.information(
                self,
                "DXF imported",
                f"Found {len(shapes)} closed polylines. Select a shape, choose a zone and assign it.",
            )
        except Exception as error:
            QMessageBox.critical(self, "DXF import failed", str(error))

    def replace_dxf(self) -> None:
        if not self.repository or self.floor_combo.currentData() is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select replacement floor drawing",
            "",
            "DXF drawings (*.dxf)",
        )
        if not path:
            return
        try:
            shapes = read_closed_shapes(path)
        except Exception as error:
            QMessageBox.critical(
                self,
                "DXF replacement failed",
                f"The replacement drawing could not be read.\n\n{error}",
            )
            return
        if (
            QMessageBox.question(
                self,
                "Replace floor DXF",
                "Replace the current DXF underlay? Existing assigned zone "
                "polygons and placed devices will be retained.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        floor_id = int(self.floor_combo.currentData())
        self.repository.set_floor_dxf(floor_id, path)
        assigned_points = {
            tuple(
                (round(float(x), 5), round(float(y), 5))
                for x, y in json.loads(row["geometry_json"])
            )
            for row in self.repository.fetch_zone_geometry()
            if int(row["floor_id"]) == floor_id
        }
        self.pending_shapes[floor_id] = [
            shape
            for shape in shapes
            if tuple(
                (round(float(x), 5), round(float(y), 5))
                for x, y in shape.points
            )
            not in assigned_points
        ]
        self.layer_visibility.pop(floor_id, None)
        self.floor_changed()
        QMessageBox.information(
            self,
            "DXF replaced",
            f"The underlay was replaced and {len(shapes):,} enclosed "
            "polylines were found.",
        )

    def remove_dxf(self) -> None:
        if not self.repository or self.floor_combo.currentData() is None:
            return
        floor_id = int(self.floor_combo.currentData())
        floor = next(
            (
                row
                for row in self.repository.fetch_floors()
                if int(row["id"]) == floor_id
            ),
            None,
        )
        if floor is None or not floor["dxf_path"]:
            QMessageBox.information(
                self,
                "No DXF attached",
                "The selected floor does not have a DXF underlay.",
            )
            return
        if (
            QMessageBox.question(
                self,
                "Remove floor DXF",
                "Remove the DXF underlay from this floor? Existing assigned "
                "zone polygons and placed devices will be retained.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.repository.set_floor_dxf(floor_id, None)
        self.pending_shapes.pop(floor_id, None)
        self.layer_visibility.pop(floor_id, None)
        self.floor_changed()
        QMessageBox.information(
            self,
            "DXF removed",
            "The DXF underlay was removed. Zone polygons and placed devices "
            "were retained.",
        )

    def refresh_scene(self, fit: bool = True) -> None:
        self.view.cancel_scene_interaction()
        self.map_popup = None
        self.vertex_handles.clear()
        self._editing_polygon = None
        self.shape_items.clear()
        self.geometry_items.clear()
        self.door_items.clear()
        self.scene.blockSignals(True)
        try:
            self.scene.clear()
        finally:
            self.scene.blockSignals(False)
        self.drawing_points.clear()
        self.drawing_preview = None
        self.drawing_markers.clear()
        if not self.repository:
            return
        floor_id = self.floor_combo.currentData()
        if floor_id is None:
            return
        floor = next(
            (row for row in self.repository.fetch_floors() if row["id"] == floor_id),
            None,
        )
        visibility = self.layer_visibility.setdefault(int(floor_id), {})
        underlay_visible = self.show_underlay.isChecked()
        if (
            underlay_visible
            and floor
            and floor["dxf_path"]
            and Path(floor["dxf_path"]).exists()
        ):
            for entity in read_linework(floor["dxf_path"]):
                if not visibility.get(entity.layer, True):
                    continue
                path = QPainterPath()
                path.moveTo(entity.points[0][0], -entity.points[0][1])
                for x, y in entity.points[1:]:
                    path.lineTo(x, -y)
                if entity.closed:
                    path.closeSubpath()
                brush = (
                    QBrush(QColor("#64748b"))
                    if entity.entity_type in {"SOLID", "TRACE", "3DFACE"}
                    else QBrush(Qt.BrushStyle.NoBrush)
                )
                item = self.scene.addPath(
                    path,
                    QPen(QColor("#94a3b8"), 0),
                    brush,
                )
                item.setZValue(-20)
                item.setToolTip(f"DXF layer: {entity.layer}")
                item.setData(20, entity.layer)
            for text in read_text(floor["dxf_path"]):
                if not visibility.get(text.layer, True):
                    continue
                font, font_scale, resolved_family = _dxf_text_font(text)
                item = self.scene.addSimpleText(text.text)
                item.setFont(font)
                item.setBrush(QBrush(QColor("#52657a")))
                item.setPos(text.x, -text.y)
                item.setRotation(-text.rotation)
                item.setScale(font_scale)
                item.setZValue(1000)
                source_font = text.font_family or text.font_file or "unspecified"
                item.setToolTip(
                    f"DXF text layer: {text.layer}\n"
                    f"Style: {text.style_name}\n"
                    f"Font: {resolved_family} (source: {source_font})\n"
                    f"Rotation: {text.rotation:g}\u00b0"
                )
                item.setData(20, text.layer)
        assigned = [
            row for row in self.repository.fetch_zone_geometry() if row["floor_id"] == floor_id
        ]
        if (
            underlay_visible
            and int(floor_id) not in self.pending_shapes
            and floor
            and floor["dxf_path"]
        ):
            try:
                assigned_points = {
                    tuple(
                        (round(float(x), 5), round(float(y), 5))
                        for x, y in json.loads(row["geometry_json"])
                    )
                    for row in assigned
                }
                fetch_ignored = getattr(
                    self.repository,
                    "fetch_ignored_zone_shape_keys",
                    None,
                )
                ignored_keys = (
                    fetch_ignored(int(floor_id))
                    if fetch_ignored is not None
                    else set()
                )
                pending = []
                for shape in read_closed_shapes(floor["dxf_path"]):
                    shape.source_key = zone_shape_key(shape.points)
                    points_key = tuple(
                        (round(float(x), 5), round(float(y), 5))
                        for x, y in shape.points
                    )
                    if (
                        points_key not in assigned_points
                        and shape.source_key not in ignored_keys
                    ):
                        pending.append(shape)
                self.pending_shapes[int(floor_id)] = pending
            except Exception:
                self.pending_shapes[int(floor_id)] = []
        for row in assigned:
            points = [tuple(point) for point in json.loads(row["geometry_json"])]
            item = self._add_polygon(
                points,
                QColor("#6fca8c"),
                f"Zone {row['zone']} — {row['description']}",
                None,
            )
            item.setZValue(-10)
            item.setData(30, int(row["id"]))
            item.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable,
                self.view.edit_geometry_mode,
            )
            self.geometry_items[item] = dict(row)
        if underlay_visible:
            for shape in self.pending_shapes.get(floor_id, []):
                if not visibility.get(shape.layer, True):
                    continue
                item = self._add_polygon(
                    shape.points,
                    QColor("#dbe3ec"),
                    shape.layer,
                    shape,
                )
                self.shape_items[item] = shape
        rendered_assets: set[tuple[str, str]] = set()
        for placement in self.repository.fetch_map_assets(int(floor_id)):
            placement_key = (
                str(placement["entity_kind"]),
                str(placement["entity_key"]),
            )
            canonical_key = self._canonical_asset_key(*placement_key)
            if canonical_key in rendered_assets:
                continue
            payload = self.asset_by_key.get(placement_key)
            if payload:
                rendered_assets.add(canonical_key)
                self._add_asset_marker(
                    payload,
                    float(placement["x"]),
                    float(placement["y"]),
                )
        for door_row in _fetch_doors(self.repository, int(floor_id)):
            door = dict(door_row)
            for item in _add_door_graphics(
                self.scene,
                door,
                movable=True,
            ):
                self.door_items[item] = door
        if fit and self.scene.itemsBoundingRect().isValid():
            self.view.fitInView(self.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _add_polygon(
        self,
        points: list[tuple[float, float]],
        colour: QColor,
        tooltip: str,
        shape: DxfShape | None,
    ) -> QGraphicsPolygonItem:
        polygon = QPolygonF([QPointF(x, -y) for x, y in points])
        item = self.scene.addPolygon(polygon, QPen(QColor("#42566f"), 0), QBrush(colour))
        item.setFillRule(Qt.FillRule.WindingFill)
        item.setOpacity(0.72)
        item.setToolTip(tooltip)
        item.setFlag(QGraphicsPolygonItem.GraphicsItemFlag.ItemIsSelectable, True)
        item.setData(40, colour.name())
        if shape is not None:
            item.setData(0, shape.layer)
        return item

    def _add_asset_marker(self, payload: dict, x: float, y: float) -> None:
        _add_fire_alarm_symbol(
            self.scene,
            payload,
            x,
            y,
            self._asset_detail_text(payload),
            selectable=True,
            show_address=True,
        )

    def _asset_detail_text(self, payload: dict) -> str:
        if payload["kind"] == "panel":
            return (
                f"Panel: {payload['name']}\n"
                f"Node: {payload['node']}\n"
                f"Loops: {payload['loops_json']}\n"
                f"Configured devices: {payload['device_count']}"
            )
        lines = [
            f"{payload['symbol']}: {payload['name']}",
            f"Node {payload['node']} · Zones "
            f"{', '.join(str(value) for value in payload.get('zones', (payload['zone'],)))} · "
            f"Loop {payload['loop']} · Address {payload['address']} · "
            f"Sub-addresses "
            f"{', '.join(str(value) for value in payload.get('sub_addresses', (payload['sub_address'],)))}",
        ]
        if payload.get("output_group") is not None and self.repository:
            group = int(payload["output_group"])
            name, zones = self.repository.output_group_details(group)
            lines.append(f"Output group {group}: {name or 'Unnamed output group'}")
            lines.append(
                "Triggered by zones: " + (", ".join(map(str, zones)) if zones else "not defined")
            )
        return "\n".join(lines)

    def asset_chosen(self, current=None, previous=None) -> None:
        item = current or self.asset_list.currentItem()
        if not item:
            return
        payload = self.asset_by_key.get(item.data(Qt.ItemDataRole.UserRole))
        if payload:
            self.asset_details.setText(self._asset_detail_text(payload))
            if (
                self.place_zone_devices_button.isChecked()
                and payload["kind"] == "device"
                and self.device_zone_combo.currentData() is not None
                and int(self.device_zone_combo.currentData())
                in {
                    int(value)
                    for value in payload.get("zones", (payload["zone"],))
                }
            ):
                self.zone_placement_status.setText(
                    (
                        f"Current device: Node {payload['node']} Â· "
                        f"L{payload['loop']}/A{payload['address']} Â· "
                        f"{payload['name']}. "
                        "Click its position on the drawing."
                    )
                )

    def place_selected(self, point: QPointF) -> None:
        if self.view.draw_mode:
            self._add_draw_point(point)
            return
        if not self.repository or self.floor_combo.currentData() is None:
            return
        item = self.asset_list.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        payload = self.asset_by_key.get(key)
        if not payload:
            return
        transform = self.view.transform()
        centre = self.view.mapToScene(
            self.view.viewport().rect().center()
        )
        if payload["kind"] == "device":
            for member_key in payload.get("member_keys", (payload["key"],)):
                if str(member_key) != str(payload["key"]):
                    self.repository.remove_map_asset(
                        "device",
                        str(member_key),
                    )
        self.repository.place_map_asset(
            payload["kind"],
            payload["key"],
            int(self.floor_combo.currentData()),
            point.x(),
            point.y(),
            payload["symbol"],
        )
        self.refresh_asset_list()
        if self.place_zone_devices_button.isChecked():
            self._advance_zone_device()
        self.refresh_scene(False)
        self._restore_map_view_state(transform, centre)

    def _restore_map_view_state(self, transform, centre: QPointF) -> None:
        self.view.setTransform(transform)
        inverse, invertible = transform.inverted()
        if invertible:
            visible = inverse.mapRect(QRectF(self.view.viewport().rect()))
            required = QRectF(
                centre.x() - visible.width() / 2.0,
                centre.y() - visible.height() / 2.0,
                visible.width(),
                visible.height(),
            )
            drawing = self.scene.itemsBoundingRect()
            self.scene.setSceneRect(
                drawing.united(required)
                if drawing.isValid()
                else required
            )
        self.view.centerOn(centre)

    def set_draw_mode(self, enabled: bool) -> None:
        if enabled:
            self.draw_door_button.setChecked(False)
            self.drawing_kind = "zone"
            self.move_polygon_button.setChecked(False)
            self.cancel_drawing(leave_mode=True)
            self.finish_polygon_button.setText("Finish polygon")
            self.view.setCursor(Qt.CursorShape.CrossCursor)
            self.view.setFocus()
        elif self.drawing_kind == "zone":
            self.drawing_kind = None
        self._update_drawing_controls()

    def set_door_draw_mode(self, enabled: bool) -> None:
        if enabled:
            self.draw_polygon_button.setChecked(False)
            self.drawing_kind = "door"
            self.move_polygon_button.setChecked(False)
            self.cancel_drawing(leave_mode=True)
            self.finish_polygon_button.setText("Place door")
            self.view.setCursor(Qt.CursorShape.CrossCursor)
            self.view.setFocus()
        elif self.drawing_kind == "door":
            self.drawing_kind = None
        self._update_drawing_controls()

    def _update_drawing_controls(self) -> None:
        enabled = self.drawing_kind is not None
        self.view.draw_mode = enabled
        self.finish_polygon_button.setEnabled(enabled)
        self.undo_polygon_button.setEnabled(enabled)
        self.cancel_polygon_button.setEnabled(enabled)
        if not enabled:
            self.view.unsetCursor()
            self.cancel_drawing(leave_mode=True)

    def set_move_mode(self, enabled: bool) -> None:
        self.view.edit_geometry_mode = bool(enabled)
        if enabled:
            self.draw_polygon_button.setChecked(False)
        for item in self.geometry_items:
            item.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable,
                bool(enabled),
            )

    def _add_draw_point(self, point: QPointF) -> None:
        if self.drawing_points and (
            point - self.drawing_points[-1]
        ).manhattanLength() < 0.001:
            return
        self.drawing_points.append(point)
        self._update_drawing_preview()
        if self.drawing_kind == "door" and len(self.drawing_points) == 1:
            self.finish_drawn_door()

    def _update_drawing_preview(self) -> None:
        if self.drawing_preview is not None:
            self.scene.removeItem(self.drawing_preview)
            self.drawing_preview = None
        for marker in self.drawing_markers:
            if marker.scene() is self.scene:
                self.scene.removeItem(marker)
        self.drawing_markers.clear()
        if not self.drawing_points:
            return
        path = QPainterPath(self.drawing_points[0])
        for point in self.drawing_points[1:]:
            path.lineTo(point)
        self.drawing_preview = self.scene.addPath(
            path,
            QPen(QColor("#dc3545"), 0),
        )
        self.drawing_preview.setZValue(50)
        for point in self.drawing_points:
            marker = self.scene.addEllipse(
                -4,
                -4,
                8,
                8,
                QPen(QColor("#ffffff"), 1),
                QBrush(QColor("#dc3545")),
            )
            marker.setPos(point)
            marker.setZValue(51)
            marker.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
                True,
            )
            self.drawing_markers.append(marker)

    def undo_draw_point(self) -> None:
        if not self.view.draw_mode or not self.drawing_points:
            return
        self.drawing_points.pop()
        self._update_drawing_preview()

    def cancel_drawing(self, leave_mode: bool = False) -> None:
        self.drawing_points.clear()
        self._update_drawing_preview()
        if not leave_mode:
            if self.draw_polygon_button.isChecked():
                self.draw_polygon_button.setChecked(False)
            if self.draw_door_button.isChecked():
                self.draw_door_button.setChecked(False)

    def finish_drawing(self, point: QPointF | None = None) -> None:
        if self.drawing_kind == "door":
            if point is not None and not self.drawing_points:
                self._add_draw_point(point)
            elif len(self.drawing_points) == 1:
                self.finish_drawn_door()
            return
        self.finish_drawn_polygon(point)

    def finish_drawn_polygon(self, point: QPointF | None = None) -> None:
        if not self.view.draw_mode or self.drawing_kind != "zone":
            return
        if point is not None:
            self._add_draw_point(point)
        if len(self.drawing_points) < 3:
            QMessageBox.information(
                self,
                "More points required",
                "A zone polygon needs at least three points. Click each corner, "
                "then use Finish polygon, right-click, double-click or Enter.",
            )
            return
        if (
            not self.repository
            or self.floor_combo.currentData() is None
            or self.zone_combo.currentData() is None
        ):
            return
        points = [(value.x(), -value.y()) for value in self.drawing_points]
        points.append(points[0])
        zone = int(self.zone_combo.currentData())
        floor_id = int(self.floor_combo.currentData())
        if not self._confirm_geometry_replacement(zone, floor_id):
            return
        try:
            self.repository.assign_zone_geometry(
                zone,
                floor_id,
                points,
                "USER_DRAWN",
            )
        except Exception as error:
            QMessageBox.critical(self, "Could not save polygon", str(error))
            return
        self.draw_polygon_button.setChecked(False)
        self._refresh_preserving_view()
        self.geometry_changed.emit()

    def finish_drawn_door(self) -> None:
        if (
            self.drawing_kind != "door"
            or len(self.drawing_points) != 1
            or not self.repository
            or self.floor_combo.currentData() is None
        ):
            return
        position = self.drawing_points[0]
        dialog = DoorDialog(
            self.repository,
            int(self.floor_combo.currentData()),
            (position.x() - 0.5, position.y()),
            (position.x() + 0.5, position.y()),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.drawing_points.clear()
            self._update_drawing_preview()
            return
        try:
            self.repository.create_door(**dialog.values())
        except ValueError as error:
            QMessageBox.critical(self, "Could not save door", str(error))
            self.drawing_points.clear()
            self._update_drawing_preview()
            return
        self.drawing_points.clear()
        self._update_drawing_preview()
        self._refresh_preserving_view()
        self.view.setCursor(Qt.CursorShape.CrossCursor)
        self.view.setFocus()
        self.geometry_changed.emit()

    def geometry_item_moved(self, item: QGraphicsItem) -> None:
        door_id = item.data(70)
        if self.repository and door_id is not None:
            position = item.scenePos()
            self.repository.move_door(
                int(door_id),
                position.x(),
                position.y(),
            )
            for door in self.door_items.values():
                if int(door["id"]) == int(door_id):
                    door["sprite_x"] = position.x()
                    door["sprite_y"] = position.y()
            self.geometry_changed.emit()
            return
        asset_key = item.data(10)
        if self.repository and asset_key is not None:
            canonical_key = self._canonical_asset_key(*asset_key)
            payload = self.asset_by_key.get(canonical_key)
            floor_id = self.floor_combo.currentData()
            if payload is None or floor_id is None:
                return
            if payload["kind"] == "device":
                for member_key in payload.get(
                    "member_keys",
                    (payload["key"],),
                ):
                    if str(member_key) != str(payload["key"]):
                        self.repository.remove_map_asset(
                            "device",
                            str(member_key),
                        )
            position = item.scenePos()
            self.repository.place_map_asset(
                payload["kind"],
                payload["key"],
                int(floor_id),
                position.x(),
                position.y(),
                payload["symbol"],
            )
            self.refresh_asset_list()
            self._show_asset_map_popup(item, payload)
            self.geometry_changed.emit()
            return
        if item in self.vertex_handles:
            self._vertex_handle_moved(item)
            return
        row = self.geometry_items.get(item)
        if not self.repository or row is None:
            return
        offset = item.pos()
        if abs(offset.x()) < 1e-9 and abs(offset.y()) < 1e-9:
            return
        points = [
            (float(x) + offset.x(), float(y) - offset.y())
            for x, y in json.loads(row["geometry_json"])
        ]
        self.repository.update_zone_geometry(int(row["id"]), points)
        item.setPos(0, 0)
        self.refresh_scene(False)
        self.geometry_changed.emit()

    def show_selection_details(self) -> None:
        try:
            selected_items = set(self.scene.selectedItems())
        except RuntimeError:
            return
        for polygon in list(
            set(self.shape_items) | set(self.geometry_items)
        ):
            try:
                if polygon in selected_items:
                    polygon.setBrush(QBrush(QColor("#4da3ff")))
                    polygon.setPen(QPen(QColor("#0b5ed7"), 0))
                    polygon.setOpacity(0.9)
                else:
                    polygon.setBrush(
                        QBrush(
                            QColor(
                                str(polygon.data(40) or "#dbe3ec")
                            )
                        )
                    )
                    polygon.setPen(QPen(QColor("#42566f"), 0))
                    polygon.setOpacity(0.72)
            except RuntimeError:
                self.shape_items.pop(polygon, None)
                self.geometry_items.pop(polygon, None)
        for item in self.scene.selectedItems():
            key = item.data(10)
            if key:
                payload = self.asset_by_key.get(key)
                if payload:
                    self.asset_details.setText(self._asset_detail_text(payload))
                    if payload["kind"] == "device":
                        self._show_asset_map_popup(item, payload)
                return

    def _show_asset_map_popup(
        self,
        item: QGraphicsItem,
        payload: dict,
    ) -> None:
        self.map_popup = _add_map_popup(
            self.scene,
            self.map_popup,
            _device_popup_content(
                name=payload["name"],
                node=int(payload["node"]),
                zones=payload.get("zones", (payload["zone"],)),
                loop=int(payload["loop"]),
                address=int(payload["address"]),
                status="NORMAL",
                channels=payload.get("channel_details", ()),
            ),
            item,
            self.view,
        )

    def remove_selected_placement(self) -> None:
        if not self.repository:
            return
        key = None
        for scene_item in self.scene.selectedItems():
            if scene_item.data(10):
                key = scene_item.data(10)
                break
        if key is None and self.asset_list.currentItem():
            key = self.asset_list.currentItem().data(Qt.ItemDataRole.UserRole)
        if key:
            payload = self.asset_by_key.get(
                self._canonical_asset_key(*key)
            )
            if payload and payload["kind"] == "device":
                for member_key in payload.get(
                    "member_keys",
                    (payload["key"],),
                ):
                    self.repository.remove_map_asset(
                        "device",
                        str(member_key),
                    )
            else:
                self.repository.remove_map_asset(*key)
            self.refresh_asset_list()
            self.refresh_scene()

    def name_selected_output_group(self) -> None:
        if not self.repository:
            return
        payload = None
        for scene_item in self.scene.selectedItems():
            key = scene_item.data(10)
            if key:
                payload = self.asset_by_key.get(key)
                break
        if payload is None and self.asset_list.currentItem():
            payload = self.asset_by_key.get(
                self.asset_list.currentItem().data(Qt.ItemDataRole.UserRole)
            )
        if not payload or payload.get("output_group") is None:
            QMessageBox.information(
                self,
                "No output group",
                "Select an output device that has a decoded or assigned output-group number.",
            )
            return
        group = int(payload["output_group"])
        current_name, _ = self.repository.output_group_details(group)
        name, ok = QInputDialog.getText(
            self,
            f"Output group {group}",
            "Group name",
            text=current_name,
        )
        if ok:
            self.repository.set_output_group_name(group, name)
            self.asset_details.setText(self._asset_detail_text(payload))
            self.refresh_scene()

    def assign_selected(self) -> None:
        if not self.repository or self.floor_combo.currentData() is None or self.zone_combo.currentData() is None:
            return
        selected = [item for item in self.scene.selectedItems() if item in self.shape_items]
        if not selected:
            QMessageBox.information(self, "Select a shape", "Select an unassigned grey polyline first.")
            return
        item = selected[0]
        self._assign_shape(self.shape_items[item], int(self.zone_combo.currentData()))

    def assign_shape_from_context(
        self,
        item: QGraphicsItem | None,
        scene_point: QPointF,
    ) -> None:
        if item is not None and item.data(10) is not None:
            root = self.view._asset_root_item(item) or item
            self.scene.clearSelection()
            root.setSelected(True)
            self.show_selection_details()
            return
        door_id = item.data(70) if item is not None else None
        if door_id is not None:
            self._show_door_menu(int(door_id))
            return
        candidates = self._polygons_at(scene_point)
        if item in self.shape_items or item in self.geometry_items:
            polygon = item
        elif item in candidates:
            polygon = item
        elif len(candidates) > 1:
            polygon = self._choose_overlapping_polygon(candidates)
            if polygon is None:
                return
        elif len(candidates) == 1:
            polygon = candidates[0]
        else:
            return
        if not polygon.isSelected():
            self.scene.clearSelection()
            polygon.setSelected(True)
        self._show_polygon_menu(polygon)

    def _assign_polygon_zone(self, item: QGraphicsPolygonItem) -> None:
        if (
            not self.repository
            or self.floor_combo.currentData() is None
            or item not in self.shape_items
        ):
            return
        shape = self.shape_items[item]
        zones = self._available_zone_choices()
        if not zones:
            QMessageBox.information(
                self,
                "No zones available",
                "Import a configuration containing zones before assigning shapes.",
            )
            return
        dialog = ZoneSelectionDialog(
            zones,
            self.zone_combo.currentData(),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_zone = dialog.selected_zone()
        selected_index = self.zone_combo.findData(selected_zone)
        self.zone_combo.setCurrentIndex(selected_index)
        self._assign_shape(
            shape,
            int(selected_zone),
        )

    def _show_polygon_menu(self, item: QGraphicsPolygonItem) -> None:
        selected = [
            polygon
            for polygon in self.scene.selectedItems()
            if polygon in self.shape_items or polygon in self.geometry_items
        ]
        if item not in selected:
            selected = [item]
        menu, actions = self._build_polygon_menu(item, selected)
        action = menu.exec(QCursor.pos())
        assigned = item in self.geometry_items
        assigned_selected = [
            polygon for polygon in selected if polygon in self.geometry_items
        ]
        edit_points = actions["edit_points"]
        assign_zone = actions["assign_zone"]
        remove_zone = actions["remove_zone"]
        copy_above = actions["copy_above"]
        rotate_left = actions["rotate_left"]
        rotate_right = actions["rotate_right"]
        align = actions["align"]
        delete = actions["delete"]
        if action == edit_points:
            if self._editing_polygon is item:
                self._clear_vertex_handles()
            else:
                self._start_polygon_point_edit(item)
        elif action == assign_zone:
            if assigned:
                self.reassign_geometry(item)
            else:
                self._assign_polygon_zone(item)
        elif remove_zone is not None and action == remove_zone:
            self.unassign_geometry(item)
        elif action == copy_above:
            self.copy_polygons_to_floor_above(assigned_selected)
        elif action == rotate_left:
            self.rotate_geometry(item, -90.0)
        elif action == rotate_right:
            self.rotate_geometry(item, 90.0)
        elif action == align:
            self.realign_geometry(item)
        elif action == delete:
            self.delete_polygons(selected)

    def _build_polygon_menu(
        self,
        item: QGraphicsPolygonItem,
        selected: list[QGraphicsPolygonItem],
    ) -> tuple[QMenu, dict[str, QAction | None]]:
        assigned = item in self.geometry_items
        menu = QMenu(self)
        edit_points = menu.addAction(
            "Finish editing points"
            if self._editing_polygon is item
            else "Edit points"
        )
        if assigned:
            assign_zone = menu.addAction("Change assigned zoneâ€¦")
            remove_zone = menu.addAction("Remove zone assignment")
        else:
            assign_zone = menu.addAction("Assign zoneâ€¦")
            remove_zone = None
        assigned_selected = [
            polygon for polygon in selected if polygon in self.geometry_items
        ]
        copy_above = menu.addAction(
            (
                f"Copy {len(assigned_selected)} selected polygons to floor above"
                if len(assigned_selected) > 1
                else "Copy to floor above"
            )
        )
        copy_above.setEnabled(bool(assigned_selected))
        if assigned:
            transform_menu = menu.addMenu("Transform")
            rotate_left = transform_menu.addAction("Rotate 90Â° left")
            rotate_right = transform_menu.addAction("Rotate 90Â° right")
            align = transform_menu.addAction("Realign nearest edge")
        else:
            rotate_left = rotate_right = align = None
        menu.addSeparator()
        delete = menu.addAction(
            f"Delete {len(selected)} selected polygons"
            if len(selected) > 1
            else "Delete polygon"
        )
        return menu, {
            "edit_points": edit_points,
            "assign_zone": assign_zone,
            "remove_zone": remove_zone,
            "copy_above": copy_above,
            "rotate_left": rotate_left,
            "rotate_right": rotate_right,
            "align": align,
            "delete": delete,
        }

    def _start_polygon_point_edit(
        self,
        polygon: QGraphicsPolygonItem,
    ) -> None:
        self._clear_vertex_handles()
        if polygon in self.geometry_items:
            points = [
                (float(x), float(y))
                for x, y in json.loads(
                    self.geometry_items[polygon]["geometry_json"]
                )
            ]
        elif polygon in self.shape_items:
            points = list(self.shape_items[polygon].points)
        else:
            return
        unique_points = (
            points[:-1]
            if len(points) > 1 and points[0] == points[-1]
            else points
        )
        for index, (x, y) in enumerate(unique_points):
            handle = self.scene.addEllipse(
                -5,
                -5,
                10,
                10,
                QPen(QColor("#ffffff"), 1),
                QBrush(QColor("#0b5ed7")),
            )
            handle.setPos(float(x), -float(y))
            handle.setZValue(80)
            handle.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable,
                True,
            )
            handle.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
                True,
            )
            handle.setData(31, index)
            handle.setToolTip(
                f"Polygon point {index + 1} â€” drag to reposition"
            )
            self.vertex_handles[handle] = (polygon, index)
        self._editing_polygon = polygon
        self.scene.clearSelection()
        polygon.setSelected(True)

    def _clear_vertex_handles(self) -> None:
        for handle in list(self.vertex_handles):
            if handle.scene() is self.scene:
                self.scene.removeItem(handle)
        self.vertex_handles.clear()
        self._editing_polygon = None

    def _vertex_handle_moved(self, handle: QGraphicsItem) -> None:
        details = self.vertex_handles.get(handle)
        if details is None or not self.repository:
            return
        polygon, index = details
        if polygon in self.geometry_items:
            row = self.geometry_items[polygon]
            points = [
                (float(x), float(y))
                for x, y in json.loads(row["geometry_json"])
            ]
        elif polygon in self.shape_items:
            row = None
            points = list(self.shape_items[polygon].points)
        else:
            return
        closed = len(points) > 1 and points[0] == points[-1]
        if index >= len(points) - int(closed):
            return
        points[index] = (handle.pos().x(), -handle.pos().y())
        if closed and index == 0:
            points[-1] = points[0]
        polygon.setPolygon(
            QPolygonF([QPointF(x, -y) for x, y in points])
        )
        if row is not None:
            self.repository.update_zone_geometry(int(row["id"]), points)
            row["geometry_json"] = json.dumps(
                [[float(x), float(y)] for x, y in points]
            )
        else:
            self.shape_items[polygon].points = points
        self.geometry_changed.emit()

    def copy_polygons_to_floor_above(
        self,
        polygons: list[QGraphicsPolygonItem],
    ) -> None:
        if not self.repository or not polygons:
            return
        current_floor_id = self.floor_combo.currentData()
        floors = list(self.repository.fetch_floors())
        current = next(
            (
                floor
                for floor in floors
                if current_floor_id is not None
                and int(floor["id"]) == int(current_floor_id)
            ),
            None,
        )
        candidates = [
            floor
            for floor in floors
            if current is not None
            and int(floor["level_order"]) > int(current["level_order"])
        ]
        above = (
            min(
                candidates,
                key=lambda floor: (
                    int(floor["level_order"]),
                    str(floor["name"]),
                ),
            )
            if candidates
            else None
        )
        if above is None:
            QMessageBox.information(
                self,
                "No floor above",
                "The selected floor is already the highest floor.",
            )
            return
        rows = [
            self.geometry_items[polygon]
            for polygon in polygons
            if polygon in self.geometry_items
        ]
        existing_zones = {
            int(row["zone"])
            for row in self.repository.fetch_zone_geometry()
            if int(row["floor_id"]) == int(above["id"])
        }
        replacements = sorted(
            {int(row["zone"]) for row in rows} & existing_zones
        )
        if replacements and (
            QMessageBox.question(
                self,
                "Replace polygons on floor above",
                (
                    f"{above['name']} already has polygons for zones "
                    f"{', '.join(map(str, replacements))}. Replace them?"
                ),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        for row in rows:
            self.repository.assign_zone_geometry(
                int(row["zone"]),
                int(above["id"]),
                [
                    (float(x), float(y))
                    for x, y in json.loads(row["geometry_json"])
                ],
                str(row["source_layer"] or ""),
            )
        self._refresh_preserving_view()
        self.geometry_changed.emit()
        QMessageBox.information(
            self,
            "Polygons copied",
            (
                f"Copied {len(rows)} polygon"
                f"{'s' if len(rows) != 1 else ''} to {above['name']}."
            ),
        )

    def delete_polygons(
        self,
        polygons: list[QGraphicsPolygonItem],
    ) -> None:
        if not self.repository or not polygons:
            return
        if (
            QMessageBox.question(
                self,
                "Delete polygons",
                (
                    f"Delete {len(polygons)} selected polygon"
                    f"{'s' if len(polygons) != 1 else ''}? "
                    "Any zone assignments will also be removed."
                ),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        floor_id = int(self.floor_combo.currentData())
        for polygon in polygons:
            row = self.geometry_items.get(polygon)
            if row is not None:
                points = [
                    (float(x), float(y))
                    for x, y in json.loads(row["geometry_json"])
                ]
                self.repository.remove_zone_geometry(int(row["id"]))
                if str(row["source_layer"] or "") != "USER_DRAWN":
                    self.repository.ignore_zone_shape(
                        floor_id,
                        points,
                        str(row["source_layer"] or ""),
                    )
                continue
            shape = self.shape_items.get(polygon)
            if shape is None:
                continue
            self.repository.ignore_zone_shape(
                floor_id,
                shape.points,
                shape.layer,
                shape.source_key or zone_shape_key(shape.points),
            )
            if shape in self.pending_shapes.get(floor_id, []):
                self.pending_shapes[floor_id].remove(shape)
        self._refresh_preserving_view()
        self.geometry_changed.emit()

    def _selected_door_id(self) -> int | None:
        items = self.door_table.selectedItems()
        if not items:
            return None
        value = items[0].data(Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    def _show_door_menu(self, door_id: int) -> None:
        menu = QMenu(self)
        edit = menu.addAction("Edit door…")
        delete = menu.addAction("Delete door")
        action = menu.exec(QCursor.pos())
        if action == edit:
            self.edit_door(door_id)
        elif action == delete:
            self.delete_door(door_id)

    def edit_selected_door(self) -> None:
        door_id = self._selected_door_id()
        if door_id is not None:
            self.edit_door(door_id)

    def edit_door(self, door_id: int) -> None:
        if not self.repository:
            return
        door = next(
            (
                dict(row)
                for row in _fetch_doors(self.repository)
                if int(row["id"]) == int(door_id)
            ),
            None,
        )
        if door is None:
            return
        dialog = DoorDialog(
            self.repository,
            int(door["floor_id"]),
            (float(door["start_x"]), float(door["start_y"])),
            (float(door["end_x"]), float(door["end_y"])),
            door,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.repository.update_door(int(door_id), **dialog.values())
        except ValueError as error:
            QMessageBox.critical(self, "Could not update door", str(error))
            return
        self._refresh_preserving_view()
        self.geometry_changed.emit()

    def delete_selected_door(self) -> None:
        door_id = self._selected_door_id()
        if door_id is not None:
            self.delete_door(door_id)

    def delete_door(self, door_id: int) -> None:
        if not self.repository:
            return
        door = next(
            (
                row
                for row in _fetch_doors(self.repository)
                if int(row["id"]) == int(door_id)
            ),
            None,
        )
        if door is None:
            return
        if (
            QMessageBox.question(
                self,
                "Delete door",
                f"Delete {door['name']} and its linked fire-control details?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.repository.delete_door(int(door_id))
        self._refresh_preserving_view()
        self.geometry_changed.emit()

    def select_polygon_at_point(
        self,
        scene_point: QPointF,
        additive: bool = False,
        previous_selection: object = None,
    ) -> None:
        candidates = self._polygons_at(scene_point)
        if not candidates:
            return
        polygon = (
            self._choose_overlapping_polygon(candidates)
            if len(candidates) > 1
            else candidates[0]
        )
        if polygon is None:
            return
        previous = {
            item
            for item in (
                previous_selection
                if isinstance(previous_selection, (list, tuple, set))
                else []
            )
            if item in self.shape_items or item in self.geometry_items
        }
        self.scene.clearSelection()
        if additive:
            for selected in previous:
                try:
                    selected.setSelected(True)
                except RuntimeError:
                    continue
            polygon.setSelected(polygon not in previous)
        else:
            polygon.setSelected(True)

    def _polygons_at(
        self,
        scene_point: QPointF,
    ) -> list[QGraphicsPolygonItem]:
        return [
            item
            for item in self.scene.items(scene_point)
            if item in self.shape_items or item in self.geometry_items
        ]

    def _choose_overlapping_polygon(
        self,
        polygons: list[QGraphicsPolygonItem],
    ) -> QGraphicsPolygonItem | None:
        choices: list[tuple[str, QGraphicsPolygonItem]] = []
        for index, polygon in enumerate(polygons, start=1):
            if polygon in self.geometry_items:
                row = self.geometry_items[polygon]
                description = str(row.get("description") or "").strip()
                label = (
                    f"Assigned: Zone {row['zone']}"
                    f"{' - ' + description if description else ''}"
                )
            else:
                shape = self.shape_items[polygon]
                label = (
                    f"Unassigned: {shape.layer} "
                    f"({shape.entity_type}, polygon {index})"
                )
            choices.append((label, polygon))
        dialog = PolygonSelectionDialog(choices, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_polygon()

    def _show_assigned_geometry_menu(
        self,
        item: QGraphicsPolygonItem,
    ) -> None:
        menu = QMenu(self)
        reassign = menu.addAction("Reassign to another zone…")
        rotate_left = menu.addAction("Rotate 90° left")
        rotate_right = menu.addAction("Rotate 90° right")
        align = menu.addAction("Realign nearest edge")
        menu.addSeparator()
        unassign = menu.addAction("Unassign polygon")
        action = menu.exec(QCursor.pos())
        if action == reassign:
            self.reassign_geometry(item)
        elif action == rotate_left:
            self.rotate_geometry(item, -90.0)
        elif action == rotate_right:
            self.rotate_geometry(item, 90.0)
        elif action == align:
            self.realign_geometry(item)
        elif action == unassign:
            self.unassign_geometry(item)

    def reassign_geometry(self, item: QGraphicsPolygonItem) -> None:
        row = self.geometry_items.get(item)
        if not self.repository or row is None:
            return
        zones = self._available_zone_choices(row["zone"])
        dialog = ZoneSelectionDialog(
            zones,
            row["zone"],
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_zone = dialog.selected_zone()
        try:
            self.repository.reassign_zone_geometry(
                int(row["id"]),
                int(selected_zone),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could not reassign polygon", str(error))
            return
        self._refresh_preserving_view()
        self.geometry_changed.emit()

    def unassign_geometry(self, item: QGraphicsPolygonItem) -> None:
        row = self.geometry_items.get(item)
        if not self.repository or row is None:
            return
        if (
            QMessageBox.question(
                self,
                "Unassign zone polygon",
                f"Remove this polygon from zone {row['zone']}?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        floor_id = int(row["floor_id"])
        points = [
            (float(x), float(y))
            for x, y in json.loads(row["geometry_json"])
        ]
        self.repository.remove_zone_geometry(int(row["id"]))
        self.pending_shapes.setdefault(floor_id, []).append(
            DxfShape(
                layer=str(row["source_layer"] or "USER_DRAWN"),
                entity_type="LWPOLYLINE",
                points=points,
            )
        )
        self._refresh_preserving_view()
        self.geometry_changed.emit()

    def rotate_geometry(
        self,
        item: QGraphicsPolygonItem,
        degrees: float,
    ) -> None:
        row = self.geometry_items.get(item)
        if not self.repository or row is None:
            return
        points = [
            (float(x), float(y))
            for x, y in json.loads(row["geometry_json"])
        ]
        rotated = self._rotated_points(points, degrees)
        self.repository.update_zone_geometry(int(row["id"]), rotated)
        self.refresh_scene(False)
        self.geometry_changed.emit()

    def realign_geometry(self, item: QGraphicsPolygonItem) -> None:
        row = self.geometry_items.get(item)
        if row is None:
            return
        points = [
            (float(x), float(y))
            for x, y in json.loads(row["geometry_json"])
        ]
        unique = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
        if len(unique) < 2:
            return
        longest = max(
            zip(unique, unique[1:] + unique[:1]),
            key=lambda pair: (
                (pair[1][0] - pair[0][0]) ** 2
                + (pair[1][1] - pair[0][1]) ** 2
            ),
        )
        angle = math.degrees(
            math.atan2(
                longest[1][1] - longest[0][1],
                longest[1][0] - longest[0][0],
            )
        )
        target = round(angle / 90.0) * 90.0
        self.rotate_geometry(item, target - angle)

    @staticmethod
    def _rotated_points(
        points: list[tuple[float, float]],
        degrees: float,
    ) -> list[tuple[float, float]]:
        closed = len(points) > 1 and points[0] == points[-1]
        unique = points[:-1] if closed else points
        if not unique:
            return points
        centre_x = sum(point[0] for point in unique) / len(unique)
        centre_y = sum(point[1] for point in unique) / len(unique)
        radians = math.radians(degrees)
        cosine = math.cos(radians)
        sine = math.sin(radians)
        rotated = [
            (
                centre_x
                + (x - centre_x) * cosine
                - (y - centre_y) * sine,
                centre_y
                + (x - centre_x) * sine
                + (y - centre_y) * cosine,
            )
            for x, y in unique
        ]
        if closed:
            rotated.append(rotated[0])
        return rotated

    def _assign_shape(self, shape: DxfShape, zone: int) -> None:
        if not self.repository or self.floor_combo.currentData() is None:
            return
        floor_id = int(self.floor_combo.currentData())
        if not self._confirm_geometry_replacement(int(zone), floor_id):
            return
        self.repository.assign_zone_geometry(
            int(zone),
            floor_id,
            shape.points,
            shape.layer,
        )
        if shape in self.pending_shapes.get(floor_id, []):
            self.pending_shapes[floor_id].remove(shape)
        self._refresh_preserving_view()
        self.geometry_changed.emit()

    def _confirm_geometry_replacement(self, zone: int, floor_id: int) -> bool:
        if not self.repository:
            return False
        existing = any(
            int(row["zone"]) == int(zone)
            and int(row["floor_id"]) == int(floor_id)
            for row in self.repository.fetch_zone_geometry()
        )
        if not existing:
            return True
        return (
            QMessageBox.question(
                self,
                "Replace zone polygon",
                f"Zone {zone} already has a polygon on this floor. Replace it?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )


class MatrixPage(Page):
    matrix_imported = Signal()

    def __init__(self):
        super().__init__("Cause and effect matrix")
        note = QLabel(
            "HTM suggestions are a commissioning aid, not an approved fire strategy. "
            "The project-specific cause-and-effect must be agreed by competent stakeholders."
        )
        note.setWordWrap(True)
        self.layout.addWidget(note)
        controls = QHBoxLayout()
        import_matrix = QPushButton("Import Cause & Effect workbook")
        import_matrix.clicked.connect(self.import_workbook)
        generate = QPushButton("Generate HTM adjacency suggestions")
        generate.setProperty("secondary", True)
        generate.clicked.connect(self.generate)
        generate_doors = QPushButton("Generate door control suggestions")
        generate_doors.setProperty("secondary", True)
        generate_doors.clicked.connect(self.generate_doors)
        custom = QPushButton("Add custom door / output rule")
        custom.setProperty("secondary", True)
        custom.clicked.connect(self.add_custom)
        self.edit_custom_button = QPushButton("Edit selected custom rule")
        self.edit_custom_button.setProperty("secondary", True)
        self.edit_custom_button.setEnabled(False)
        self.edit_custom_button.clicked.connect(self.edit_custom)
        self.remove_custom_button = QPushButton("Remove selected rules")
        self.remove_custom_button.setProperty("secondary", True)
        self.remove_custom_button.setEnabled(False)
        self.remove_custom_button.clicked.connect(self.remove_custom)
        controls.addWidget(import_matrix)
        controls.addWidget(generate)
        controls.addWidget(generate_doors)
        controls.addWidget(custom)
        controls.addWidget(self.edit_custom_button)
        controls.addWidget(self.remove_custom_button)
        controls.addStretch()
        self.layout.addLayout(controls)

        self.validation = QLabel("No Cause & Effect workbook has been imported.")
        self.validation.setWordWrap(True)
        self.layout.addWidget(self.validation)

        self.tabs = QTabWidget()
        self.activation_table = FilterableTableWidget(0, 9)
        self.activation_table.setHorizontalHeaderLabels(
            [
                "Node Number",
                "Node Name",
                "Output Group Number",
                "Output Group Name",
                "Zone Number",
                "Zone Name",
                "Ringing Style",
                "Reference Check",
                "Comments",
            ]
        )
        self.activation_table.setAlternatingRowColors(True)
        self.activation_table.setSortingEnabled(True)
        self.activation_table.setColumnWidth(1, 240)
        self.activation_table.setColumnWidth(3, 300)
        self.activation_table.setColumnWidth(5, 260)
        self.activation_table.setColumnWidth(8, 320)
        self.activation_table.itemChanged.connect(self._activation_changed)
        self.tabs.addTab(self.activation_table, "Imported activations")

        self.activation_matrix = CauseEffectMatrixWidget()
        self.tabs.addTab(self.activation_matrix, "Activation matrix")

        self.reference_table = FilterableTableWidget(0, 8)
        self.reference_table.setHorizontalHeaderLabels(
            [
                "Node Number",
                "Node Name",
                "Output Group Number",
                "Output Group Name",
                "Zone Number",
                "Zone Name",
                "Ringing Style",
                "Issue",
            ]
        )
        self.reference_table.setAlternatingRowColors(True)
        self.reference_table.setSortingEnabled(True)
        self.reference_table.setColumnWidth(1, 240)
        self.reference_table.setColumnWidth(3, 300)
        self.reference_table.setColumnWidth(5, 260)
        self.tabs.addTab(self.reference_table, "OutputGroupInfo-only")

        self.table = FilterableTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Trigger", "Relation", "Target zone", "Target node", "Output group", "Action", "Source", "Notes"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setColumnWidth(5, 180)
        self.table.setColumnWidth(7, 360)
        self.table.itemSelectionChanged.connect(
            self._custom_rule_selection_changed
        )
        self.table.itemDoubleClicked.connect(self.edit_custom)
        self.tabs.addTab(self.table, "Editable rules")
        self.layout.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        self.refresh_activations()
        self.refresh_rules()

    def refresh_activations(self) -> None:
        self.activation_table.blockSignals(True)
        self.activation_table.setSortingEnabled(False)
        self.activation_table.setRowCount(0)
        self.activation_matrix.set_activations([])
        self.reference_table.setSortingEnabled(False)
        self.reference_table.setRowCount(0)
        if not self.repository:
            self.validation.setText("No Cause & Effect workbook has been imported.")
            self.activation_table.setSortingEnabled(True)
            self.reference_table.setSortingEnabled(True)
            self.activation_table.blockSignals(False)
            return

        imported = self.repository.latest_cause_effect_import()
        if imported is None:
            self.validation.setText("No Cause & Effect workbook has been imported.")
            self.activation_table.setSortingEnabled(True)
            self.reference_table.setSortingEnabled(True)
            self.activation_table.blockSignals(False)
            return

        issue_count = imported["matrix_only_count"] + imported["reference_only_count"]
        self.validation.setText(
            f"{imported['source_name']}: {imported['activation_count']:,} matrix "
            f"activations; {imported['matched_count']:,} matched OutputGroupInfo; "
            f"{imported['matrix_only_count']:,} matrix-only; "
            f"{imported['reference_only_count']:,} reference-only."
        )
        self.validation.setStyleSheet(
            "color: #b45309;" if issue_count else "color: #166534;"
        )
        activations = self.repository.fetch_cause_effect_activations()
        for activation in activations:
            row = self.activation_table.rowCount()
            self.activation_table.insertRow(row)
            values = [
                activation["target_node"],
                activation["target_node_name"],
                activation["output_group"],
                activation["output_group_name"],
                activation["trigger_zone"],
                activation["output_zone_name"],
                activation["ringing_style"],
                (
                    "Matched"
                    if activation["reference_status"] == "matched"
                    else "Matrix only"
                ),
                activation["comments"],
            ]
            for column, value in enumerate(values):
                item = _item(value)
                if column != 8:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setData(Qt.ItemDataRole.UserRole, activation["id"])
                if column == 7 and activation["reference_status"] != "matched":
                    item.setBackground(QColor("#fff3cd"))
                self.activation_table.setItem(row, column, item)
        self.activation_matrix.set_activations(
            activations,
            self.repository.fetch_cause_effect_output_groups(),
        )
        for reference in self.repository.fetch_cause_effect_reference_only():
            row = self.reference_table.rowCount()
            self.reference_table.insertRow(row)
            values = [
                reference["target_node"],
                reference["target_node_name"],
                reference["output_group"],
                reference["output_group_name"],
                reference["trigger_zone"],
                reference["output_zone_name"],
                reference["ringing_style"],
                "Not present in Cause & Effect matrix",
            ]
            for column, value in enumerate(values):
                item = _item(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 7:
                    item.setBackground(QColor("#f8d7da"))
                self.reference_table.setItem(row, column, item)
        self.activation_table.setSortingEnabled(True)
        self.activation_table.apply_filters()
        self.reference_table.setSortingEnabled(True)
        self.reference_table.apply_filters()
        self.activation_table.blockSignals(False)

    def refresh_rules(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if not self.repository:
            return
        for rule in self.repository.fetch_rules():
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                rule["trigger_zone"], rule["relation"], rule["target_zone"], rule["target_node"],
                rule["output_group"], rule["action"], rule["source"], rule["notes"],
            ]
            for column, value in enumerate(values):
                item = _item(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, int(rule["id"]))
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.apply_filters()
        self._custom_rule_selection_changed()

    def import_workbook(self) -> None:
        if not self.repository:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Cause & Effect workbook",
            "",
            "Excel workbooks (*.xlsx)",
        )
        if not path:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            _, parsed = self.repository.import_cause_effect(path)
            self.refresh()
            self.matrix_imported.emit()
            self.tabs.setCurrentIndex(0)
            summary = (
                f"Imported {len(parsed.activations):,} matrix activations.\n"
                f"{parsed.matched_count:,} matched OutputGroupInfo; "
                f"{parsed.matrix_only_count:,} matrix-only; "
                f"{parsed.reference_only_count:,} reference-only."
            )
            if parsed.warnings:
                summary += "\n\n" + "\n".join(parsed.warnings)
            QMessageBox.information(self, "Cause & Effect imported", summary)
        except Exception as error:
            QMessageBox.critical(self, "Cause & Effect import failed", str(error))
        finally:
            QApplication.restoreOverrideCursor()

    def _activation_changed(self, item: QTableWidgetItem) -> None:
        if not self.repository or item.column() != 8:
            return
        activation_id = item.data(Qt.ItemDataRole.UserRole)
        if activation_id is not None:
            self.repository.update_cause_effect_comment(
                int(activation_id),
                item.text(),
            )
            self.activation_table.apply_filters()

    def generate(self) -> None:
        if not self.repository:
            return
        count = generate_htm_rules(self.repository)
        self.refresh()
        QMessageBox.information(
            self,
            "Suggestions generated",
            f"Created {count} rules from same-floor adjacency and direct vertical overlap.",
        )

    def generate_doors(self) -> None:
        if not self.repository:
            return
        count = generate_door_rules(self.repository)
        self.refresh()
        QMessageBox.information(
            self,
            "Door suggestions generated",
            (
                f"Created {count} door-control rules from the zones on either "
                "side and the linked device output groups. Verify them against "
                "the approved fire strategy."
            ),
        )

    def add_custom(self) -> None:
        if not self.repository:
            return
        dialog = RuleDialog(self.repository, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _selected_custom_rule_ids(self) -> list[int]:
        rule_ids = self._selected_rule_ids()
        if not rule_ids:
            return []
        for row in self._selected_rule_rows():
            source_item = self.table.item(row, 6)
            if (
                source_item is None
                or source_item.text().casefold() != "custom"
            ):
                return []
        return rule_ids

    def _selected_rule_rows(self) -> list[int]:
        return sorted(
            {
                index.row()
                for index in self.table.selectionModel().selectedRows()
            }
        )

    def _selected_rule_ids(self) -> list[int]:
        rows = self._selected_rule_rows()
        if not rows:
            return []
        rule_ids: list[int] = []
        for row in rows:
            id_item = self.table.item(row, 0)
            if id_item is None:
                return []
            rule_id = id_item.data(Qt.ItemDataRole.UserRole)
            if rule_id is None:
                return []
            rule_ids.append(int(rule_id))
        return rule_ids

    def _custom_rule_selection_changed(self) -> None:
        selected_rule_ids = self._selected_rule_ids()
        custom_rule_ids = self._selected_custom_rule_ids()
        self.edit_custom_button.setEnabled(
            len(selected_rule_ids) == 1
            and len(custom_rule_ids) == 1
        )
        self.remove_custom_button.setEnabled(bool(selected_rule_ids))

    def edit_custom(self, *_args) -> None:
        if not self.repository:
            return
        rule_ids = self._selected_custom_rule_ids()
        if len(rule_ids) != 1:
            return
        rule_id = rule_ids[0]
        rule = self.repository.fetch_rule(rule_id)
        if rule is None or str(rule["source"]).casefold() != "custom":
            return
        dialog = RuleDialog(self.repository, self, rule=rule)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_rules()

    def remove_custom(self) -> None:
        if not self.repository:
            return
        rule_ids = self._selected_rule_ids()
        if not rule_ids:
            return
        rules = [
            self.repository.fetch_rule(rule_id)
            for rule_id in rule_ids
        ]
        if any(rule is None for rule in rules):
            return
        count = len(rule_ids)
        prompt = (
            (
                f"Remove the rule “{rules[0]['name']}”?\n\n"
                f"Source: {rules[0]['source']}"
            )
            if count == 1
            else f"Remove the {count} selected rules?"
        )
        if (
            QMessageBox.question(
                self,
                "Remove selected rules",
                prompt,
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.repository.delete_rules(rule_ids)
        except ValueError as error:
            QMessageBox.warning(self, "Could not remove rules", str(error))
            return
        self.refresh_rules()


class RuleDialog(QDialog):
    def __init__(
        self,
        repository: ProjectRepository,
        parent: QWidget | None = None,
        rule=None,
    ):
        super().__init__(parent)
        self.repository = repository
        self.rule_id = int(rule["id"]) if rule is not None else None
        self.setWindowTitle(
            "Edit custom cause-and-effect rule"
            if rule is not None
            else "Add custom cause-and-effect rule"
        )
        form = QFormLayout(self)
        zones = repository.fetch_zones()
        self.name = QLineEdit("Close straddling fire door")
        self.trigger = QComboBox()
        self.target = QComboBox()
        for zone in zones:
            self.trigger.addItem(_zone_label(zone), zone["number"])
            self.target.addItem(_zone_label(zone), zone["number"])
        self.relation = QComboBox()
        self.relation.addItems(["exact", "adjacent", "straddles two zones", "directly above/below"])
        self.action = QComboBox()
        self.action.addItems(
            ["ALERT / intermittent", "EVACUATE / continuous", "CLOSE FIRE DOOR", "GROUND LIFT", "SHUT DOWN HVAC", "ACTIVATE OUTPUT"]
        )
        self.target_node = QSpinBox()
        self.target_node.setRange(0, 200)
        self.output_group = QSpinBox()
        self.output_group.setRange(0, 2000)
        self.notes = QTextEdit()
        form.addRow("Rule name", self.name)
        form.addRow("Trigger zone", self.trigger)
        form.addRow("Relation", self.relation)
        form.addRow("Target zone", self.target)
        form.addRow("Target node (0 = any)", self.target_node)
        form.addRow("Output group (0 = none)", self.output_group)
        form.addRow("Action", self.action)
        form.addRow("Notes", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        if rule is not None:
            self.name.setText(str(rule["name"]))
            self._set_combo_data(self.trigger, rule["trigger_zone"])
            self.relation.setCurrentText(str(rule["relation"]))
            self._set_combo_data(self.target, rule["target_zone"])
            self.target_node.setValue(int(rule["target_node"] or 0))
            self.output_group.setValue(int(rule["output_group"] or 0))
            self.action.setCurrentText(str(rule["action"]))
            self.notes.setPlainText(str(rule["notes"] or ""))

    @staticmethod
    def _set_combo_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def save(self) -> None:
        values = (
            self.name.text().strip() or "Custom rule",
            int(self.trigger.currentData()),
            self.relation.currentText(),
            int(self.target.currentData()),
            self.target_node.value() or None,
            self.output_group.value() or None,
            self.action.currentText(),
            self.notes.toPlainText(),
        )
        if self.rule_id is None:
            self.repository.add_rule(*values)
        else:
            self.repository.update_custom_rule(self.rule_id, *values)
        self.accept()


class ZoneTestExportDialog(QDialog):
    def __init__(self, repository: ProjectRepository, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Select zones for spreadsheet testing")
        self.resize(980, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Select the fire-call trigger zones to include. Every expected "
                "output-group activation for each selected zone will be listed."
            )
        )

        zone_names = {
            normalise_zone_key(row["number"]): str(
                row["description"] or ""
            ).strip()
            for row in repository.fetch_zones()
        }
        self._zone_labels: dict[str, str] = {}
        for row in repository.fetch_cause_effect_trigger_zones():
            zone = normalise_zone_key(row["trigger_zone"])
            name = str(row["trigger_zone_name"] or "").strip()
            name = name or zone_names.get(zone, "")
            self._zone_labels[zone] = (
                f"Zone {zone}{' - ' + name if name else ''}"
            )

        lists = QHBoxLayout()
        available_layout = QVBoxLayout()
        available_layout.addWidget(QLabel("Available zones"))
        self.zone_search = QLineEdit()
        self.zone_search.setPlaceholderText("Type a zone number or name…")
        self.zone_search.setClearButtonEnabled(True)
        self.zone_search.textChanged.connect(self._filter_zones)
        completer = QCompleter(
            sorted(self._zone_labels.values(), key=natural_sort_key),
            self.zone_search,
        )
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setMaxVisibleItems(20)
        self.zone_search.setCompleter(completer)
        available_layout.addWidget(self.zone_search)

        self.available_zones = QListWidget()
        self.available_zones.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.available_zones.itemDoubleClicked.connect(
            lambda _item: self._move_right()
        )
        self._set_zone_values(
            self.available_zones, list(self._zone_labels)
        )
        available_layout.addWidget(self.available_zones, 1)
        lists.addLayout(available_layout, 1)

        transfer_layout = QVBoxLayout()
        transfer_layout.addStretch()
        self.move_all_left_button = QPushButton("Move all left")
        self.move_left_button = QPushButton("Move left")
        self.move_right_button = QPushButton("Move right")
        self.move_all_right_button = QPushButton("Move all right")
        for button in (
            self.move_all_left_button,
            self.move_left_button,
            self.move_right_button,
            self.move_all_right_button,
        ):
            button.setMinimumWidth(130)
            button.setProperty("secondary", True)
            transfer_layout.addWidget(button)
        self.move_all_left_button.clicked.connect(self._move_all_left)
        self.move_left_button.clicked.connect(self._move_left)
        self.move_right_button.clicked.connect(self._move_right)
        self.move_all_right_button.clicked.connect(self._move_all_right)
        transfer_layout.addStretch()
        lists.addLayout(transfer_layout)

        selected_layout = QVBoxLayout()
        selected_layout.addWidget(QLabel("Selected zones"))
        self.selected_zones_list = QListWidget()
        self.selected_zones_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.selected_zones_list.itemDoubleClicked.connect(
            lambda _item: self._move_left()
        )
        selected_layout.addWidget(self.selected_zones_list, 1)
        lists.addLayout(selected_layout, 1)
        layout.addLayout(lists, 1)

        # Retain the old public name for callers that use it to inspect the
        # source list; selection now lives in selected_zones_list.
        self.zones = self.available_zones

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Export workbook"
        )
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_zones(self) -> list[str]:
        return [
            str(
                self.selected_zones_list.item(index).data(
                    Qt.ItemDataRole.UserRole
                )
            )
            for index in range(self.selected_zones_list.count())
        ]

    def _select_all(self) -> None:
        self._move_all_right()

    def _clear_selection(self) -> None:
        self._move_all_left()

    def _filter_zones(self, text: str) -> None:
        query = text.strip().casefold()
        for index in range(self.available_zones.count()):
            item = self.available_zones.item(index)
            item.setHidden(query not in item.text().casefold())

    def _set_zone_values(
        self, widget: QListWidget, zones: list[str]
    ) -> None:
        widget.clear()
        for zone in sorted(
            zones,
            key=lambda value: natural_sort_key(
                self._zone_labels.get(value, value)
            ),
        ):
            item = QListWidgetItem(self._zone_labels.get(zone, zone))
            item.setData(Qt.ItemDataRole.UserRole, zone)
            widget.addItem(item)

    @staticmethod
    def _zone_values(widget: QListWidget) -> list[str]:
        return [
            str(widget.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(widget.count())
        ]

    def _move_selected(
        self, source: QListWidget, target: QListWidget
    ) -> None:
        moved = {
            str(item.data(Qt.ItemDataRole.UserRole))
            for item in source.selectedItems()
        }
        if not moved:
            return
        source_zones = [
            zone
            for zone in self._zone_values(source)
            if zone not in moved
        ]
        target_zones = self._zone_values(target) + list(moved)
        self._set_zone_values(source, source_zones)
        self._set_zone_values(target, target_zones)
        self._filter_zones(self.zone_search.text())

    def _move_all(
        self, source: QListWidget, target: QListWidget
    ) -> None:
        moved = self._zone_values(source)
        if not moved:
            return
        self._set_zone_values(source, [])
        self._set_zone_values(target, self._zone_values(target) + moved)
        self._filter_zones(self.zone_search.text())

    def _move_left(self) -> None:
        self._move_selected(
            self.selected_zones_list, self.available_zones
        )

    def _move_right(self) -> None:
        self._move_selected(
            self.available_zones, self.selected_zones_list
        )

    def _move_all_left(self) -> None:
        self._move_all(
            self.selected_zones_list, self.available_zones
        )

    def _move_all_right(self) -> None:
        self._move_all(
            self.available_zones, self.selected_zones_list
        )

    def _accept_selection(self) -> None:
        if not self.selected_zones():
            QMessageBox.information(
                self,
                "Select zones",
                "Move at least one zone into the Selected zones list.",
            )
            return
        self.accept()


class ChangeRevisionDialog(QDialog):
    def __init__(
        self,
        repository: ProjectRepository,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Select revisions for tracked changes")
        self.resize(720, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Select the configuration revisions to include. Project and "
                "Cause & Effect changes recorded while each revision was "
                "current will be grouped beneath it."
            )
        )
        controls = QHBoxLayout()
        select_all = QPushButton("Select all revisions")
        select_all.clicked.connect(self._select_all)
        clear = QPushButton("Clear selection")
        clear.setProperty("secondary", True)
        clear.clicked.connect(self._clear_selection)
        controls.addWidget(select_all)
        controls.addWidget(clear)
        controls.addStretch()
        layout.addLayout(controls)

        self.revisions = QListWidget()
        self.revisions.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        snapshots = sorted(
            (dict(row) for row in repository.fetch_snapshots()),
            key=lambda row: (str(row["imported_at"]), int(row["id"])),
        )
        numbered = [
            (number, snapshot)
            for number, snapshot in enumerate(snapshots, 1)
        ]
        for number, snapshot in reversed(numbered):
            item = QListWidgetItem(
                f"Revision {number} — {snapshot['source_name']} — "
                f"imported {snapshot['imported_at']}"
            )
            item.setData(
                Qt.ItemDataRole.UserRole, int(snapshot["id"])
            )
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
            )
            item.setCheckState(Qt.CheckState.Checked)
            self.revisions.addItem(item)
        layout.addWidget(self.revisions, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Continue to export"
        )
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_revision_ids(self) -> list[int]:
        return [
            int(item.data(Qt.ItemDataRole.UserRole))
            for index in range(self.revisions.count())
            if (
                (item := self.revisions.item(index)).checkState()
                == Qt.CheckState.Checked
            )
        ]

    def _select_all(self) -> None:
        for index in range(self.revisions.count()):
            self.revisions.item(index).setCheckState(
                Qt.CheckState.Checked
            )

    def _clear_selection(self) -> None:
        for index in range(self.revisions.count()):
            self.revisions.item(index).setCheckState(
                Qt.CheckState.Unchecked
            )

    def _accept_selection(self) -> None:
        if not self.selected_revision_ids():
            QMessageBox.information(
                self,
                "Select revisions",
                "Select at least one revision to include in the PDF.",
            )
            return
        self.accept()


class TestPage(Page):
    def __init__(self):
        super().__init__("Test mode")
        controls = QHBoxLayout()
        self.zone_combo = QComboBox()
        self.zone_combo.setMinimumWidth(430)
        self.zone_combo.setMinimumContentsLength(42)
        self.zone_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.zone_combo.view().setMinimumWidth(680)
        self.zone_combo.setEditable(True)
        self.zone_combo.setInsertPolicy(
            QComboBox.InsertPolicy.NoInsert
        )
        self.zone_combo.lineEdit().setClearButtonEnabled(True)
        self.zone_combo.lineEdit().setPlaceholderText(
            "Type a zone number or description…"
        )
        zone_completer = self.zone_combo.completer()
        zone_completer.setCaseSensitivity(
            Qt.CaseSensitivity.CaseInsensitive
        )
        zone_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        zone_completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion
        )
        zone_completer.setMaxVisibleItems(20)
        self.zone_combo.currentIndexChanged.connect(
            self._selected_zone_changed
        )
        self.floor_combo = QComboBox()
        self.floor_combo.setMinimumWidth(180)
        self.floor_combo.currentIndexChanged.connect(
            self._selected_floor_changed
        )
        self.scope_combo = QComboBox()
        self.engineer = QLineEdit()
        self.engineer.setPlaceholderText("Engineer")
        self.engineer.setMaximumWidth(180)
        run = QPushButton("Simulate fire trigger")
        run.clicked.connect(self.simulate)
        save = QPushButton("Record test")
        save.setProperty("secondary", True)
        save.clicked.connect(self.record_test)
        controls.addWidget(QLabel("Fire in zone"))
        controls.addWidget(self.zone_combo)
        controls.addWidget(QLabel("Floor"))
        controls.addWidget(self.floor_combo)
        controls.addWidget(QLabel("Scope"))
        controls.addWidget(self.scope_combo)
        controls.addWidget(self.engineer)
        controls.addWidget(run)
        controls.addWidget(save)
        controls.addStretch()
        self.layout.addLayout(controls)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.scene = QGraphicsScene()
        self.map = MapGraphicsView(self.scene)
        self.map.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.map.scene_clicked.connect(self.show_zone_popup_at)
        self.zone_popup = None
        self._current_effect_map: dict[int, str] = {}
        self._current_visible_zones: set[int] = set()
        self._current_triggered_devices: dict[str, str] = {}
        self._current_sounder_zone_styles: dict[int, str] = {}
        self.results = FilterableTableWidget(0, 9)
        self.results.setHorizontalHeaderLabels(
            [
                "Expected", "Zone", "Node", "Loop", "Address", "Sub",
                "Device / effect", "Observed / result", "Comments",
            ]
        )
        self.results.setAlternatingRowColors(True)
        self.results.setColumnWidth(6, 280)
        self.results.setColumnWidth(7, 150)
        self.results.setColumnWidth(8, 320)
        splitter.addWidget(self.map)
        splitter.addWidget(self.results)
        self.layout.addWidget(splitter, 1)
        self.legend = QLabel(
            "Green = normal  •  Red = fire/evacuate  •  "
            "Yellow = adjacent/pre-alarm alert  •  "
            "Bright red device = triggered output  •  "
            "Door padlock = access state  •  OPEN/CLOSED = hold-open  •  "
            "Wheel = zoom  •  Left or middle drag = pan"
        )
        self.layout.addWidget(self.legend)

    def refresh(self) -> None:
        current_floor = self.floor_combo.currentData()
        self.zone_combo.blockSignals(True)
        self.floor_combo.blockSignals(True)
        self.zone_combo.clear()
        self.floor_combo.clear()
        self.scope_combo.clear()
        self.scope_combo.addItem("Whole site / interpanel", None)
        if self.repository:
            fetch_floors = getattr(self.repository, "fetch_floors", None)
            floors = list(fetch_floors()) if fetch_floors else []
            for floor in floors:
                self.floor_combo.addItem(
                    str(floor["name"]),
                    int(floor["id"]),
                )
            floor_index = self.floor_combo.findData(current_floor)
            if floor_index >= 0:
                self.floor_combo.setCurrentIndex(floor_index)
            seen_zones: set[str] = set()
            for zone in self.repository.fetch_zones():
                self.zone_combo.addItem(_zone_label(zone), zone["number"])
                seen_zones.add(normalise_zone_key(zone["number"]))
            for zone in self.repository.fetch_cause_effect_trigger_zones():
                zone_key = normalise_zone_key(zone["trigger_zone"])
                if zone_key in seen_zones:
                    continue
                description = str(zone["trigger_zone_name"] or "").strip()
                label = (
                    f"Zone {zone_key} - {description}"
                    if description
                    else f"Zone {zone_key}"
                )
                self.zone_combo.addItem(label, zone_key)
                seen_zones.add(zone_key)
            for panel in self.repository.fetch_panels():
                self.scope_combo.addItem(f"Node {panel['node']} — {panel['name']}", panel["node"])
        self.zone_combo.blockSignals(False)
        self.floor_combo.blockSignals(False)
        self._selected_zone_changed()

    def _selected_zone_changed(self) -> None:
        if not self.repository or self.zone_combo.currentData() is None:
            self._current_effect_map = {}
            self._current_visible_zones = set()
            self._current_triggered_devices = {}
            self._current_sounder_zone_styles = {}
            self._draw_map({}, set(), {}, {})
            return
        zone = normalise_zone_key(self.zone_combo.currentData())
        visible = {int(zone)} if zone.isdigit() else set()
        if zone.isdigit():
            self._select_floor_for_zone(int(zone))
        self._current_effect_map = {}
        self._current_visible_zones = visible
        self._current_triggered_devices = {}
        self._current_sounder_zone_styles = {}
        self._draw_map(
            self._current_effect_map,
            self._current_visible_zones,
            self._current_triggered_devices,
            self._current_sounder_zone_styles,
        )

    def _select_floor_for_zone(self, zone: int) -> None:
        if not self.repository or self.floor_combo.count() == 0:
            return
        floor_ids = {
            int(row["floor_id"])
            for row in self.repository.fetch_zone_geometry()
            if int(row["zone"]) == int(zone)
            and "floor_id" in dict(row)
        }
        if not floor_ids or self.floor_combo.currentData() in floor_ids:
            return
        for floor_id in sorted(floor_ids):
            index = self.floor_combo.findData(floor_id)
            if index >= 0:
                self.floor_combo.blockSignals(True)
                self.floor_combo.setCurrentIndex(index)
                self.floor_combo.blockSignals(False)
                return

    def _selected_floor_changed(self) -> None:
        self._draw_map(
            self._current_effect_map,
            self._current_visible_zones,
            self._current_triggered_devices,
            self._current_sounder_zone_styles,
        )

    def _triggered_device_styles(self, activations) -> dict[str, str]:
        if not self.repository:
            return {}
        styles_by_group: dict[tuple[int, int], list[str]] = defaultdict(list)
        for activation in activations:
            key = (
                int(activation["target_node"]),
                int(activation["output_group"]),
            )
            style = str(activation["ringing_style"] or "Triggered").strip()
            if style not in styles_by_group[key]:
                styles_by_group[key].append(style)
        triggered: dict[str, str] = {}
        for source_device in self.repository.fetch_devices():
            device = dict(source_device)
            output_group = device.get("output_group")
            if output_group is None:
                continue
            key = (int(device["node"]), int(output_group))
            if key in styles_by_group:
                triggered[str(device["stable_key"])] = ", ".join(
                    styles_by_group[key]
                )
        return triggered

    def _triggered_sounder_zone_styles(
        self,
        activations,
        triggered_devices: dict[str, str],
    ) -> dict[int, str]:
        if not self.repository:
            return {}
        styles_by_zone: dict[int, list[str]] = defaultdict(list)
        styles_by_group: dict[tuple[int, int], list[str]] = defaultdict(list)
        for activation in activations:
            key = (
                int(activation["target_node"]),
                int(activation["output_group"]),
            )
            style = str(
                activation["ringing_style"] or "Triggered"
            ).strip()
            if style and style not in styles_by_group[key]:
                styles_by_group[key].append(style)
        for source_device in self.repository.fetch_devices():
            device = dict(source_device)
            stable_key = str(device["stable_key"])
            if (
                stable_key not in triggered_devices
                or _device_symbol(device) not in {"Sounder", "Beacon"}
            ):
                continue
            zone = int(device["zone"])
            for style in triggered_devices[stable_key].split(","):
                style = style.strip()
                if style and style not in styles_by_zone[zone]:
                    styles_by_zone[zone].append(style)
        fetch_assignments = getattr(
            self.repository,
            "fetch_output_group_zone_assignments",
            None,
        )
        if fetch_assignments is not None:
            for assignment in fetch_assignments():
                key = (
                    int(assignment["node"]),
                    int(assignment["output_group"]),
                )
                zone = int(assignment["zone"])
                for style in styles_by_group.get(key, []):
                    if style not in styles_by_zone[zone]:
                        styles_by_zone[zone].append(style)
        return {
            zone: ", ".join(styles)
            for zone, styles in styles_by_zone.items()
        }

    def simulate(self) -> None:
        if not self.repository or self.zone_combo.currentData() is None:
            return
        trigger_zone = normalise_zone_key(self.zone_combo.currentData())
        scope_node = self.scope_combo.currentData()
        effects = (
            evaluate_zone(self.repository, int(trigger_zone))
            if trigger_zone.isdigit()
            else []
        )
        effect_map = {effect.zone: effect.state for effect in effects}
        activations = list(
            self.repository.fetch_cause_effect_activations(
                trigger_zone,
                scope_node,
            )
        )
        triggered_devices = self._triggered_device_styles(activations)
        sounder_zone_styles = self._triggered_sounder_zone_styles(
            activations,
            triggered_devices
        )
        visible_zones = set(effect_map)
        visible_zones.update(sounder_zone_styles)
        if trigger_zone.isdigit():
            visible_zones.add(int(trigger_zone))
        if trigger_zone.isdigit():
            self._select_floor_for_zone(int(trigger_zone))
        self._current_effect_map = effect_map
        self._current_visible_zones = visible_zones
        self._current_triggered_devices = triggered_devices
        self._current_sounder_zone_styles = sounder_zone_styles
        self._draw_map(
            effect_map,
            visible_zones,
            triggered_devices,
            sounder_zone_styles,
        )
        self.results.setSortingEnabled(False)
        self.results.setRowCount(0)
        for effect in effects:
            self._append_result([effect.state, effect.zone, "", "", "", "", effect.reason], None)
        for activation in activations:
            self._append_result(
                [
                    activation["ringing_style"],
                    activation["trigger_zone"],
                    activation["target_node"],
                    "",
                    "",
                    "",
                    (
                        f"Output group {activation['output_group']} - "
                        f"{activation['output_group_name']}"
                    ),
                ],
                f"activation/{activation['id']}",
                activation["comments"],
            )
        for zone, style in sorted(sounder_zone_styles.items()):
            self._append_result(
                [
                    f"SOUNDER {style}",
                    zone,
                    "",
                    "",
                    "",
                    "",
                    f"Activated sounder output assigned to zone {zone}",
                ],
                f"sounder-zone/{zone}",
            )
        device_rows = {
            str(row["stable_key"]): row
            for row in self.repository.fetch_devices()
        }
        for door_row in _fetch_doors(self.repository):
            door = dict(door_row)
            if not any(
                int(zone) in effect_map
                for zone in (door["zone_a"], door["zone_b"])
            ):
                continue
            for capability, expected, device_key, description in (
                (
                    "access",
                    "UNLOCKED",
                    door["access_device_key"],
                    "Access release — door must unlock",
                ),
                (
                    "hold-open",
                    "CLOSED",
                    door["hold_open_device_key"],
                    "Hold-open release — door must close",
                ),
            ):
                enabled = (
                    door["has_access_control"]
                    if capability == "access"
                    else door["has_hold_open"]
                )
                if not enabled:
                    continue
                device = device_rows.get(str(device_key))
                if (
                    scope_node is not None
                    and device is not None
                    and int(device["node"]) != int(scope_node)
                ):
                    continue
                self._append_result(
                    [
                        expected,
                        _door_zone_text(door),
                        device["node"] if device else "",
                        device["loop"] if device else "",
                        device["address"] if device else "",
                        device["sub_address"] if device else "",
                        f"{door['name']} — {description}",
                    ],
                    f"door/{door['id']}/{capability}",
                )
        for device in self.repository.fetch_devices():
            if device["zone"] not in effect_map:
                continue
            if scope_node is not None and device["node"] != scope_node:
                continue
            self._append_result(
                [
                    effect_map[device["zone"]],
                    device["zone"],
                    device["node"],
                    device["loop"],
                    device["address"],
                    device["sub_address"],
                    device["text"] or catalogue_display_name(
                        device["product_code"], device["observed_type"]
                    ),
                ],
                device["stable_key"],
            )
        self.results.setSortingEnabled(True)
        self.results.apply_filters()

    def _append_result(
        self,
        values: list[object],
        stable_key: str | None,
        comments: str = "",
    ) -> None:
        row = self.results.rowCount()
        self.results.insertRow(row)
        for column, value in enumerate(values + ["", comments]):
            item = _item(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, stable_key)
                if str(value) in {"EVACUATE", "E", "TE"}:
                    item.setBackground(QColor("#f8d7da"))
                elif str(value) in {"ALERT", "A", "TA"}:
                    item.setBackground(QColor("#fff3cd"))
                elif str(value) in {"UNLOCKED", "CLOSED"}:
                    item.setBackground(QColor("#d1e7dd"))
            self.results.setItem(row, column, item)

    def record_test(self) -> None:
        if not self.repository or self.zone_combo.currentData() is None or self.results.rowCount() == 0:
            QMessageBox.information(self, "Nothing to record", "Run a fire trigger simulation first.")
            return
        results: list[tuple[str | None, str, str, str]] = []
        for row in range(self.results.rowCount()):
            expected_item = self.results.item(row, 0)
            stable_key = expected_item.data(Qt.ItemDataRole.UserRole)
            # Summary effect rows have no stable device key and are retained as zone observations.
            if stable_key is None:
                stable_key = f"zone/{self.results.item(row, 1).text()}"
            result = self.results.item(row, 7).text()
            comments = self.results.item(row, 8).text()
            results.append((stable_key, expected_item.text(), result, comments))
        session_id = self.repository.create_test_session(
            self.engineer.text().strip(),
            self.scope_combo.currentData(),
            normalise_zone_key(self.zone_combo.currentData()),
            results,
        )
        QMessageBox.information(
            self,
            "Test recorded",
            f"Commissioning test session {session_id} was saved with {len(results)} results/comments.",
        )

    def _draw_map(
        self,
        effect_map: dict[int, str],
        visible_zones: set[int] | None = None,
        triggered_devices: dict[str, str] | None = None,
        sounder_zone_styles: dict[int, str] | None = None,
    ) -> None:
        triggered_devices = triggered_devices or {}
        sounder_zone_styles = sounder_zone_styles or {}
        had_scene = bool(self.scene.items())
        transform = self.map.transform()
        centre = self.map.mapToScene(
            self.map.viewport().rect().center()
        )
        self.map.cancel_scene_interaction()
        self.scene.clear()
        self.zone_popup = None
        if not self.repository:
            return
        physical_devices = _physical_device_payloads(
            self.repository.fetch_devices()
        )
        device_rows = {
            member_key: payload
            for payload in physical_devices
            for member_key in payload["member_keys"]
        }
        panel_rows = {
            str(row["node"]): dict(row) for row in self.repository.fetch_panels()
        }
        floor_id = (
            int(self.floor_combo.currentData())
            if self.floor_combo.currentData() is not None
            else None
        )
        fetch_floors = getattr(self.repository, "fetch_floors", None)
        floors = list(fetch_floors()) if fetch_floors else []
        floor = next(
            (
                row
                for row in floors
                if floor_id is not None
                and int(row["id"]) == floor_id
            ),
            None,
        )
        if (
            floor is not None
            and floor["dxf_path"]
            and Path(floor["dxf_path"]).exists()
        ):
            for entity in read_linework(floor["dxf_path"]):
                path = QPainterPath()
                path.moveTo(entity.points[0][0], -entity.points[0][1])
                for x, y in entity.points[1:]:
                    path.lineTo(x, -y)
                if entity.closed:
                    path.closeSubpath()
                brush = (
                    QBrush(QColor("#64748b"))
                    if entity.entity_type in {"SOLID", "TRACE", "3DFACE"}
                    else QBrush(Qt.BrushStyle.NoBrush)
                )
                item = self.scene.addPath(
                    path,
                    QPen(QColor("#94a3b8"), 0),
                    brush,
                )
                item.setZValue(-30)
                item.setToolTip(f"DXF layer: {entity.layer}")
                item.setData(20, entity.layer)
            for text in read_text(floor["dxf_path"]):
                font, font_scale, resolved_family = _dxf_text_font(text)
                item = self.scene.addSimpleText(text.text)
                item.setFont(font)
                item.setBrush(QBrush(QColor("#52657a")))
                item.setPos(text.x, -text.y)
                item.setRotation(-text.rotation)
                item.setScale(font_scale)
                item.setZValue(-29)
                item.setData(20, text.layer)
                item.setToolTip(
                    f"DXF text layer: {text.layer}\n"
                    f"Font: {resolved_family}\n"
                    f"Rotation: {text.rotation:g}\u00b0"
                )
        for row in self.repository.fetch_zone_geometry():
            row_floor_id = dict(row).get("floor_id")
            if (
                floor_id is not None
                and row_floor_id is not None
                and int(row_floor_id) != floor_id
            ):
                continue
            if (
                visible_zones is not None
                and int(row["zone"]) not in visible_zones
            ):
                continue
            zone = int(row["zone"])
            state = effect_map.get(zone, "NORMAL")
            sounder_style = sounder_zone_styles.get(zone)
            display_state = (
                _sounder_style_zone_state(sounder_style)
                or state
            )
            colour = QColor("#6fca8c")
            if display_state == "EVACUATE":
                colour = QColor("#dc3545")
            elif display_state == "ALERT":
                colour = QColor("#ffc107")
            points = json.loads(row["geometry_json"])
            polygon = QPolygonF([QPointF(x, -y) for x, y in points])
            outline = QPen(
                QColor("#7c3aed") if sounder_style else QColor("#334155"),
                4 if sounder_style else 0,
            )
            outline.setCosmetic(True)
            item = self.scene.addPolygon(polygon, outline, QBrush(colour))
            item.setOpacity(0.75)
            description = str(row["description"] or "").strip()
            item.setData(
                60,
                {
                    "zone": zone,
                    "name": description,
                    "status": state,
                    "sounder_status": sounder_style,
                },
            )
            tooltip = f"Zone {zone} — {state}"
            if sounder_style:
                tooltip += f"\nSounders triggered: {sounder_style}"
            item.setToolTip(tooltip)
        for door_row in _fetch_doors(self.repository, floor_id):
            door = dict(door_row)
            door_zones = {int(door["zone_a"]), int(door["zone_b"])}
            fire_active = any(zone in effect_map for zone in door_zones)
            _add_door_graphics(
                self.scene,
                door,
                fire_active,
                activated_device_keys=set(triggered_devices),
            )
        rendered_devices: set[str] = set()
        for placement in _fetch_map_assets(self.repository, floor_id):
            if placement["entity_kind"] == "device":
                device = device_rows.get(str(placement["entity_key"]))
                if not device:
                    continue
                canonical_key = str(device["key"])
                if canonical_key in rendered_devices:
                    continue
                rendered_devices.add(canonical_key)
                symbol = device["symbol"]
                states = {
                    effect_map.get(zone, "NORMAL")
                    for zone in device["zones"]
                }
                state = (
                    "EVACUATE"
                    if "EVACUATE" in states
                    else ("ALERT" if "ALERT" in states else "NORMAL")
                )
                triggered_styles = [
                    triggered_devices[member_key]
                    for member_key in device["member_keys"]
                    if member_key in triggered_devices
                ]
                fill_colour = QColor("#ffffff")
                if state == "EVACUATE":
                    fill_colour = QColor("#f8d7da")
                elif state == "ALERT":
                    fill_colour = QColor("#fff3cd")
                if triggered_styles:
                    fill_colour = QColor("#f87171")
                name = device["name"]
                tooltip = (
                    f"{symbol}: {name}\n"
                    f"Node {device['node']} · Zones "
                    f"{', '.join(map(str, device['zones']))} · "
                    f"Loop {device['loop']} · Address {device['address']} · "
                    f"Sub-addresses {', '.join(map(str, device['sub_addresses']))}"
                )
                if triggered_styles:
                    tooltip += (
                        "\nTriggered output: "
                        + ", ".join(dict.fromkeys(triggered_styles))
                    )
                payload = device
            else:
                panel = panel_rows.get(placement["entity_key"])
                if not panel:
                    continue
                fill_colour = QColor("#ffffff")
                tooltip = f"Panel: {panel['name']}\nNode: {panel['node']}"
                payload = {
                    "kind": "panel",
                    "key": str(panel["node"]),
                    "symbol": "Panel",
                    "name": panel["name"],
                    **panel,
                }
            marker = _add_fire_alarm_symbol(
                self.scene,
                payload,
                float(placement["x"]),
                float(placement["y"]),
                tooltip,
                fill_colour=fill_colour,
            )
            if placement["entity_kind"] == "device":
                status = (
                    "TRIGGERED — "
                    + ", ".join(dict.fromkeys(triggered_styles))
                    if triggered_styles
                    else state
                )
                marker.setData(
                    13,
                    {
                        "name": name,
                        "node": int(device["node"]),
                        "zones": tuple(device["zones"]),
                        "loop": int(device["loop"]),
                        "address": int(device["address"]),
                        "status": status,
                        "channels": tuple(
                            device.get("channel_details", ())
                        ),
                    },
                )
                if triggered_styles:
                    marker.setData(
                        12,
                        ", ".join(dict.fromkeys(triggered_styles)),
                    )
                    marker.setZValue(30)
        if self.scene.itemsBoundingRect().isValid():
            bounds = self.scene.itemsBoundingRect()
            if had_scene:
                self.map.setTransform(transform)
                inverse, invertible = transform.inverted()
                if invertible:
                    visible = inverse.mapRect(
                        QRectF(self.map.viewport().rect())
                    )
                    required = QRectF(
                        centre.x() - visible.width() / 2.0,
                        centre.y() - visible.height() / 2.0,
                        visible.width(),
                        visible.height(),
                    )
                    required = required.adjusted(
                        -visible.width(),
                        -visible.height(),
                        visible.width(),
                        visible.height(),
                    )
                    self.scene.setSceneRect(bounds.united(required))
                self.map.centerOn(centre)
            else:
                self.map.fitInView(
                    bounds,
                    Qt.AspectRatioMode.KeepAspectRatio,
                )

    def show_zone_popup_at(self, scene_point: QPointF) -> None:
        device_item = None
        for item in self.scene.items(scene_point):
            root = self.map._asset_root_item(item)
            if root is not None and root.data(13) is not None:
                device_item = root
                break
        if device_item is not None:
            details = device_item.data(13)
            self._show_test_map_popup(
                _device_popup_content(
                    name=details["name"],
                    node=int(details["node"]),
                    zones=details["zones"],
                    loop=int(details["loop"]),
                    address=int(details["address"]),
                    status=details["status"],
                    channels=details.get("channels", ()),
                ),
                device_item,
            )
            return
        zone_item = next(
            (
                item
                for item in self.scene.items(scene_point)
                if item.data(60) is not None
            ),
            None,
        )
        if zone_item is None:
            return
        details = zone_item.data(60)
        name = str(details["name"] or "Unnamed zone")
        sounder_status = details.get("sounder_status")
        sounder_text = (
            f"\nSounders triggered: {sounder_status}"
            if sounder_status
            else ""
        )
        self._show_test_map_popup(
            (
                f"Zone {details['zone']}\n{name}\n"
                f"Status: {details['status']}{sounder_text}"
            ),
            zone_item,
        )

    def _show_test_map_popup(
        self,
        content: str,
        anchor_item: QGraphicsItem,
    ) -> None:
        self.zone_popup = _add_map_popup(
            self.scene,
            self.zone_popup,
            content,
            anchor_item,
            self.map,
        )


class ChangesPage(Page):
    def __init__(self):
        super().__init__("Project tracked changes")
        self.tabs = QTabWidget()
        self.table = FilterableTableWidget(0, 13)
        self.table.setHorizontalHeaderLabels(
            [
                "Type", "Change", "Node", "Zone", "Loop", "Address", "Sub",
                "Device text", "Device type", "Output group", "Field",
                "Previous", "Current",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(1, 210)
        self.table.setColumnWidth(7, 300)
        self.table.setColumnWidth(8, 170)
        self.table.setColumnWidth(9, 240)
        self.table.setColumnWidth(11, 240)
        self.table.setColumnWidth(12, 240)
        self.tabs.addTab(self.table, "Configuration changes")
        self.history_table = FilterableTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(
            ["Date/time", "Type", "Area", "Record", "Fields", "Description"]
        )
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setColumnWidth(0, 190)
        self.history_table.setColumnWidth(2, 220)
        self.history_table.setColumnWidth(3, 250)
        self.history_table.setColumnWidth(5, 520)
        self.tabs.addTab(self.history_table, "Project history")
        self.layout.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.history_table.setSortingEnabled(False)
        self.history_table.setRowCount(0)
        if not self.repository:
            return
        for change in self.repository.fetch_change_details():
            row = self.table.rowCount()
            self.table.insertRow(row)
            output_group = ""
            if change["output_group"] is not None:
                output_group = str(change["output_group"])
                if str(change["output_group_name"] or "").strip():
                    output_group += f" — {change['output_group_name']}"
                if str(change["ringing_style"] or "").strip():
                    output_group += f" ({change['ringing_style']})"
            values = [
                change["change_type"],
                change["description"],
                change["node"],
                change["zone"],
                change["loop"],
                change["address"],
                change["sub_address"],
                change["device_text"],
                change["device_type"],
                output_group,
                change["field"],
                change["old_value"],
                change["new_value"],
            ]
            for column, value in enumerate(values):
                item = _item(value)
                if column == 0:
                    colours = {"added": "#d1e7dd", "removed": "#f8d7da", "modified": "#fff3cd"}
                    if change["change_type"] in colours:
                        item.setBackground(QColor(colours[change["change_type"]]))
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.apply_filters()
        for change in self.repository.fetch_project_history():
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            values = [
                change["changed_at"],
                change["change_type"],
                change["area"],
                change["record_key"],
                change["fields"],
                change["summary"],
            ]
            for column, value in enumerate(values):
                item = _item(value)
                if column == 1:
                    colours = {
                        "added": "#d1e7dd",
                        "removed": "#f8d7da",
                        "modified": "#fff3cd",
                    }
                    if value in colours:
                        item.setBackground(QColor(colours[value]))
                self.history_table.setItem(row, column, item)
        self.history_table.setSortingEnabled(True)
        self.history_table.apply_filters()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.repository: ProjectRepository | None = None
        self.setWindowTitle("FirePanel Commissioning")
        self.resize(1500, 930)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet(APP_STYLESHEET)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_ribbon())
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(225)
        self.navigation.addItems(
            [
                "Overview", "Devices", "Nodes", "Zones", "Output groups",
                "Zone drawings", "Cause & effect", "Test mode", "Tracked changes",
                "About",
            ]
        )
        self.pages = [
            DashboardPage(),
            DevicesPage(),
            NodesPage(),
            ZonesPage(),
            OutputGroupsPage(),
            ZonesMapPage(),
            MatrixPage(),
            TestPage(),
            ChangesPage(),
            AboutPage(),
        ]
        matrix_page = next(
            page for page in self.pages if isinstance(page, MatrixPage)
        )
        changes_page = next(
            page for page in self.pages if isinstance(page, ChangesPage)
        )
        matrix_page.matrix_imported.connect(self.pages[0].refresh)
        matrix_page.matrix_imported.connect(changes_page.refresh)
        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        content.addWidget(self.navigation)
        content.addWidget(self.stack, 1)
        root.addLayout(content, 1)
        self.setCentralWidget(central)
        self._build_menu()

    def _build_ribbon(self) -> QTabWidget:
        ribbon = QTabWidget()
        ribbon.setObjectName("ribbon")
        ribbon.setFixedHeight(128)
        project_tab = QWidget()
        project_layout = QHBoxLayout(project_tab)
        project_layout.setContentsMargins(8, 4, 8, 4)
        for text, icon, callback in (
            ("New", "fa6s.file-circle-plus", self.new_project),
            ("Open", "fa6s.folder-open", self.open_project),
            ("Save", "fa6s.floppy-disk", self.save_project),
            ("Save as", "fa6s.file-export", self.save_as),
            ("Close", "fa6s.xmark", self.close_project),
        ):
            project_layout.addWidget(self._ribbon_button(text, icon, callback))
        project_layout.addSpacing(18)
        for text, icon, callback in (
            ("Update configuration", "fa6s.arrows-rotate", self.update_ncf),
            ("Import DXF", "fa6s.file-import", lambda: self._navigate(5)),
            ("Export Excel", "fa6s.file-excel", self.export_excel),
            ("Changes PDF", "fa6s.file-pdf", self.export_pdf),
        ):
            project_layout.addWidget(self._ribbon_button(text, icon, callback))
        project_layout.addStretch()
        commission_tab = QWidget()
        commission_layout = QHBoxLayout(commission_tab)
        for text, icon, index in (
            ("Devices", "fa6s.microchip", 1),
            ("Nodes", "fa6s.network-wired", 2),
            ("Zones", "fa6s.layer-group", 3),
            ("Output groups", "fa6s.bolt", 4),
            ("Map zones", "fa6s.draw-polygon", 5),
            ("Matrix", "fa6s.table-cells", 6),
            ("Test mode", "fa6s.fire", 7),
            ("Changes", "fa6s.code-compare", 8),
            ("About", "fa6s.circle-info", 9),
        ):
            commission_layout.addWidget(self._ribbon_button(text, icon, lambda checked=False, i=index: self._navigate(i)))
        commission_layout.addStretch()
        ribbon.addTab(project_tab, "Project")
        ribbon.addTab(commission_tab, "Commission")
        return ribbon

    @staticmethod
    def _ribbon_button(text: str, icon: str, callback) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setProperty("iconName", icon)
        try:
            button.setIcon(qta.icon(icon, color="#183153"))
        except Exception:
            button.setIcon(
                qta.icon("fa6s.circle-question", color="#183153")
            )
        button.setIconSize(button.iconSize() * 1.7)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.clicked.connect(callback)
        return button

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.setMinimumWidth(300)
        actions = [
            ("New project", "Ctrl+N", self.new_project),
            ("Open…", "Ctrl+O", self.open_project),
            ("Save", "Ctrl+S", self.save_project),
            ("Save as…", "Ctrl+Shift+S", self.save_as),
            ("Close project", "Ctrl+W", self.close_project),
        ]
        for text, shortcut, callback in actions:
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            file_menu.addAction(action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        export_menu = self.menuBar().addMenu("&Export")
        export_menu.addSection("Commissioning data")
        device_action = QAction("Device schedule (Excel)…", self)
        device_action.triggered.connect(self.export_excel)
        export_menu.addAction(device_action)

        export_menu.addSection("Cause & Effect")
        comparison_action = QAction("Comparison workbook…", self)
        comparison_action.triggered.connect(
            self.export_cause_effect_workbook
        )
        export_menu.addAction(comparison_action)

        export_menu.addSection("Reports")
        changes_action = QAction("Tracked changes (PDF)…", self)
        changes_action.triggered.connect(self.export_pdf)
        export_menu.addAction(changes_action)

        export_menu.addSection("Testing")
        test_export_action = QAction("Output-group test workbook…", self)
        test_export_action.triggered.connect(self.export_testing_workbook)
        export_menu.addAction(test_export_action)
        test_import_action = QAction("Import completed test workbook…", self)
        test_import_action.triggered.connect(self.import_testing_workbook)
        export_menu.addAction(test_import_action)

    def _navigate(self, index: int) -> None:
        self.navigation.setCurrentRow(index)

    def _set_repository(self, repository: ProjectRepository | None) -> None:
        self.repository = repository
        for page in self.pages:
            page.set_repository(repository)
        if repository:
            self.setWindowTitle(f"{repository.name} — FirePanel Commissioning")
            self.statusBar().showMessage(f"Opened {repository.path}")
        else:
            self.setWindowTitle("FirePanel Commissioning")
            self.statusBar().showMessage("No project open")

    def new_project(self) -> None:
        ncf_path, _ = QFileDialog.getOpenFileName(
            self, "Select initial configuration", "", CONFIGURATION_FILTER
        )
        if not ncf_path:
            return
        name, ok = QInputDialog.getText(self, "Project name", "Name", text=Path(ncf_path).stem)
        if not ok or not name:
            return
        project_path, _ = QFileDialog.getSaveFileName(
            self, "Create project", f"{name}.fcp", "FirePanel project (*.fcp)"
        )
        if not project_path:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            repository = ProjectRepository.create(project_path, name, ncf_path)
            self._set_repository(repository)
        except Exception as error:
            QMessageBox.critical(self, "Project creation failed", str(error))
        finally:
            QApplication.restoreOverrideCursor()

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "FirePanel project (*.fcp)")
        if not path:
            return
        try:
            self._set_repository(ProjectRepository(path))
        except Exception as error:
            QMessageBox.critical(self, "Could not open project", str(error))

    def save_project(self) -> None:
        if self.repository:
            self.statusBar().showMessage("Project saved", 3000)

    def save_as(self) -> None:
        if not self.repository:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save project as", self.repository.path.name, "FirePanel project (*.fcp)")
        if not path:
            return
        try:
            self._set_repository(self.repository.save_as(path))
        except Exception as error:
            QMessageBox.critical(self, "Save as failed", str(error))

    def close_project(self) -> None:
        self._set_repository(None)

    def update_ncf(self) -> None:
        if not self.repository:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import updated configuration", "", CONFIGURATION_FILTER
        )
        if not path:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            _, changes = self.repository.import_configuration(path)
            self._set_repository(self.repository)
            self._navigate(8)
            QMessageBox.information(
                self,
                "Configuration update complete",
                f"Recorded {len(changes)} changes.",
            )
        except Exception as error:
            QMessageBox.critical(self, "Configuration update failed", str(error))
        finally:
            QApplication.restoreOverrideCursor()

    def export_excel(self) -> None:
        if not self.repository:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export devices", "commissioning-devices.xlsx", "Excel (*.xlsx)")
        if path:
            try:
                export_devices_xlsx(self.repository, path)
                self.statusBar().showMessage(f"Exported {path}", 5000)
            except Exception as error:
                QMessageBox.critical(self, "Export failed", str(error))

    def export_cause_effect_workbook(self) -> None:
        if not self.repository:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Cause & Effect comparison",
            "cause-effect-comparison.xlsx",
            "Excel (*.xlsx)",
        )
        if not path:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            export_cause_effect_comparison_xlsx(self.repository, path)
            self.statusBar().showMessage(f"Exported {path}", 5000)
        except Exception as error:
            QMessageBox.critical(self, "Export failed", str(error))
        finally:
            QApplication.restoreOverrideCursor()

    def export_testing_workbook(self) -> None:
        if not self.repository:
            return
        dialog = ZoneTestExportDialog(self.repository, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export output-group test workbook",
            "output-group-testing.xlsx",
            "Excel (*.xlsx)",
        )
        if not path:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            export_testing_workbook(
                self.repository,
                path,
                dialog.selected_zones(),
            )
            self.statusBar().showMessage(f"Exported {path}", 5000)
        except Exception as error:
            QMessageBox.critical(self, "Export failed", str(error))
        finally:
            QApplication.restoreOverrideCursor()

    def import_testing_workbook(self) -> None:
        if not self.repository:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import completed output-group test workbook",
            "",
            "Excel (*.xlsx)",
        )
        if not path:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            sessions = read_testing_workbook(path)
            session_count, result_count = self.repository.import_test_sessions(
                sessions
            )
            QMessageBox.information(
                self,
                "Test results imported",
                f"Imported {session_count:,} zone test sessions and "
                f"{result_count:,} output-group results.",
            )
            self.statusBar().showMessage(f"Imported test results from {path}", 5000)
        except Exception as error:
            QMessageBox.critical(self, "Import failed", str(error))
        finally:
            QApplication.restoreOverrideCursor()

    def export_pdf(self) -> None:
        if not self.repository:
            return
        revision_dialog = ChangeRevisionDialog(
            self.repository, self
        )
        if revision_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        revision_ids = revision_dialog.selected_revision_ids()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export tracked changes",
            "project-tracked-changes.pdf",
            "PDF (*.pdf)",
        )
        if path:
            try:
                export_change_pdf(
                    self.repository,
                    path,
                    revision_ids=revision_ids,
                )
                self.statusBar().showMessage(f"Exported {path}", 5000)
            except Exception as error:
                QMessageBox.critical(self, "Export failed", str(error))
