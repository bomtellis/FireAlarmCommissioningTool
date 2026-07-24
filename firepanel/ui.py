from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QAction, QColor, QBrush, QPainter, QPen, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
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

from .dxf import DxfShape, read_closed_shapes
from .exports import export_change_pdf, export_devices_xlsx
from .project import ProjectRepository
from .rules import evaluate_zone, generate_htm_rules
from .styles import APP_STYLESHEET


CURRENT_MA = {
    "Optical Smoke": 0.50,
    "Heat Detector": 0.50,
    "Call Point": 0.25,
    "Relay": 1.50,
    "Sounder": 0.30,
}


def _item(value: object, alignment: Qt.AlignmentFlag | None = None) -> QTableWidgetItem:
    result = QTableWidgetItem("" if value is None else str(value))
    if alignment is not None:
        result.setTextAlignment(alignment)
    return result


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
        self.table = QTableWidget(0, len(headers))
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
                row["zone"], row["text"], row["observed_type"] or "Unmapped",
                row["product_code"], row["output_group"],
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, _item(value))
        self.table.setSortingEnabled(True)


class NodesPage(Page):
    def __init__(self):
        super().__init__("Nodes and power")
        note = QLabel(
            "Loop current and autonomy are engineering estimates until manufacturer current data, "
            "battery capacity and alarm loading are confirmed."
        )
        note.setWordWrap(True)
        self.layout.addWidget(note)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "Node", "Panel", "Loops", "Devices", "Expected loop mA",
                "Battery Ah", "Standby h", "Alarm min", "Required Ah", "Autonomy check",
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
        self.table.setRowCount(0)
        if not self.repository:
            return
        power = self.repository.fetch_node_power()
        devices_by_node: dict[int, dict[tuple[int, int], object]] = defaultdict(dict)
        for device in self.repository.fetch_devices():
            devices_by_node[device["node"]][(device["loop"], device["address"])] = device
        for panel in self.repository.fetch_panels():
            node = panel["node"]
            unique = devices_by_node[node]
            expected_ma = sum(CURRENT_MA.get(row["observed_type"], 0.75) for row in unique.values())
            settings = power.get(node)
            battery = settings["battery_ah"] if settings else None
            standby = settings["standby_hours"] if settings else 24.0
            alarm_minutes = settings["alarm_minutes"] if settings else 30.0
            factor = settings["safety_factor"] if settings else 1.25
            alarm_a = sum(
                0.020 if row["observed_type"] == "Sounder" else CURRENT_MA.get(row["observed_type"], 0.75) / 1000
                for row in unique.values()
            )
            required = ((expected_ma / 1000) * standby + alarm_a * (alarm_minutes / 60)) * factor
            status = "Enter battery"
            if battery is not None:
                status = "PASS (estimate)" if battery >= required else "REVIEW"
            row_index = self.table.rowCount()
            self.table.insertRow(row_index)
            values = [
                node, panel["name"], panel["loops_json"], len(unique), f"{expected_ma:.1f}",
                "" if battery is None else f"{battery:.1f}", f"{standby:.1f}",
                f"{alarm_minutes:.0f}", f"{required:.1f}", status,
            ]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, _item(value))

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


