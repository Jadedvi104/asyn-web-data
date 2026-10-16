"""Streaming normalized JSONL and CSV export."""

import csv
import json
from pathlib import Path

from webcollect.storage import Store

FIELDS = ("url", "canonical_url", "title", "description", "status", "fetched_at", "outbound_links")


def export_pages(store: Store, job_id: str, output: str | Path, format: str) -> int:
    if format not in {"jsonl", "csv"}:
        raise ValueError("Export format must be jsonl or csv")
    store.get_job(job_id)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS) if format == "csv" else None
        if writer:
            writer.writeheader()
        for page in store.pages(job_id):
            if writer:
                row = {name: page.get(name) for name in FIELDS}
                row["outbound_links"] = json.dumps(row["outbound_links"], ensure_ascii=False)
                writer.writerow(row)
            else:
                handle.write(json.dumps(page, ensure_ascii=False) + "\n")
            count += 1
    return count
