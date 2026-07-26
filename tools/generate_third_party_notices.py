"""Generate third-party package notices from the active Python environment."""

from __future__ import annotations

import json
import re
import shutil
import sys
from collections import defaultdict
from importlib import metadata
from pathlib import Path, PurePath

from packaging.markers import default_environment
from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[1]
LICENSE_ROOT = ROOT / "LICENSES" / "third-party"
OVERRIDE_ROOT = ROOT / "tools" / "license_overrides"
MANUALLY_MAINTAINED_DIRECTORIES = {"icon-fonts", "qt-6.11.1"}

ROOTS = {
    "runtime": [
        "PySide6",
        "QtAwesome",
        "ezdxf",
        "Shapely",
        "reportlab",
        "openpyxl",
    ],
    "test": ["pytest"],
    "build": ["setuptools", "pyinstaller", "pyinstaller-hooks-contrib"],
}

LICENSE_OVERRIDES = {
    "colorama": "BSD-3-Clause",
    "et-xmlfile": "MIT",
    "ezdxf": "MIT",
    "openpyxl": "MIT",
    "pyinstaller-hooks-contrib": (
        "GPL-2.0-or-later WITH PyInstaller-exception-2.0 AND Apache-2.0"
    ),
    "reportlab": "BSD-3-Clause",
    "shapely": "BSD-3-Clause",
}

PROJECT_URL_LABELS = ("Homepage", "Repository", "Source", "Documentation")


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dependency_closure(root_names: list[str]) -> dict[str, metadata.Distribution]:
    environment = default_environment()
    found: dict[str, metadata.Distribution] = {}
    pending = list(root_names)
    while pending:
        requested_name = pending.pop()
        key = normalise(requested_name)
        if key in found:
            continue
        distribution = metadata.distribution(requested_name)
        found[key] = distribution
        for raw_requirement in distribution.requires or ():
            requirement = Requirement(raw_requirement)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            if requirement.extras:
                continue
            pending.append(requirement.name)
    return found


def licence_expression(distribution: metadata.Distribution) -> str:
    package_name = normalise(distribution.metadata["Name"])
    if package_name in LICENSE_OVERRIDES:
        return LICENSE_OVERRIDES[package_name]
    return (
        distribution.metadata.get("License-Expression")
        or distribution.metadata.get("License")
        or "See copied upstream licence files"
    ).replace("\n", " ").strip()


def project_url(distribution: metadata.Distribution) -> str:
    for entry in distribution.metadata.get_all("Project-URL") or ():
        label, separator, url = entry.partition(",")
        if separator and label.strip() in PROJECT_URL_LABELS:
            return url.strip()
    return distribution.metadata.get("Home-page") or ""


def is_notice_file(path: PurePath) -> bool:
    filename = path.name.casefold()
    text_suffix = path.suffix.casefold() in {"", ".md", ".rst", ".txt"}
    if not text_suffix:
        return False
    return (
        filename.startswith(("license", "licence", "copying", "notice", "authors"))
        or any(part.casefold() in {"license", "licenses"} for part in path.parts)
    )


def copy_package_notices(
    distribution: metadata.Distribution,
    package_directory: Path,
) -> list[str]:
    copied: list[str] = []
    used_destinations: set[str] = set()
    for relative_path in distribution.files or ():
        if not is_notice_file(relative_path):
            continue
        source = Path(distribution.locate_file(relative_path))
        if not source.is_file():
            continue
        destination_name = "__".join(relative_path.parts)
        if destination_name.casefold() in used_destinations:
            continue
        used_destinations.add(destination_name.casefold())
        destination = package_directory / destination_name
        shutil.copyfile(source, destination)
        copied.append(destination.name)
    return sorted(copied, key=str.casefold)


def copy_notice_overrides(package_directory: Path) -> list[str]:
    source_directory = OVERRIDE_ROOT / package_directory.name
    if not source_directory.is_dir():
        return []
    copied: list[str] = []
    for source in sorted(source_directory.iterdir()):
        if not source.is_file():
            continue
        shutil.copyfile(source, package_directory / source.name)
        copied.append(source.name)
    return copied


def write_declared_licence(
    distribution: metadata.Distribution,
    package_directory: Path,
) -> str:
    filename = "DECLARED-LICENCE.txt"
    metadata_values = [
        f"Package: {distribution.metadata['Name']}",
        f"Version: {distribution.version}",
        f"Declared licence: {licence_expression(distribution)}",
    ]
    author = distribution.metadata.get("Author")
    author_email = distribution.metadata.get("Author-email")
    if author:
        metadata_values.append(f"Author: {author}")
    if author_email:
        metadata_values.append(f"Author contact: {author_email}")
    metadata_values.extend(
        [
            "",
            "The installed wheel did not contain a separate licence text.",
            "This record preserves the licence declaration supplied in its",
            "installed package metadata.",
            "",
        ]
    )
    (package_directory / filename).write_text(
        "\n".join(metadata_values),
        encoding="utf-8",
    )
    return filename


