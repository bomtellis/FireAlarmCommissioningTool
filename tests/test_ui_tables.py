import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QGraphicsScene, QMessageBox

from firepanel.device_catalog import device_current_ma
from firepanel.dxf import DxfShape
from firepanel.ui import (
    CauseEffectMatrixWidget,
    FilterableTableWidget,
    MainWindow,
    MapGraphicsView,
    MatrixPage,
    PolygonSelectionDialog,
    TestPage as CommissioningTestPage,
    ZoneSelectionDialog,
    ZonesMapPage,
    ZoneTestExportDialog,
    _item,
    natural_sort_key,
    node_current_totals,
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_export_menu_is_next_to_file_and_grouped() -> None:
    _application()
    window = MainWindow()
    top_level = window.menuBar().actions()
    assert [action.text() for action in top_level[:2]] == ["&File", "&Export"]

    export_actions = top_level[1].menu().actions()
    assert [
        action.text() for action in export_actions if not action.isSeparator()
    ] == [
        "Device schedule (Excel)…",
        "Comparison workbook…",
        "Tracked changes (PDF)…",
        "Output-group test workbook…",
        "Import completed test workbook…",
    ]
    assert [
        action.text() for action in export_actions if action.isSeparator()
    ] == ["Commissioning data", "Cause & Effect", "Reports", "Testing"]
    window.close()


def test_testing_export_dialog_uses_checkboxes_and_typeahead() -> None:
    class Repository:
        def fetch_zones(self):
            return [
                {"number": 1, "description": "Ground floor"},
                {"number": 2, "description": "First floor"},
            ]

        def fetch_cause_effect_trigger_zones(self):
            return [
                {"trigger_zone": "1", "trigger_zone_name": "Ground floor"},
                {"trigger_zone": "2", "trigger_zone_name": "First floor"},
            ]

    _application()
    dialog = ZoneTestExportDialog(Repository())
    assert dialog.zones.count() == 2
    assert dialog.selected_zones() == []

    dialog.zones.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.zone_search.setText("first")
    assert dialog.zones.item(0).isHidden()
    assert not dialog.zones.item(1).isHidden()
    assert dialog.selected_zones() == ["1"]

    dialog._select_all()
    assert dialog.selected_zones() == ["1", "2"]
    dialog._clear_selection()
    assert dialog.selected_zones() == []
    dialog.close()


def test_map_view_wheel_zooms() -> None:
    class WheelEvent:
        accepted = False

        def angleDelta(self):
            return QPoint(0, 120)

        def accept(self):
            self.accepted = True

    _application()
    view = MapGraphicsView(QGraphicsScene())
    event = WheelEvent()
    before = view.transform().m11()
    view.wheelEvent(event)
    assert view.transform().m11() > before
    assert event.accepted
    view.close()


def test_zone_polygon_draw_mode_places_undoes_and_cancels_vertices() -> None:
    app = _application()
    page = ZonesMapPage()
    page.resize(1100, 700)
    page.show()
    app.processEvents()
    page.draw_polygon_button.setChecked(True)

    for point in (QPoint(80, 80), QPoint(180, 80), QPoint(180, 180)):
        QTest.mouseClick(
            page.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=point,
        )
    assert len(page.drawing_points) == 3
    assert len(page.drawing_markers) == 3

    QTest.keyClick(page.view, Qt.Key.Key_Backspace)
    assert len(page.drawing_points) == 2
    QTest.keyClick(page.view, Qt.Key.Key_Escape)
    assert not page.draw_polygon_button.isChecked()
    assert page.drawing_points == []
    page.close()


def test_right_click_zone_assignment_uses_selected_zone(monkeypatch) -> None:
    class Repository:
        def __init__(self):
            self.assignments = []

        def assign_zone_geometry(
            self,
            zone,
            floor_id,
            points,
            source_layer,
        ):
            self.assignments.append((zone, floor_id, points, source_layer))

        def fetch_zone_geometry(self):
            return []

        def fetch_zones(self):
            return [
                {"number": 10, "description": "Ground floor"},
                {"number": 11, "description": "First floor"},
            ]

    _application()
    page = ZonesMapPage()
    repository = Repository()
    page.floor_combo.addItem("Ground", 3)
    page.zone_combo.addItem("Zone 10 - Ground floor", 10)
    page.zone_combo.addItem("Zone 11 - First floor", 11)
    page.repository = repository
    shape = DxfShape(
        layer="ZONE_OUTLINES",
        entity_type="LWPOLYLINE",
        points=[(0, 0), (10, 0), (10, 10), (0, 10)],
    )
    item = page._add_polygon(
        shape.points,
        page.palette().color(page.backgroundRole()),
        shape.layer,
        shape,
    )
    page.shape_items[item] = shape
    page.pending_shapes[3] = [shape]
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        ZoneSelectionDialog,
        "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        ZoneSelectionDialog,
        "selected_zone",
        lambda self: 11,
    )
    monkeypatch.setattr(page, "refresh", lambda: None)

    page.assign_shape_from_context(item, QPointF())

    assert repository.assignments == [
        (11, 3, shape.points, "ZONE_OUTLINES")
    ]
    assert page.zone_combo.currentData() == 11
    assert page.pending_shapes[3] == []
    page.close()


