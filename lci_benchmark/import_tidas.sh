#!/usr/bin/env bash
set -euo pipefail

VERSION="${TIDAS_VERSION:-0.2.0}"
ROOT="${1:-work}"
DOWNLOADS="$ROOT/downloads"
TOOLS="$ROOT/tools"
OUT="$ROOT/tidas_import"
REPORT="$ROOT/tidas_diagnostic"
mkdir -p "$DOWNLOADS" "$TOOLS" "$OUT" "$REPORT"

JSONLD="$DOWNLOADS/uslci_fy19_q2_json_ld.zip"
PATCHED_JSONLD="$DOWNLOADS/uslci_fy19_q2_json_ld_tidas_compat.zip"
if [[ ! -s "$JSONLD" ]]; then
  curl --retry 5 --retry-all-errors -L --fail --show-error \
    -o "$JSONLD" \
    'https://raw.githubusercontent.com/FLCAC-admin/uslci-content/dev/downloads/uslci_fy19_q2_01_olca1_8_0_json_ld.zip.zip'
fi

python lci_benchmark/patch_openlca_for_tidas.py \
  "$JSONLD" "$PATCHED_JSONLD" "$REPORT/tidas_compatibility_manifest.json"
sha256sum "$JSONLD" "$PATCHED_JSONLD" > "$REPORT/jsonld_sha256.txt"

release_json="$REPORT/tidas_release_v${VERSION}.json"
curl --retry 5 --retry-all-errors -L --fail --show-error \
  "https://api.github.com/repos/tiangong-lca/tidas-tools/releases/tags/v${VERSION}" \
  -o "$release_json"

python - "$release_json" "$REPORT/release_assets.txt" <<'PY'
import json, sys
p, out = sys.argv[1:]
data = json.load(open(p, encoding='utf-8'))
with open(out, 'w', encoding='utf-8') as f:
    f.write(f"tag_name={data.get('tag_name')}\n")
    f.write(f"target_commitish={data.get('target_commitish')}\n")
    f.write(f"published_at={data.get('published_at')}\n")
    for a in data.get('assets', []):
        f.write(f"{a.get('name')}\t{a.get('size')}\t{a.get('browser_download_url')}\n")
PY

asset_url=$(python - "$release_json" <<'PY'
import json, re, sys
assets=json.load(open(sys.argv[1], encoding='utf-8')).get('assets', [])
patterns=[r'x86_64-unknown-linux-gnu', r'linux.*x86[_-]?64', r'x86[_-]?64.*linux']
for pat in patterns:
    for a in assets:
        name=a.get('name','')
        if re.search(pat, name, re.I) and not name.endswith(('.sha256','.spdx.json','.intoto.jsonl')):
            print(a['browser_download_url'])
            raise SystemExit
raise SystemExit('No Linux x86_64 release asset found')
PY
)
asset_name=$(basename "${asset_url%%\?*}")
archive="$TOOLS/$asset_name"
curl --retry 5 --retry-all-errors -L --fail --show-error -o "$archive" "$asset_url"
sha256sum "$archive" > "$REPORT/tidas_asset_sha256.txt"
mkdir -p "$TOOLS/extracted"
case "$archive" in
  *.tar.gz|*.tgz) tar -xzf "$archive" -C "$TOOLS/extracted" ;;
  *.tar.xz) tar -xJf "$archive" -C "$TOOLS/extracted" ;;
  *.zip) unzip -q "$archive" -d "$TOOLS/extracted" ;;
  *) cp "$archive" "$TOOLS/extracted/tidas" ;;
esac

TIDAS=$(find "$TOOLS/extracted" -type f -name tidas -perm /111 -print -quit || true)
if [[ -z "$TIDAS" ]]; then TIDAS=$(find "$TOOLS/extracted" -type f -name tidas -print -quit || true); fi
if [[ -z "$TIDAS" ]]; then
  find "$TOOLS/extracted" -maxdepth 4 -type f -printf '%p\n' > "$REPORT/extracted_files.txt"
  echo "Unable to locate tidas executable" >&2
  exit 3
fi
chmod +x "$TIDAS"
printf '%s\n' "$TIDAS" > "$REPORT/tidas_binary_path.txt"
"$TIDAS" --format json version > "$REPORT/tidas_version.json" 2> "$REPORT/tidas_version.stderr"
"$TIDAS" --help > "$REPORT/tidas_help.txt" 2>&1
"$TIDAS" import --help > "$REPORT/tidas_import_help.txt" 2>&1
"$TIDAS" validate --help > "$REPORT/tidas_validate_help.txt" 2>&1

rm -rf "$OUT"
mkdir -p "$OUT"
set +e
"$TIDAS" import "$PATCHED_JSONLD" \
  --from-format openlca-jsonld \
  --output "$OUT" \
  --target tidas \
  --write-mapping \
  --format json \
  --report "$REPORT/import_report.json" \
  > "$REPORT/import_stdout.json" \
  2> "$REPORT/import_stderr.txt"
status=$?
set -e
printf '%s\n' "$status" > "$REPORT/import_exit_status.txt"

find "$OUT" -type f -printf '%P\t%s\n' | sort > "$REPORT/tidas_output_files.tsv"
python - "$OUT" "$REPORT/tidas_output_summary.json" "$REPORT/tidas_samples" <<'PY'
from __future__ import annotations
import collections, json, pathlib, shutil, sys
root=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); samples=pathlib.Path(sys.argv[3])
files=[p for p in root.rglob('*') if p.is_file()]
prefix=collections.Counter((p.relative_to(root).parts[0] if p.relative_to(root).parts else '<root>') for p in files)
suffix=collections.Counter(p.suffix.lower() or '<none>' for p in files)
summary={
  'file_count': len(files),
  'total_bytes': sum(p.stat().st_size for p in files),
  'top_level_counts': dict(prefix),
  'suffix_counts': dict(suffix),
  'first_200_files': [{'path':str(p.relative_to(root)), 'size':p.stat().st_size} for p in files[:200]],
}
out.write_text(json.dumps(summary, indent=2), encoding='utf-8')
samples.mkdir(parents=True, exist_ok=True)
json_files=[p for p in files if p.suffix.lower()=='.json']
process_like=[p for p in json_files if 'process' in str(p).lower()]
for p in (process_like or json_files)[:10]:
    shutil.copy2(p, samples / p.name)
PY

if [[ "$status" -eq 0 ]]; then
  TIDAS_DIR="$OUT/tidas"
  if [[ -d "$TIDAS_DIR" ]]; then
    set +e
    "$TIDAS" validate "$TIDAS_DIR" \
      --input-format tidas-json \
      --format json \
      --report "$REPORT/tidas_validation_report.json" \
      > "$REPORT/tidas_validation_stdout.json" \
      2> "$REPORT/tidas_validation_stderr.txt"
    validation_status=$?
    set -e
    printf '%s\n' "$validation_status" > "$REPORT/tidas_validation_exit_status.txt"
  fi
fi

exit "$status"
