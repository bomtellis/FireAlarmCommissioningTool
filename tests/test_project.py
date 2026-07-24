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

    snapshot_id, changes = repository.import_ncf(ROOT / "Leighton-Site.NCF")
    assert snapshot_id == 1
    assert changes == []
    assert len(repository.fetch_snapshots()) == 1
