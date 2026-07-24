# FirePanel Commissioning

FirePanel Commissioning is a local-first Python desktop application for turning
Advanced MxPro network configuration (`.NCF`) files into a traceable
commissioning project.

The application is licensed under the GNU Affero General Public License v3.0.

## Current capabilities

- Create a project from an NCF and retain immutable configuration snapshots.
- Import an updated NCF and identify added, removed and modified point records.
- Export a signed-review-style tracked changes PDF.
- Browse nodes, loops, addresses, sub-addresses, zones and observed device types.
- Estimate loop current and battery autonomy using editable project assumptions.
- Import closed DXF polylines, create floors and assign shapes to zones.
- Render the DXF as a persistent floor-plan underlay and place imported
  detectors, call points, sounders, output devices, power supplies and panels
  directly on it.
- Select a placed symbol to inspect its node, zone, loop, address, sub-address
  and any decoded output-group relationships.
- Suggest same-floor and directly-above/below alert zones from drawing geometry.
- Add custom rules for doors straddling zones, output groups, HVAC, lifts and
  other ancillary interfaces.
- Simulate a fire by zone for the whole site or an individual panel.
- Visualise normal zones in green, the origin/evacuate zone in red and
  adjacent/alert zones in yellow.
- Export the device schedule to an Excel commissioning workbook.

## Firecode basis

The suggestion engine follows the topology illustrated by Figure 2,
"Alert and evacuate audibility matrix", in NHS England HTM 05-03 Part B
(2024):

- the originating alarm zone receives `EVACUATE / continuous`;
- adjoining zones receive `ALERT / intermittent`;
- adjoining is interpreted as cardinal adjacency on the same floor and direct
  overlap on the floor above or below.

The rules are deliberately labelled as suggestions. HTM 05-03 requires the
actual cause-and-effect strategy to be agreed with project stakeholders and
requires integrated cause-and-effect systems to be 100% tested. A site fire
strategy and competent-person review take precedence over generated rules.

Official sources:

- [HTM 05-03 Part B (2024)](https://www.england.nhs.uk/wp-content/uploads/2008/08/Health-technical-memorandum-05-03-operational-provisions-part-B-fire-detection-and-alarm-systems-including-the.pdf)
- [HTM 05-02 (2015)](https://www.england.nhs.uk/wp-content/uploads/2021/05/HTM_05-02_2015.pdf)

## Install and run

Python 3.11 or later is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

## Project format

A project is a single SQLite database using the `.fcp` extension. NCF snapshots,
panels and devices are stored alongside project-owned drawings, geometry, rules,
power assumptions and commissioning results.

The original NCF is never modified. Re-importing an identical file is detected
by SHA-256 and does not create a duplicate snapshot.

## Known parser boundary

The current read-only parser reliably imports the SITE node table and the
224-byte loop point records validated against the supplied node 52 ConfigTool
export.

The proprietary fields for native output-group assignment and the original
ConfigTool cause-and-effect program have not yet been decoded. Output groups
and rules are therefore nullable/editable project data in this first version.
The application does not claim that generated rules reproduce the source NCF
cause-and-effect until those sections are decoded and validated.

See [NCF_FORMAT_NOTES.md](NCF_FORMAT_NOTES.md) for the observed binary layout.
