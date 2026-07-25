# SKF format notes

These notes describe the newer Advanced fire-panel configuration format
observed in `Leighton-Site-26-11-25 1pm.skf`.

## Container

SKF is a ZIP archive. Unlike the legacy NCF container, it has no `SITE` record
or per-panel binary PCF entries. It contains one FireDAC JSON document per
database table.

Each document has this shape:

```text
FDBS
  Version
  Manager
    TableList
      Name
      ColumnList
      RowList
        RowID
        Original
        Current (optional changed values)
```

The importer merges `Current` over `Original` when both are present.

## Imported tables

- `tblNode.json`: `NetworkAddress` and `NodeName` define panels and repeaters.
- `tblPoint.json`: addressable point channels. The stable identity remains
  node / positive loop / address / sub-address.
- `tblZone.json`: zone descriptions.
- `tblOutputGroup.json`: panel-scoped output-group names.
- `tblOutputGroupLine.json`: configured ringing-style references for groups.
- `tblRingingStyle.json`: panel-scoped ringing-style descriptions.

`tblPoint` also contains panel peripherals, LEDs and internal controls on
negative loop numbers. These are deliberately excluded because the application
models addressable loop devices.

## Supplied-file validation

The supplied SKF contains:

- FireDAC schema version 16;
- 61 network nodes;
- 7,927 addressable physical devices;
- 10,155 addressable point/sub-address records;
- 363 zones used by those points.

Node 52 remains `Main Entrance Panel 1`. Loop 1 address 1 remains zone 179,
`DUCT                ROOM 4`, and zone 179 remains `PATH LAB FIRST FLOOR`.
Node 52 loop 1 address 41 has three channels; channel 3 is assigned to output
group 50, `PATHOLOGY ACCESS DOORS`, using `Evacuate`.

## Boundary

The importer reads source configuration only. It does not rewrite SKF files.
Complete translation of `tblOutputGroupLine` and the other logic tables into
editable cause-and-effect rules remains future work.