class ZonesMapPage(Page):
    geometry_changed = Signal()

    def __init__(self):
        super().__init__("Site drawings and zones")
        controls = QHBoxLayout()
        self.floor_combo = QComboBox()
        self.floor_combo.currentIndexChanged.connect(self.refresh_scene)
        import_button = QPushButton("Import floor DXF")
        import_button.clicked.connect(self.import_dxf)
        self.zone_combo = QComboBox()
        assign_button = QPushButton("Assign selected shape to zone")
        assign_button.clicked.connect(self.assign_selected)
        controls.addWidget(QLabel("Floor"))
        controls.addWidget(self.floor_combo)
        controls.addWidget(import_button)
        controls.addStretch()
        controls.addWidget(self.zone_combo)
        controls.addWidget(assign_button)
        self.layout.addLayout(controls)
        splitter = QSplitter()
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.zone_table = QTableWidget(0, 4)
        self.zone_table.setHorizontalHeaderLabels(["Zone", "Description", "Floor", "Devices"])
        self.zone_table.setMaximumWidth(440)
        splitter.addWidget(self.view)
        splitter.addWidget(self.zone_table)
        splitter.setStretchFactor(0, 1)
        self.layout.addWidget(splitter, 1)
        self.pending_shapes: dict[int, list[DxfShape]] = {}
        self.shape_items: dict[QGraphicsPolygonItem, DxfShape] = {}

    def refresh(self) -> None:
        self.floor_combo.blockSignals(True)
        self.floor_combo.clear()
        self.zone_combo.clear()
        self.zone_table.setRowCount(0)
        if self.repository:
            for floor in self.repository.fetch_floors():
                self.floor_combo.addItem(floor["name"], floor["id"])
            for zone in self.repository.fetch_zones():
                self.zone_combo.addItem(f"Zone {zone['number']} — {zone['description']}", zone["number"])
                row = self.zone_table.rowCount()
                self.zone_table.insertRow(row)
                for column, value in enumerate(
                    [zone["number"], zone["description"], zone["floor_name"], zone["device_count"]]
                ):
                    self.zone_table.setItem(row, column, _item(value))
        self.floor_combo.blockSignals(False)
        self.refresh_scene()

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

    def refresh_scene(self) -> None:
        self.scene.clear()
        self.shape_items.clear()
        if not self.repository:
            return
        floor_id = self.floor_combo.currentData()
        if floor_id is None:
            return
        assigned = [
            row for row in self.repository.fetch_zone_geometry() if row["floor_id"] == floor_id
        ]
        for row in assigned:
            points = [tuple(point) for point in json.loads(row["geometry_json"])]
            self._add_polygon(points, QColor("#6fca8c"), f"Zone {row['zone']}", None)
        for shape in self.pending_shapes.get(floor_id, []):
            item = self._add_polygon(shape.points, QColor("#dbe3ec"), shape.layer, shape)
            self.shape_items[item] = shape
        if self.scene.itemsBoundingRect().isValid():
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
        item.setOpacity(0.72)
        item.setToolTip(tooltip)
        item.setFlag(QGraphicsPolygonItem.GraphicsItemFlag.ItemIsSelectable, True)
        if shape is not None:
            item.setData(0, shape.layer)
        return item

    def assign_selected(self) -> None:
        if not self.repository or self.floor_combo.currentData() is None or self.zone_combo.currentData() is None:
            return
        selected = [item for item in self.scene.selectedItems() if item in self.shape_items]
        if not selected:
            QMessageBox.information(self, "Select a shape", "Select an unassigned grey polyline first.")
            return
        item = selected[0]
        shape = self.shape_items[item]
        self.repository.assign_zone_geometry(
            int(self.zone_combo.currentData()),
            int(self.floor_combo.currentData()),
            shape.points,
            shape.layer,
        )
        self.pending_shapes[int(self.floor_combo.currentData())].remove(shape)
        self.refresh()
        self.geometry_changed.emit()


class MatrixPage(Page):
    def __init__(self):
        super().__init__("Cause and effect matrix")
        note = QLabel(
            "HTM suggestions are a commissioning aid, not an approved fire strategy. "
            "The project-specific cause-and-effect must be agreed by competent stakeholders."
        )
        note.setWordWrap(True)
        self.layout.addWidget(note)
        controls = QHBoxLayout()
        generate = QPushButton("Generate HTM adjacency suggestions")
        generate.clicked.connect(self.generate)
        custom = QPushButton("Add custom door / output rule")
        custom.setProperty("secondary", True)
        custom.clicked.connect(self.add_custom)
        controls.addWidget(generate)
        controls.addWidget(custom)
        controls.addStretch()
        self.layout.addLayout(controls)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Trigger", "Relation", "Target zone", "Target node", "Output group", "Action", "Source", "Notes"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(5, 180)
        self.table.setColumnWidth(7, 360)
        self.layout.addWidget(self.table, 1)

    def refresh(self) -> None:
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
        zones = [row["number"] for row in repository.fetch_zones()]
        self.name = QLineEdit("Close straddling fire door")
        self.trigger = QComboBox()
        self.target = QComboBox()
        for zone in zones:
            self.trigger.addItem(str(zone), zone)
            self.target.addItem(str(zone), zone)
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


