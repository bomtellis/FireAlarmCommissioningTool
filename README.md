# FirePanel Commissioning

FirePanel Commissioning is a local-first Python desktop application for turning
Advanced MxPro network configuration files into a traceable commissioning
project. It supports both legacy ConfigTool `.NCF` files and newer `.SKF`
exports.

The application is licensed under the GNU Affero General Public License v3.0.

## Current capabilities

- Create a project from an NCF or SKF file and retain immutable configuration snapshots.
- Import an updated NCF or SKF and identify added, removed and modified point records.
- Export a signed-review-style tracked changes PDF.
- Browse naturally sorted nodes, loops, addresses, sub-addresses, zones and observed device types.
- Review zone numbers, descriptions and physical device counts in a dedicated Zones view.
- Filter every table by right-clicking any column heading.
- Calculate separate quiescent and alarm current totals per node and estimate
  battery autonomy using editable project assumptions.
- Import closed DXF polylines, create floors and assign shapes to zones.
- Render the DXF as a persistent floor-plan underlay and place imported
  detectors, call points, sounders, output devices, power supplies and panels
  directly on it.
- Display DXF text, toggle drawing layers, and create or edit zone polygons by
  moving, rotating, realigning, reassigning or unassigning them.
- Select a placed symbol to inspect its node, zone, loop, address, sub-address
  and any decoded output-group relationships.
- Suggest same-floor and directly-above/below alert zones from drawing geometry.
- Add custom rules for doors straddling zones, output groups, HVAC, lifts and
  other ancillary interfaces.
- Import a Cause & Effect `.xlsx` matrix as zone-triggered output-group
  activations while preserving its ringing-style codes.
- Check matrix-derived activations against the workbook's `OutputGroupInfo`
  sheet, expose both directions of mismatch, and retain filterable engineer
  comments.
- Simulate a fire by zone for the whole site or an individual panel.
- Visualise normal zones in green, the origin/evacuate zone in red and
  adjacent/alert zones in yellow.
- Export the device schedule to an Excel commissioning workbook.
- Export selected or all fire-call zones to an output-group testing workbook,
  with controlled result fields that can be imported back into test sessions.
- Export configuration/imported Cause & Effect matrices and their differences
  as separate sheets in one comparison workbook.

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

A project is a single SQLite database using the `.fcp` extension. Configuration
snapshots, panels and devices are stored alongside project-owned drawings,
geometry, rules, power assumptions and commissioning results.

The original NCF or SKF is never modified. Re-importing an identical file is
detected by SHA-256 and does not create a duplicate snapshot.

## Known parser boundary

The read-only parser detects the format from archive contents rather than the
filename:

- Legacy NCF: imports the SITE node table and 224-byte PCF loop-point records.
- Newer SKF: imports FireDAC JSON node, point, zone, output-group and ringing-style tables.

Native point output-group assignments and ringing styles are imported from both
formats. The application does not translate the complete cause-and-effect
program embedded in an NCF or SKF into editable project rules; an approved
Cause & Effect Excel matrix can instead be imported and checked against its
`OutputGroupInfo` tab.

See [NCF_FORMAT_NOTES.md](NCF_FORMAT_NOTES.md) and
[SKF_FORMAT_NOTES.md](SKF_FORMAT_NOTES.md) for the observed layouts.
