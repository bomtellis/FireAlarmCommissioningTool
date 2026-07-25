import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, QPoint, QPointF, Qt
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGraphicsItem,
    QGraphicsScene,
    QMenu,
    QMessageBox,
    QTabWidget,
    QToolButton,
)

from firepanel import __version__
from firepanel.device_catalog import device_current_ma
from firepanel.dxf import DxfLinework, DxfShape, DxfText
from firepanel.ui import (
    AboutPage,
    CauseEffectMatrixWidget,
    ChangeRevisionDialog,
    DoorDialog,
    DxfManagementDialog,
    FilterableTableWidget,
    MainWindow,
    MapGraphicsView,
    MatrixPage,
    OutputGroupZoneAssignmentDialog,
    PolygonSelectionDialog,
    RuleDialog,
    TestPage as CommissioningTestPage,
    ZoneSelectionDialog,
    ZonesMapPage,
    ZonesPage,
    ZoneTestExportDialog,
    _add_door_graphics,
    _add_fire_alarm_symbol,
    _device_symbol,
    _dxf_text_font,
    _item,
    _nearest_zone_numbers,
    natural_sort_key,
    node_current_totals,
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_application_icon_contains_windows_display_sizes() -> None:
    _application()
    icon_path = (
        Path(__file__).resolve().parents[1]
        / "firepanel"
        / "assets"
        / "firepanel.ico"
    )
    icon = QIcon(str(icon_path))

    assert icon_path.is_file()
    assert not icon.isNull()
    available_sizes = {(size.width(), size.height()) for size in icon.availableSizes()}
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= available_sizes


def test_zones_page_shows_nodes_using_each_zone() -> None:
    class Repository:
        def fetch_zones(self):
            return [
                {
                    "number": 10,
                    "description": "Ground floor",
                    "nodes": "1, 3",
                    "device_count": 24,
                }
            ]

    _application()
    page = ZonesPage()
    page.repository = Repository()
    page.refresh()

    assert page.table.horizontalHeaderItem(2).text().startswith("Nodes")
    assert page.table.item(0, 2).text() == "1, 3"
    assert page.table.item(0, 3).text() == "24"
    page.close()


def test_export_menu_is_next_to_file_and_grouped() -> None:
    _application()
    window = MainWindow()
    top_level = window.menuBar().actions()
    assert [action.text() for action in top_level[:2]] == ["&File", "&Export"]
    assert top_level[0].menu().minimumWidth() == 300

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


def test_every_ribbon_action_has_an_appropriate_icon() -> None:
    _application()
    window = MainWindow()
    ribbon = window.findChild(QTabWidget, "ribbon")
    buttons = {
        button.text(): button
        for button in ribbon.findChildren(QToolButton)
        if button.text()
    }
    expected_icons = {
        "New": "fa6s.file-circle-plus",
        "Open": "fa6s.folder-open",
        "Save": "fa6s.floppy-disk",
        "Save as": "fa6s.file-export",
        "Close": "fa6s.xmark",
        "Update configuration": "fa6s.arrows-rotate",
        "Import DXF": "fa6s.file-import",
        "Export Excel": "fa6s.file-excel",
        "Changes PDF": "fa6s.file-pdf",
        "Devices": "fa6s.microchip",
        "Nodes": "fa6s.network-wired",
        "Zones": "fa6s.layer-group",
        "Output groups": "fa6s.bolt",
        "Map zones": "fa6s.draw-polygon",
        "Matrix": "fa6s.table-cells",
        "Test mode": "fa6s.fire",
        "Changes": "fa6s.code-compare",
        "About": "fa6s.circle-info",
    }
    assert set(buttons) == set(expected_icons)
    for text, icon_name in expected_icons.items():
        assert buttons[text].property("iconName") == icon_name
        assert not buttons[text].icon().isNull()
    window.close()


def test_main_window_has_about_tab_with_application_version() -> None:
    _application()
    window = MainWindow()

    assert window.navigation.item(
        window.navigation.count() - 1
    ).text() == "About"
    about_page = window.pages[-1]
    assert isinstance(about_page, AboutPage)
    assert about_page.version_label.text() == f"Version {__version__}"
    assert window.stack.widget(window.stack.count() - 1) is about_page
    window.close()


def test_testing_export_dialog_uses_transfer_lists_and_typeahead() -> None:
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
    assert dialog.available_zones.count() == 2
    assert dialog.selected_zones_list.count() == 0
    assert dialog.selected_zones() == []

    dialog.zone_search.setText("first")
    assert dialog.available_zones.item(0).isHidden()
    assert not dialog.available_zones.item(1).isHidden()
    assert (
        dialog.zone_search.completer().filterMode()
        == Qt.MatchFlag.MatchContains
    )
    assert (
        dialog.zone_search.completer().caseSensitivity()
        == Qt.CaseSensitivity.CaseInsensitive
    )

    dialog.available_zones.item(1).setSelected(True)
    dialog._move_right()
    assert dialog.available_zones.count() == 1
    assert dialog.selected_zones_list.count() == 1
    assert dialog.selected_zones() == ["2"]

    dialog.zone_search.clear()
    dialog.available_zones.item(0).setSelected(True)
    dialog._move_right()
    assert dialog.selected_zones() == ["1", "2"]

    dialog.selected_zones_list.item(0).setSelected(True)
    dialog._move_left()
    assert dialog.selected_zones() == ["2"]
    assert dialog.available_zones.count() == 1

    dialog._select_all()
    assert dialog.selected_zones() == ["1", "2"]
    dialog._clear_selection()
    assert dialog.selected_zones() == []
    dialog.close()


def test_change_revision_dialog_lists_and_selects_revisions() -> None:
    class Repository:
        def fetch_snapshots(self):
            return [
                {
                    "id": 2,
                    "source_name": "revised.skf",
                    "imported_at": "2026-07-25T12:00:00",
                },
                {
                    "id": 1,
                    "source_name": "original.skf",
                    "imported_at": "2026-07-24T12:00:00",
                },
            ]

    _application()
    dialog = ChangeRevisionDialog(Repository())

    assert dialog.revisions.count() == 2
    assert dialog.revisions.item(0).text().startswith(
        "Revision 2 — revised.skf"
    )
    assert dialog.selected_revision_ids() == [2, 1]

    dialog.revisions.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.selected_revision_ids() == [1]
    dialog._clear_selection()
    assert dialog.selected_revision_ids() == []
    dialog._select_all()
    assert dialog.selected_revision_ids() == [2, 1]
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


def test_map_view_pans_beyond_drawing_extents() -> None:
    application = _application()
    scene = QGraphicsScene()
    scene.addRect(0, 0, 100, 100)
    scene.setSceneRect(0, 0, 100, 100)
    view = MapGraphicsView(scene)
    view.resize(500, 400)
    view.show()
    application.processEvents()
    before = view.mapToScene(view.viewport().rect().center())

    view._pan_view_by(QPoint(1200, 900))
    application.processEvents()
    beyond_top_left = view.mapToScene(view.viewport().rect().center())

    assert beyond_top_left.x() < before.x() - 500
    assert beyond_top_left.y() < before.y() - 400
    assert scene.sceneRect().left() < 0
    assert scene.sceneRect().top() < 0

    view._pan_view_by(QPoint(-2600, -2000))
    application.processEvents()
    beyond_bottom_right = view.mapToScene(view.viewport().rect().center())

    assert beyond_bottom_right.x() > 100
    assert beyond_bottom_right.y() > 100
    assert scene.sceneRect().right() > 100
    assert scene.sceneRect().bottom() > 100
    view.close()


def test_map_release_ignores_item_deleted_after_press() -> None:
    application = _application()
    scene = QGraphicsScene()
    scene.setSceneRect(-100, -100, 200, 200)
    polygon = scene.addRect(-50, -50, 100, 100)
    polygon.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
    view = MapGraphicsView(scene)
    view.resize(400, 300)
    view.show()
    application.processEvents()
    point = view.mapFromScene(QPointF(0, 0))

    QTest.mousePress(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=point,
    )
    assert view._press_item is polygon
    scene.clear()
    QTest.mouseRelease(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=point,
    )
    application.processEvents()

    assert view._press_item is None
    view.close()


def test_dxf_text_font_uses_installed_fallback_and_style() -> None:
    _application()
    label = DxfText(
        layer="LABELS",
        text="Room 101",
        x=0,
        y=0,
        height=3,
        rotation=32,
        font_family="Definitely unavailable font",
        font_file="missing-font.shx",
        bold=True,
        italic=False,
        width_factor=0.8,
        oblique=12,
    )

    font, scale, family = _dxf_text_font(label)

    assert (
        QFontDatabase.hasFamily(family)
        or family
        == QFontDatabase.systemFont(
            QFontDatabase.SystemFont.GeneralFont
        ).family()
    )
    assert font.family() == family
    assert font.bold()
    assert font.italic()
    assert font.stretch() == 80
    assert scale > 0


def test_changing_floor_preserves_scene_position_and_zoom() -> None:
    class Repository:
        def fetch_floors(self):
            return [
                {"id": 1, "name": "Ground", "level_order": 0, "dxf_path": None},
                {"id": 2, "name": "First", "level_order": 1, "dxf_path": None},
            ]

        def fetch_zones(self):
            return []

        def fetch_zone_geometry(self):
            return []

        def fetch_map_assets(self, floor_id=None):
            return []

        def fetch_doors(self, floor_id=None):
            return []

    application = _application()
    page = ZonesMapPage()
    page.repository = Repository()
    page.floor_combo.blockSignals(True)
    page.floor_combo.addItem("Ground", 1)
    page.floor_combo.addItem("First", 2)
    page.floor_combo.setCurrentIndex(0)
    page.floor_combo.blockSignals(False)
    page.floor_changed()
    page.scene.setSceneRect(-5000, -5000, 10000, 10000)
    page.view.resetTransform()
    page.view.scale(1.75, 1.75)
    page.view.centerOn(1250, -640)
    application.processEvents()
    before_transform = page.view.transform()
    before_centre = page.view.mapToScene(
        page.view.viewport().rect().center()
    )

    page.floor_combo.setCurrentIndex(1)
    application.processEvents()
    after_centre = page.view.mapToScene(
        page.view.viewport().rect().center()
    )

    assert page._displayed_floor_id == 2
    assert page.view.transform() == before_transform
    assert abs(after_centre.x() - before_centre.x()) < 1
    assert abs(after_centre.y() - before_centre.y()) < 1
    page.close()


def test_door_sprite_has_fixed_scene_size_and_scales_with_zoom() -> None:
    _application()
    scene = QGraphicsScene()
    view = MapGraphicsView(scene)
    door = {
        "id": 1,
        "name": "Ward entrance",
        "start_x": 0,
        "start_y": 0,
        "end_x": 1,
        "end_y": 0,
        "sprite_x": 0,
        "sprite_y": 0,
        "rotation_degrees": 0,
        "door_type": "SINGLE",
        "zone_a": 10,
        "zone_b": 11,
        "has_access_control": 1,
        "access_normal_state": "LOCKED",
        "has_hold_open": 0,
        "hold_open_normal_state": "HELD OPEN",
    }
    door_graphics = _add_door_graphics(scene, door)
    door_item = door_graphics[0]
    scene_width = door_item.sceneBoundingRect().width()
    before = view.transform().mapRect(
        door_item.sceneBoundingRect()
    ).width()

    view.scale(2.0, 2.0)
    after = view.transform().mapRect(
        door_item.sceneBoundingRect()
    ).width()

    assert door_item.sceneBoundingRect().width() == scene_width
    assert after == before * 2
    assert all(
        not item.flags()
        & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        for item in door_graphics
    )
    assert not door_item.pen().isCosmetic()
    assert door_item.pen().widthF() == 28.0
    view.close()


def test_optical_detector_symbol_is_red_o_and_scales_with_drawing() -> None:
    _application()
    scene = QGraphicsScene()
    view = MapGraphicsView(scene)
    payload = {
        "kind": "device",
        "key": "1/1/1/0",
        "symbol": "Detector",
        "observed_type": "Optical detector",
        "name": "Ward optical detector",
        "node": 1,
        "loop": 1,
        "address": 1,
    }
    marker = _add_fire_alarm_symbol(
        scene,
        payload,
        1000,
        2000,
        "Optical detector",
        selectable=True,
        show_address=True,
    )
    before = view.transform().mapRect(marker.sceneBoundingRect()).width()
    view.scale(2.0, 2.0)
    after = view.transform().mapRect(marker.sceneBoundingRect()).width()

    assert marker.pen().color().name() == "#d71920"
    assert marker.pen().widthF() == 16.0
    assert not marker.pen().isCosmetic()
    assert any(
        getattr(item, "text", lambda: "")() == "O"
        for item in marker.childItems()
    )
    assert all(
        not item.flags()
        & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        for item in [marker, *marker.childItems()]
    )
    assert after == before * 2
    view.close()


def test_input_output_device_uses_red_io_rectangle() -> None:
    _application()
    scene = QGraphicsScene()
    payload = {
        "kind": "device",
        "key": "1/1/20/0",
        "symbol": "Output device",
        "observed_type": "Input/output interface",
        "name": "Door interface",
        "node": 1,
        "loop": 1,
        "address": 20,
    }
    marker = _add_fire_alarm_symbol(
        scene,
        payload,
        0,
        0,
        "Input/output interface",
    )

    assert marker.pen().color().name() == "#d71920"
    assert marker.boundingRect().width() > marker.boundingRect().height()
    assert any(
        getattr(item, "text", lambda: "")() == "I/O"
        for item in marker.childItems()
    )


def test_beacon_output_group_is_not_displayed_as_a_sounder() -> None:
    _application()
    row = {
        "observed_type": "Sounder",
        "text": "Female WC",
        "panel": "Panel 7",
        "output_group": 2,
        "output_group_name": "Beacons",
    }
    assert _device_symbol(row) == "Beacon"

    scene = QGraphicsScene()
    marker = _add_fire_alarm_symbol(
        scene,
        {
            **row,
            "kind": "device",
            "key": "7/1/8/1",
            "symbol": "Beacon",
            "name": "Female WC beacon",
            "node": 7,
            "loop": 1,
            "address": 8,
        },
        0,
        0,
        "Beacon",
    )
    assert marker.boundingRect().width() == marker.boundingRect().height()
    assert any(
        getattr(item, "text", lambda: "")() == "B"
        for item in marker.childItems()
    )


def test_panel_output_group_can_be_assigned_to_sounder_and_beacon_zones() -> None:
    class Repository:
        def __init__(self):
            self.saved = None

        def fetch_zones(self):
            return [
                {"number": 10, "description": "Ward"},
                {"number": 11, "description": "Corridor"},
            ]

        def fetch_output_group_zone_assignments(self, node, output_group):
            return [
                {
                    "node": node,
                    "output_group": output_group,
                    "zone": 10,
                    "output_kind": "SOUNDER",
                }
            ]

        def replace_output_group_zone_assignments(
            self,
            node,
            output_group,
            assignments,
        ):
            self.saved = (node, output_group, assignments)

    repository = Repository()
    _application()
    dialog = OutputGroupZoneAssignmentDialog(
        repository,
        7,
        4,
        "Panel sounder circuit",
    )
    assert dialog.table.item(0, 1).checkState() == Qt.CheckState.Checked
    dialog.table.item(1, 2).setCheckState(Qt.CheckState.Checked)
    dialog.save()
    assert repository.saved == (
        7,
        4,
        [(10, "SOUNDER"), (11, "BEACON")],
    )


def test_device_symbol_hit_resolves_above_polygon_and_is_movable() -> None:
    _application()
    scene = QGraphicsScene()
    polygon = scene.addRect(-500, -500, 1000, 1000)
    polygon.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
    polygon.setData(30, 1)
    polygon.setZValue(-10)
    marker = _add_fire_alarm_symbol(
        scene,
        {
            "kind": "device",
            "key": "1/1/1/0",
            "symbol": "Detector",
            "observed_type": "Optical detector",
            "node": 1,
            "loop": 1,
            "address": 1,
        },
        0,
        0,
        "Optical detector",
        selectable=True,
    )

    assert marker.zValue() > polygon.zValue()
    assert marker.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    assert marker.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert all(
        MapGraphicsView._asset_root_item(child) is marker
        for child in marker.childItems()
    )


def test_door_sprites_have_open_closed_geometry_and_lock_colours() -> None:
    _application()
    scene = QGraphicsScene()
    door = {
        "id": 1,
        "name": "Ward entrance",
        "start_x": 0,
        "start_y": 0,
        "end_x": 1,
        "end_y": 0,
        "sprite_x": 0,
        "sprite_y": 0,
        "rotation_degrees": 0,
        "door_type": "DOUBLE",
        "zone_a": 10,
        "zone_b": 11,
        "has_access_control": 1,
        "access_device_key": "1/1/20/0",
        "access_normal_state": "LOCKED",
        "has_hold_open": 1,
        "hold_open_device_key": "1/1/21/0",
        "hold_open_normal_state": "HELD OPEN",
    }

    open_graphics = _add_door_graphics(
        scene,
        door,
        movable=True,
    )
    closed_graphics = _add_door_graphics(
        scene,
        {**door, "id": 2, "sprite_y": 3000},
        fire_active=True,
    )

    assert (
        open_graphics[0].sceneBoundingRect().height()
        > closed_graphics[0].sceneBoundingRect().height()
    )
    assert open_graphics[0].path().elementCount() != (
        closed_graphics[0].path().elementCount()
    )
    assert closed_graphics[0].sceneBoundingRect().height() > 100
    assert open_graphics[1].data(71) == "#dc3545"
    assert closed_graphics[1].data(71) == "#198754"
    assert (
        open_graphics[0].flags()
        & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    )
    assert open_graphics[1].parentItem() is open_graphics[0]
    assert MapGraphicsView._door_root_item(open_graphics[1]) is (
        open_graphics[0]
    )

    access_only = _add_door_graphics(
        scene,
        {**door, "id": 3, "sprite_y": 6000},
        fire_active=True,
        activated_device_keys={"1/1/20/0"},
    )
    hold_open_only = _add_door_graphics(
        scene,
        {**door, "id": 4, "sprite_y": 9000},
        fire_active=True,
        activated_device_keys={"1/1/21/0"},
    )
    assert access_only[1].data(71) == "#198754"
    assert (
        access_only[0].path().elementCount()
        == open_graphics[0].path().elementCount()
    )
    assert hold_open_only[1].data(71) == "#dc3545"
    assert (
        hold_open_only[0].path().elementCount()
        == closed_graphics[0].path().elementCount()
    )


def test_moving_door_persists_its_sprite_position() -> None:
    class Repository:
        def __init__(self):
            self.moved = []

        def move_door(self, door_id, x, y):
            self.moved.append((door_id, x, y))

    _application()
    page = ZonesMapPage()
    page.repository = Repository()
    door = {
        "id": 12,
        "name": "Plant door",
        "sprite_x": 100,
        "sprite_y": -200,
        "rotation_degrees": 30,
        "door_type": "SINGLE",
        "zone_a": 10,
        "zone_b": 11,
        "has_access_control": 1,
        "access_normal_state": "LOCKED",
        "has_hold_open": 0,
        "hold_open_normal_state": "CLOSED",
    }
    graphics = _add_door_graphics(
        page.scene,
        door,
        movable=True,
    )
    for graphic in graphics:
        page.door_items[graphic] = door
    graphics[0].setPos(450, -725)

    page.geometry_item_moved(graphics[0])

    assert page.repository.moved == [(12, 450.0, -725.0)]
    assert door["sprite_x"] == 450
    assert door["sprite_y"] == -725
    page.close()


def test_door_device_selectors_use_contains_typeahead() -> None:
    devices = [
        {
            "stable_key": "1/1/20/0",
            "node": 1,
            "loop": 1,
            "address": 20,
            "sub_address": 0,
            "zone": 10,
            "text": "Access release relay",
            "product_code": 0,
            "observed_type": "Output relay",
            "output_group": 20,
            "output_group_name": "Access doors",
        },
        {
            "stable_key": "2/1/45/0",
            "node": 2,
            "loop": 1,
            "address": 45,
            "sub_address": 0,
            "zone": 11,
            "text": "Ward hold-open magnet",
            "product_code": 0,
            "observed_type": "Output relay",
            "output_group": 45,
            "output_group_name": "Fire hold opens",
        },
    ]

    class Repository:
        def fetch_doors(self, floor_id=None):
            return []

        def fetch_zones(self):
            return [
                {"number": 10, "description": "Ward"},
                {"number": 11, "description": "Corridor"},
            ]

        def suggest_door_control_devices(
            self, zone_a, zone_b, capability
        ):
            return devices

    _application()
    dialog = DoorDialog(
        Repository(),
        1,
        (0, 0),
        (1, 0),
    )
    completer = dialog.access_device.completer()
    completer.setCompletionPrefix("hold-open magnet")
    matches = [
        completer.completionModel().index(row, 0).data()
        for row in range(completer.completionCount())
    ]

    assert dialog.access_device.isEditable()
    assert completer.filterMode() == Qt.MatchFlag.MatchContains
    assert len(matches) == 1
    assert "Ward hold-open magnet" in matches[0]
    dialog.close()


def test_door_dialog_preselects_nearest_zones_on_selected_floor() -> None:
    class Repository:
        def fetch_doors(self, floor_id=None):
            return []

        def fetch_zones(self):
            return [
                {"number": 10, "description": "Far plant"},
                {"number": 21, "description": "Corridor"},
                {"number": 20, "description": "Ward"},
            ]

        def fetch_zone_geometry(self):
            return [
                {
                    "zone": 10,
                    "floor_id": 1,
                    "geometry_json": json.dumps(
                        [[1000, 0], [1100, 0], [1100, 100], [1000, 100]]
                    ),
                },
                {
                    "zone": 20,
                    "floor_id": 1,
                    "geometry_json": json.dumps(
                        [[0, 0], [100, 0], [100, 100], [0, 100]]
                    ),
                },
                {
                    "zone": 21,
                    "floor_id": 1,
                    "geometry_json": json.dumps(
                        [[100, 0], [200, 0], [200, 100], [100, 100]]
                    ),
                },
                {
                    "zone": 10,
                    "floor_id": 2,
                    "geometry_json": json.dumps(
                        [[95, 45], [105, 45], [105, 55], [95, 55]]
                    ),
                },
            ]

        def suggest_door_control_devices(
            self, zone_a, zone_b, capability
        ):
            return []

    repository = Repository()
    nearest = _nearest_zone_numbers(repository, 1, (100, -50))
    assert nearest == [20, 21]

    _application()
    dialog = DoorDialog(
        repository,
        1,
        (99, -50),
        (101, -50),
    )
    assert dialog.zone_a.currentData() == 20
    assert dialog.zone_b.currentData() == 21
    assert "20 and 21" in dialog.nearest_zone_hint.text()
    dialog.close()

    assert _nearest_zone_numbers(repository, 1, (50, -50)) == [20, 20]
    internal_dialog = DoorDialog(
        repository,
        1,
        (49, -50),
        (51, -50),
    )
    assert internal_dialog.zone_a.currentData() == 20
    assert internal_dialog.zone_b.currentData() == 20
    assert "within Zone 20" in internal_dialog.nearest_zone_hint.text()
    internal_dialog.close()


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


def test_middle_click_does_not_cancel_polygon_drawing() -> None:
    application = _application()
    page = ZonesMapPage()
    page.resize(1100, 700)
    page.show()
    application.processEvents()
    page.draw_polygon_button.setChecked(True)
    page._add_draw_point(QPointF(10, 20))
    page._add_draw_point(QPointF(30, 40))

    QTest.mouseClick(
        page.view.viewport(),
        Qt.MouseButton.MiddleButton,
        pos=QPoint(150, 150),
    )
    application.processEvents()

    assert page.draw_polygon_button.isChecked()
    assert page.drawing_kind == "zone"
    assert page.view.draw_mode
    assert page.drawing_points == [QPointF(10, 20), QPointF(30, 40)]
    assert page.view.cursor().shape() == Qt.CursorShape.CrossCursor
    page.close()


def test_door_tool_places_one_click_sprite_with_both_functions(
    monkeypatch,
) -> None:
    device = {
        "stable_key": "1/1/20/0",
        "node": 1,
        "loop": 1,
        "address": 20,
        "sub_address": 0,
        "zone": 10,
        "text": "Door relay",
        "product_code": 0,
        "observed_type": "Output relay",
        "output_group": 20,
        "output_group_name": "Fire doors",
    }

    class Repository:
        def __init__(self):
            self.created = []

        def fetch_doors(self, floor_id=None):
            return []

        def fetch_zones(self):
            return [
                {"number": 10, "description": "Ward"},
                {"number": 11, "description": "Corridor"},
            ]

        def fetch_devices(self):
            return [device]

        def suggest_door_control_devices(
            self, zone_a, zone_b, capability
        ):
            return [device]

        def create_door(self, **values):
            self.created.append(values)
            return 1

    _application()
    page = ZonesMapPage()
    repository = Repository()
    page.repository = repository
    page.floor_combo.addItem("Ground", 3)
    monkeypatch.setattr(
        DoorDialog,
        "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        DoorDialog,
        "values",
        lambda self: {
            "name": "Ward entrance",
            "floor_id": self.floor_id,
            "start": self.start,
            "end": self.end,
            "zone_a": 10,
            "zone_b": 11,
            "has_access_control": True,
            "access_device_key": device["stable_key"],
            "access_normal_state": "LOCKED",
            "has_hold_open": True,
            "hold_open_device_key": device["stable_key"],
            "hold_open_normal_state": "HELD OPEN",
            "notes": "",
            "door_type": "DOUBLE",
            "sprite_position": self.sprite_position,
            "rotation_degrees": 90,
        },
    )
    monkeypatch.setattr(page, "_refresh_preserving_view", lambda: None)

    page.draw_door_button.setChecked(True)
    page._add_draw_point(QPointF(10, 20))

    assert len(repository.created) == 1
    assert repository.created[0]["sprite_position"] == (10.0, 20.0)
    assert repository.created[0]["door_type"] == "DOUBLE"
    assert repository.created[0]["rotation_degrees"] == 90
    assert repository.created[0]["has_access_control"]
    assert repository.created[0]["has_hold_open"]
    assert page.draw_door_button.isChecked()
    assert page.drawing_kind == "door"
    assert page.drawing_points == []

    page._add_draw_point(QPointF(30, 40))

    assert len(repository.created) == 2
    assert repository.created[1]["sprite_position"] == (30.0, 40.0)
    assert page.draw_door_button.isChecked()
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

    page._assign_polygon_zone(item)

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


def test_control_selection_keeps_multiple_polygons_selected() -> None:
    _application()
    page = ZonesMapPage()
    first_shape = DxfShape(
        "ZONES",
        "LWPOLYLINE",
        [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
    )
    second_shape = DxfShape(
        "ZONES",
        "LWPOLYLINE",
        [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)],
    )
    first = page._add_polygon(
        first_shape.points,
        page.palette().color(page.backgroundRole()),
        "First",
        first_shape,
    )
    second = page._add_polygon(
        second_shape.points,
        page.palette().color(page.backgroundRole()),
        "Second",
        second_shape,
    )
    page.shape_items[first] = first_shape
    page.shape_items[second] = second_shape

    page.select_polygon_at_point(QPointF(5, -5))
    page.select_polygon_at_point(QPointF(25, -5), True, [first])

    assert first.isSelected()
    assert second.isSelected()

    page.select_polygon_at_point(
        QPointF(5, -5),
        True,
        [first, second],
    )
    assert not first.isSelected()
    assert second.isSelected()
    page.close()


def test_control_click_selects_multiple_polygons_in_map_view() -> None:
    application = _application()
    page = ZonesMapPage()
    page.resize(900, 650)
    shapes = [
        DxfShape(
            "ZONES",
            "LWPOLYLINE",
            points,
        )
        for points in (
            [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
            [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)],
        )
    ]
    polygons = []
    for shape in shapes:
        polygon = page._add_polygon(
            shape.points,
            page.palette().color(page.backgroundRole()),
            shape.layer,
            shape,
        )
        page.shape_items[polygon] = shape
        polygons.append(polygon)
    page.show()
    page.view.fitInView(
        page.scene.itemsBoundingRect(),
        Qt.AspectRatioMode.KeepAspectRatio,
    )
    application.processEvents()

    QTest.mouseClick(
        page.view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=page.view.mapFromScene(QPointF(5, -5)),
    )
    QTest.mouseClick(
        page.view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        pos=page.view.mapFromScene(QPointF(25, -5)),
    )
    application.processEvents()

    assert all(polygon.isSelected() for polygon in polygons)
    page.close()


def test_assigned_polygon_context_menu_has_edit_assignment_and_delete() -> None:
    _application()
    page = ZonesMapPage()
    points = [(0, 0), (10, 0), (10, 10), (0, 0)]
    polygon = page._add_polygon(
        points,
        page.palette().color(page.backgroundRole()),
        "Zone 10",
        None,
    )
    page.geometry_items[polygon] = {
        "id": 1,
        "zone": 10,
        "floor_id": 1,
        "source_layer": "USER_DRAWN",
        "geometry_json": json.dumps(points),
    }
    menu, _actions = page._build_polygon_menu(polygon, [polygon])
    captured = [action.text() for action in menu.actions()]

    assert "Edit points" in captured
    assert any(text.startswith("Change assigned zone") for text in captured)
    assert "Remove zone assignment" in captured
    assert "Copy to floor above" in captured
    assert "Delete polygon" in captured
    menu.close()
    page.close()


def test_polygon_point_handles_update_saved_geometry() -> None:
    class Repository:
        def __init__(self):
            self.updates = []

        def update_zone_geometry(self, geometry_id, points):
            self.updates.append((geometry_id, points))

    _application()
    page = ZonesMapPage()
    page.repository = Repository()
    points = [(0, 0), (10, 0), (10, 10), (0, 0)]
    polygon = page._add_polygon(
        points,
        page.palette().color(page.backgroundRole()),
        "Zone 10",
        None,
    )
    page.geometry_items[polygon] = {
        "id": 7,
        "zone": 10,
        "floor_id": 1,
        "source_layer": "USER_DRAWN",
        "geometry_json": json.dumps(points),
    }

    page._start_polygon_point_edit(polygon)
    first_handle = next(
        handle
        for handle, (_polygon, index) in page.vertex_handles.items()
        if index == 0
    )
    first_handle.setPos(5, -6)
    page.geometry_item_moved(first_handle)

    assert len(page.vertex_handles) == 3
    assert page.repository.updates[-1][0] == 7
    assert page.repository.updates[-1][1][0] == (5.0, 6.0)
    assert page.repository.updates[-1][1][-1] == (5.0, 6.0)
    assert polygon.polygon().first() == QPointF(5, -6)
    page.close()


def test_selected_polygons_copy_to_next_floor(monkeypatch) -> None:
    class Repository:
        def __init__(self):
            self.copies = []

        def fetch_floors(self):
            return [
                {"id": 1, "name": "Ground", "level_order": 0},
                {"id": 2, "name": "First", "level_order": 1},
                {"id": 3, "name": "Second", "level_order": 2},
            ]

        def fetch_zone_geometry(self):
            return []

        def assign_zone_geometry(
            self,
            zone,
            floor_id,
            points,
            source_layer,
        ):
            self.copies.append((zone, floor_id, points, source_layer))

    _application()
    page = ZonesMapPage()
    page.repository = Repository()
    page.floor_combo.addItem("Ground", 1)
    polygons = []
    for geometry_id, zone, offset in ((1, 10, 0), (2, 11, 20)):
        points = [
            (offset, 0),
            (offset + 10, 0),
            (offset + 10, 10),
            (offset, 0),
        ]
        polygon = page._add_polygon(
            points,
            page.palette().color(page.backgroundRole()),
            f"Zone {zone}",
            None,
        )
        page.geometry_items[polygon] = {
            "id": geometry_id,
            "zone": zone,
            "floor_id": 1,
            "source_layer": "USER_DRAWN",
            "geometry_json": json.dumps(points),
        }
        polygons.append(polygon)
    monkeypatch.setattr(page, "_refresh_preserving_view", lambda: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)

    page.copy_polygons_to_floor_above(polygons)

    assert [(row[0], row[1]) for row in page.repository.copies] == [
        (10, 2),
        (11, 2),
    ]
    page.close()


def test_assigned_zones_are_excluded_from_assignment_choices() -> None:
    class Repository:
        def fetch_zone_geometry(self):
            return [
                {"zone": 11, "floor_id": 1},
                {"zone": 12, "floor_id": 2},
            ]

        def fetch_zones(self):
            return [
                {"number": 10, "description": "Ground floor"},
                {"number": 11, "description": "First floor"},
                {"number": 12, "description": "Plant room"},
            ]

    _application()
    page = ZonesMapPage()
    page.repository = Repository()
    page.floor_combo.blockSignals(True)
    page.floor_combo.addItem("Ground", 1)
    page.floor_combo.addItem("First", 2)
    page.floor_combo.blockSignals(False)
    assert page._available_zone_choices() == [
        ("Zone 10 — Ground floor", 10),
        ("Zone 12 — Plant room", 12),
    ]
    assert page._available_zone_choices(include_zone=11) == [
        ("Zone 10 — Ground floor", 10),
        ("Zone 11 — First floor", 11),
        ("Zone 12 — Plant room", 12),
    ]
    page.floor_combo.blockSignals(True)
    page.floor_combo.setCurrentIndex(1)
    page.floor_combo.blockSignals(False)
    assert page._available_zone_choices() == [
        ("Zone 10 — Ground floor", 10),
        ("Zone 11 — First floor", 11),
    ]
    page.close()


def test_zone_device_placement_advances_and_preserves_view() -> None:
    devices = [
        {
            "stable_key": f"1/1/{address}/0",
            "node": 1,
            "panel": "Panel",
            "loop": 1,
            "address": address,
            "sub_address": 0,
            "zone": zone,
            "text": name,
            "product_code": 0,
            "observed_type": "Optical detector",
            "output_group": None,
            "output_group_name": None,
            "ringing_style": None,
        }
        for address, zone, name in (
            (1, 10, "Ward detector 1"),
            (2, 10, "Ward detector 2"),
            (3, 11, "Corridor detector"),
        )
    ]
    devices.append(
        {
            **devices[0],
            "stable_key": "1/1/1/1",
            "sub_address": 1,
            "text": "Ward detector 1 second channel",
        }
    )

    class Repository:
        def __init__(self):
            self.placements = []

        def fetch_devices(self):
            return devices

        def fetch_panels(self):
            return []

        def fetch_zones(self):
            return [
                {"number": 10, "description": "Ward"},
                {"number": 11, "description": "Corridor"},
            ]

        def fetch_floors(self):
            return [
                {
                    "id": 1,
                    "name": "Ground",
                    "level_order": 0,
                    "dxf_path": None,
                }
            ]

        def fetch_zone_geometry(self):
            return []

        def fetch_doors(self, floor_id=None):
            return []

        def fetch_map_assets(self, floor_id=None):
            rows = self.placements
            if floor_id is not None:
                rows = [
                    row for row in rows if row["floor_id"] == int(floor_id)
                ]
            return rows

        def place_map_asset(
            self,
            entity_kind,
            entity_key,
            floor_id,
            x,
            y,
            symbol_type,
        ):
            self.placements = [
                row
                for row in self.placements
                if not (
                    row["entity_kind"] == entity_kind
                    and row["entity_key"] == entity_key
                )
            ]
            self.placements.append(
                {
                    "entity_kind": entity_kind,
                    "entity_key": entity_key,
                    "floor_id": int(floor_id),
                    "x": float(x),
                    "y": float(y),
                    "symbol_type": symbol_type,
                }
            )

        def remove_map_asset(self, entity_kind, entity_key):
            self.placements = [
                row
                for row in self.placements
                if not (
                    row["entity_kind"] == entity_kind
                    and row["entity_key"] == entity_key
                )
            ]

    application = _application()
    page = ZonesMapPage()
    page.resize(1000, 700)
    page.show()
    repository = Repository()
    page.repository = repository
    page.floor_combo.addItem("Ground", 1)
    page._build_asset_rows()
    physical_devices = [
        payload
        for payload in page.asset_rows
        if payload["kind"] == "device"
    ]
    assert len(physical_devices) == 3
    assert physical_devices[0]["member_keys"] == (
        "1/1/1/0",
        "1/1/1/1",
    )
    page._refresh_device_zone_choices()
    page.device_zone_combo.setCurrentIndex(
        page.device_zone_combo.findData(10)
    )
    page.refresh_asset_list()
    page.scene.setSceneRect(-5000, -5000, 10000, 10000)
    page.view.scale(2.2, 2.2)
    page.view.centerOn(750, -420)
    application.processEvents()
    before_transform = page.view.transform()
    before_centre = page.view.mapToScene(
        page.view.viewport().rect().center()
    )

    page.place_zone_devices_button.setChecked(True)
    assert page.asset_list.currentItem().data(
        Qt.ItemDataRole.UserRole
    ) == ("device", "1/1/1/0")

    page.place_selected(QPointF(100, -100))
    after_first = page.view.mapToScene(
        page.view.viewport().rect().center()
    )

    assert repository.placements[0]["entity_key"] == "1/1/1/0"
    assert page.place_zone_devices_button.isChecked()
    assert page.asset_list.currentItem().data(
        Qt.ItemDataRole.UserRole
    ) == ("device", "1/1/2/0")
    assert page.view.transform() == before_transform
    assert abs(after_first.x() - before_centre.x()) < 1
    assert abs(after_first.y() - before_centre.y()) < 1

    page.place_selected(QPointF(200, -150))
    after_second = page.view.mapToScene(
        page.view.viewport().rect().center()
    )

    assert [row["entity_key"] for row in repository.placements] == [
        "1/1/1/0",
        "1/1/2/0",
    ]
    assert not page.place_zone_devices_button.isChecked()
    assert "complete" in page.zone_placement_status.text().casefold()
    assert page.view.transform() == before_transform
    assert abs(after_second.x() - before_centre.x()) < 1
    assert abs(after_second.y() - before_centre.y()) < 1

    marker = next(
        item
        for item in page.scene.items()
        if item.parentItem() is None
        and item.data(10) == ("device", "1/1/1/0")
    )
    marker.setSelected(True)
    page.show_selection_details()
    assert page.map_popup is not None
    marker.setPos(350, -275)
    page.geometry_item_moved(marker)
    moved_placement = next(
        row
        for row in repository.placements
        if row["entity_key"] == "1/1/1/0"
    )
    assert moved_placement["x"] == 350
    assert moved_placement["y"] == -275
    assert page.map_popup is not None
    zone_popup_text = page.map_popup.childItems()[0]
    assert "Ward detector 1" in zone_popup_text.text()
    assert "Node: 1" in zone_popup_text.text()
    assert "Zone: 10" in zone_popup_text.text()
    assert "Loop: 1" in zone_popup_text.text()
    assert "Address: 1" in zone_popup_text.text()
    assert "Status: NORMAL" in zone_popup_text.text()
    assert "Sub-addresses:" in zone_popup_text.text()
    assert "0: Ward detector 1" in zone_popup_text.text()
    assert "1: Ward detector 1 second channel" in zone_popup_text.text()
    assert zone_popup_text.font().pixelSize() == 18
    assert (
        page.map_popup.flags()
        & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
    )
    page.view.scale(0.5, 0.5)
    assert page.map_popup.scale() == 1.0
    page.view.scale(4.0, 4.0)
    assert abs(page.map_popup.scale() - 2.0) < 1e-9
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


def test_architectural_underlay_dialog_replaces_and_removes_floor_dxf(
    tmp_path,
    monkeypatch,
) -> None:
    original = tmp_path / "original.dxf"
    original.write_text("original", encoding="utf-8")
    replacement = tmp_path / "replacement.dxf"
    replacement.write_text("replacement", encoding="utf-8")

    class Repository:
        def __init__(self):
            self.floors = [
                {
                    "id": 1,
                    "name": "Ground",
                    "level_order": 0,
                    "dxf_path": str(original),
                }
            ]

        def fetch_floors(self):
            return self.floors

        def set_floor_dxf(self, floor_id, path):
            self.floors[0]["dxf_path"] = path

    repository = Repository()
    _application()
    dialog = DxfManagementDialog(repository, 1)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(replacement), "DXF"),
    )
    monkeypatch.setattr(
        "firepanel.ui.read_linework",
        lambda _path: [],
    )
    dialog.table.selectRow(0)
    dialog.attach_or_replace()

    assert repository.floors[0]["dxf_path"] == str(replacement)
    assert dialog.changed
    assert dialog.table.item(0, 3).text() == "Available"

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog.table.selectRow(0)
    dialog.remove_underlay()
    assert repository.floors[0]["dxf_path"] is None
    assert dialog.table.item(0, 3).text() == "Not assigned"
    dialog.close()


