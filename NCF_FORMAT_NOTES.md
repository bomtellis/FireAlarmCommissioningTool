# Leighton-Site.NCF format notes

## Identification

`Leighton-Site.NCF` is an Advanced MxPro/ConfigTool network configuration.
It appears to use the legacy ConfigTool v7.68 format and also contains the
string `5.89`.
The outer file is a standard ZIP archive despite the `.NCF` extension.

The archive contains:

- one binary `SITE` entry describing the network;
- one `AppDefaults.ini` entry containing configuration defaults, service
  settings, and user/password data;
- 61 binary `.pcf` panel configuration files;
- 61 matching `.txt` note files. Most are empty. The non-empty examples are
  RTF documents despite their `.txt` extension.

Treat the NCF as sensitive because `AppDefaults.ini` contains plaintext access
settings.

## SITE entry

The first confirmed table in `SITE` begins at byte offset 112.

| Field | Observed location |
|---|---:|
| Node record size | 112 bytes |
| Record marker | byte `+8` = `0x12` |
| Panel-name length | byte `+9` |
| Panel name | ASCII at `+10`, up to 32 bytes |
| Network node number | byte `+44` |

There are 61 consecutive node records in this file. Node 52 is
`Main Entrance Panel 1`, which agrees with `zones.csv`.

The remaining bytes in each SITE record and the later SITE sections have not
yet been assigned semantic names. They should be retained unchanged if an
application ever writes NCF files.

## PCF point table

PCF files are binary panel configurations. Panels that have configured
loop-addressable points contain a consecutive fixed-size point table. The table
does not always begin at the same offset, so it should be found by its record
signature rather than a hard-coded address.

Known table offsets in this NCF include 29,456, 43,568, 72,240 and 83,664.

Each configured point/sub-point record is 224 bytes:

| Field | Offset | Size | Notes |
|---|---:|---:|---|
| Unknown/check value | `+0` | 4 | Varies per record |
| Record class | `+8` | 4 | Value `2` for the point records examined |
| Product/type code | `+12` | 4 | Confirmed mappings below |
| Channel/sub-point | `+16` | 1 | Normally 1; multi-I/O modules use 1, 2, 3, etc. |
| Address | `+17` | 1 | Physical loop address, 1–126 |
| Loop designation | `+18` | 1 | Site-level loop number; not necessarily 1–4 |
| Unknown flags | `+19` | 1 | Not yet decoded |
| Text length | `+20` | 1 | Length of the following ASCII label |
| Device/sub-point text | `+21` | 27 | Length-prefixed ASCII |
| Zone number | `+48` | 4 | Little-endian signed integer |
| Remaining settings | `+52` | 172 | Sensitivity, actions, groups, flags, etc.; not yet decoded |

The physical-device key is `(node, loop designation, address)`. Do not count
each 224-byte record as a separate device: multi-I/O modules have several
channel records at one address.

For node 52:

- 292 binary point/sub-point records collapse to 248 unique loop/address pairs;
- `zones.csv` contains exactly 248 device rows;
- the loop, address, zone, label, and device type align with the binary records.

Confirmed product-code mappings from that comparison:

| Product code | Exported device type |
|---:|---|
| 6 | Call Point |
| 33 | Relay |
| 38 | Relay |
| 40 | Sounder |
| 45 | Optical Smoke |
| 46 | Heat Detector |

Other product codes occur in the full network and still need authoritative
labels or additional ConfigTool exports.

## Protocol catalogue files

Four ConfigTool 7.68 catalogue configurations were added on 24 July 2026.
They contain blank device text, so they prove which numeric codes occur but do
not directly store the picker/model name. Static inspection of the official
ConfigTool resources confirms that the picker includes the corresponding
Apollo, Hochiki, Argus/Vega and Nittan product ranges.

| File | Catalogue | Physical devices | Point/sub-point records | Distinct codes |
|---|---|---:|---:|---:|
| `apollo.NCF` | Apollo | 109 | 142 | 63 |
| `hokiko.NCF` | Hochiki | 43 | 103 | 29 |
| `AV.NCF` | Argus/Vega | 109 | 111 | 22 |
| `nittan.NCF` | Nittan | 41 | 70 | 18 |

Together they contain 99 distinct numeric product codes, 93 more than the six
previously labelled codes. The audited sets are held in
`firepanel/device_catalog.py`.

Codes are not safe model identifiers on their own. For example, code `154`
occurs in all four protocol catalogues, while other codes occur in two or three.
The application therefore records catalogue membership separately and does not
invent a manufacturer/model label. Protocol-specific names should only be
enabled after the PCF panel-protocol field is decoded or after a ConfigTool
device export supplies an authoritative code/name join.

## Current network totals

The read-only scan finds:

- 61 configured network nodes / PCF files;
- 41 PCFs with addressable point tables;
- 7,777 unique physical `(node, loop, address)` devices;
- 9,965 point/sub-point records.

The other 20 PCFs are repeaters, BMS interfaces, empty panels, or similar
nodes with no detected addressable point table. They still contain panel,
network, cause-and-effect, password, and other configuration data.

## Inspector

`tools/inspect-ncf.ps1` is a read-only structural inspector.

Summary:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\inspect-ncf.ps1 .\Leighton-Site.NCF
```

Include physical devices and their individual channel/sub-point records:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\inspect-ncf.ps1 .\Leighton-Site.NCF -IncludePoints -JsonOutput .\ncf-points.json
```

The parser intentionally does not attempt to rewrite NCF/PCF files. Unknown
fields should not be modified until their checksums and semantics are understood.