def generate() -> list[dict[str, object]]:
    resolved_root = ROOT.resolve()
    resolved_licence_root = LICENSE_ROOT.resolve()
    if resolved_licence_root.parent.parent != resolved_root:
        raise RuntimeError("Refusing to replace a licence directory outside the project")
    LICENSE_ROOT.mkdir(parents=True, exist_ok=True)
    for child in LICENSE_ROOT.iterdir():
        if child.name in MANUALLY_MAINTAINED_DIRECTORIES:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    packages: dict[str, metadata.Distribution] = {}
    scopes: dict[str, set[str]] = defaultdict(set)
    for scope, root_names in ROOTS.items():
        for key, distribution in dependency_closure(root_names).items():
            packages[key] = distribution
            scopes[key].add(scope)

    records: list[dict[str, object]] = []
    for key, distribution in sorted(
        packages.items(),
        key=lambda item: item[1].metadata["Name"].casefold(),
    ):
        display_name = distribution.metadata["Name"]
        package_directory = (
            LICENSE_ROOT
            / f"{normalise(display_name)}-{distribution.version}"
        )
        package_directory.mkdir()
        copied = copy_package_notices(distribution, package_directory)
        copied.extend(copy_notice_overrides(package_directory))
        copied = sorted(set(copied), key=str.casefold)
        if not copied:
            copied = [write_declared_licence(distribution, package_directory)]
        records.append(
            {
                "name": display_name,
                "version": distribution.version,
                "scope": sorted(scopes[key]),
                "licence": licence_expression(distribution),
                "homepage": project_url(distribution),
                "directory": package_directory.name,
                "licence_files": copied,
            }
        )

    python_directory = LICENSE_ROOT / f"python-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_directory.mkdir()
    python_licence = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_licence.is_file():
        raise FileNotFoundError(f"Python licence not found: {python_licence}")
    shutil.copyfile(python_licence, python_directory / "LICENSE.txt")
    records.insert(
        0,
        {
            "name": "CPython",
            "version": (
                f"{sys.version_info.major}.{sys.version_info.minor}."
                f"{sys.version_info.micro}"
            ),
            "scope": ["runtime"],
            "licence": "PSF-2.0",
            "homepage": "https://www.python.org/",
            "directory": python_directory.name,
            "licence_files": ["LICENSE.txt"],
        },
    )
    return records


def write_outputs(records: list[dict[str, object]]) -> None:
    runtime_rows = [
        record for record in records if "runtime" in record["scope"]
    ]
    development_rows = [
        record for record in records if "runtime" not in record["scope"]
    ]

    def markdown_table(rows: list[dict[str, object]]) -> list[str]:
        result = [
            "| Component | Version | Licence | Purpose |",
            "|---|---:|---|---|",
        ]
        for row in rows:
            scopes = ", ".join(row["scope"])
            component = str(row["name"])
            if row["homepage"]:
                component = f"[{component}]({row['homepage']})"
            result.append(
                f"| {component} | {row['version']} | {row['licence']} | {scopes} |"
            )
        return result

    notice_lines = [
        "# Third-party software notices",
        "",
        "Generated from the FirePanel Commissioning Python environment.",
        "Versions are the versions used to build and verify version 0.1.0.",
        "",
        "Each component remains the property of its respective copyright",
        "holders and is provided under its own terms. Full upstream notices",
        "copied from installed distributions are in `LICENSES/third-party`.",
        "",
        "## Distributed runtime components",
        "",
        *markdown_table(runtime_rows),
        "",
        "## Build and test components",
        "",
        "These packages are used to build or verify the project and are not",
        "intended to be imported by the application at runtime.",
        "",
        *markdown_table(development_rows),
        "",
        "## Bundled icon fonts",
        "",
        "QtAwesome includes Font Awesome 5.15.4 and 6.7.2, Elusive Icons 2.0,",
        "Material Design Icons 5.9.55 and 6.9.96, Phosphor 1.3.0,",
        "Remix Icon 2.5.0, and Microsoft Codicons 0.0.36. Their separate",
        "licences and attribution notices are in",
        "`LICENSES/third-party/icon-fonts`.",
        "",
        "## Qt framework",
        "",
        "The PySide6 wheels contain Qt libraries. Qt and Qt for Python are",
        "available under LGPL/GPL terms or under a separately purchased Qt",
        "commercial licence. Some Qt modules have module-specific terms.",
        "The applicable GNU licence texts and Qt notice are in",
        "`LICENSES/third-party/qt-6.11.1`.",
        "",
        "## Updating this record",
        "",
        "Run the following command after changing dependencies:",
        "",
        "```powershell",
        r".\.venv\Scripts\python.exe tools\generate_third_party_notices.py",
        "```",
        "",
        "Review native libraries and bundled assets whenever the executable",
        "packaging configuration changes.",
        "",
    ]
    (ROOT / "THIRD_PARTY_NOTICES.md").write_text(
        "\n".join(notice_lines),
        encoding="utf-8",
    )
    (ROOT / "THIRD_PARTY_PACKAGES.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (ROOT / "LICENSES" / "README.md").write_text(
        "# Licence archive\n\n"
        "This directory contains verbatim upstream notices copied from the "
        "installed distributions used by FirePanel Commissioning. "
        "`THIRD_PARTY_PACKAGES.json` records the Python package-to-file "
        "mapping. The `icon-fonts` and `qt-6.11.1` directories are maintained "
        "separately because those components are embedded inside other "
        "packages.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    package_records = generate()
    write_outputs(package_records)
    print(f"Recorded {len(package_records)} Python runtime/build/test components.")
