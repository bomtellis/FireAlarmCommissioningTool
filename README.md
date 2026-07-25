# FirePanel Commissioning

FirePanel Commissioning is a local-first Python desktop application for turning
Advanced MxPro network configuration files into a traceable commissioning
project. It supports both legacy ConfigTool `.NCF` files and newer `.SKF`
exports.

The application is licensed under the GNU Affero General Public License v3.0.

## Current capabilities

- Create a project from an NCF or SKF file and retain immutable configuration snapshots.
- Import an updated NCF or SKF and identify added, removed and modified point records.
- Preserve every SKF output-group line with its node/group assignment,
  `ZoneFrom`/`ZoneTo` trigger extent, operation, qualifiers, and resolved
  ringing-style number, name, and code. Panel-only outputs can therefore drive
  Test mode without an addressable loop device.
- Export a signed-review-style tracked changes PDF containing the complete
  project history and configuration/Cause & Effect differences from every
  selected revision, with an export-time revision picker, overall totals, page
  numbering, and device/output-group changes grouped first by revision and
  then under titled node sections.
- Show configuration changes with full node, zone, loop, address, sub-address,
  device text, device type, output group, and ringing-style context. The SKF
  `Location` field is presented consistently as `Device text`. Maintain a
  separate append-only Project History journal for ongoing drawing, placement,
  door, rule, output assignment, settings, import, and testing changes.
- Track SKF output-group additions, removals, names, node names, ringing
  styles, operations, zone-trigger extents, and qualifiers between revisions,
  and include them under the relevant node in the changes view and PDF.
- Browse naturally sorted nodes, loops, addresses, sub-addresses, zones and observed device types.
- View the installed application version, purpose, and licence on the About
  page in the main navigation.
- Use complete, action-specific icons throughout the Project and Commission
  ribbons, with a widened File menu that keeps action names and shortcuts
  clearly separated.
- Review zone numbers, descriptions, physical device counts and the nodes using
  each zone in a dedicated Zones view.
- Filter every table by right-clicking any column heading.
- Calculate separate quiescent and alarm current totals per node and estimate
  battery autonomy using editable project assumptions.
- Manage a separate architectural DXF underlay for every floor through one
  drawing-management dialog, and assign closed DXF polylines to zones.
- Render DXF geometry and text natively at full resolution, honour text
  rotation and available DXF fonts with installed-system fallbacks, preserve
  the current x/y view and zoom while changing floors, pan freely beyond the
  DXF extents, and middle-pan while drawing without cancelling the current
  polygon. Toggle the complete
  architectural underlay independently of commissioning overlays, and place imported
  detectors, call points, sounders, output devices, power supplies and panels
  directly on it.
- Toggle individual DXF layers; Ctrl-click to select multiple polygons; and
  use the polygon right-click menu to drag-edit points, assign/remove zones,
  copy selected zone polygons to the floor above, transform, or delete them.
  Deleted DXF-derived polygons remain suppressed until the floor underlay is
  replaced.
- Assign one zone across multiple floors while retaining a single polygon for
  that zone on each floor.
- Place rotatable single- or double-door sprites between two zones, configure
  access release and/or fire hold-open functions, link each function to a
  fire-alarm device immediately or later, and generate suggested output rules
  from the linked output groups. Doors wholly within one zone can record that
  same zone on both sides. Door sprites use fixed floor-plan dimensions
  and scale with the DXF as the view zooms. Control-device selectors provide
  contains-style typeahead, while architectural swing arcs and red/green
  padlocks show open/closed and locked/unlocked states. Placement preselects
  the two nearest zone polygons on the current floor for review.
- Select a placed symbol to inspect its node, zone, loop, address, sub-address
  and any decoded output-group relationships.
- Place devices zone-by-zone from an automatic queue: each drawing click
  positions the displayed current device, advances to the next unplaced
  device, and preserves the current drawing position and zoom.
- Represent every physical device once in the zone view, combining all of its
  sub-addresses into that marker, and drag a placed marker to save a revised
  position without selecting the zone polygon underneath.
- Select the floor shown in test mode and render its native architectural DXF
  beneath only that floor's zone polygons, doors and placed devices.
- Drag a complete door sprite, including its attached state icon, to save a
  revised position; test mode shows every door on the selected floor.
- Search the test-mode fire-zone selector by zone number or any part of its
  description using case-insensitive typeahead.
- Highlight placed devices whose node/output group is activated by the current
  fire simulation, retain the map zoom and centre while redrawing, and show a
  device status popup with its name and full address when clicked.
- Assign activated sounder and beacon output groups to the zones containing
  their configured devices. Test mode includes those zones, outlines them, and
  shows the ringing style in the zone tooltip, popup, and results table;
  evacuate sounders colour the zone red and alert sounders colour it yellow.
- Merge panel-only output groups from imported Cause & Effect/SKF data into the
  Output Groups view and explicitly assign each group to the sounder and/or
  beacon zones it serves. Beacon/VAD points retain a separate beacon symbol
  even when the panel configuration reports their generic type as `Sounder`.
- Show the same larger, fixed-screen device information card in the zone
  drawing view. The card keeps a readable minimum size while zooming out and
  grows with the background while zooming in; grouped devices list the
  configured name of every sub-address.
- Use conventional red-outline fire-alarm plan symbols, including an `O`
  optical detector and rectangular `I/O` interface, with scene-sized linework
  and labels that zoom at the same rate as the architectural underlay.
- Suggest same-floor and directly-above/below alert zones from drawing geometry.
- Add custom rules for doors straddling zones, output groups, HVAC, lifts and
  other ancillary interfaces, then explicitly edit a selected custom rule or
  Ctrl/Shift-select and remove any combination of custom, HTM, or door-control
  rules after confirmation.
- Import a Cause & Effect `.xlsx` matrix as zone-triggered output-group
  activations while preserving its ringing-style codes.
- Check matrix-derived activations against the workbook's `OutputGroupInfo`
  sheet, expose both directions of mismatch, and retain filterable engineer
  comments.
- Simulate a fire by zone for the whole site or an individual panel.
- Show door access as locked/unlocked and hold-open as open/closed, with
  explicit unlock and close checks in test mode.
- During a fire simulation, change each door function independently when its
  linked output device activates: access releases unlock and hold-open releases
  close the door.
- Visualise normal zones in green, the origin/evacuate zone in red and
  adjacent/alert zones in yellow.
- Export the device schedule to an Excel commissioning workbook.
- Choose fire-call zones for an output-group testing workbook with a searchable
  available-zones list and explicit left/right transfer controls, then import
  the workbook's controlled result fields back into test sessions.
- Include a filterable Zone List worksheet in every output-group testing
  workbook, showing zone names, nodes, floors, device counts, and highlighting
  the zones selected for that testing export.
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
