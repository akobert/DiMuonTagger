#!/usr/bin/env python3
"""Write analysis/filesets/<tag>.json from the published DiMuonNano datasets.

Run on lxplus or the LPC (needs dasgoclient and a grid proxy; not coffea):

    source /cvmfs/cms.cern.ch/cmsset_default.sh
    voms-proxy-init -voms cms -rfc -valid 192:00
    python3 analysis/scripts/build_fileset.py --tag v1

It finds /*/<user>-DiMuonNano_<tag>*/USER in DAS instance prod/phys03, lists
the files, prefixes them with root://xcache/ (coffea-casa's XCache), and tags
each dataset with process, role and era from samples/catalog.yaml. Commit the
JSON and use it on coffea-casa with analysis/scripts/run.py.
"""

import argparse
import functools
import getpass
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dimuontagger.config import ANALYSIS_DIR  # noqa: E402
from dimuontagger.fileset import (  # noqa: E402
    DEFAULT_CATALOG, DEFAULT_REDIRECTOR, build_fileset, dasgoclient_query, load_catalog, write_fileset,
)


def check_proxy():
    if not shutil.which("voms-proxy-info"):
        print("warning: voms-proxy-info not found; assuming a valid grid proxy exists", file=sys.stderr)
        return
    ok = subprocess.run(["voms-proxy-info", "-exists", "-valid", "0:10"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not ok:
        sys.exit("No valid grid proxy. Run:\n  voms-proxy-init -voms cms -rfc -valid 192:00")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", required=True, help="production tag used with nanoaod/crab/submit.py --tag")
    parser.add_argument("--user", default=getpass.getuser(),
                        help="CRAB (CERN) username in the dataset name (default: %(default)s)")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--out", help="output JSON (default: analysis/filesets/<tag>.json)")
    parser.add_argument("--redirector", default=DEFAULT_REDIRECTOR,
                        help="prefix for the LFNs (default: %(default)s)")
    parser.add_argument("--no-summary", action="store_true", help="skip per-dataset event counts")
    parser.add_argument("--dasgoclient", default="dasgoclient")
    parser.add_argument("--skip-proxy-check", action="store_true")
    args = parser.parse_args(argv)

    if not shutil.which(args.dasgoclient):
        sys.exit("%s not found. On lxplus: source /cvmfs/cms.cern.ch/cmsset_default.sh" % args.dasgoclient)
    if not args.skip_proxy_check:
        check_proxy()

    out = args.out or os.path.join(ANALYSIS_DIR, "filesets", "%s.json" % args.tag)
    query = functools.partial(dasgoclient_query, exe=args.dasgoclient)
    fileset, problems = build_fileset(args.tag, args.user, load_catalog(args.catalog), query,
                                      redirector=args.redirector, summary=not args.no_summary)
    if problems:
        print("\nProblems:", file=sys.stderr)
        for line in problems:
            print("  " + line, file=sys.stderr)
    if not fileset:
        return 1
    write_fileset(fileset, out)
    nfiles = sum(len(entry["files"]) for entry in fileset.values())
    print("\nWrote %d datasets, %d files to %s" % (len(fileset), nfiles, os.path.relpath(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
