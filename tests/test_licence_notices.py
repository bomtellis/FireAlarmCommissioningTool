import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_every_recorded_package_has_licence_files() -> None:
    records = json.loads(
        (ROOT / "THIRD_PARTY_PACKAGES.json").read_text(encoding="utf-8")
    )
    assert len(records) == 30
    assert {
        "CPython",
        "PySide6",
        "QtAwesome",
        "ezdxf",
        "shapely",
        "reportlab",
        "openpyxl",
        "pytest",
        "pyinstaller",
    } <= {record["name"] for record in records}

    for record in records:
        directory = (
            ROOT / "LICENSES" / "third-party" / record["directory"]
        )
        assert directory.is_dir(), record["name"]
        for filename in record["licence_files"]:
            assert (directory / filename).is_file(), (
                record["name"],
                filename,
            )


def test_embedded_component_notices_are_present() -> None:
    icon_directory = ROOT / "LICENSES" / "third-party" / "icon-fonts"
    qt_directory = ROOT / "LICENSES" / "third-party" / "qt-6.11.1"

    assert (icon_directory / "NOTICE.md").is_file()
    assert (icon_directory / "Font-Awesome-5.15.4-and-6.7.2-LICENSE.txt").is_file()
    assert (icon_directory / "Codicons-0.0.36-LICENSE.txt").is_file()
    assert (qt_directory / "NOTICE.md").is_file()
    assert (qt_directory / "LGPL-3.0.txt").is_file()
    assert (qt_directory / "GPL-2.0.txt").is_file()
    assert (qt_directory / "GPL-3.0.txt").is_file()


def test_executable_build_includes_notices() -> None:
    spec = (ROOT / "main.spec").read_text(encoding="utf-8")
    for required_path in (
        "LICENSE",
        "RIGHTS_NOTICE.md",
        "THIRD_PARTY_NOTICES.md",
        "THIRD_PARTY_PACKAGES.json",
        "LICENSES",
    ):
        assert f"'{required_path}'" in spec
