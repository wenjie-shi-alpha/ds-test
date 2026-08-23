#!/usr/bin/env python3
"""Add auditable compatibility annotations required by the strict TIDAS importer.

The FY19.Q2.01 openLCA JSON-LD archive predates the TIDAS flow-name
components `treatmentStandardsRoutes` and `mixAndLocationTypes`.  The TIDAS
v0.1.3/v0.2.0 importer refuses Product, Waste, and Other flows when those
fields are absent.  This script does not invent process quantities.  It adds
explicit compatibility annotations derived only from source fields and writes
an exact modification manifest.

These annotations are excluded from benchmark questions so they cannot create
an information advantage for TIDAS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "code", "@id"):
            x = value.get(key)
            if isinstance(x, str) and x.strip():
                return x.strip()
    return None


def route_annotation(obj: dict[str, Any]) -> str:
    """Return an auditable source-derived annotation, not an inferred route."""
    name = source_text(obj.get("name")) or "unnamed flow"
    description = source_text(obj.get("description"))
    if description:
        compact = re.sub(r"\s+", " ", description)[:180]
        return f"Source does not expose a separate route field; flow label={name}; source description={compact}"
    return f"Source does not expose a separate route field; flow label={name}"


def mix_location_annotation(obj: dict[str, Any]) -> str:
    parts: list[str] = []
    location = source_text(obj.get("location"))
    if location:
        parts.append(f"location={location}")
    category = obj.get("category")
    if isinstance(category, dict):
        path = category.get("categoryPath")
        if isinstance(path, list):
            vals = [str(x).strip() for x in path if str(x).strip()]
            if vals:
                parts.append("category=" + " > ".join(vals))
        cat_name = source_text(category.get("name"))
        if cat_name:
            parts.append(f"category_leaf={cat_name}")
    category_path = obj.get("categoryPath")
    if isinstance(category_path, list):
        vals = [str(x).strip() for x in category_path if str(x).strip()]
        if vals:
            parts.append("category=" + " > ".join(vals))
    if not parts:
        parts.append("source does not expose a separate mix/location field")
    return "; ".join(parts)


def flow_property_annotation(obj: dict[str, Any]) -> str | None:
    unit = source_text(obj.get("refUnit"))
    factors = obj.get("flowProperties")
    names: list[str] = []
    if isinstance(factors, list):
        for factor in factors:
            if not isinstance(factor, dict):
                continue
            prop = factor.get("flowProperty")
            name = source_text(prop)
            if name:
                names.append(name)
    vals = []
    if names:
        vals.append("properties=" + ", ".join(dict.fromkeys(names)))
    if unit:
        vals.append(f"reference_unit={unit}")
    return "; ".join(vals) if vals else None


def patch_flow(obj: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    flow_type = str(obj.get("flowType", "")).upper()
    if flow_type == "ELEMENTARY_FLOW":
        return obj, []
    if flow_type not in {"PRODUCT_FLOW", "WASTE_FLOW", "OTHER_FLOW"}:
        return obj, []

    patched = dict(obj)
    existing = patched.get("flowName")
    flow_name = dict(existing) if isinstance(existing, dict) else {}
    changed: list[str] = []
    if not source_text(flow_name.get("treatmentStandardsRoutes")):
        flow_name["treatmentStandardsRoutes"] = route_annotation(obj)
        changed.append("flowName.treatmentStandardsRoutes")
    if not source_text(flow_name.get("mixAndLocationTypes")):
        flow_name["mixAndLocationTypes"] = mix_location_annotation(obj)
        changed.append("flowName.mixAndLocationTypes")
    if not source_text(flow_name.get("flowProperties")):
        value = flow_property_annotation(obj)
        if value:
            flow_name["flowProperties"] = value
            changed.append("flowName.flowProperties")
    if changed:
        patched["flowName"] = flow_name
    return patched, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_zip", type=Path)
    parser.add_argument("output_zip", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    args.output_zip.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)

    modifications: list[dict[str, Any]] = []
    flow_types: Counter[str] = Counter()
    with zipfile.ZipFile(args.input_zip) as zin, zipfile.ZipFile(
        args.output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            out_data = data
            if not info.is_dir() and info.filename.startswith("flows/") and info.filename.endswith(".json"):
                obj = json.loads(data.decode("utf-8-sig"))
                if isinstance(obj, dict):
                    flow_types[str(obj.get("flowType", "<missing>"))] += 1
                    patched, changed = patch_flow(obj)
                    if changed:
                        out_data = json.dumps(
                            patched,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=False,
                        ).encode("utf-8")
                        modifications.append(
                            {
                                "path": info.filename,
                                "flow_id": obj.get("@id"),
                                "flow_type": obj.get("flowType"),
                                "source_sha256": sha256_bytes(data),
                                "patched_sha256": sha256_bytes(out_data),
                                "fields_added": changed,
                                "source_name": obj.get("name"),
                            }
                        )
            new_info = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            new_info.compress_type = zipfile.ZIP_DEFLATED
            new_info.external_attr = info.external_attr
            new_info.create_system = info.create_system
            zout.writestr(new_info, out_data)

    manifest = {
        "schema_version": "lci-benchmark.tidas-compatibility-manifest.v1",
        "input_file": args.input_zip.name,
        "input_sha256": hashlib.sha256(args.input_zip.read_bytes()).hexdigest(),
        "output_file": args.output_zip.name,
        "output_sha256": hashlib.sha256(args.output_zip.read_bytes()).hexdigest(),
        "flow_type_counts": dict(flow_types),
        "modified_flow_count": len(modifications),
        "modifications": modifications,
        "scientific_use_constraint": (
            "Compatibility annotations are excluded from all benchmark questions and scoring. "
            "They permit strict TIDAS package generation but are not treated as original USLCI facts."
        ),
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("input_sha256", "output_sha256", "flow_type_counts", "modified_flow_count")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