def test_zone_selection_dialog_typeahead_filters_number_and_name() -> None:
    _application()
    dialog = ZoneSelectionDialog(
        [
            ("Zone 10 - Ground floor", 10),
            ("Zone 11 - First floor", 11),
            ("Zone 210 - Plant room", 210),
        ],
        10,
    )
    dialog.search.setText("plant")
    assert [dialog.zone_list.item(index).isHidden() for index in range(3)] == [
        True,
        True,
        False,
    ]
    assert dialog.selected_zone() == 210

    dialog.search.setText("11")
    assert [dialog.zone_list.item(index).isHidden() for index in range(3)] == [
        True,
        False,
        True,
    ]
    assert dialog.selected_zone() == 11
    dialog.close()


def test_assigned_zones_are_excluded_from_assignment_choices() -> None:
    class Repository:
        def fetch_zone_geometry(self):
            return [{"zone": 11}]

        def fetch_zones(self):
            return [
                {"number": 10, "description": "Ground floor"},
                {"number": 11, "description": "First floor"},
                {"number": 12, "description": "Plant room"},
            ]

    _application()
    page = ZonesMapPage()
    page.repository = Repository()
    assert page._available_zone_choices() == [
        ("Zone 10 — Ground floor", 10),
        ("Zone 12 — Plant room", 12),
    ]
    assert page._available_zone_choices(include_zone=11) == [
        ("Zone 10 — Ground floor", 10),
        ("Zone 11 — First floor", 11),
        ("Zone 12 — Plant room", 12),
    ]
    page.close()


def test_geometry_refresh_preserves_zoom_and_scene_position(monkeypatch) -> None:
    app = _application()
    page = ZonesMapPage()
    page.floor_combo.addItem("Ground", 1)
    page.view.setSceneRect(-2000, -2000, 4000, 4000)
    page.resize(1000, 650)
    page.show()
    app.processEvents()
    page.view.scale(2.4, 2.4)
    page.view.centerOn(QPointF(320, -410))
    app.processEvents()
    before_transform = page.view.transform().m11()
    before_centre = page.view.mapToScene(
        page.view.viewport().rect().center()
    )

    def reset_view():
        page.view.resetTransform()
        page.view.centerOn(QPointF())

    monkeypatch.setattr(page, "refresh", reset_view)
    page._refresh_preserving_view()
    after_centre = page.view.mapToScene(
        page.view.viewport().rect().center()
    )
    assert page.view.transform().m11() == before_transform
    assert abs(after_centre.x() - before_centre.x()) < 1
    assert abs(after_centre.y() - before_centre.y()) < 1
    page.close()


def test_test_mode_shows_only_relevant_assigned_polygons_and_zone_popup() -> None:
    class Repository:
        def fetch_devices(self):
            return []

        def fetch_panels(self):
            return []

        def fetch_map_assets(self):
            return []

        def fetch_zone_geometry(self):
            return [
                {
                    "zone": 10,
                    "description": "Ground floor",
                    "geometry_json": json.dumps(
                        [[0, 0], [20, 0], [20, 20], [0, 20], [0, 0]]
                    ),
                },
                {
                    "zone": 11,
                    "description": "First floor",
                    "geometry_json": json.dumps(
                        [[5, 5], [25, 5], [25, 25], [5, 25], [5, 5]]
                    ),
                },
            ]

    _application()
    page = CommissioningTestPage()
    page.repository = Repository()
    page._draw_map({10: "EVACUATE"}, {10})

    polygons = [
        item for item in page.scene.items() if item.data(60) is not None
    ]
    assert isinstance(page.map, MapGraphicsView)
    assert len(polygons) == 1
    assert polygons[0].data(60) == {
        "zone": 10,
        "name": "Ground floor",
        "status": "EVACUATE",
    }

    page.show_zone_popup_at(QPointF(10, -10))
    assert page.zone_popup is not None
    popup_text = page.zone_popup.childItems()[0].text()
    assert "Zone 10" in popup_text
    assert "Ground floor" in popup_text
    assert "Status: EVACUATE" in popup_text
    page.close()


