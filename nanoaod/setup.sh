#!/bin/bash
# Create a CMSSW area for the DiMuonTagger custom NanoAOD and build the package.
#
#   bash nanoaod/setup.sh 2022_23 [workdir]   # CMSSW_13_0_X: 2022 and 2023 MC
#   bash nanoaod/setup.sh 2024    [workdir]   # CMSSW_15_0_X: 2024 MC
#
# workdir defaults to the current directory. The newest installed release of the
# series is used unless CMSSW_RELEASE is set; SCRAM_ARCH can be overridden with
# DIMUON_SCRAM_ARCH. The package is symlinked from this repo into
# $CMSSW_BASE/src/DiMuonTagger/NanoProd, so edits here are picked up by `scram b`.
# Set DIMUON_COPY=1 to copy it instead (e.g. if CRAB does not follow the link).
#
# CMSSW_13_0_X (el8) on an el9 host such as lxplus: run this inside `cmssw-el8`.
set -euo pipefail

usage() { sed -n '2,15p' "$0"; exit 1; }
[ $# -ge 1 ] || usage
target=$1
workdir=${2:-$PWD}
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

case "$target" in
  2022_23) series=13_0; arch=${DIMUON_SCRAM_ARCH:-el8_amd64_gcc11} ;;
  2024)    series=15_0; arch=${DIMUON_SCRAM_ARCH:-el9_amd64_gcc12} ;;
  *) usage ;;
esac

host_el=$(sed -n 's/^VERSION_ID="\{0,1\}\([0-9]*\).*/\1/p' /etc/os-release)
arch_el=${arch%%_*}; arch_el=${arch_el#el}
if [ -n "$host_el" ] && [ "$host_el" != "$arch_el" ]; then
  echo "This host is el$host_el but $arch needs el$arch_el: start 'cmssw-el$arch_el' first," >&2
  echo "or set DIMUON_SCRAM_ARCH to an el$host_el build of CMSSW_${series}_X." >&2
  exit 1
fi

set +u  # cmsset_default.sh uses unset variables
source /cvmfs/cms.cern.ch/cmsset_default.sh
set -u
export SCRAM_ARCH=$arch

release=${CMSSW_RELEASE:-$(scram list -c CMSSW | awk '{print $2}' \
          | grep -E "^CMSSW_${series}_[0-9]+$" | sort -V | tail -1)}
if [ -z "$release" ]; then
  echo "No CMSSW_${series}_X release found for $SCRAM_ARCH" >&2
  exit 1
fi
echo "Using $release ($SCRAM_ARCH) in $workdir"

mkdir -p "$workdir"
cd "$workdir"
[ -d "$release" ] || scram project CMSSW "$release"
cd "$release/src"
set +u
eval "$(scram runtime -sh)"
set -u

mkdir -p DiMuonTagger
if [ "${DIMUON_COPY:-0}" = "1" ]; then
  rm -rf DiMuonTagger/NanoProd
  cp -r "$repo/nanoaod/DiMuonTagger/NanoProd" DiMuonTagger/NanoProd
else
  ln -sfn "$repo/nanoaod/DiMuonTagger/NanoProd" DiMuonTagger/NanoProd
fi
scram b -j "$(nproc)"

echo
echo "Done. To use this area:"
echo "  cd $workdir/$release/src && cmsenv"
