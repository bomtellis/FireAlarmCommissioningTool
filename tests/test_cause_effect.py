from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from openpyxl import Workbook, load_workbook

from firepanel.cause_effect import read_cause_effect_workbook
from firepanel.project import ProjectRepository
from firepanel.testing_workbook import (
    RESULT_HEADERS,
    export_testing_workbook,
    read_testing_workbook,
)


def _workbook(path: Path) -> Path:
    workbook = Workbook()
    matrix = workbook.active
    matrix.title = "Cause & Effect"
    matrix["F1"] = "ZONE 1 SOUNDERS"
    matrix["G1"] = "FIRE DOORS"
    matrix["H1"] = "UNUSED SPARE OUTPUT"
    matrix["F2"] = "Node 10 Panel A"
    matrix["F3"] = 1
    matrix["G3"] = 50
    matrix["H3"] = 99
    matrix["C4"] = "32 CHARACTER ZONE LABEL"
    matrix["D4"] = "Old Zone"
    matrix["E4"] = "New Zone"
    matrix["C7"] = "Ward 1"
    matrix["E7"] = 1
    matrix["F7"] = "E"
    matrix["G7"] = "TA"
    matrix["C8"] = "Ward 1 detector 10"
    matrix["E8"] = 1.11
    matrix["F8"] = "TE"

    reference = workbook.create_sheet("OutputGroupInfo")
    reference.append(
        [
            "Node Number",
            "Node Name",
            "Output Group Number",
            "Output Group Name",
            "Zone Number",
            "Zone Name",
            "Activation",
        ]
    )
    reference.append([10, "Panel A", 1, "ZONE 1 SOUNDERS", 1, "Ward 1", "E"])
    reference.append([10, "Panel A", 50, "FIRE DOORS", 1, "N/A", "TA"])
    reference.append([10, "Panel A", 1, "ZONE 1 SOUNDERS", 99, "Ward 99", "E"])
    workbook.save(path)
    return path


def test_reads_matrix_and_checks_output_group_info(tmp_path: Path) -> None:
    parsed = read_cause_effect_workbook(_workbook(tmp_path / "matrix.xlsx"))

    assert len(parsed.activations) == 3
    assert [
        (output.target_node, output.output_group)
        for output in parsed.output_groups
    ] == [(10, 1), (10, 50), (10, 99)]
    assert parsed.reference_count == 3
    assert parsed.matched_count == 2
    assert parsed.matrix_only_count == 1
    assert parsed.reference_only_count == 1
    assert len(parsed.reference_only) == 1
    assert parsed.activation_codes == ["E", "TA", "TE"]

    timed = next(
        activation
        for activation in parsed.activations
        if activation.output_group == 50
    )
    assert timed.trigger_zone == "1"
    assert timed.target_node == 10
    assert timed.target_node_name == "Panel A"
    assert timed.activation_code == "TA"
    assert timed.reference_status == "matched"

    matrix_only = next(
        activation
        for activation in parsed.activations
        if activation.activation_code == "TE"
    )
    assert matrix_only.trigger_zone == "1.11"
    assert matrix_only.reference_status == "matrix_only"


def test_project_import_persists_activations_and_comments(tmp_path: Path) -> None:
    workbook_path = _workbook(tmp_path / "matrix.xlsx")
    project_path = tmp_path / "project.fcp"
    sqlite3.connect(project_path).close()
    repository = ProjectRepository(project_path)

    import_id, parsed = repository.import_cause_effect(workbook_path)
    assert parsed.matched_count == 2
    initial_changes = repository.fetch_cause_effect_changes(import_id)
    assert len(initial_changes) == 1
    assert initial_changes[0]["entity"] == "cause_effect_matrix"
    assert initial_changes[0]["change_type"] == "initial"
    assert initial_changes[0]["new_value"] == "3"
    assert repository.latest_cause_effect_import()["id"] == import_id
    assert len(repository.fetch_cause_effect_activations()) == 3
    assert [
        (row["target_node"], row["output_group"])
        for row in repository.fetch_cause_effect_output_groups()
    ] == [(10, 1), (10, 50), (10, 99)]
    assert len(repository.fetch_cause_effect_reference_only()) == 1
    assert len(repository.fetch_cause_effect_activations("1")) == 2
    assert len(repository.fetch_cause_effect_activations(1.11)) == 1

    activation = repository.fetch_cause_effect_activations("1")[0]
    repository.update_cause_effect_comment(
        activation["id"],
        "Witness with maternity team",
    )
    updated = repository.fetch_cause_effect_activations("1")[0]
    assert updated["comments"] == "Witness with maternity team"

    duplicate_id, _ = repository.import_cause_effect(workbook_path)
    assert duplicate_id == import_id
    assert len(repository.fetch_cause_effect_activations()) == 3

    revised_path = tmp_path / "matrix-revised.xlsx"
    revised = load_workbook(workbook_path)
    revised["Cause & Effect"]["A20"] = "Revision 2"
    revised.save(revised_path)
    revised.close()
    revised_id, _ = repository.import_cause_effect(revised_path)
    assert revised_id != import_id
    assert repository.fetch_cause_effect_activations("1")[0]["comments"] == (
        "Witness with maternity team"
    )
    assert [
        row["trigger_zone"] for row in repository.fetch_cause_effect_trigger_zones()
    ] == ["1", "1.11"]
    assert repository.fetch_cause_effect_changes(revised_id) == []