def test_underlay_toggle_hides_only_architectural_dxf(
    tmp_path,
    monkeypatch,
) -> None:
    underlay = tmp_path / "ground.dxf"
    underlay.write_text("placeholder", encoding="utf-8")

    class Line:
        layer = "WALLS"
        points = [(0, 0), (100, 0)]
        closed = False
        entity_type = "LINE"

    class Repository:
        def fetch_floors(self):
            return [
                {
                    "id": 1,
                    "name": "Ground",
                    "level_order": 0,
                    "dxf_path": str(underlay),
                }
            ]

        def fetch_zone_geometry(self):
            return [
                {
                    "id": 1,
                    "zone": 10,
                    "floor_id": 1,
                    "description": "Ward",
                    "geometry_json": json.dumps(
                        [[0, 0], [100, 0], [100, 100], [0, 0]]
                    ),
                    "source_layer": "USER_DRAWN",
                }
            ]

        def fetch_map_assets(self, floor_id=None):
            return []

        def fetch_doors(self, floor_id=None):
            return []

    monkeypatch.setattr(
        "firepanel.ui.read_linework",
        lambda _path: [Line()],
    )
    monkeypatch.setattr(
        "firepanel.ui.read_text",
        lambda _path: [
            DxfText(
                layer="LABELS",
                text="Ward",
                x=20,
                y=30,
                height=4,
                rotation=32,
                font_family="Definitely unavailable font",
                font_file="simplex.shx",
            )
        ],
    )
    monkeypatch.setattr("firepanel.ui.read_closed_shapes", lambda _path: [])
    _application()
    page = ZonesMapPage()
    page.repository = Repository()
    page.floor_combo.blockSignals(True)
    page.floor_combo.addItem("Ground", 1)
    page.floor_combo.blockSignals(False)
    page.refresh_scene(False)

    assert any(item.data(20) == "WALLS" for item in page.scene.items())
    text_item = next(
        item for item in page.scene.items() if item.data(20) == "LABELS"
    )
    assert text_item.rotation() == -32
    assert (
        QFontDatabase.hasFamily(text_item.font().family())
        or text_item.font().family()
        == QFontDatabase.systemFont(
            QFontDatabase.SystemFont.GeneralFont
        ).family()
    )
    assert len(page.geometry_items) == 1

    page.show_underlay.setChecked(False)

    assert not any(item.data(20) is not None for item in page.scene.items())
    assert page.shape_items == {}
    assert len(page.geometry_items) == 1
    assert not page.layer_list.isEnabled()
    page.close()


