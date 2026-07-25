from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


MATRIX_SHEET = "Cause & Effect"
REFERENCE_SHEET = "OutputGroupInfo"
NODE_HEADER = re.compile(
    r"^\s*node\s+(\d+)\s*(?:[-–—]\s*)?(.*?)\s*$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class CauseEffectActivation:
    trigger_zone: str
    trigger_zone_name: str
    target_node: int
    target_node_name: str
    output_group: int
    output_group_name: str
    output_zone_name: str
    activation_code: str
    source_row: int
    source_column: str
    reference_status: str = "matrix_only"


@dataclass(slots=True)
class ReferenceActivation:
    target_node: int
    target_node_name: str
    output_group: int
    output_group_name: str
    trigger_zone: str
    output_zone_name: str
    activation_code: str
    source_row: int


@dataclass(slots=True)
class CauseEffectWorkbook:
    source: Path
    sha256: str
    activations: list[CauseEffectActivation]
    output_groups: list[CauseEffectOutputGroup]
    reference_count: int
    matched_count: int
    matrix_only_count: int
    reference_only_count: int
    activation_codes: list[str]
    reference_only: list[ReferenceActivation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reference_only_examples: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CauseEffectOutputGroup:
    index: int
    target_node: int
    target_node_name: str
    output_group: int
    output_group_name: str


def read_cause_effect_workbook(path: str | Path) -> CauseEffectWorkbook:
    source = Path(path).resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    workbook = load_workbook(
        source,
        read_only=True,
        data_only=True,
        keep_links=False,
    )
    try:
        matrix = workbook[_sheet_name(workbook.sheetnames, MATRIX_SHEET)]
        reference = workbook[_sheet_name(workbook.sheetnames, REFERENCE_SHEET)]
        activations, output_groups = _read_matrix(matrix)
        references = _read_reference(reference)
    finally:
        workbook.close()

    matched_count, reference_only, warnings = _check_reference(
        activations,
        references,
    )
    matrix_only_count = sum(
        activation.reference_status == "matrix_only" for activation in activations
    )
    if matrix_only_count:
        warnings.append(
            f"{matrix_only_count} matrix activations were not present in "
            f"{REFERENCE_SHEET}."
        )
    if reference_only:
        warnings.append(
            f"{len(reference_only)} {REFERENCE_SHEET} rows were not present in "
            f"{MATRIX_SHEET}."
        )

    activation_codes = sorted(
        {activation.activation_code for activation in activations}
    )
    return CauseEffectWorkbook(
        source=source,
        sha256=_sha256(source),
        activations=activations,
        output_groups=output_groups,
        reference_count=len(references),
        matched_count=matched_count,
        matrix_only_count=matrix_only_count,
        reference_only_count=len(reference_only),
        activation_codes=activation_codes,
        reference_only=reference_only,
        warnings=warnings,
        reference_only_examples=[
            _reference_label(item) for item in reference_only[:20]
        ],
    )


def _sheet_name(sheet_names: Iterable[str], expected: str) -> str:
    normalised_expected = _normalise_header(expected)
    for name in sheet_names:
        if _normalise_header(name) == normalised_expected:
            return name
    raise ValueError(f"Workbook is missing the required '{expected}' sheet.")


def _read_matrix(
    sheet,
) -> tuple[list[CauseEffectActivation], list[CauseEffectOutputGroup]]:
    rows = sheet.iter_rows(values_only=True)
    try:
        names = next(rows)
        node_headers = next(rows)
        group_numbers = next(rows)
    except StopIteration as error:
        raise ValueError(f"'{MATRIX_SHEET}' does not contain its three header rows.") from error

    output_columns = _output_columns(names, node_headers, group_numbers)
    if not output_columns:
        raise ValueError(
            f"'{MATRIX_SHEET}' did not contain any node/output-group columns."
    )

    activations: list[CauseEffectActivation] = []
    for source_row, row in enumerate(rows, start=4):
        trigger_zone = normalise_zone_key(_value(row, 4))
        if not trigger_zone:
            continue
        trigger_zone_name = _text(_value(row, 2))
        for output in output_columns:
            raw_code = _value(row, output.index)
            if raw_code in (None, ""):
                continue
            code = _activation_code(raw_code)
            if not code:
                continue
            activations.append(
                CauseEffectActivation(
                    trigger_zone=trigger_zone,
                    trigger_zone_name=trigger_zone_name,
                    target_node=output.target_node,
                    target_node_name=output.target_node_name,
                    output_group=output.output_group,
                    output_group_name=output.output_group_name,
                    output_zone_name="",
                    activation_code=code,
                    source_row=source_row,
                    source_column=get_column_letter(output.index + 1),
                )
            )

    if not activations:
        raise ValueError(f"'{MATRIX_SHEET}' did not contain any activations.")
    return activations, output_columns


def _output_columns(
    names: tuple[object, ...],
    node_headers: tuple[object, ...],
    group_numbers: tuple[object, ...],
) -> list[CauseEffectOutputGroup]:
    result: list[CauseEffectOutputGroup] = []
    target_node: int | None = None
    target_node_name = ""
    width = max(len(names), len(node_headers), len(group_numbers))
    for index in range(5, width):
        header = _text(_value(node_headers, index))
        if header:
            match = NODE_HEADER.match(header)
            if not match:
                target_node = None
                target_node_name = ""
                continue
            target_node = int(match.group(1))
            target_node_name = match.group(2).strip()

        output_group = _positive_integer(_value(group_numbers, index))
        output_group_name = _text(_value(names, index))
        if target_node is None or output_group is None or not output_group_name:
            continue
        result.append(
            CauseEffectOutputGroup(
                index=index,
                target_node=target_node,
                target_node_name=target_node_name,
                output_group=output_group,
                output_group_name=output_group_name,
            )
        )
    return result


def _read_reference(sheet) -> list[ReferenceActivation]:
    rows = sheet.iter_rows(values_only=True)
    try:
        raw_headers = next(rows)
    except StopIteration as error:
        raise ValueError(f"'{REFERENCE_SHEET}' is empty.") from error

    headers = {
        _normalise_header(value): index
        for index, value in enumerate(raw_headers)
        if value not in (None, "")
    }
    required = {
        "nodenumber",
        "nodename",
        "outputgroupnumber",
        "outputgroupname",
        "zonenumber",
        "zonename",
        "activation",
    }
    missing = sorted(required - headers.keys())
    if missing:
        raise ValueError(
            f"'{REFERENCE_SHEET}' is missing required columns: {', '.join(missing)}."
        )

    result: list[ReferenceActivation] = []
    for source_row, row in enumerate(rows, start=2):
        target_node = _positive_integer(_value(row, headers["nodenumber"]))
        output_group = _positive_integer(
            _value(row, headers["outputgroupnumber"])
        )
        trigger_zone = normalise_zone_key(_value(row, headers["zonenumber"]))
        code = _activation_code(_value(row, headers["activation"]))
        if (
            target_node is None
            or output_group is None
            or not trigger_zone
            or not code
        ):
            continue
        result.append(
            ReferenceActivation(
                target_node=target_node,
                target_node_name=_text(_value(row, headers["nodename"])),
                output_group=output_group,
                output_group_name=_text(
                    _value(row, headers["outputgroupname"])
                ),
                trigger_zone=trigger_zone,
                output_zone_name=_text(_value(row, headers["zonename"])),
                activation_code=code,
                source_row=source_row,
            )
        )
    return result


def _check_reference(
    activations: list[CauseEffectActivation],
    references: list[ReferenceActivation],
) -> tuple[int, list[ReferenceActivation], list[str]]:
    by_key: dict[tuple[int, int, str, str], deque[ReferenceActivation]] = (
        defaultdict(deque)
    )
    output_zone_names: dict[tuple[int, int], list[str]] = defaultdict(list)
    for reference in references:
        by_key[_activation_key(reference)].append(reference)
        if (
            reference.output_zone_name
            and reference.output_zone_name.casefold() not in {"n/a", "#value!"}
        ):
            output_zone_names[
                (reference.target_node, reference.output_group)
            ].append(reference.output_zone_name)

    matched = 0
    warnings: list[str] = []
    metadata_mismatches = 0
    for activation in activations:
        candidates = by_key.get(_activation_key(activation))
        if candidates:
            reference = candidates.popleft()
            activation.reference_status = "matched"
            activation.output_zone_name = reference.output_zone_name
            matched += 1
            if (
                _normalise_text(activation.target_node_name)
                != _normalise_text(reference.target_node_name)
                or _normalise_text(activation.output_group_name)
                != _normalise_text(reference.output_group_name)
            ):
                metadata_mismatches += 1
            continue
        fallback_names = output_zone_names.get(
            (activation.target_node, activation.output_group),
            [],
        )
        if fallback_names:
            activation.output_zone_name = fallback_names[0]

    reference_only = [
        reference
        for queue in by_key.values()
        for reference in queue
    ]
    if metadata_mismatches:
        warnings.append(
            f"{metadata_mismatches} matched rows had different node or output-group names."
        )
    return matched, reference_only, warnings


def _activation_key(
    item: CauseEffectActivation | ReferenceActivation,
) -> tuple[int, int, str, str]:
    return (
        item.target_node,
        item.output_group,
        item.trigger_zone.casefold(),
        item.activation_code.casefold(),
    )


def _reference_label(item: ReferenceActivation) -> str:
    return (
        f"Row {item.source_row}: zone {item.trigger_zone}, node "
        f"{item.target_node}, output group {item.output_group}, "
        f"activation {item.activation_code}"
    )


def _positive_integer(value: object) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if parsed != parsed.to_integral_value() or parsed <= 0:
        return None
    return int(parsed)


def normalise_zone_key(value: object) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
            if number == number.to_integral_value():
                return str(int(number))
            return format(number.normalize(), "f")
        except InvalidOperation:
            pass
    return _text(value)


def _activation_code(value: object) -> str:
    return _text(value).upper()


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _normalise_text(value: object) -> str:
    return " ".join(_text(value).casefold().split())


def _normalise_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).casefold())


def _value(row: tuple[object, ...], index: int) -> object | None:
    return row[index] if index < len(row) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
