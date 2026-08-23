#!/usr/bin/env python3
"""Build a deterministic four-format process sample from one matched USLCI release."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

UUID_RE = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def bytes_sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def uuid_from_member(name: str) -> str | None:
    stem = Path(name).stem.lower()
    return stem if UUID_RE.fullmatch(stem) else None


def zip_process_index(path: Path, kind: str) -> dict[str, zipfile.ZipInfo]:
    with zipfile.ZipFile(path) as zf:
        out: dict[str, zipfile.ZipInfo] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            normalized = info.filename.replace("\\", "/")
            lower = normalized.lower()
            keep = False
            if kind == "jsonld":
                keep = lower.startswith("processes/") and lower.endswith(".json")
            elif kind == "ecospold2":
                keep = lower.startswith("activities/") and lower.endswith(".spold")
            elif kind == "ilcd":
                keep = lower.startswith("ilcd/processes/") and lower.endswith(".xml")
            else:
                raise ValueError(f"unknown kind: {kind}")
            if not keep:
                continue
            uid = uuid_from_member(normalized)
            if uid:
                out[uid] = info
        return out


def choose_even_quantiles(uuids: list[str], sizes: dict[str, int], n: int) -> list[str]:
    ordered = sorted(uuids, key=lambda u: (sizes[u], u))
    if n >= len(ordered):
        return ordered
    selected: list[str] = []
    for i in range(n):
        # Midpoint of n equal-probability bins; deterministic and size-stratified.
        idx = min(len(ordered) - 1, int(((i + 0.5) * len(ordered)) / n))
        uid = ordered[idx]
        if uid not in selected:
            selected.append(uid)
    # Guard against any duplicated integer bins.
    for uid in ordered:
        if len(selected) >= n:
            break
        if uid not in selected:
            selected.append(uid)
    return selected


@dataclass(frozen=True)
class CaseRow:
    case_index: int
    process_uuid: str
    jsonld_member: str
    ecospold2_member: str
    ilcd_member: str
    tidas_path: str
    jsonld_bytes: int
    ecospold2_bytes: int
    ilcd_bytes: int
    tidas_bytes: int
    jsonld_sha256: str
    ecospold2_sha256: str
    ilcd_sha256: str
    tidas_sha256: str


def write_blob(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonld", required=True, type=Path)
    ap.add_argument("--ecospold2", required=True, type=Path)
    ap.add_argument("--ilcd", required=True, type=Path)
    ap.add_argument("--tidas-dir", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()

    if args.n <= 0:
        raise SystemExit("--n must be positive")
    for path in (args.jsonld, args.ecospold2, args.ilcd):
        if not zipfile.is_zipfile(path):
            raise SystemExit(f"not a ZIP archive: {path}")

    indexes = {
        "jsonld": zip_process_index(args.jsonld, "jsonld"),
        "ecospold2": zip_process_index(args.ecospold2, "ecospold2"),
        "ilcd": zip_process_index(args.ilcd, "ilcd"),
    }
    tidas_index = {
        p.stem.lower(): p
        for p in args.tidas_dir.rglob("*.json")
        if "process" in "/".join(part.lower() for part in p.parts) and UUID_RE.fullmatch(p.stem.lower())
    }
    common = sorted(set(indexes["jsonld"]) & set(indexes["ecospold2"]) & set(indexes["ilcd"]) & set(tidas_index))
    if len(common) < args.n:
        raise SystemExit(f"only {len(common)} matched process UUIDs; requested {args.n}")

    jsonld_sizes = {u: indexes["jsonld"][u].file_size for u in common}
    selected = choose_even_quantiles(common, jsonld_sizes, args.n)

    if args.output.exists():
        shutil.rmtree(args.output)
    cases_dir = args.output / "cases"
    evidence_dir = args.output / "evidence"
    cases_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)

    rows: list[CaseRow] = []
    with zipfile.ZipFile(args.jsonld) as z_json, zipfile.ZipFile(args.ecospold2) as z_eco, zipfile.ZipFile(args.ilcd) as z_ilcd:
        for case_index, uid in enumerate(selected, start=1):
            blobs = {
                "jsonld": z_json.read(indexes["jsonld"][uid]),
                "ecospold2": z_eco.read(indexes["ecospold2"][uid]),
                "ilcd": z_ilcd.read(indexes["ilcd"][uid]),
                "tidas": tidas_index[uid].read_bytes(),
            }
            case = cases_dir / uid
            write_blob(case / "openlca_jsonld.json", blobs["jsonld"])
            write_blob(case / "ecospold2.spold", blobs["ecospold2"])
            write_blob(case / "ilcd.xml", blobs["ilcd"])
            write_blob(case / "tidas.json", blobs["tidas"])
            rows.append(
                CaseRow(
                    case_index=case_index,
                    process_uuid=uid,
                    jsonld_member=indexes["jsonld"][uid].filename,
                    ecospold2_member=indexes["ecospold2"][uid].filename,
                    ilcd_member=indexes["ilcd"][uid].filename,
                    tidas_path=str(tidas_index[uid]),
                    jsonld_bytes=len(blobs["jsonld"]),
                    ecospold2_bytes=len(blobs["ecospold2"]),
                    ilcd_bytes=len(blobs["ilcd"]),
                    tidas_bytes=len(blobs["tidas"]),
                    jsonld_sha256=bytes_sha256(blobs["jsonld"]),
                    ecospold2_sha256=bytes_sha256(blobs["ecospold2"]),
                    ilcd_sha256=bytes_sha256(blobs["ilcd"]),
                    tidas_sha256=bytes_sha256(blobs["tidas"]),
                )
            )

    with (args.output / "manifest.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)

    metadata = {
        "schema_version": "lci-format-benchmark.sample-manifest.v1",
        "selection_method": "30 evenly spaced quantile midpoints of openLCA JSON-LD process-file byte size after exact UUID intersection",
        "requested_n": args.n,
        "selected_n": len(rows),
        "available_counts": {k: len(v) for k, v in indexes.items()} | {"tidas": len(tidas_index), "four_way_intersection": len(common)},
        "source_archives": {
            "jsonld": {"path": str(args.jsonld), "bytes": args.jsonld.stat().st_size, "sha256": file_sha256(args.jsonld)},
            "ecospold2": {"path": str(args.ecospold2), "bytes": args.ecospold2.stat().st_size, "sha256": file_sha256(args.ecospold2)},
            "ilcd": {"path": str(args.ilcd), "bytes": args.ilcd.stat().st_size, "sha256": file_sha256(args.ilcd)},
        },
        "selected_uuids": selected,
    }
    (args.output / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    # Copy locked schema and methodology evidence from the generated TIDAS package.
    for relative in (
        "schemas/tidas_processes.json",
        "schemas/tidas_data_types.json",
        "schemas/tidas_processes_category.json",
        "methodologies/runtime_rulesets.json",
    ):
        src = args.tidas_dir.parent / relative if args.tidas_dir.name == "data" else args.tidas_dir / relative
        if src.exists():
            destination = evidence_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, destination)

    checksum_lines = []
    for path in sorted(p for p in args.output.rglob("*") if p.is_file()):
        if path.name == "all_files_sha256.txt":
            continue
        checksum_lines.append(f"{file_sha256(path)}  {path.relative_to(args.output).as_posix()}")
    (args.output / "all_files_sha256.txt").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )

    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