def test_test_mode_shows_only_relevant_assigned_polygons_and_zone_popup() -> None:
    class Repository:
        def fetch_devices(self):
            return []

        def fetch_panels(self):
            return []

        def fetch_map_assets(self):
            return []

        def fetch_doors(self, floor_id=None):
            return [
                {
                    "id": 4,
                    "name": "Ward entrance",
                    "floor_id": 1,
                    "start_x": 8,
                    "start_y": -10,
                    "end_x": 12,
                    "end_y": -10,
                    "zone_a": 10,
                    "zone_b": 11,
                    "has_access_control": 1,
                    "access_device_key": "1/1/20/0",
                    "access_normal_state": "LOCKED",
                    "has_hold_open": 1,
                    "hold_open_device_key": "1/1/21/0",
                    "hold_open_normal_state": "HELD OPEN",
                },
                {
                    "id": 5,
                    "name": "Unrelated floor door",
                    "floor_id": 1,
                    "start_x": 30,
                    "start_y": -10,
                    "end_x": 31,
                    "end_y": -10,
                    "zone_a": 90,
                    "zone_b": 91,
                    "has_access_control": 0,
                    "access_device_key": None,
                    "access_normal_state": "UNLOCKED",
                    "has_hold_open": 0,
                    "hold_open_device_key": None,
                    "hold_open_normal_state": "CLOSED",
                },
            ]

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
    page._draw_map(
        {10: "EVACUATE"},
        {10},
        {
            "1/1/20/0": "E",
            "1/1/21/0": "E",
        },
    )

    polygons = [
        item for item in page.scene.items() if item.data(60) is not None
    ]
    assert isinstance(page.map, MapGraphicsView)
    assert len(polygons) == 1
    assert polygons[0].data(60) == {
        "zone": 10,
        "name": "Ground floor",
        "status": "EVACUATE",
        "sounder_status": None,
    }

    page.show_zone_popup_at(QPointF(10, -10))
    assert page.zone_popup is not None
    popup_text = page.zone_popup.childItems()[0].text()
    assert "Zone 10" in popup_text
    assert "Ground floor" in popup_text
    assert "Status: EVACUATE" in popup_text
    door_items = [
        item for item in page.scene.items() if item.data(70) == 4
    ]
    assert door_items
    assert any(item.data(70) == 5 for item in page.scene.items())
    assert "Access: UNLOCKED" in door_items[0].toolTip()
    assert "Hold-open: CLOSED" in door_items[0].toolTip()
    unrelated = next(
        item for item in page.scene.items() if item.data(70) == 5
    )
    assert "Access:" not in unrelated.toolTip()
    assert "Hold-open:" not in unrelated.toolTip()
    page.close()


