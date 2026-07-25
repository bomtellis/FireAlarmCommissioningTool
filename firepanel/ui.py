from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QAction, QColor, QBrush, QCursor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
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

from .cause_effect import normalise_zone_key
from .device_catalog import catalogue_display_name, device_current_ma
from .dxf import DxfShape, read_closed_shapes, read_layers, read_linework, read_text
from .exports import (
    export_cause_effect_comparison_xlsx,
    export_change_pdf,
    export_devices_xlsx,
)
from .project import ProjectRepository
from .rules import evaluate_zone, generate_htm_rules
from .styles import APP_STYLESHEET
from .testing_workbook import export_testing_workbook, read_testing_workbook


CONFIGURATION_FILTER = "Network configurations (*.ncf *.NCF *.skf *.SKF)"


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


def _device_symbol(row: dict) -> str:
    text = " ".join(
        str(row.get(key) or "") for key in ("observed_type", "text", "panel")
    ).casefold()
    if "call point" in text or "mcp" in text:
        return "Call point"
    if any(word in text for word in ("power supply", "psu", "mains unit")):
        return "Power supply"
    if row.get("output_group") is not None or any(
        word in text for word in ("relay", "output", "door holder", "interface")
    ):
        return "Output device"
    if any(word in text for word in ("sounder", "beacon", "vad", "vid")):
        return "Sounder"
    if any(word in text for word in ("smoke", "heat", "detector", "sensor", "multi")):
        return "Detector"
    return "Device"


SYMBOL_COLOURS = {
    "Detector": QColor("#0d6efd"),
    "Call point": QColor("#dc3545"),
    "Sounder": QColor("#fd7e14"),
    "Output device": QColor("#6f42c1"),
    "Power supply": QColor("#198754"),
    "Panel": QColor("#183153"),
    "Device": QColor("#64748b"),
}


