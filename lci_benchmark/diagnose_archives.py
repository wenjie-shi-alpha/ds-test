#!/usr/bin/env python3
"""Inspect matched USLCI release archives without assuming internal layouts."""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def inspect_json(blob: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        obj = json.loads(blob.decode("utf-8-sig"))
        out["valid_json"] = True
        out["python_type"] = type(obj).__name__
        if isinstance(obj, dict):
            out["top_keys"] = list(obj)[:30]
            for key in ("@type", "@id", "id", "name", "processType"):
                if key in obj:
                    out[key] = obj[key]
        elif isinstance(obj, list):
            out["length"] = len(obj)
            if obj and isinstance(obj[0], dict):
                out["first_item_keys"] = list(obj[0])[:30]
    except Exception as exc:  # diagnostic only
        out["valid_json"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def inspect_xml(blob: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        root = ET.fromstring(blob)
        out["valid_xml"] = True
        out["root_tag"] = root.tag
        out["root_local_name"] = local_name(root.tag)
        out["root_attributes"] = dict(list(root.attrib.items())[:30])
        out["first_child_local_names"] = [local_name(x.tag) for x in list(root)[:30]]
        names = Counter(local_name(e.tag) for e in root.iter())
        out["common_element_names"] = names.most_common(30)
    except Exception as exc:  # diagnostic only
        out["valid_xml"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def inspect_archive(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
        "is_zip": zipfile.is_zipfile(path),
    }
    if not result["is_zip"]:
        result["prefix_hex"] = path.read_bytes()[:64].hex()
        result["prefix_text"] = path.read_bytes()[:256].decode("utf-8", errors="replace")
        return result

    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        result["member_count"] = len(infos)
        result["total_uncompressed_bytes"] = sum(i.file_size for i in infos)
        suffix_counts = Counter(Path(i.filename).suffix.lower() or "<none>" for i in infos)
        result["suffix_counts"] = dict(suffix_counts.most_common())
        result["first_100_members"] = [
            {"name": i.filename, "size": i.file_size} for i in infos[:100]
        ]

        samples: list[dict[str, Any]] = []
        preferred = [i for i in infos if Path(i.filename).suffix.lower() in {".json", ".xml", ".spold"}]
        for info in preferred[:20]:
            sample: dict[str, Any] = {"name": info.filename, "size": info.file_size}
            try:
                blob = zf.read(info)
                suffix = Path(info.filename).suffix.lower()
                if suffix == ".json":
                    sample["inspection"] = inspect_json(blob)
                else:
                    sample["inspection"] = inspect_xml(blob)
                sample["prefix_text"] = blob[:1000].decode("utf-8", errors="replace")
            except Exception as exc:
                sample["read_error"] = f"{type(exc).__name__}: {exc}"
            samples.append(sample)
        result["samples"] = samples
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: diagnose_archives.py OUTPUT.json ARCHIVE...", file=sys.stderr)
        return 2
    output = Path(argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "python_version": sys.version,
        "archives": [inspect_archive(Path(x)) for x in argv[2:]],
    }
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(output), "archives": len(report["archives"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
