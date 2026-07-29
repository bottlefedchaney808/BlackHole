"""Incremental ingestion pass against DTCC's live slice feed."""
import json
import time
from datetime import date

from dtcc_api_client import list_live_slices, download_zip
from dtcc_parser import parse_swap_zip
from db_loader import SwapsLoader

TARGETS = [("SEC", "EQ"), ("CFTC", "EQ")]


def run(targets=TARGETS) -> dict:
    summary = {}

    for regulator, asset in targets:
        start_time = time.time()
        key = f"{regulator}/{asset}"
        loader = SwapsLoader()

        try:
            state = loader.get_state(regulator, asset)
            last_id = (state['last_live_slice_id'] or 0) if state else 0

            slices = list_live_slices(regulator, asset)
            new_slices = [s for s in slices if s['sliceId'] > last_id]
            new_slices.sort(key=lambda s: s['sliceId'])

            total_records = 0
            max_slice_id_seen = last_id

            for entry in new_slices:
                zip_bytes = download_zip(entry['fullFilePath'])
                records = parse_swap_zip(
                    zip_bytes, regulator, asset, source_file=entry['fileName']
                )
                loader.upsert_trades(records)
                total_records += len(records)
                if entry['sliceId'] > max_slice_id_seen:
                    max_slice_id_seen = entry['sliceId']

            if new_slices:
                loader.set_state(regulator, asset, last_live_slice_id=max_slice_id_seen)

            loader.log_scrape(
                scrape_date=date.today(),
                fetched=len(new_slices),
                inserted=total_records,
                updated=0,
                errors=0,
                status='success',
                duration=int(time.time() - start_time),
            )

            summary[key] = {'new_slices': len(new_slices), 'records': total_records}

        except Exception as e:
            loader.log_scrape(
                scrape_date=date.today(),
                fetched=0,
                inserted=0,
                updated=0,
                errors=1,
                status='failed',
                error_msg=str(e),
                duration=int(time.time() - start_time),
            )
            summary[key] = {'new_slices': 0, 'records': 0}
            continue

    return summary


if __name__ == '__main__':
    result = run()
    print(json.dumps(result))
