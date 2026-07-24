from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .project import ProjectRepository


def export_change_pdf(
    repository: ProjectRepository,
    target: str | Path,
    snapshot_id: int | None = None,
) -> Path:
    destination = Path(target)
    snapshot_id = snapshot_id or repository.latest_snapshot_id()
    changes = repository.fetch_changes(snapshot_id)
    snapshots = {row["id"]: row for row in repository.fetch_snapshots()}
    snapshot = snapshots.get(snapshot_id)
    styles = getSampleStyleSheet()

    document = SimpleDocTemplate(
        str(destination),
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=13 * mm,
        bottomMargin=13 * mm,
        title=f"{repository.name} — NCF tracked changes",
        author="FirePanel",
    )
    story = [
        Paragraph(f"{repository.name} — NCF tracked changes", styles["Title"]),
        Spacer(1, 4 * mm),
        Paragraph(
            f"Snapshot: {snapshot['source_name'] if snapshot else snapshot_id}<br/>"
            f"Imported: {snapshot['imported_at'] if snapshot else 'Unknown'}<br/>"
            f"Generated: {datetime.now().isoformat(timespec='minutes')}",
            styles["Normal"],
        ),
        Spacer(1, 5 * mm),
    ]
    if not changes:
        story.append(Paragraph("No changes were recorded for this snapshot.", styles["Heading2"]))
    else:
        rows = [["Type", "Entity", "Key", "Field", "Previous", "Current"]]
        rows.extend(
            [
                [
                    row["change_type"].upper(),
                    row["entity"],
                    row["stable_key"],
                    row["field"] or "",
                    row["old_value"] or "",
                    row["new_value"] or "",
                ]
                for row in changes
            ]
        )
        table = Table(rows, repeatRows=1, colWidths=[24 * mm, 22 * mm, 34 * mm, 25 * mm, 69 * mm, 69 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#183153")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)
        story.extend(
            [
                PageBreak(),
                Paragraph("Review and approval", styles["Heading1"]),
                Paragraph(
                    "This report identifies configuration differences. It does not by itself approve "
                    "the fire strategy or demonstrate successful cause-and-effect testing.",
                    styles["Normal"],
                ),
                Spacer(1, 12 * mm),
                Paragraph("Reviewed by: ____________________________________", styles["Normal"]),
                Spacer(1, 8 * mm),
                Paragraph("Role: ___________________________________________", styles["Normal"]),
                Spacer(1, 8 * mm),
                Paragraph("Date: ___________________________________________", styles["Normal"]),
            ]
        )
    document.build(story)
    return destination


def export_devices_xlsx(repository: ProjectRepository, target: str | Path) -> Path:
    destination = Path(target)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Devices"
    headers = [
        "Node",
        "Panel",
        "Loop",
        "Address",
        "Sub Address",
        "Zone",
        "Device Text",
        "Device Type",
        "Product Code",
        "Output Group",
        "Test Result",
        "Comments",
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="183153")
        cell.alignment = Alignment(horizontal="center")

    for row in repository.fetch_devices():
        sheet.append(
            [
                row["node"],
                row["panel"],
                row["loop"],
                row["address"],
                row["sub_address"],
                row["zone"],
                row["text"],
                row["observed_type"] or f"Product {row['product_code']}",
                row["product_code"],
                row["output_group"],
                "",
                "",
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [9, 34, 9, 10, 13, 10, 38, 20, 14, 15, 15, 40]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[chr(64 + index)].width = width
    workbook.save(destination)
    return destination