def test_test_mode_assigns_triggered_sounder_outputs_to_their_zones() -> None:
    devices = [
        {
            "stable_key": "7/1/1/0",
            "node": 7,
            "panel": "Panel 7",
            "loop": 1,
            "address": 1,
            "sub_address": 0,
            "zone": 10,
            "text": "Test detector",
            "product_code": 0,
            "observed_type": "Optical detector",
            "output_group": None,
            "output_group_name": None,
            "ringing_style": None,
        },
        {
            "stable_key": "7/1/2/0",
            "node": 7,
            "panel": "Panel 7",
            "loop": 1,
            "address": 2,
            "sub_address": 0,
            "zone": 11,
            "text": "Adjacent ward sounder",
            "product_code": 0,
            "observed_type": "Sounder",
            "output_group": 2,
            "output_group_name": "Zone sounders",
            "ringing_style": "Evacuate",
        },
        {
            "stable_key": "7/1/3/0",
            "node": 7,
            "panel": "Panel 7",
            "loop": 1,
            "address": 3,
            "sub_address": 0,
            "zone": 12,
            "text": "Alert-area sounder",
            "product_code": 0,
            "observed_type": "Sounder",
            "output_group": 3,
            "output_group_name": "Alert sounders",
            "ringing_style": "Alert",
        },
    ]

    class Repository:
        def fetch_rules(self):
            return []

        def fetch_cause_effect_activations(self, trigger_zone, scope_node):
            return [
                {
                    "id": 1,
                    "trigger_zone": str(trigger_zone),
                    "target_node": 7,
                    "output_group": 2,
                    "output_group_name": "Zone sounders",
                    "ringing_style": "E",
                    "comments": "",
                },
                {
                    "id": 2,
                    "trigger_zone": str(trigger_zone),
                    "target_node": 7,
                    "output_group": 3,
                    "output_group_name": "Alert sounders",
                    "ringing_style": "A",
                    "comments": "",
                },
                {
                    "id": 3,
                    "trigger_zone": str(trigger_zone),
                    "target_node": 7,
                    "output_group": 4,
                    "output_group_name": "Panel sounder circuit",
                    "ringing_style": "E",
                    "comments": "",
                },
            ]

        def fetch_devices(self):
            return devices

        def fetch_panels(self):
            return []

        def fetch_doors(self, floor_id=None):
            return []

        def fetch_map_assets(self, floor_id=None):
            return []

        def fetch_output_group_zone_assignments(
            self,
            node=None,
            output_group=None,
        ):
            rows = [
                {
                    "node": 7,
                    "output_group": 4,
                    "zone": 13,
                    "output_kind": "SOUNDER",
                }
            ]
            return [
                row
                for row in rows
                if (node is None or row["node"] == node)
                and (
                    output_group is None
                    or row["output_group"] == output_group
                )
            ]

        def fetch_zone_geometry(self):
            return [
                {
                    "floor_id": 1,
                    "zone": 10,
                    "description": "Test zone",
                    "geometry_json": json.dumps(
                        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
                    ),
                },
                {
                    "floor_id": 1,
                    "zone": 11,
                    "description": "Adjacent zone",
                    "geometry_json": json.dumps(
                        [[10, 0], [20, 0], [20, 10], [10, 10], [10, 0]]
                    ),
                },
                {
                    "floor_id": 1,
                    "zone": 12,
                    "description": "Alert zone",
                    "geometry_json": json.dumps(
                        [[20, 0], [30, 0], [30, 10], [20, 10], [20, 0]]
                    ),
                },
                {
                    "floor_id": 1,
                    "zone": 13,
                    "description": "Panel sounder zone",
                    "geometry_json": json.dumps(
                        [[30, 0], [40, 0], [40, 10], [30, 10], [30, 0]]
                    ),
                },
            ]

    _application()
    page = CommissioningTestPage()
    page.repository = Repository()
    page.floor_combo.addItem("Ground", 1)
    page.zone_combo.addItem("Zone 10 — Test zone", 10)
    page.scope_combo.addItem("Whole site", None)
    page.simulate()

    polygons = {
        item.data(60)["zone"]: item
        for item in page.scene.items()
        if item.data(60) is not None
    }
    assert set(polygons) == {10, 11, 12, 13}
    assert polygons[11].data(60)["status"] == "NORMAL"
    assert polygons[11].data(60)["sounder_status"] == "E"
    assert polygons[11].brush().color().name() == "#dc3545"
    assert "Sounders triggered: E" in polygons[11].toolTip()
    assert polygons[12].data(60)["sounder_status"] == "A"
    assert polygons[12].brush().color().name() == "#ffc107"
    assert polygons[13].data(60)["sounder_status"] == "E"
    assert polygons[13].brush().color().name() == "#dc3545"

    page.show_zone_popup_at(QPointF(15, -5))
    popup_text = page.zone_popup.childItems()[0].text()
    assert "Zone 11" in popup_text
    assert "Sounders triggered: E" in popup_text

    sounder_rows = [
        row
        for row in range(page.results.rowCount())
        if page.results.item(row, 0).data(Qt.ItemDataRole.UserRole)
        == "sounder-zone/11"
    ]
    assert len(sounder_rows) == 1
    assert page.results.item(sounder_rows[0], 0).text() == "SOUNDER E"
    page.close()


