#!/bin/bash
# Create a CMSSW area for the DiMuonTagger custom NanoAOD and build the package.
#
#   bash nanoaod/setup.sh 2022_23 [workdir]   # CMSSW_13_0_X: 2022 and 2023 MC
#   bash nanoaod/setup.sh 2024    [workdir]   # CMSSW_15_0_X: 2024 MC
#
# workdir defaults to the current directory. The newest installed release of the
# series is used unless DIMUON_CMSSW_RELEASE is set; SCRAM_ARCH can be overridden
# with DIMUON_SCRAM_ARCH. The package is symlinked from this repo into
# $CMSSW_BASE/src/DiMuonTagger/NanoProd, so edits here are picked up by `scram b`.
# Set DIMUON_COPY=1 to copy it instead (e.g. if CRAB does not follow the link).
#
# Both release series are used in their production architecture, el8 (15_0 also
# has el9 builds, but official 2024 NanoAOD used el8). On an el9 host such as
# lxplus, run this inside `cmssw-el8`.
set -euo pipefail

# All variables are dm_-prefixed: sourcing cmsset_default.sh unsets common
# names such as `arch`.
usage() { sed -n '2,15p' "$0"; exit 1; }
[ $# -ge 1 ] || usage
dm_target=$1
dm_workdir=${2:-$PWD}
dm_repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dm_cmsset=${DIMUON_CMSSET:-/cvmfs/cms.cern.ch/cmsset_default.sh}

case "$dm_target" in
  2022_23) dm_series=13_0; dm_arch=${DIMUON_SCRAM_ARCH:-el8_amd64_gcc11} ;;
  2024)    dm_series=15_0; dm_arch=${DIMUON_SCRAM_ARCH:-el8_amd64_gcc12} ;;
  *) usage ;;
esac

# On an EL host (lxplus, or inside cmssw-elN) the OS must match the release.
if grep -Eqi '^(ID|ID_LIKE)=.*(rhel|centos|fedora|almalinux|rocky)' /etc/os-release; then
  dm_host_el=$(sed -n 's/^VERSION_ID="\{0,1\}\([0-9]*\).*/\1/p' /etc/os-release)
  dm_arch_el=${dm_arch%%_*}; dm_arch_el=${dm_arch_el#el}
  if [ -n "$dm_host_el" ] && [ "$dm_host_el" != "$dm_arch_el" ]; then
    echo "This host is el$dm_host_el but $dm_arch needs el$dm_arch_el: start 'cmssw-el$dm_arch_el' first," >&2
    echo "or set DIMUON_SCRAM_ARCH to an el$dm_host_el build of CMSSW_${dm_series}_X." >&2
    exit 1
  fi
fi

export SCRAM_ARCH=$dm_arch
set +u  # cmsset_default.sh uses unset variables
source "$dm_cmsset"
set -u
export SCRAM_ARCH=$dm_arch

dm_release=${DIMUON_CMSSW_RELEASE:-$(scram list -c CMSSW | awk '{print $2}' \
             | grep -E "^CMSSW_${dm_series}_[0-9]+$" | sort -V | tail -1)}
if [ -z "$dm_release" ]; then
  echo "No CMSSW_${dm_series}_X release found for $SCRAM_ARCH" >&2
  exit 1
fi

mkdir -p "$dm_workdir"
dm_workdir=$(cd "$dm_workdir" && pwd)
echo "Using $dm_release ($SCRAM_ARCH) in $dm_workdir"
cd "$dm_workdir"
[ -d "$dm_release" ] || scram project CMSSW "$dm_release"
cd "$dm_release/src"
set +u
eval "$(scram runtime -sh)"
set -u

mkdir -p DiMuonTagger
if [ "${DIMUON_COPY:-0}" = "1" ]; then
  rm -rf DiMuonTagger/NanoProd
  cp -r "$dm_repo/nanoaod/DiMuonTagger/NanoProd" DiMuonTagger/NanoProd
else
  ln -sfn "$dm_repo/nanoaod/DiMuonTagger/NanoProd" DiMuonTagger/NanoProd
fi
scram b -j "$(nproc)"

echo
echo "Done. To use this area:"
echo "  cd $dm_workdir/$dm_release/src && cmsenv"
