#!/usr/bin/env python3
"""Run DiMuonProcessor over a fileset and save analysis/outputs/<tag>/hists.coffea.

    # quick local check: one chunk per dataset
    python3 analysis/scripts/run.py --fileset analysis/filesets/v1.json --maxchunks 1

    # coffea-casa, Dask scheduler at tls://localhost:8786, with skims
    python3 analysis/scripts/run.py --fileset analysis/filesets/v1.json --executor dask \\
        --skim-dest root://<writable xrootd endpoint>//store/user/<user>/DiMuonSkims/v1

<tag> is the fileset file name without .json, unless --tag is given. Skims are
only written with --skim-dest (or skim.dest in the config). Dask workers write
them, so the destination must be reachable from the workers, not just from the
notebook pod.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dimuontagger import runner  # noqa: E402
from dimuontagger.config import load_config  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fileset", required=True, help="fileset JSON (from build_fileset.py)")
    parser.add_argument("--executor", choices=["iterative", "futures", "dask"], default="iterative")
    parser.add_argument("--workers", type=int, default=4, help="processes for --executor futures")
    parser.add_argument("--scheduler", help="Dask scheduler address (default %s)" % runner.DEFAULT_SCHEDULER)
    parser.add_argument("--chunksize", type=int, default=100_000)
    parser.add_argument("--maxchunks", type=int, help="at most this many chunks per dataset")
    parser.add_argument("--max-files", type=int, help="at most this many files per dataset")
    parser.add_argument("--skim-dest", help="fsspec URL or path for per-candidate parquet skims")
    parser.add_argument("--process", nargs="+", help="only datasets of these catalog processes")
    parser.add_argument("--era", nargs="+", help="only datasets of these eras")
    parser.add_argument("--role", nargs="+", help="only datasets of these roles (signal/background/control)")
    parser.add_argument("--dataset", nargs="+", help="only datasets whose name matches these globs")
    parser.add_argument("--config", help="config YAML (default analysis/config.yaml)")
    parser.add_argument("--skipbadfiles", action="store_true", help="skip files that fail to open")
    parser.add_argument("--tag", help="output tag (default: fileset file name)")
    parser.add_argument("--output", help="output file (default analysis/outputs/<tag>/hists.coffea)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    from dimuontagger.processor import DiMuonProcessor

    tag = args.tag or os.path.splitext(os.path.basename(args.fileset))[0]
    output = args.output or runner.default_output_path(tag)
    fileset = runner.load_fileset(args.fileset, processes=args.process, eras=args.era, roles=args.role,
                                  datasets=args.dataset, max_files=args.max_files)
    if not fileset:
        sys.exit("No datasets left after the --process/--era/--role/--dataset selection.")
    nfiles = sum(len(entry["files"]) for entry in fileset.values())
    print("%d datasets, %d files, executor %s" % (len(fileset), nfiles, args.executor))

    config = load_config(args.config)
    proc = DiMuonProcessor(config, skim_dest=args.skim_dest)
    print("skims: %s" % (proc.skim_dest or "off (no --skim-dest)"))
    if not config["truth"]["llp_pdgids_verified"]:
        print("note: LLP pdgIds in the config are unverified; check ancestors.md from plot.py")

    executor = runner.make_executor(args.executor, workers=args.workers, scheduler=args.scheduler)
    start = time.time()
    result = runner.run(fileset, proc, executor, chunksize=args.chunksize, maxchunks=args.maxchunks,
                        skipbadfiles=args.skipbadfiles)
    record = runner.result_record(result, fileset, config, args=vars(args), tag=tag,
                                  skim_dest=proc.skim_dest, wall_time=time.time() - start)
    runner.save(record, output)

    out = result["output"]
    print("\n%-60s %10s %12s %10s" % ("dataset", "events", "candidates", "skim rows"))
    for name in sorted(out):
        cut = out[name]["cutflow"]
        print("%-60s %10d %12d %10d" % (name, cut["events"], cut["candidates_used"], cut["skim_rows"]))
    rel = os.path.relpath(output)
    print("\nsaved %s (%.1f s)" % (output if rel.startswith("..") else rel, record["wall_time"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