def test_selected_zone_polygon_highlights_its_inside_area() -> None:
    _application()
    page = ZonesMapPage()
    shape = DxfShape(
        layer="ZONE_OUTLINES",
        entity_type="LWPOLYLINE",
        points=[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
    )
    item = page._add_polygon(
        shape.points,
        page.palette().color(page.backgroundRole()),
        shape.layer,
        shape,
    )
    page.shape_items[item] = shape
    original_colour = item.brush().color().name()

    item.setSelected(True)
    page.show_selection_details()
    assert item.brush().color().name() == "#4da3ff"
    assert item.opacity() == 0.9

    item.setSelected(False)
    page.show_selection_details()
    assert item.brush().color().name() == original_colour
    assert item.opacity() == 0.72
    page.close()


def test_overlapping_zone_polygons_show_chooser_and_select_requested_item(
    monkeypatch,
) -> None:
    _application()
    page = ZonesMapPage()
    first_shape = DxfShape(
        layer="FIRST",
        entity_type="LWPOLYLINE",
        points=[(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)],
    )
    second_shape = DxfShape(
        layer="SECOND",
        entity_type="LWPOLYLINE",
        points=[(5, 5), (25, 5), (25, 25), (5, 25), (5, 5)],
    )
    first = page._add_polygon(
        first_shape.points,
        page.palette().color(page.backgroundRole()),
        first_shape.layer,
        first_shape,
    )
    second = page._add_polygon(
        second_shape.points,
        page.palette().color(page.backgroundRole()),
        second_shape.layer,
        second_shape,
    )
    page.shape_items[first] = first_shape
    page.shape_items[second] = second_shape
    chosen = []

    def accept_dialog(dialog):
        chosen.extend(
            dialog.polygon_list.item(index).text()
            for index in range(dialog.polygon_list.count())
        )
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PolygonSelectionDialog, "exec", accept_dialog)
    monkeypatch.setattr(
        PolygonSelectionDialog,
        "selected_polygon",
        lambda _dialog: first,
    )
    page.select_polygon_at_point(QPointF(10, -10))

    assert len(chosen) == 2
    assert all(label.startswith("Unassigned:") for label in chosen)
    assert first.isSelected()
    assert not second.isSelected()
    page.close()


def test_natural_sort_key_orders_embedded_numbers_numerically() -> None:
    values = ["Node 10", "Node 2", "Node 1"]
    assert sorted(values, key=natural_sort_key) == ["Node 1", "Node 2", "Node 10"]

    _application()
    table = FilterableTableWidget(0, 1)
    table.setHorizontalHeaderLabels(["Node"])
    for value in (10, 2, 1):
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, _item(value))
    table.setSortingEnabled(True)
    table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    assert [table.item(row, 0).text() for row in range(3)] == ["1", "2", "10"]


def test_column_filters_combine_across_columns() -> None:
    _application()
    table = FilterableTableWidget(0, 2)
    table.setHorizontalHeaderLabels(["Node", "Description"])
    for values in ((2, "First floor"), (10, "Ground floor"), (11, "Plant room")):
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, _item(value))

    table.set_column_filter(1, "floor")
    assert [table.isRowHidden(row) for row in range(3)] == [False, False, True]
    table.set_column_filter(0, "10")
    assert [table.isRowHidden(row) for row in range(3)] == [True, False, True]
    table.clear_filters()
    assert not any(table.isRowHidden(row) for row in range(3))


def test_node_current_totals_count_multichannel_devices_once() -> None:
    devices = [
        {"loop": 1, "address": 1, "observed_type": "Optical Smoke"},
        {"loop": 1, "address": 2, "observed_type": "Input Module"},
        {"loop": 1, "address": 2, "observed_type": "Relay"},
        {"loop": 1, "address": 3, "observed_type": "Sounder"},
    ]

    count, quiescent_ma, alarm_ma = node_current_totals(devices)

    assert count == 3
    assert quiescent_ma == 2.3
    assert alarm_ma == 27.5
    assert device_current_ma("Sounder") == (0.30, 20.00)


