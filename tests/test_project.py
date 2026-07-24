from pathlib import Path

from firepanel.project import ProjectRepository


ROOT = Path(__file__).resolve().parents[1]


def test_project_import_and_duplicate_detection(tmp_path: Path) -> None:
    project_path = tmp_path / "test.fcp"
    repository = ProjectRepository.create(
        project_path,
        "Integration test",
        ROOT / "Leighton-Site.NCF",
    )
    assert repository.latest_snapshot_id() == 1
    assert len(repository.fetch_panels()) == 61
    assert len(repository.fetch_devices()) == 9965
    assert len(repository.fetch_zones()) > 0
    zone_179 = next(row for row in repository.fetch_zones() if row["number"] == 179)
    assert zone_179["description"] == "PATH LAB FIRST FLOOR"

    snapshot_id, changes = repository.import_ncf(ROOT / "Leighton-Site.NCF")
    assert snapshot_id == 1
    assert changes == []
    assert len(repository.fetch_snapshots()) == 1

    session_id = repository.create_test_session(
        engineer="Commissioning Engineer",
        scope_node=52,
        trigger_zone=179,
        results=[("52/1/1/1", "EVACUATE", "Pass", "Detector and outputs witnessed")],
    )
    assert session_id == 1
    sessions = repository.fetch_test_sessions()
    assert sessions[0]["result_count"] == 1

    floor_id = repository.add_floor("Ground", 0)
    repository.place_map_asset("device", "52/1/1/1", floor_id, 125.5, -80.25, "Detector")
    placement = repository.fetch_map_assets(floor_id)[0]
    assert placement["entity_kind"] == "device"
    assert placement["entity_key"] == "52/1/1/1"
    assert placement["x"] == 125.5
    repository.remove_map_asset("device", "52/1/1/1")
    assert repository.fetch_map_assets(floor_id) == []

    repository.add_rule(
        "Release doors",
        trigger_zone=179,
        relation="exact",
        target_zone=180,
        target_node=52,
        output_group=17,
        action="CLOSE FIRE DOOR",
    )
    assert repository.output_group_details(17) == ("Release doors", [179])
    repository.set_output_group_name(17, "Ward fire doors")
    assert repository.output_group_details(17) == ("Ward fire doors", [179])
