#!/usr/bin/env python3
"""Plots and tables from analysis/outputs/<tag>/hists.coffea.

    python3 analysis/scripts/plot.py analysis/outputs/v1/hists.coffea

Writes next to the input (or to --outdir):
    plots/<era>/<variable>.png   normalised shapes per truth category, per era
                                 and for all eras ('all'); lxy_residual.png for signal
    cutflow.md, cutflow.csv      per-dataset cutflow
    ancestors.md                 ancestor pdgIds of gen-matched muons, to check
                                 the LLP pdgIds in analysis/config.yaml
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dimuontagger import plotting  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="hists.coffea from scripts/run.py")
    parser.add_argument("--outdir", help="default: the input's directory")
    parser.add_argument("--era", nargs="+", help="only these eras")
    parser.add_argument("--format", nargs="+", default=["png"], help="png, pdf, ...")
    parser.add_argument("--top", type=int, default=12, help="ancestor pdgIds listed per dataset")
    parser.add_argument("--no-plots", action="store_true", help="tables only")
    args = parser.parse_args(argv)

    record = plotting.load(args.input)
    outdir = args.outdir or os.path.dirname(os.path.abspath(args.input))

    rows = plotting.write_cutflow(record, outdir)
    print("Cutflow (%d datasets):" % len(rows))
    for row in rows:
        print("  %-60s events %8d  candidates %8d  signal %6d" % (
            row["dataset"], row["events"], row["candidates_used"], row["cat_signal"]))

    _, warnings = plotting.write_ancestors(record, outdir, top=args.top)
    for name in warnings:
        print("WARNING: signal dataset %s has no configured LLP pdgId among muon ancestors" % name)

    written = []
    if not args.no_plots:
        written = plotting.all_plots(record, os.path.join(outdir, "plots"), eras=args.era,
                                     formats=tuple(args.format))
    print("wrote cutflow.md, cutflow.csv, ancestors.md and %d plots under %s" % (len(written), outdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
