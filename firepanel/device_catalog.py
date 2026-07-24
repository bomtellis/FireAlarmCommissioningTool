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

KNOWN_CATALOGUE_CODES = frozenset().union(*CATALOGUE_CODES_BY_PROTOCOL.values())


def protocols_for_code(product_code: int) -> tuple[str, ...]:
    """Return protocols whose supplied catalogue contains *product_code*."""
    return tuple(
        protocol
        for protocol, codes in CATALOGUE_CODES_BY_PROTOCOL.items()
        if product_code in codes
    )
