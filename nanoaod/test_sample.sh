#!/bin/bash
# Run the custom NanoAOD on a few hundred events of one MiniAOD dataset and check it.
#
#   bash nanoaod/test_sample.sh <MiniAOD dataset> [-n EVENTS] [--out DIR] [--file LFN] [--redirector URL]
#
# Run inside the CMSSW area for the dataset's era (after cmsenv), with a grid
# proxy. It takes a file stored at a disk site (tape-only datasets are refused),
# makes a test config with make_cfg.py, runs cmsRun (log: DIR/cmsRun.log) and
# then check_nano.py. Defaults: 500 events; DIR is
# ${DIMUON_TEST_DIR:-$HOME/dimuon_tests}/<primary>__<campaign>.
# --file uses that file (LFN or root:// URL) instead of asking DAS.
# DIMUON_CFG_DIR overrides where make_cfg.py writes the config.
set -euo pipefail

usage() { sed -n '2,12p' "$0"; exit "${1:-1}"; }
fail() { echo "test_sample: $*" >&2; exit 1; }

dm_repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dm_nevents=500
dm_out=""
dm_file=""
dm_site="(given)"
dm_redirector=root://cms-xrd-global.cern.ch/
dm_dataset=""
while [ $# -gt 0 ]; do
  case "$1" in
    -n|--nevents) dm_nevents=$2; shift 2 ;;
    --out) dm_out=$2; shift 2 ;;
    --file) dm_file=$2; shift 2 ;;
    --redirector) dm_redirector=$2; shift 2 ;;
    -h|--help) usage 0 ;;
    -*) echo "unknown option: $1" >&2; usage ;;
    *) [ -z "$dm_dataset" ] || fail "give only one dataset"; dm_dataset=$1; shift ;;
  esac
done
[ -n "$dm_dataset" ] || usage
# Names cut from files or pasted can carry \r or spaces.
dm_dataset=$(printf '%s' "$dm_dataset" | tr -d '[:space:]')
[[ "$dm_dataset" =~ ^/[^/]+/[^/]+/MINIAODSIM$ ]] || fail "not a MINIAODSIM dataset name: $dm_dataset"
[[ "$dm_nevents" =~ ^-?[0-9]+$ ]] || fail "-n needs a number, got $dm_nevents"

[ -n "${CMSSW_BASE:-}" ] || fail "no CMSSW environment: cd to the CMSSW area's src and run cmsenv"
command -v dasgoclient >/dev/null || fail "dasgoclient not found: source /cvmfs/cms.cern.ch/cmsset_default.sh"
if command -v voms-proxy-info >/dev/null; then
  voms-proxy-info -exists -valid 0:30 >/dev/null 2>&1 \
    || fail "no valid grid proxy: voms-proxy-init -voms cms -rfc -valid 192:00"
fi

echo "dataset: $dm_dataset"

# ---- input file: one stored at a disk site
if [ -z "$dm_file" ]; then
  sites=$(dasgoclient -query="site dataset=$dm_dataset" 2>&1) || fail "DAS site query failed: $sites"
  disk=$(printf '%s\n' "$sites" | grep -E '^T[0-9]_' | grep -vE '_(Tape|MSS|Buffer|Export)$' || true)
  tape=$(printf '%s\n' "$sites" | grep -E '^T[0-9]_.*_(Tape|MSS|Buffer|Export)$' || true)
  [ -n "$disk$tape" ] || fail "DAS lists no site for this dataset. dasgoclient says: $sites"
  [ -n "$disk" ] || fail "only on tape ($(echo $tape)); it needs a recall before jobs can read it"
  for site in $disk; do
    dm_file=$(dasgoclient -query="file dataset=$dm_dataset site=$site" 2>/dev/null | grep '^/store/' | head -1 || true)
    if [ -n "$dm_file" ]; then dm_site=$site; break; fi
  done
  if [ -z "$dm_file" ]; then
    echo "test_sample: DAS gave no per-site file list; using the dataset's first file" >&2
    files=$(dasgoclient -query="file dataset=$dm_dataset" 2>&1 || true)
    dm_file=$(printf '%s\n' "$files" | grep '^/store/' | head -1 || true)
    dm_site="unknown ($(echo $disk))"
    [ -n "$dm_file" ] || fail "DAS returned no files. dasgoclient says: $(printf '%s\n' "$files" | head -5)"
  fi
fi
case "$dm_file" in
  root://*) dm_url=$dm_file ;;
  /store/*) dm_url="${dm_redirector%/}/$dm_file" ;;
  *) fail "--file must be an LFN (/store/...) or a root:// URL, got $dm_file" ;;
esac
echo "file:    $dm_url"
echo "site:    $dm_site"

# ---- config
cfg_args=()
[ -n "${DIMUON_CFG_DIR:-}" ] && cfg_args=(--out-dir "$DIMUON_CFG_DIR")
cd "$dm_repo"
mc_out=$(python3 nanoaod/make_cfg.py --dataset "$dm_dataset" --filein "$dm_url" -n "$dm_nevents" \
           ${cfg_args[@]+"${cfg_args[@]}"}) \
  || { printf '%s\n' "$mc_out"; fail "make_cfg.py failed"; }
cfg=$(printf '%s\n' "$mc_out" | sed -n 's/^\(generating\|exists, keeping:\) //p' | head -1)
if [ -z "$cfg" ] || [ ! -f "$cfg" ]; then
  printf '%s\n' "$mc_out"
  fail "make_cfg.py made no config (is this the CMSSW release for the dataset's era?)"
fi
cfg=$(cd "$(dirname "$cfg")" && pwd)/$(basename "$cfg")
echo "config:  $cfg"

# ---- run
primary=$(printf '%s' "$dm_dataset" | cut -d/ -f2)
campaign=$(printf '%s' "$dm_dataset" | cut -d/ -f3 | cut -d- -f1)
dm_out=${dm_out:-${DIMUON_TEST_DIR:-$HOME/dimuon_tests}/${primary}__${campaign}}
mkdir -p "$dm_out"
cd "$dm_out"
rm -f nano.root
echo "cmsRun:  $dm_nevents events in $dm_out (log: cmsRun.log)"
if ! cmsRun "$cfg" > cmsRun.log 2>&1; then
  tail -25 cmsRun.log
  fail "cmsRun failed; full log in $dm_out/cmsRun.log"
fi
[ -f nano.root ] || fail "cmsRun finished but wrote no nano.root (see $dm_out/cmsRun.log)"

echo
python3 "$dm_repo/nanoaod/DiMuonTagger/NanoProd/test/check_nano.py" nano.root \
  || fail "check_nano.py found problems in $dm_out/nano.root"

echo "Output: $dm_out/nano.root"
echo "To copy it (e.g. for coffea-casa), from your laptop:"
echo "  scp ${USER:-<user>}@lxplus.cern.ch:$dm_out/nano.root ."