def test_test_mode_zone_selector_uses_contains_typeahead() -> None:
    _application()
    page = CommissioningTestPage()
    page.zone_combo.addItem("Zone 10 — Ground floor ward", 10)
    page.zone_combo.addItem("Zone 25 — First floor corridor", 25)
    completer = page.zone_combo.completer()
    completer.setCompletionPrefix("first floor")
    matches = [
        completer.completionModel().index(row, 0).data()
        for row in range(completer.completionCount())
    ]

    assert page.zone_combo.isEditable()
    assert (
        page.zone_combo.insertPolicy()
        == QComboBox.InsertPolicy.NoInsert
    )
    assert completer.filterMode() == Qt.MatchFlag.MatchContains
    assert completer.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
    assert matches == ["Zone 25 — First floor corridor"]
    page.close()


def test_test_mode_adds_unlock_and_close_checks_for_fire_door() -> None:
    device = {
        "stable_key": "1/1/20/0",
        "node": 1,
        "panel": "Panel",
        "loop": 1,
        "address": 20,
        "sub_address": 0,
        "zone": 10,
        "text": "Door control relay",
        "product_code": 0,
        "observed_type": "Output relay",
    }

    class Repository:
        def fetch_rules(self):
            return []

        def fetch_cause_effect_activations(self, trigger_zone, scope_node):
            return []

        def fetch_devices(self):
            return [device]

        def fetch_doors(self, floor_id=None):
            return [
                {
                    "id": 4,
                    "name": "Ward entrance",
                    "floor_id": 1,
                    "start_x": 0,
                    "start_y": 0,
                    "end_x": 20,
                    "end_y": 0,
                    "zone_a": 10,
                    "zone_b": 11,
                    "has_access_control": 1,
                    "access_device_key": device["stable_key"],
                    "access_normal_state": "LOCKED",
                    "has_hold_open": 1,
                    "hold_open_device_key": None,
                    "hold_open_normal_state": "HELD OPEN",
                }
            ]

        def fetch_zone_geometry(self):
            return []

        def fetch_panels(self):
            return []

        def fetch_map_assets(self):
            return []

    _application()
    page = CommissioningTestPage()
    page.repository = Repository()
    page.zone_combo.addItem("Zone 10 — Ward", 10)
    page.scope_combo.addItem("Whole site", None)
    page.simulate()

    door_expectations = {
        page.results.item(row, 0).data(Qt.ItemDataRole.UserRole):
        page.results.item(row, 0).text()
        for row in range(page.results.rowCount())
        if str(
            page.results.item(row, 0).data(Qt.ItemDataRole.UserRole) or ""
        ).startswith("door/")
    }
    assert door_expectations == {
        "door/4/access": "UNLOCKED",
        "door/4/hold-open": "CLOSED",
    }
    page.close()