def test_testing_workbook_round_trips_selected_zone_output_groups(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "project.fcp"
    sqlite3.connect(project_path).close()
    repository = ProjectRepository(project_path)
    repository.import_cause_effect(_workbook(tmp_path / "matrix.xlsx"))

    workbook_path = tmp_path / "testing.xlsx"
    export_testing_workbook(repository, workbook_path, ["1"])
    workbook = load_workbook(workbook_path)
    assert workbook.sheetnames == [
        "Instructions",
        "Test Sessions",
        "Test Results",
    ]
    results = workbook["Test Results"]
    assert [cell.value for cell in results[1]] == RESULT_HEADERS
    assert results.freeze_panes == "A2"
    assert results.max_row == 3
    assert {results.cell(row=row, column=2).value for row in (2, 3)} == {"1"}
    assert {
        results.cell(row=row, column=11).value for row in (2, 3)
    } == {1, 50}

    workbook["Test Sessions"]["D2"] = "Commissioning Engineer"
    results["E2"] = "activated"
    results["F2"] = "pass"
    results["G2"] = "Witnessed at panel"
    results["H2"] = "2026-07-24T10:30:00+00:00"
    results["E3"] = "not-activated"
    results["F3"] = "fail"
    workbook.save(workbook_path)

    imported = read_testing_workbook(workbook_path)
    assert len(imported) == 1
    assert imported[0].trigger_zone == "1"
    assert len(imported[0].results) == 2
    assert repository.import_test_sessions(imported) == (1, 2)
    stored = repository.fetch_test_results()
    assert [row["actual_state"] for row in stored] == [
        "activated",
        "not-activated",
    ]
    assert [row["result"] for row in stored] == ["pass", "fail"]
    assert stored[0]["comments"] == "Witnessed at panel"


def test_zone_geometry_can_be_moved_reassigned_and_unassigned(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "geometry.fcp"
    sqlite3.connect(project_path).close()
    repository = ProjectRepository(project_path)
    floor_id = repository.add_floor("Ground", 0)
    replacement_dxf = tmp_path / "replacement.dxf"
    replacement_dxf.write_text("placeholder", encoding="utf-8")
    repository.set_floor_dxf(floor_id, replacement_dxf)
    assert Path(repository.fetch_floors()[0]["dxf_path"]) == replacement_dxf
    repository.set_floor_dxf(floor_id, None)
    assert repository.fetch_floors()[0]["dxf_path"] is None
    with repository.connection() as connection:
        connection.executemany(
            "INSERT INTO zones(number, description) VALUES (?, ?)",
            [(10, "Ground floor"), (11, "First floor")],
        )

    original = [(0, 0), (10, 0), (10, 10), (0, 0)]
    repository.assign_zone_geometry(10, floor_id, original, "USER_DRAWN")
    geometry = repository.fetch_zone_geometry()[0]
    moved = [(5, 4), (15, 4), (15, 14), (5, 4)]
    repository.update_zone_geometry(geometry["id"], moved)
    assert json.loads(
        repository.fetch_zone_geometry()[0]["geometry_json"]
    ) == [[5.0, 4.0], [15.0, 4.0], [15.0, 14.0], [5.0, 4.0]]

    repository.reassign_zone_geometry(geometry["id"], 11)
    reassigned = repository.fetch_zone_geometry()[0]
    assert reassigned["zone"] == 11
    repository.remove_zone_geometry(reassigned["id"])
    assert repository.fetch_zone_geometry() == []


def test_project_tracks_added_removed_and_modified_matrix_cells(
    tmp_path: Path,
) -> None:
    original_path = _workbook(tmp_path / "matrix.xlsx")
    project_path = tmp_path / "project.fcp"
    sqlite3.connect(project_path).close()
    repository = ProjectRepository(project_path)
    repository.import_cause_effect(original_path)

    revised_path = tmp_path / "matrix-revised.xlsx"
    revised = load_workbook(original_path)
    revised["Cause & Effect"]["F1"] = "RENAMED ZONE SOUNDERS"
    revised["Cause & Effect"]["H1"] = "RENAMED UNUSED SPARE"
    revised["Cause & Effect"]["F7"] = "TA"
    revised["Cause & Effect"]["G7"] = None
    revised["Cause & Effect"]["G8"] = "E"
    revised.save(revised_path)
    revised.close()

    revised_id, _ = repository.import_cause_effect(revised_path)
    changes = repository.fetch_cause_effect_changes(revised_id)

    assert {
        (
            row["stable_key"],
            row["change_type"],
            row["field"],
            row["old_value"],
            row["new_value"],
        )
        for row in changes
    } == {
        (
            "zone 1 -> node 10/group 1",
            "modified",
            "ringing_style",
            "E",
            "TA",
        ),
        (
            "zone 1 -> node 10/group 50",
            "removed",
            None,
            "TA - FIRE DOORS",
            None,
        ),
        (
            "zone 1.11 -> node 10/group 50",
            "added",
            None,
            None,
            "E - FIRE DOORS",
        ),
        (
            "node 10/group 1",
            "modified",
            "output_group_name",
            "ZONE 1 SOUNDERS",
            "RENAMED ZONE SOUNDERS",
        ),
        (
            "node 10/group 99",
            "modified",
            "output_group_name",
            "UNUSED SPARE OUTPUT",
            "RENAMED UNUSED SPARE",
        ),
    }
    assert [
        row["entity"]
        for row in repository.fetch_changes()
    ] == [
        "cause_effect_activation",
        "cause_effect_activation",
        "cause_effect_activation",
        "cause_effect_output_group",
        "cause_effect_output_group",
    ]
