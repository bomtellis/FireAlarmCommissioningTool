from __future__ import annotations

"""Product-code evidence gathered from ConfigTool catalogue NCF files.

The integer stored in a PCF point record is not a globally unique model
identifier.  A code must not be assigned a manufacturer/model name until the
panel protocol field is decoded.  These sets let the importer distinguish a
code seen in a supplied catalogue from a completely unknown code without
inventing a label.
"""


CONFIRMED_GENERIC_TYPES: dict[int, str] = {
    # Confirmed by matching node 52 of Leighton-Site.NCF to zones.csv.
    6: "Call Point",
    33: "Relay",
    38: "Relay",
    40: "Sounder",
    45: "Optical Smoke",
    46: "Heat Detector",
}

# Exact Apollo picker labels matched to the sequential XP95 catalogue records
# in apollo.NCF and the official ConfigTool 7.68 resource table.  Codes reused
# by another protocol, or by more than one picker entry, are intentionally
# excluded.
CONFIRMED_PRODUCT_NAMES: dict[int, str] = {
    18: "Apollo XP95 Ionisation Smoke Detector",
    19: "Apollo XP95 Optical Smoke Detector",
    20: "Apollo XP95 Heat Detector",
    21: "Apollo XP95 High Temperature Heat Detector",
    23: "Apollo XP95 Reflective Beam Detector",
    24: "Apollo XP95 Flame Detector",
    25: "Apollo XP95 Multisensor",
    26: "Apollo XP95 High Output Loop Powered Sounder",
    28: "Apollo XP95 Switch Monitor",
    29: "Apollo XP95 Switch Monitor Plus",
    31: "Apollo XP95 Mini Switch Monitor (Interrupt)",
    36: "Apollo XP95 Radio Interface",
    96: "Apollo XP95 DIN Sounder Circuit Controller",
    386: "Apollo XP95 DIN Zone Monitor",
    387: "Apollo XP95 DIN Switch Monitor",
}


CATALOGUE_CODES_BY_PROTOCOL: dict[str, frozenset[int]] = {
    "Apollo": frozenset(
        {
            6, 11, 13, 14, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28,
            29, 30, 31, 33, 34, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45,
            46, 48, 49, 77, 96, 114, 154, 199, 200, 201, 202, 203, 204,
            205, 206, 213, 218, 221, 224, 255, 280, 319, 320, 330, 333,
            358, 360, 362, 366, 372, 385, 386, 387,
        }
    ),
    "Hochiki": frozenset(
        {
            6, 34, 40, 42, 56, 58, 60, 62, 64, 65, 66, 67, 68, 69, 70,
            71, 72, 73, 74, 77, 98, 119, 154, 163, 164, 185, 186, 256,
            302,
        }
    ),
    "Argus/Vega": frozenset(
        {
            13, 22, 27, 34, 35, 39, 42, 68, 77, 154, 213, 218, 221, 224,
            255, 256, 259, 279, 280, 281, 319, 358,
        }
    ),
    "Nittan": frozenset(
        {
            13, 14, 30, 33, 34, 65, 68, 150, 151, 153, 154, 183, 213,
            277, 283, 328, 342, 343,
        }
    ),
}

# Additional codes visible in the supplied ConfigTool export screenshots.  The
# screenshots prove catalogue membership but do not identify the protocol or
# model name, so they remain deliberately unassigned.
ADDITIONAL_OBSERVED_CODES = frozenset({4, 92})

KNOWN_CATALOGUE_CODES = (
    frozenset().union(*CATALOGUE_CODES_BY_PROTOCOL.values())
    | ADDITIONAL_OBSERVED_CODES
)


def protocols_for_code(product_code: int) -> tuple[str, ...]:
    """Return protocols whose supplied catalogue contains *product_code*."""
    return tuple(
        protocol
        for protocol, codes in CATALOGUE_CODES_BY_PROTOCOL.items()
        if product_code in codes
    )


def catalogue_display_name(product_code: int, observed_type: str | None = None) -> str:
    """Return an honest UI/export label without inventing a product model."""
    if observed_type:
        return observed_type
    if product_code in CONFIRMED_PRODUCT_NAMES:
        return CONFIRMED_PRODUCT_NAMES[product_code]
    protocols = protocols_for_code(product_code)
    if protocols:
        return f"{'/'.join(protocols)} catalogue code {product_code}"
    if product_code in ADDITIONAL_OBSERVED_CODES:
        return f"ConfigTool catalogue code {product_code}"
    return f"Uncatalogued product code {product_code}"