def test_test_mode_draws_selected_floor_dxf_and_only_its_devices(
    tmp_path,
    monkeypatch,
) -> None:
    ground_dxf = tmp_path / "ground.dxf"
    first_dxf = tmp_path / "first.dxf"
    ground_dxf.touch()
    first_dxf.touch()
    monkeypatch.setattr(
        "firepanel.ui.read_linework",
        lambda path: [
            DxfLinework(
                "GROUND-WALLS" if str(path) == str(ground_dxf) else "FIRST-WALLS",
                "LINE",
                [(0, 0), (1000, 0)],
            )
        ],
    )
    monkeypatch.setattr("firepanel.ui.read_text", lambda path: [])

    devices = [
        {
            "stable_key": f"1/1/{address}/0",
            "node": 1,
            "panel": "Panel",
            "loop": 1,
            "address": address,
            "sub_address": 0,
            "zone": zone,
            "text": f"Detector {address}",
            "product_code": 0,
            "observed_type": "Optical detector",
            "output_group": None,
        }
        for address, zone in ((1, 10), (2, 20))
    ]
    devices[1]["observed_type"] = "Input/output interface"
    devices[1]["output_group"] = 50

    class Repository:
        def fetch_floors(self):
            return [
                {"id": 1, "name": "Ground", "dxf_path": str(ground_dxf)},
                {"id": 2, "name": "First", "dxf_path": str(first_dxf)},
            ]

        def fetch_devices(self):
            return devices

        def fetch_panels(self):
            return []

        def fetch_zone_geometry(self):
            return [
                {
                    "floor_id": 1,
                    "zone": 10,
                    "description": "Ground",
                    "geometry_json": json.dumps(
                        [[0, 0], [1000, 0], [1000, 1000], [0, 0]]
                    ),
                },
                {
                    "floor_id": 2,
                    "zone": 20,
                    "description": "First",
                    "geometry_json": json.dumps(
                        [[0, 0], [800, 0], [800, 800], [0, 0]]
                    ),
                },
            ]

        def fetch_doors(self, floor_id=None):
            return []

        def fetch_map_assets(self, floor_id=None):
            return [
                {
                    "entity_kind": "device",
                    "entity_key": "1/1/1/0",
                    "floor_id": 1,
                    "x": 100,
                    "y": -100,
                },
                {
                    "entity_kind": "device",
                    "entity_key": "1/1/2/0",
                    "floor_id": 2,
                    "x": 200,
                    "y": -200,
                },
            ]

    application = _application()
    page = CommissioningTestPage()
    page.resize(1000, 700)
    page.show()
    page.repository = Repository()
    page.floor_combo.addItem("Ground", 1)
    page.floor_combo.addItem("First", 2)
    page._draw_map({}, None)

    assert {
        item.data(20)
        for item in page.scene.items()
        if item.data(20) is not None
    } == {"GROUND-WALLS"}
    assert {
        item.data(10)
        for item in page.scene.items()
        if item.data(11)
    } == {("device", "1/1/1/0")}
    assert len(
        [item for item in page.scene.items() if item.data(60) is not None]
    ) == 1
    application.processEvents()
    page.map.scale(1.8, 1.8)
    page.map.centerOn(350, -275)
    application.processEvents()
    before_transform = page.map.transform()
    before_centre = page.map.mapToScene(
        page.map.viewport().rect().center()
    )

    page.floor_combo.setCurrentIndex(1)
    application.processEvents()
    after_centre = page.map.mapToScene(
        page.map.viewport().rect().center()
    )

    assert {
        item.data(20)
        for item in page.scene.items()
        if item.data(20) is not None
    } == {"FIRST-WALLS"}
    assert {
        item.data(10)
        for item in page.scene.items()
        if item.data(11)
    } == {("device", "1/1/2/0")}
    assert page.map.transform() == before_transform
    one_pixel = 1.1 / abs(before_transform.m11())
    assert abs(after_centre.x() - before_centre.x()) < one_pixel
    assert abs(after_centre.y() - before_centre.y()) < one_pixel

    activation = {
        "target_node": 1,
        "output_group": 50,
        "ringing_style": "E",
    }
    triggered = page._triggered_device_styles([activation])
    assert triggered == {"1/1/2/0": "E"}
    page._draw_map({}, None, triggered)
    marker = next(
        item for item in page.scene.items() if item.data(11)
    )
    assert marker.data(12) == "E"
    assert marker.brush().color().name() == "#f87171"
    assert marker.zValue() == 30

    page.show_zone_popup_at(QPointF(200, -200))
    popup_text = page.zone_popup.childItems()[0].text()
    assert "Detector 2" in popup_text
    assert "Node: 1" in popup_text
    assert "Zone: 20" in popup_text
    assert "Loop: 1" in popup_text
    assert "Address: 2" in popup_text
    assert "Status: TRIGGERED — E" in popup_text
    popup_text_item = page.zone_popup.childItems()[0]
    assert popup_text_item.font().pixelSize() == 18
    assert page.zone_popup.boundingRect().width() > 200
    assert (
        page.zone_popup.flags()
        & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
    )
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