class MapGraphicsView(QGraphicsView):
    scene_clicked = Signal(QPointF)
    scene_right_clicked = Signal(object, QPointF)
    scene_double_clicked = Signal(QPointF)
    item_moved = Signal(object)
    drawing_cancelled = Signal()
    drawing_undo_requested = Signal()
    drawing_finish_requested = Signal()
    polygon_selection_requested = Signal(QPointF)

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
        self._moving_item = None
        self.draw_mode = False
        self.edit_geometry_mode = False

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
        if event.button() == Qt.MouseButton.RightButton:
            self.scene_right_clicked.emit(
                self.itemAt(point),
                self.mapToScene(point),
            )
            event.accept()
            return
        if event.button() in {
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.MiddleButton,
        }:
            pressed_item = self.itemAt(point)
            if (
                event.button() == Qt.MouseButton.LeftButton
                and self.edit_geometry_mode
                and pressed_item is not None
                and pressed_item.data(30) is not None
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
            self._pan_button is not None
            and self._pan_last is not None
            and not self.draw_mode
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
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - delta.x()
                )
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - delta.y()
                )
                self._pan_last = point
                event.accept()
                return
        super().mouseMoveEvent(event)

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
            super().mouseReleaseEvent(event)
            self.item_moved.emit(moved_item)
            return
        if event.button() == self._pan_button:
            point = event.position().toPoint()
            was_dragged = self._pan_dragged
            pressed_item = self._press_item
            self._pan_button = None
            self._pan_start = None
            self._pan_last = None
            self._pan_dragged = False
            self._press_item = None
            self.unsetCursor()
            if was_dragged:
                event.accept()
                return
            if event.button() == Qt.MouseButton.MiddleButton:
                event.accept()
                return
            if pressed_item is None or pressed_item.data(10) is None:
                self.scene_clicked.emit(self.mapToScene(point))
            polygon_click = (
                pressed_item is not None
                and pressed_item.data(10) is None
            )
            super().mouseReleaseEvent(event)
            if polygon_click:
                self.polygon_selection_requested.emit(
                    self.mapToScene(point)
                )
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
        self.table = FilterableTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ["Zone number", "Zone description", "Devices"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setColumnWidth(1, 520)
        self.layout.addWidget(self.table, 1)

    def refresh(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if self.repository:
            for zone in self.repository.fetch_zones():
                row_index = self.table.rowCount()
                self.table.insertRow(row_index)
                for column, value in enumerate(
                    [zone["number"], zone["description"], zone["device_count"]]
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


class OutputGroupsPage(Page):
    def __init__(self):
        super().__init__("Output groups")
        note = QLabel(
            "Output groups are shown per panel node. Double-click a group to see its "
            "associated output points and configured ringing styles."
        )
        note.setWordWrap(True)
        self.layout.addWidget(note)
        self.table = FilterableTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Node", "Panel", "Output group", "Group name", "Devices", "Ringing styles"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(3, 280)
        self.table.setColumnWidth(5, 300)
        self.table.doubleClicked.connect(self.open_group)
        self.layout.addWidget(self.table, 1)

    def refresh(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if not self.repository:
            return
        for row in self.repository.fetch_output_groups():
            row_index = self.table.rowCount()
            self.table.insertRow(row_index)
            values = [
                row["node"],
                row["panel"],
                row["output_group"],
                row["group_name"],
                row["device_count"],
                row["ringing_styles"] or "Not specified",
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


class ZonesMapPage(Page):
    geometry_changed = Signal()

    def __init__(self):
        super().__init__("Site drawings and zones")
        controls = QHBoxLayout()
        self.floor_combo = QComboBox()
        self.floor_combo.currentIndexChanged.connect(self.floor_changed)
        import_button = QPushButton("Import floor DXF")
        import_button.clicked.connect(self.import_dxf)
        replace_dxf_button = QPushButton("Replace DXF")
        replace_dxf_button.setProperty("secondary", True)
        replace_dxf_button.clicked.connect(self.replace_dxf)
        remove_dxf_button = QPushButton("Remove DXF")
        remove_dxf_button.setProperty("secondary", True)
        remove_dxf_button.clicked.connect(self.remove_dxf)
        self.draw_polygon_button = QPushButton("Draw zone polyline")
        self.draw_polygon_button.setCheckable(True)
        self.draw_polygon_button.setProperty("secondary", True)
        self.draw_polygon_button.toggled.connect(self.set_draw_mode)
        self.finish_polygon_button = QPushButton("Finish polygon")
        self.finish_polygon_button.setProperty("secondary", True)
        self.finish_polygon_button.setEnabled(False)
        self.finish_polygon_button.clicked.connect(
            lambda: self.finish_drawn_polygon()
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
        controls.addWidget(import_button)
        controls.addWidget(replace_dxf_button)
        controls.addWidget(remove_dxf_button)
        controls.addWidget(self.draw_polygon_button)
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
            "right-click a polygon to assign, reassign, rotate, realign or "
            "unassign it. In draw mode, click each corner and double-click to finish."
        )
        navigation_hint.setWordWrap(True)
        self.layout.addWidget(navigation_hint)
        splitter = QSplitter()
        self.scene = QGraphicsScene()
        self.view = MapGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.scene_clicked.connect(self.place_selected)
        self.view.scene_right_clicked.connect(self.assign_shape_from_context)
        self.view.scene_double_clicked.connect(self.finish_drawn_polygon)
        self.view.item_moved.connect(self.geometry_item_moved)
        self.view.drawing_cancelled.connect(self.cancel_drawing)
        self.view.drawing_undo_requested.connect(self.undo_draw_point)
        self.view.drawing_finish_requested.connect(
            lambda: self.finish_drawn_polygon()
        )
        self.view.polygon_selection_requested.connect(
            self.select_polygon_at_point
        )
        self.scene.selectionChanged.connect(self.show_selection_details)

        side_tabs = QTabWidget()
        self.zone_table = FilterableTableWidget(0, 4)
        self.zone_table.setHorizontalHeaderLabels(["Zone", "Description", "Floor", "Devices"])
        side_tabs.addTab(self.zone_table, "Zones")

        layers_page = QWidget()
        layers_layout = QVBoxLayout(layers_page)
        layers_layout.setContentsMargins(6, 6, 6, 6)
        layers_hint = QLabel(
            "Toggle DXF geometry and text layers. Visibility is retained while "
            "this project is open."
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
        self.asset_search = QLineEdit()
        self.asset_search.setPlaceholderText("Filter node, zone, address or device name…")
        self.asset_search.textChanged.connect(self.refresh_asset_list)
        self.asset_category = QComboBox()
        self.asset_category.addItems(
            ["All", "Detector", "Call point", "Sounder", "Output device", "Power supply", "Panel", "Device"]
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
        self.layer_visibility: dict[int, dict[str, bool]] = {}
        self.drawing_points: list[QPointF] = []
        self.drawing_preview = None
        self.drawing_markers: list[QGraphicsItem] = []
        self.asset_rows: list[dict] = []
        self.asset_by_key: dict[tuple[str, str], dict] = {}

    def refresh(self) -> None:
        self.floor_combo.blockSignals(True)
        self.floor_combo.clear()
        self.zone_combo.clear()
        self.zone_table.setSortingEnabled(False)
        self.zone_table.setRowCount(0)
        if self.repository:
            assigned_zones = {
                int(row["zone"])
                for row in self.repository.fetch_zone_geometry()
            }
            for floor in self.repository.fetch_floors():
                self.floor_combo.addItem(floor["name"], floor["id"])
            for zone in self.repository.fetch_zones():
                if int(zone["number"]) not in assigned_zones:
                    self.zone_combo.addItem(
                        _zone_label(zone),
                        zone["number"],
                    )
                row = self.zone_table.rowCount()
                self.zone_table.insertRow(row)
                for column, value in enumerate(
                    [zone["number"], zone["description"], zone["floor_name"], zone["device_count"]]
                ):
                    self.zone_table.setItem(row, column, _item(value))
        self.zone_table.setSortingEnabled(True)
        self.zone_table.apply_filters()
        self.floor_combo.blockSignals(False)
        self._build_asset_rows()
        self.refresh_asset_list()
        self.floor_changed()

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
        assigned = {
            int(row["zone"])
            for row in self.repository.fetch_zone_geometry()
            if included is None or int(row["zone"]) != included
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
                self._refresh_layer_list()
                self.refresh_scene(False)
        self.view.setTransform(transform)
        self.view.centerOn(centre)

    def floor_changed(self) -> None:
        self._refresh_layer_list()
        self.refresh_scene()

    def _refresh_layer_list(self) -> None:
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
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
        if not self.repository:
            return
        for sqlite_row in self.repository.fetch_devices():
            row = dict(sqlite_row)
            symbol = _device_symbol(row)
            name = row["text"] or catalogue_display_name(
                row["product_code"], row["observed_type"]
            )
            payload = {
                "kind": "device",
                "key": row["stable_key"],
                "symbol": symbol,
                "name": name,
                **row,
            }
            self.asset_rows.append(payload)
            self.asset_by_key[("device", row["stable_key"])] = payload
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
            self.asset_by_key[("panel", str(row["node"]))] = payload

    def refresh_asset_list(self) -> None:
        current = None
        if self.asset_list.currentItem():
            current = self.asset_list.currentItem().data(Qt.ItemDataRole.UserRole)
        self.asset_list.clear()
        if not self.repository:
            return
        category = self.asset_category.currentText()
        needle = self.asset_search.text().strip().casefold()
        placed = {
            (row["entity_kind"], row["entity_key"])
            for row in self.repository.fetch_map_assets()
        }
        for payload in self.asset_rows:
            if category != "All" and payload["symbol"] != category:
                continue
            if payload["kind"] == "device":
                identity = (
                    f"Node {payload['node']} · Zone {payload['zone']} · "
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
        self.shape_items.clear()
        self.geometry_items.clear()
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
        if floor and floor["dxf_path"] and Path(floor["dxf_path"]).exists():
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
                item = self.scene.addSimpleText(text.text)
                item.setBrush(QBrush(QColor("#52657a")))
                item.setPos(text.x, -text.y)
                item.setRotation(-text.rotation)
                item.setScale(max(text.height / 12.0, 0.05))
                item.setZValue(1000)
                item.setToolTip(f"DXF text layer: {text.layer}")
                item.setData(20, text.layer)
        assigned = [
            row for row in self.repository.fetch_zone_geometry() if row["floor_id"] == floor_id
        ]
        if int(floor_id) not in self.pending_shapes and floor and floor["dxf_path"]:
            try:
                assigned_points = {
                    tuple(
                        (round(float(x), 5), round(float(y), 5))
                        for x, y in json.loads(row["geometry_json"])
                    )
                    for row in assigned
                }
                self.pending_shapes[int(floor_id)] = [
                    shape
                    for shape in read_closed_shapes(floor["dxf_path"])
                    if tuple(
                        (round(float(x), 5), round(float(y), 5))
                        for x, y in shape.points
                    )
                    not in assigned_points
                ]
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
        for shape in self.pending_shapes.get(floor_id, []):
            if not visibility.get(shape.layer, True):
                continue
            item = self._add_polygon(shape.points, QColor("#dbe3ec"), shape.layer, shape)
            self.shape_items[item] = shape
        for placement in self.repository.fetch_map_assets(int(floor_id)):
            payload = self.asset_by_key.get(
                (placement["entity_kind"], placement["entity_key"])
            )
            if payload:
                self._add_asset_marker(
                    payload,
                    float(placement["x"]),
                    float(placement["y"]),
                )
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
        symbol = payload["symbol"]
        colour = SYMBOL_COLOURS[symbol]
        pen = QPen(QColor("#ffffff"), 1.5)
        brush = QBrush(colour)
        if symbol == "Detector":
            marker = self.scene.addEllipse(-7, -7, 14, 14, pen, brush)
        elif symbol in {"Call point", "Power supply", "Panel"}:
            size = 18 if symbol == "Panel" else 14
            marker = self.scene.addRect(-size / 2, -size / 2, size, size, pen, brush)
        elif symbol == "Sounder":
            marker = self.scene.addPolygon(
                QPolygonF([QPointF(0, -8), QPointF(8, 0), QPointF(0, 8), QPointF(-8, 0)]),
                pen,
                brush,
            )
        elif symbol == "Output device":
            marker = self.scene.addPolygon(
                QPolygonF([QPointF(0, -8), QPointF(8, 7), QPointF(-8, 7)]),
                pen,
                brush,
            )
        else:
            marker = self.scene.addEllipse(-6, -6, 12, 12, pen, brush)
        marker.setPos(x, y)
        marker.setZValue(20)
        marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        marker.setData(10, (payload["kind"], payload["key"]))
        marker.setToolTip(self._asset_detail_text(payload))
        label = self.scene.addSimpleText(
            f"N{payload['node']}" if payload["kind"] == "panel"
            else f"{payload['loop']}/{payload['address']}"
        )
        label.setBrush(QBrush(QColor("#172033")))
        label.setPos(x + 9, y - 9)
        label.setZValue(21)
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        label.setData(10, (payload["kind"], payload["key"]))

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
            f"Node {payload['node']} · Zone {payload['zone']} · "
            f"Loop {payload['loop']} · Address {payload['address']} · "
            f"Sub-address {payload['sub_address']}",
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
        self.repository.place_map_asset(
            payload["kind"],
            payload["key"],
            int(self.floor_combo.currentData()),
            point.x(),
            point.y(),
            payload["symbol"],
        )
        self.refresh_asset_list()
        self.refresh_scene()

    def set_draw_mode(self, enabled: bool) -> None:
        self.view.draw_mode = bool(enabled)
        self.finish_polygon_button.setEnabled(bool(enabled))
        self.undo_polygon_button.setEnabled(bool(enabled))
        self.cancel_polygon_button.setEnabled(bool(enabled))
        if enabled:
            self.move_polygon_button.setChecked(False)
            self.cancel_drawing(leave_mode=True)
            self.view.setCursor(Qt.CursorShape.CrossCursor)
            self.view.setFocus()
        else:
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
        if not leave_mode and self.draw_polygon_button.isChecked():
            self.draw_polygon_button.setChecked(False)

    def finish_drawn_polygon(self, point: QPointF | None = None) -> None:
        if not self.view.draw_mode:
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

    def geometry_item_moved(self, item: QGraphicsItem) -> None:
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
                return

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
        candidates = self._polygons_at(scene_point)
        if len(candidates) > 1:
            item = self._choose_overlapping_polygon(candidates)
            if item is None:
                return
        elif len(candidates) == 1:
            item = candidates[0]
        if item in self.geometry_items:
            self._show_assigned_geometry_menu(item)
            return
        if (
            not self.repository
            or self.floor_combo.currentData() is None
            or item not in self.shape_items
        ):
            return
        shape = self.shape_items[item]
        if (
            QMessageBox.question(
                self,
                "Assign enclosed polyline",
                "Do you want to assign a zone to this enclosed DXF polyline?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
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

    def select_polygon_at_point(self, scene_point: QPointF) -> None:
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
        self.scene.clearSelection()
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
        custom = QPushButton("Add custom door / output rule")
        custom.setProperty("secondary", True)
        custom.clicked.connect(self.add_custom)
        controls.addWidget(import_matrix)
        controls.addWidget(generate)
        controls.addWidget(custom)
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
        self.table.setColumnWidth(5, 180)
        self.table.setColumnWidth(7, 360)
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
                self.table.setItem(row, column, _item(value))
        self.table.setSortingEnabled(True)
        self.table.apply_filters()

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

    def add_custom(self) -> None:
        if not self.repository:
            return
        dialog = RuleDialog(self.repository, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()


class RuleDialog(QDialog):
    def __init__(self, repository: ProjectRepository, parent: QWidget | None = None):
        super().__init__(parent)
        self.repository = repository
        self.setWindowTitle("Custom cause-and-effect rule")
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

    def save(self) -> None:
        self.repository.add_rule(
            self.name.text().strip() or "Custom rule",
            int(self.trigger.currentData()),
            self.relation.currentText(),
            int(self.target.currentData()),
            self.target_node.value() or None,
            self.output_group.value() or None,
            self.action.currentText(),
            self.notes.toPlainText(),
        )
        self.accept()


class ZoneTestExportDialog(QDialog):
    def __init__(self, repository: ProjectRepository, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Select zones for spreadsheet testing")
        self.resize(620, 540)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Select the fire-call trigger zones to include. Every expected "
                "output-group activation for each selected zone will be listed."
            )
        )
        self.zone_search = QLineEdit()
        self.zone_search.setPlaceholderText("Type a zone number or name…")
        self.zone_search.setClearButtonEnabled(True)
        self.zone_search.textChanged.connect(self._filter_zones)
        layout.addWidget(self.zone_search)
        controls = QHBoxLayout()
        select_all = QPushButton("Select all zones")
        select_all.clicked.connect(self._select_all)
        clear = QPushButton("Clear selection")
        clear.setProperty("secondary", True)
        clear.clicked.connect(self._clear_selection)
        controls.addWidget(select_all)
        controls.addWidget(clear)
        controls.addStretch()
        layout.addLayout(controls)

        self.zones = QListWidget()
        self.zones.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        zone_names = {
            normalise_zone_key(row["number"]): str(
                row["description"] or ""
            ).strip()
            for row in repository.fetch_zones()
        }
        for row in repository.fetch_cause_effect_trigger_zones():
            zone = normalise_zone_key(row["trigger_zone"])
            name = str(row["trigger_zone_name"] or "").strip()
            name = name or zone_names.get(zone, "")
            item = QListWidgetItem(
                f"Zone {zone}{' - ' + name if name else ''}"
            )
            item.setData(Qt.ItemDataRole.UserRole, zone)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
            )
            item.setCheckState(Qt.CheckState.Unchecked)
            self.zones.addItem(item)
        layout.addWidget(self.zones, 1)

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
            str(self.zones.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.zones.count())
            if self.zones.item(index).checkState() == Qt.CheckState.Checked
        ]

    def _select_all(self) -> None:
        for index in range(self.zones.count()):
            self.zones.item(index).setCheckState(Qt.CheckState.Checked)

    def _clear_selection(self) -> None:
        for index in range(self.zones.count()):
            self.zones.item(index).setCheckState(Qt.CheckState.Unchecked)

    def _filter_zones(self, text: str) -> None:
        query = text.strip().casefold()
        for index in range(self.zones.count()):
            item = self.zones.item(index)
            item.setHidden(query not in item.text().casefold())

    def _accept_selection(self) -> None:
        if not self.selected_zones():
            QMessageBox.information(
                self,
                "Select zones",
                "Select at least one zone, or choose Select all zones.",
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
        self.zone_combo.currentIndexChanged.connect(
            self._selected_zone_changed
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
            "Wheel = zoom  •  Left or middle drag = pan"
        )
        self.layout.addWidget(self.legend)

    def refresh(self) -> None:
        self.zone_combo.clear()
        self.scope_combo.clear()
        self.scope_combo.addItem("Whole site / interpanel", None)
        if self.repository:
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
        self._selected_zone_changed()

    def _selected_zone_changed(self) -> None:
        if not self.repository or self.zone_combo.currentData() is None:
            self._draw_map({}, set())
            return
        zone = normalise_zone_key(self.zone_combo.currentData())
        visible = {int(zone)} if zone.isdigit() else set()
        self._draw_map({}, visible)

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
        visible_zones = set(effect_map)
        if trigger_zone.isdigit():
            visible_zones.add(int(trigger_zone))
        self._draw_map(effect_map, visible_zones)
        self.results.setSortingEnabled(False)
        self.results.setRowCount(0)
        for effect in effects:
            self._append_result([effect.state, effect.zone, "", "", "", "", effect.reason], None)
        for activation in self.repository.fetch_cause_effect_activations(
            trigger_zone,
            scope_node,
        ):
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
    ) -> None:
        self.scene.clear()
        self.zone_popup = None
        if not self.repository:
            return
        device_rows = {
            row["stable_key"]: dict(row) for row in self.repository.fetch_devices()
        }
        panel_rows = {
            str(row["node"]): dict(row) for row in self.repository.fetch_panels()
        }
        for row in self.repository.fetch_zone_geometry():
            if (
                visible_zones is not None
                and int(row["zone"]) not in visible_zones
            ):
                continue
            state = effect_map.get(row["zone"], "NORMAL")
            colour = QColor("#6fca8c")
            if state == "EVACUATE":
                colour = QColor("#dc3545")
            elif state == "ALERT":
                colour = QColor("#ffc107")
            points = json.loads(row["geometry_json"])
            polygon = QPolygonF([QPointF(x, -y) for x, y in points])
            item = self.scene.addPolygon(polygon, QPen(QColor("#334155"), 0), QBrush(colour))
            item.setOpacity(0.75)
            description = str(row["description"] or "").strip()
            item.setData(
                60,
                {
                    "zone": int(row["zone"]),
                    "name": description,
                    "status": state,
                },
            )
            item.setToolTip(f"Zone {row['zone']} — {state}")
        for placement in self.repository.fetch_map_assets():
            if placement["entity_kind"] == "device":
                device = device_rows.get(placement["entity_key"])
                if not device:
                    continue
                symbol = _device_symbol(device)
                state = effect_map.get(device["zone"], "NORMAL")
                colour = SYMBOL_COLOURS[symbol]
                if state == "EVACUATE":
                    colour = QColor("#dc3545")
                elif state == "ALERT":
                    colour = QColor("#ffc107")
                name = device["text"] or catalogue_display_name(
                    device["product_code"], device["observed_type"]
                )
                tooltip = (
                    f"{symbol}: {name}\n"
                    f"Node {device['node']} · Zone {device['zone']} · "
                    f"Loop {device['loop']} · Address {device['address']} · "
                    f"Sub-address {device['sub_address']}"
                )
            else:
                panel = panel_rows.get(placement["entity_key"])
                if not panel:
                    continue
                colour = SYMBOL_COLOURS["Panel"]
                tooltip = f"Panel: {panel['name']}\nNode: {panel['node']}"
            marker = self.scene.addEllipse(
                -7,
                -7,
                14,
                14,
                QPen(QColor("#ffffff"), 1.5),
                QBrush(colour),
            )
            marker.setPos(float(placement["x"]), float(placement["y"]))
            marker.setZValue(20)
            marker.setToolTip(tooltip)
            marker.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True
            )
        if self.scene.itemsBoundingRect().isValid():
            self.map.fitInView(self.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def show_zone_popup_at(self, scene_point: QPointF) -> None:
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
        if self.zone_popup is not None:
            try:
                self.scene.removeItem(self.zone_popup)
            except RuntimeError:
                pass
            self.zone_popup = None
        details = zone_item.data(60)
        name = str(details["name"] or "Unnamed zone")
        text = self.scene.addSimpleText(
            f"Zone {details['zone']}\n{name}\nStatus: {details['status']}"
        )
        text.setBrush(QBrush(QColor("#172033")))
        bounds = text.boundingRect()
        popup = self.scene.addRect(
            0,
            0,
            bounds.width() + 18,
            bounds.height() + 14,
            QPen(QColor("#183153"), 0),
            QBrush(QColor("#ffffff")),
        )
        popup.setPos(
            zone_item.sceneBoundingRect().topRight() + QPointF(12, 0)
        )
        popup.setZValue(2000)
        text.setParentItem(popup)
        text.setPos(9, 7)
        text.setZValue(1)
        self.zone_popup = popup


class ChangesPage(Page):
    def __init__(self):
        super().__init__("Project tracked changes")
        self.table = FilterableTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Type", "Entity", "Key", "Field", "Previous", "Current"])
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(4, 260)
        self.table.setColumnWidth(5, 260)
        self.layout.addWidget(self.table, 1)

    def refresh(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if not self.repository:
            return
        for change in self.repository.fetch_changes():
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                change["change_type"], change["entity"], change["stable_key"],
                change["field"], change["old_value"], change["new_value"],
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
        ]
        matrix_page = next(
            page for page in self.pages if isinstance(page, MatrixPage)
        )
        matrix_page.matrix_imported.connect(self.pages[0].refresh)
        matrix_page.matrix_imported.connect(self.pages[-1].refresh)
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
            ("New", "fa5s.file-circle-plus", self.new_project),
            ("Open", "fa5s.folder-open", self.open_project),
            ("Save", "fa5s.floppy-disk", self.save_project),
            ("Save as", "fa5s.copy", self.save_as),
            ("Close", "fa5s.xmark", self.close_project),
        ):
            project_layout.addWidget(self._ribbon_button(text, icon, callback))
        project_layout.addSpacing(18)
        for text, icon, callback in (
            ("Update configuration", "fa5s.arrows-rotate", self.update_ncf),
            ("Import DXF", "fa5s.map", lambda: self._navigate(5)),
            ("Export Excel", "fa5s.file-excel", self.export_excel),
            ("Changes PDF", "fa5s.file-pdf", self.export_pdf),
        ):
            project_layout.addWidget(self._ribbon_button(text, icon, callback))
        project_layout.addStretch()
        commission_tab = QWidget()
        commission_layout = QHBoxLayout(commission_tab)
        for text, icon, index in (
            ("Devices", "fa5s.microchip", 1),
            ("Nodes", "fa5s.network-wired", 2),
            ("Zones", "fa5s.layer-group", 3),
            ("Output groups", "fa5s.volume-high", 4),
            ("Map zones", "fa5s.draw-polygon", 5),
            ("Matrix", "fa5s.table-cells", 6),
            ("Test mode", "fa5s.fire", 7),
            ("Changes", "fa5s.code-compare", 8),
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
        try:
            button.setIcon(qta.icon(icon, color="#183153"))
        except Exception:
            pass
        button.setIconSize(button.iconSize() * 1.7)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.clicked.connect(callback)
        return button

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
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
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export tracked changes",
            "project-tracked-changes.pdf",
            "PDF (*.pdf)",
        )
        if path:
            try:
                export_change_pdf(self.repository, path)
                self.statusBar().showMessage(f"Exported {path}", 5000)
            except Exception as error:
                QMessageBox.critical(self, "Export failed", str(error))
