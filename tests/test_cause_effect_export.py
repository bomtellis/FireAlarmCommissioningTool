import json
import zipfile
from pathlib import Path

from openpyxl import load_workbook

from firepanel.exports import export_cause_effect_comparison_xlsx


def _write_table(
    archive: zipfile.ZipFile,
    name: str,
    rows: list[dict],
) -> None:
    table_name = Path(name).stem
    archive.writestr(
        name,
        json.dumps(
            {
                "FDBS": {
                    "Version": 16,
                    "Manager": {
                        "TableList": [
                            {
                                "Name": table_name,
                                "RowList": [
                                    {"RowID": index, "Original": row}
                                    for index, row in enumerate(rows)
                                ],
                            }
                        ]
                    },
                }
            }
        ),
    )


def _configuration(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        _write_table(
            archive,
            "tblNode.json",
            [{"NetworkAddress": 7, "NodeName": "NHP Cabin"}],
        )
        _write_table(
            archive,
            "tblOutputGroup.json",
            [
                {"NetworkAddress": 7, "GroupNo": 1, "GroupText": "Sounders"},
                {"NetworkAddress": 7, "GroupNo": 2, "GroupText": "Beacons"},
                {"NetworkAddress": 7, "GroupNo": 3, "GroupText": "Doors"},
            ],
        )
        _write_table(
            archive,
            "tblRingingStyle.json",
            [
                {
                    "NetworkAddress": 7,
                    "StyleNumber": 0,
                    "Description": "Evacuate",
                },
                {
                    "NetworkAddress": 7,
                    "StyleNumber": 2,
                    "Description": "5 Minute Timed Alert",
                },
            ],
        )
        _write_table(
            archive,
            "tblOutputGroupLine.json",
            [
                {
                    "NetworkAddress": 7,
                    "GroupNo": 1,
                    "Operation": 0,
                    "OutputStyleNo": 0,
                    "ZoneFrom": 10,
                    "ZoneTo": 10,
                },
                {
                    "NetworkAddress": 7,
                    "GroupNo": 2,
                    "Operation": 0,
                    "OutputStyleNo": 2,
                    "ZoneFrom": 10,
                    "ZoneTo": 10,
                },
                {
                    "NetworkAddress": 7,
                    "GroupNo": 3,
                    "Operation": 0,
                    "OutputStyleNo": 0,
                    "ZoneFrom": 10,
                    "ZoneTo": 10,
                },
            ],
        )


class _Repository:
    name = "Export test"

    def __init__(self, configuration: Path) -> None:
        self.configuration = configuration

    def fetch_snapshots(self):
        return [
            {
                "source_path": str(self.configuration),
                "source_name": self.configuration.name,
            }
        ]

    def fetch_zones(self):
        return [{"number": 10, "description": "Ground floor"}]

    def fetch_cause_effect_output_groups(self):
        return [
            {
                "target_node": 7,
                "target_node_name": "NHP Cabin",
                "output_group": group,
                "output_group_name": name,
            }
            for group, name in ((1, "Sounders"), (2, "Beacons"), (4, "Access"))
        ]

    def fetch_cause_effect_activations(self):
        return [
            {
                "trigger_zone": "10",
                "trigger_zone_name": "Ground floor",
                "target_node": 7,
                "target_node_name": "NHP Cabin",
                "output_group": group,
                "output_group_name": name,
                "ringing_style": style,
                "reference_status": "Matched",
                "comments": "",
            }
            for group, name, style in (
                (1, "Sounders", "E"),
                (2, "Beacons", "E"),
                (4, "Access", "E"),
            )
        ]

    def latest_cause_effect_import(self):
        return {"source_path": "imported-matrix.xlsx"}

    def fetch_output_groups(self):
        return []


def test_comparison_export_has_three_frozen_sheets_and_all_difference_types(
    tmp_path: Path,
) -> None:
    configuration = tmp_path / "site.skf"
    _configuration(configuration)
    destination = tmp_path / "comparison.xlsx"

    export_cause_effect_comparison_xlsx(
        _Repository(configuration),
        destination,
    )

    workbook = load_workbook(destination, data_only=False)
    assert workbook.sheetnames == [
        "NCF-SKF Matrix",
        "Imported Matrix",
        "Differences",
    ]
    assert workbook["NCF-SKF Matrix"].freeze_panes == "C5"
    assert workbook["Imported Matrix"].freeze_panes == "C5"
    assert workbook["Differences"].freeze_panes == "A5"

    differences = workbook["Differences"]
    assert differences["A2"].value == "1 matched links; 3 differences."
    statuses = {
        differences.cell(row=row, column=1).value
        for row in range(5, differences.max_row + 1)
    }
    assert statuses == {
        "Different ringing style",
        "Configuration only",
        "Imported only",
    }