def test_rules_page_edits_and_removes_only_selected_custom_rules(
    monkeypatch,
) -> None:
    custom_rule = {
        "id": 1,
        "name": "Release access door",
        "trigger_zone": 10,
        "relation": "exact",
        "target_zone": 11,
        "target_node": 2,
        "output_group": 30,
        "action": "ACTIVATE OUTPUT",
        "source": "custom",
        "enabled": 1,
        "notes": "Witness operation",
    }
    generated_rule = {
        **custom_rule,
        "id": 2,
        "name": "Generated adjacency",
        "source": "HTM 05-03 Figure 2",
    }
    second_custom_rule = {
        **custom_rule,
        "id": 3,
        "name": "Close hold-open door",
    }

    class Repository:
        def __init__(self):
            self.rules = [custom_rule, generated_rule, second_custom_rule]
            self.updates = []
            self.deleted = []

        def fetch_rules(self):
            return self.rules

        def fetch_rule(self, rule_id):
            return next(
                (rule for rule in self.rules if rule["id"] == rule_id),
                None,
            )

        def fetch_zones(self):
            return [
                {"number": 10, "description": "Ward"},
                {"number": 11, "description": "Corridor"},
            ]

        def update_custom_rule(self, rule_id, *values):
            self.updates.append((rule_id, values))

        def delete_rules(self, rule_ids):
            self.deleted.append(list(rule_ids))

    repository = Repository()
    _application()
    page = MatrixPage()
    page.repository = repository
    page.refresh_rules()

    generated_row = next(
        row
        for row in range(page.table.rowCount())
        if page.table.item(row, 6).text() != "custom"
    )
    page.table.selectRow(generated_row)
    assert not page.edit_custom_button.isEnabled()
    assert page.remove_custom_button.isEnabled()

    custom_row = next(
        row
        for row in range(page.table.rowCount())
        if page.table.item(row, 6).text() == "custom"
    )
    page.table.clearSelection()
    page.table.selectRow(custom_row)
    assert page.edit_custom_button.isEnabled()
    assert page.remove_custom_button.isEnabled()
    assert not (
        page.table.item(custom_row, 0).flags()
        & Qt.ItemFlag.ItemIsEditable
    )

    def edit_dialog(dialog):
        assert isinstance(dialog, RuleDialog)
        assert dialog.name.text() == "Release access door"
        assert dialog.trigger.currentData() == 10
        assert dialog.target.currentData() == 11
        dialog.name.setText("Release access door revised")
        dialog.output_group.setValue(31)
        dialog.save()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RuleDialog, "exec", edit_dialog)
    page.edit_custom()

    assert repository.updates[0][0] == 1
    assert repository.updates[0][1][0] == "Release access door revised"
    assert repository.updates[0][1][5] == 31

    selection = page.table.selectionModel()
    selection.clearSelection()
    for row in range(page.table.rowCount()):
        selection.select(
            page.table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
    assert not page.edit_custom_button.isEnabled()
    assert page.remove_custom_button.isEnabled()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    page.remove_custom()
    assert repository.deleted == [[1, 2, 3]]
    page.close()


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
