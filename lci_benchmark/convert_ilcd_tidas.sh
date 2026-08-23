#!/usr/bin/env bash
set -euo pipefail

VERSION="${TIDAS_VERSION:-0.2.0}"
ROOT="${1:-work}"
DOWNLOADS="$ROOT/downloads"
TOOLS="$ROOT/tools-convert"
SOURCE="$ROOT/ilcd_source"
OUT="$ROOT/tidas_from_ilcd"
REPORT="$ROOT/tidas_convert_diagnostic"
mkdir -p "$DOWNLOADS" "$TOOLS" "$SOURCE" "$REPORT"

ILCD_ZIP="$DOWNLOADS/uslci_fy19_q2_ilcd.zip"
if [[ ! -s "$ILCD_ZIP" ]]; then
  curl --retry 5 --retry-all-errors -L --fail --show-error \
    -o "$ILCD_ZIP" \
    'https://raw.githubusercontent.com/FLCAC-admin/uslci-content/dev/downloads/uslcy_fy19_q2_01_olca1_8_0_ilcd.zip.zip'
fi
rm -rf "$SOURCE" "$OUT"
mkdir -p "$SOURCE" "$OUT"
unzip -q "$ILCD_ZIP" -d "$SOURCE"

release_json="$REPORT/tidas_release_v${VERSION}.json"
curl --retry 5 --retry-all-errors -L --fail --show-error \
  "https://api.github.com/repos/tiangong-lca/tidas-tools/releases/tags/v${VERSION}" \
  -o "$release_json"
asset_url=$(python - "$release_json" <<'PY'
import json, re, sys
assets=json.load(open(sys.argv[1], encoding='utf-8')).get('assets', [])
for pat in (r'x86_64-unknown-linux-gnu.*\.tar\.gz$', r'linux.*x86[_-]?64'):
    for a in assets:
        if re.search(pat, a.get('name',''), re.I):
            print(a['browser_download_url']); raise SystemExit
raise SystemExit('No Linux x86_64 release asset found')
PY
)
archive="$TOOLS/$(basename "$asset_url")"
curl --retry 5 --retry-all-errors -L --fail --show-error -o "$archive" "$asset_url"
sha256sum "$archive" > "$REPORT/tidas_asset_sha256.txt"
mkdir -p "$TOOLS/extracted"
tar -xzf "$archive" -C "$TOOLS/extracted"
TIDAS=$(find "$TOOLS/extracted" -type f -name tidas -print -quit)
chmod +x "$TIDAS"
"$TIDAS" --format json version > "$REPORT/tidas_version.json"
"$TIDAS" convert --help > "$REPORT/convert_help.txt" 2>&1

INPUT="$SOURCE/ILCD"
set +e
"$TIDAS" convert "$INPUT" \
  --output "$OUT" \
  --to tidas \
  --format json \
  --report "$REPORT/convert_report.json" \
  > "$REPORT/convert_stdout.json" \
  2> "$REPORT/convert_stderr.txt"
status=$?
set -e
printf '%s\n' "$status" > "$REPORT/convert_exit_status.txt"
find "$OUT" -type f -printf '%P\t%s\n' | sort > "$REPORT/tidas_output_files.tsv"
python - "$OUT" "$REPORT/tidas_output_summary.json" "$REPORT/tidas_samples" <<'PY'
from __future__ import annotations
import collections, json, pathlib, shutil, sys
root=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); samples=pathlib.Path(sys.argv[3])
files=[p for p in root.rglob('*') if p.is_file()]
summary={
 'file_count':len(files),
 'total_bytes':sum(p.stat().st_size for p in files),
 'suffix_counts':dict(collections.Counter(p.suffix.lower() or '<none>' for p in files)),
 'top_level_counts':dict(collections.Counter((p.relative_to(root).parts[0] if p.relative_to(root).parts else '<root>') for p in files)),
 'first_200_files':[{'path':str(p.relative_to(root)),'size':p.stat().st_size} for p in files[:200]],
}
out.write_text(json.dumps(summary,indent=2),encoding='utf-8')
samples.mkdir(parents=True,exist_ok=True)
process_like=[p for p in files if p.suffix.lower()=='.json' and 'process' in str(p).lower()]
for p in process_like[:5]: shutil.copy2(p,samples/p.name)
PY
exit "$status"
