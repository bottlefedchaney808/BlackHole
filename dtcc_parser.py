import csv
import io
import json
import logging
import zipfile

logger = logging.getLogger(__name__)


def _to_number(value):
    if value is None:
        return None
    stripped = value.replace(",", "").strip()
    if stripped == "":
        return None
    # DTCC/CFTC public dissemination caps notional at a reporting threshold and
    # flags capped values with a trailing "+" (e.g. "250000000+" means "at
    # least $250,000,000, true value undisclosed"). Strip it so the capped
    # amount still parses -- the "+" itself isn't stored, only the floor value.
    if stripped.endswith("+"):
        stripped = stripped[:-1]
    if stripped == "":
        return None
    return float(stripped)


def parse_swap_zip(zip_bytes: bytes, regulator: str, asset_class: str, source_file: str) -> list[dict]:
    records = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(
                f"Expected exactly one .csv member in zip, found {len(csv_names)}: {csv_names}"
            )
        csv_name = csv_names[0]

        with zf.open(csv_name) as raw_file:
            text_stream = io.TextIOWrapper(raw_file, encoding="utf-8")
            reader = csv.DictReader(text_stream)

            for row in reader:
                dissemination_id = (row.get("Dissemination Identifier") or "").strip()
                if not dissemination_id:
                    continue

                # A single malformed value (unexpected DTCC reporting convention,
                # truncated row, etc.) must not lose the rest of the day's file --
                # skip just that row and keep going.
                try:
                    record = {
                        "dissemination_id": dissemination_id,
                        "original_dissemination_id": row.get("Original Dissemination Identifier"),
                        "regulator": regulator,
                        "asset_class": asset_class,
                        "action_type": row.get("Action type"),
                        "event_type": row.get("Event type"),
                        "event_timestamp": row.get("Event timestamp"),
                        "execution_timestamp": row.get("Execution Timestamp"),
                        "effective_date": row.get("Effective Date"),
                        "expiration_date": row.get("Expiration Date"),
                        "cleared": row.get("Cleared"),
                        "notional_amount_leg1": _to_number(row.get("Notional amount-Leg 1")),
                        "notional_currency_leg1": row.get("Notional currency-Leg 1"),
                        "notional_amount_leg2": _to_number(row.get("Notional amount-Leg 2")),
                        "notional_currency_leg2": row.get("Notional currency-Leg 2"),
                        "price": _to_number(row.get("Price")),
                        "price_currency": row.get("Price currency"),
                        "price_unit_of_measure": row.get("Price unit of measure"),
                        "underlier_id_leg1": row.get("Underlier ID-Leg 1"),
                        "underlier_id_source_leg1": row.get("Underlier ID source-Leg 1"),
                        "underlying_asset_name": row.get("Underlying Asset Name"),
                        "upi": row.get("Unique Product Identifier"),
                        "upi_fisn": row.get("UPI FISN"),
                        "upi_underlier_name": row.get("UPI Underlier Name"),
                        "source_file": source_file,
                        "raw_json": json.dumps(row),
                    }
                except (ValueError, TypeError) as e:
                    logger.warning(
                        "Skipping unparsable row (dissemination_id=%s) in %s: %s",
                        dissemination_id, source_file, e,
                    )
                    continue

                records.append(record)

    return records