def test_cause_effect_matrix_shows_all_nodes_and_filters_columns() -> None:
    _application()
    matrix = CauseEffectMatrixWidget()
    matrix.set_activations(
        [
            {
                "target_node": 10,
                "target_node_name": "Panel A",
                "output_group": 1,
                "output_group_name": "Zone sounders",
                "trigger_zone": "1",
                "trigger_zone_name": "Ward 1",
                "ringing_style": "E",
                "reference_status": "matched",
                "comments": "",
            },
            {
                "target_node": 10,
                "target_node_name": "Panel A",
                "output_group": 50,
                "output_group_name": "Fire doors",
                "trigger_zone": "1.11",
                "trigger_zone_name": "Ward 1 detector 10",
                "ringing_style": "TA",
                "reference_status": "matrix_only",
                "comments": "Confirm timer",
            },
            {
                "target_node": 11,
                "target_node_name": "Panel B",
                "output_group": 2,
                "output_group_name": "Floor sounders",
                "trigger_zone": "2",
                "trigger_zone_name": "Ward 2",
                "ringing_style": "E",
                "reference_status": "matched",
                "comments": "",
            },
        ],
        [
            {
                "target_node": 10,
                "target_node_name": "Panel A",
                "output_group": 1,
                "output_group_name": "Zone sounders",
            },
            {
                "target_node": 10,
                "target_node_name": "Panel A",
                "output_group": 50,
                "output_group_name": "Fire doors",
            },
            {
                "target_node": 11,
                "target_node_name": "Panel B",
                "output_group": 2,
                "output_group_name": "Floor sounders",
            },
            {
                "target_node": 11,
                "target_node_name": "Panel B",
                "output_group": 99,
                "output_group_name": "Unused spare group",
            },
        ],
    )

    assert matrix.node_filter.count() == 3
    assert matrix.node_filter.itemText(0) == "All nodes"
    assert matrix.table.rowCount() == 3
    assert matrix.zone_table.rowCount() == 3
    assert matrix.table.columnCount() == 4

    columns = {
        (
            matrix.table.horizontalHeaderItem(column).data(
                Qt.ItemDataRole.UserRole
            ),
            matrix.table.horizontalHeaderItem(column).data(
                Qt.ItemDataRole.UserRole + 1
            ),
        ): column
        for column in range(matrix.table.columnCount())
    }
    zone_rows = {
        matrix.zone_table.item(row, 0).data(Qt.ItemDataRole.UserRole): row
        for row in range(matrix.zone_table.rowCount())
    }
    assert matrix.table.item(zone_rows["1"], columns[(10, 1)]).text() == "E"
    assert matrix.table.item(zone_rows["1.11"], columns[(10, 50)]).text() == "TA"
    assert "Matrix only" in matrix.table.item(
        zone_rows["1.11"],
        columns[(10, 50)],
    ).toolTip()
    assert matrix.table.item(zone_rows["2"], columns[(11, 2)]).text() == "E"
    assert matrix.table.item(zone_rows["2"], columns[(11, 99)]) is None
    assert not any(
        matrix.table.isColumnHidden(column)
        for column in range(matrix.table.columnCount())
    )

    matrix.node_filter.setCurrentIndex(matrix.node_filter.findData(10))
    assert not matrix.table.isColumnHidden(columns[(10, 1)])
    assert not matrix.table.isColumnHidden(columns[(10, 50)])
    assert matrix.table.isColumnHidden(columns[(11, 2)])

    matrix.node_filter.setCurrentIndex(0)
    assert not any(
        matrix.table.isColumnHidden(column)
        for column in range(matrix.table.columnCount())
    )

    matrix.table.verticalScrollBar().setRange(0, 10)
    matrix.zone_table.verticalScrollBar().setRange(0, 10)
    matrix.table.verticalScrollBar().setValue(1)
    assert matrix.zone_table.verticalScrollBar().value() == 1
    matrix.table.horizontalScrollBar().setValue(100)
    assert matrix.zone_table.item(zone_rows["1"], 0).text().startswith("Zone 1")


def test_matrix_tab_sits_next_to_imported_activations() -> None:
    _application()
    page = MatrixPage()

    assert page.tabs.tabText(0) == "Imported activations"
    assert page.tabs.tabText(1) == "Activation matrix"