class TestPage(Page):
    def __init__(self):
        super().__init__("Test mode")
        controls = QHBoxLayout()
        self.zone_combo = QComboBox()
        self.scope_combo = QComboBox()
        run = QPushButton("Simulate fire trigger")
        run.clicked.connect(self.simulate)
        controls.addWidget(QLabel("Fire in zone"))
        controls.addWidget(self.zone_combo)
        controls.addWidget(QLabel("Scope"))
        controls.addWidget(self.scope_combo)
        controls.addWidget(run)
        controls.addStretch()
        self.layout.addLayout(controls)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.scene = QGraphicsScene()
        self.map = QGraphicsView(self.scene)
        self.map.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.results = QTableWidget(0, 7)
        self.results.setHorizontalHeaderLabels(
            ["State", "Zone", "Node", "Loop", "Address", "Sub", "Device / effect"]
        )
        self.results.setAlternatingRowColors(True)
        splitter.addWidget(self.map)
        splitter.addWidget(self.results)
        self.layout.addWidget(splitter, 1)
        self.legend = QLabel("Green = normal  •  Red = fire/evacuate  •  Yellow = adjacent/pre-alarm alert")
        self.layout.addWidget(self.legend)

    def refresh(self) -> None:
        self.zone_combo.clear()
        self.scope_combo.clear()
        self.scope_combo.addItem("Whole site / interpanel", None)
        if self.repository:
            for zone in self.repository.fetch_zones():
                self.zone_combo.addItem(f"Zone {zone['number']} — {zone['description']}", zone["number"])
            for panel in self.repository.fetch_panels():
                self.scope_combo.addItem(f"Node {panel['node']} — {panel['name']}", panel["node"])
        self._draw_map({})

    def simulate(self) -> None:
        if not self.repository or self.zone_combo.currentData() is None:
            return
        trigger_zone = int(self.zone_combo.currentData())
        scope_node = self.scope_combo.currentData()
        effects = evaluate_zone(self.repository, trigger_zone)
        effect_map = {effect.zone: effect.state for effect in effects}
        self._draw_map(effect_map)
        self.results.setRowCount(0)
        for effect in effects:
            self._append_result([effect.state, effect.zone, "", "", "", "", effect.reason])
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
                    device["text"] or device["observed_type"],
                ]
            )

    def _append_result(self, values: list[object]) -> None:
        row = self.results.rowCount()
        self.results.insertRow(row)
        for column, value in enumerate(values):
            item = _item(value)
            if column == 0:
                if str(value) == "EVACUATE":
                    item.setBackground(QColor("#f8d7da"))
                elif str(value) == "ALERT":
                    item.setBackground(QColor("#fff3cd"))
            self.results.setItem(row, column, item)

    def _draw_map(self, effect_map: dict[int, str]) -> None:
        self.scene.clear()
        if not self.repository:
            return
        for row in self.repository.fetch_zone_geometry():
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
            item.setToolTip(f"Zone {row['zone']} — {state}")
        if self.scene.itemsBoundingRect().isValid():
            self.map.fitInView(self.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)


class ChangesPage(Page):
    def __init__(self):
        super().__init__("NCF tracked changes")
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Type", "Entity", "Key", "Field", "Previous", "Current"])
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(4, 260)
        self.table.setColumnWidth(5, 260)
        self.layout.addWidget(self.table, 1)

    def refresh(self) -> None:
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
            ["Overview", "Devices", "Nodes", "Zones & drawings", "Cause & effect", "Test mode", "Tracked changes"]
        )
        self.pages = [
            DashboardPage(),
            DevicesPage(),
            NodesPage(),
            ZonesMapPage(),
            MatrixPage(),
            TestPage(),
            ChangesPage(),
        ]
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
            ("Update NCF", "fa5s.arrows-rotate", self.update_ncf),
            ("Import DXF", "fa5s.map", lambda: self._navigate(3)),
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
            ("Map zones", "fa5s.draw-polygon", 3),
            ("Matrix", "fa5s.table-cells", 4),
            ("Test mode", "fa5s.fire", 5),
            ("Changes", "fa5s.code-compare", 6),
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
        ncf_path, _ = QFileDialog.getOpenFileName(self, "Select initial NCF", "", "Network configuration (*.ncf *.NCF)")
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
        path, _ = QFileDialog.getOpenFileName(self, "Import updated NCF", "", "Network configuration (*.ncf *.NCF)")
        if not path:
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            _, changes = self.repository.import_ncf(path)
            self._set_repository(self.repository)
            self._navigate(6)
            QMessageBox.information(self, "NCF update complete", f"Recorded {len(changes)} changes.")
        except Exception as error:
            QMessageBox.critical(self, "NCF update failed", str(error))
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

    def export_pdf(self) -> None:
        if not self.repository:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export tracked changes", "ncf-tracked-changes.pdf", "PDF (*.pdf)")
        if path:
            try:
                export_change_pdf(self.repository, path)
                self.statusBar().showMessage(f"Exported {path}", 5000)
            except Exception as error:
                QMessageBox.critical(self, "Export failed", str(error))
