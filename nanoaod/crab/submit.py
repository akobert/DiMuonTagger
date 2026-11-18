#!/usr/bin/env python3
"""Submit CRAB tasks that produce the DiMuonTagger custom NanoAOD.

Run inside the CMSSW area made by nanoaod/setup.sh, after `cmsenv` and
`source /cvmfs/cms.cern.ch/common/crab-setup.sh`, with a valid grid proxy.
Reads nanoaod/cfg/index.json, written by nanoaod/make_cfg.py, and submits one
task per MiniAOD dataset whose config belongs to the current release.

Output goes to /store/user/<user>/DiMuonNano/<tag>/ at the storage site
(default T2_US_Nebraska). It is published in DBS prod/phys03 as
/<primary>/<user>-DiMuonNano_<tag>_<era>-<hash>/USER. Extension samples of the
same primary and era publish into the same output dataset.

Datasets that exist only on tape cannot be read until they are recalled, so
they are skipped. Disk status comes from the resolver's
samples/resolved/datasets.json (--resolved). --include-tape submits them anyway,
e.g. after a Rucio recall. Datasets without disk status (not in that file, or
resolved with --no-sites) are submitted with a warning.

    python3 nanoaod/crab/submit.py --tag v1 --dry-run        # show what would be submitted
    python3 nanoaod/crab/submit.py --tag v1 --era 2022postEE --process qcd_muenriched
"""

import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NANOAOD = os.path.dirname(HERE)
REPO = os.path.dirname(NANOAOD)
sys.path.insert(0, NANOAOD)

from make_cfg import release_series  # noqa: E402

DEFAULT_INDEX = os.path.join(NANOAOD, "cfg", "index.json")
DEFAULT_RESOLVED = os.path.join(REPO, "samples", "resolved", "datasets.json")
MAX_REQUEST_NAME = 100
SHOW_SKIPPED = 10


def load_disk_status(path):
    """{dataset: on_disk} from the resolver's datasets.json; None if the file is missing.

    Datasets the resolver did not check (on_disk null) are left out.
    """
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        records = json.load(handle).get("datasets", [])
    return {rec["dataset"]: bool(rec["on_disk"]) for rec in records if rec.get("on_disk") is not None}


def request_name(tag, era, dataset):
    """Unique, CRAB-safe request name: <tag>_<era>_<primary>_<hash of dataset>."""
    primary = dataset.split("/")[1]
    digest = hashlib.sha1(dataset.encode()).hexdigest()[:8]
    head = re.sub(r"[^A-Za-z0-9_-]", "_", "%s_%s_" % (tag, era))
    room = MAX_REQUEST_NAME - len(head) - len(digest) - 1
    body = re.sub(r"[^A-Za-z0-9_-]", "_", primary)[:max(room, 0)]
    return "%s%s_%s" % (head, body, digest)


def output_dataset_tag(tag, era):
    return "DiMuonNano_%s_%s" % (tag, era)


def task_settings(dataset, entry, args, user):
    """Plain description of one CRAB task (turned into a CRAB config by make_config)."""
    return {
        "General.requestName": request_name(args.tag, entry["era"], dataset),
        "General.workArea": args.work_area,
        "General.transferOutputs": True,
        "General.transferLogs": False,
        "JobType.pluginName": "Analysis",
        "JobType.psetName": os.path.join(REPO, entry["cfg"]),
        "JobType.maxMemoryMB": args.max_memory,
        "Data.inputDataset": dataset,
        "Data.inputDBS": "global",
        "Data.splitting": args.splitting,
        "Data.unitsPerJob": args.units_per_job,
        "Data.outLFNDirBase": "/store/user/%s/DiMuonNano/%s" % (user, args.tag),
        "Data.publication": True,
        "Data.outputDatasetTag": output_dataset_tag(args.tag, entry["era"]),
        "Site.storageSite": args.site,
    }


def make_config(settings):
    from CRABClient.UserUtilities import config

    cfg = config()
    for key, value in settings.items():
        section, name = key.split(".")
        setattr(getattr(cfg, section), name, value)
    return cfg


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", required=True, help="production tag, e.g. v1 (letters, digits, _)")
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--era", nargs="+", help="only these eras")
    parser.add_argument("--process", nargs="+", help="only these catalog processes")
    parser.add_argument("--site", default="T2_US_Nebraska", help="storage site")
    parser.add_argument("--splitting", default="Automatic", choices=["Automatic", "FileBased", "EventAwareLumiBased"])
    parser.add_argument("--units-per-job", type=int, default=480,
                        help="minutes of job runtime for Automatic, else files/events per job")
    parser.add_argument("--max-memory", type=int, default=2500, help="MB per job")
    parser.add_argument("--work-area", default="crab_projects")
    parser.add_argument("--resolved", default=DEFAULT_RESOLVED,
                        help="resolver output with disk/tape status (datasets.json)")
    parser.add_argument("--include-tape", action="store_true",
                        help="also submit datasets that are only on tape (e.g. after a recall)")
    parser.add_argument("--dry-run", action="store_true", help="print the tasks without submitting")
    args = parser.parse_args(argv)

    if not re.match(r"^[A-Za-z0-9_]+$", args.tag):
        parser.error("--tag may contain only letters, digits and _")
    if not os.path.exists(args.index):
        sys.exit("%s not found; run nanoaod/make_cfg.py --lists ... first" % args.index)
    with open(args.index) as handle:
        index = json.load(handle)

    current = release_series(os.environ.get("CMSSW_VERSION"))
    if current is None and not args.dry_run:
        sys.exit("Run inside the CMSSW area (cmsenv) that matches the configs.")

    disk = load_disk_status(args.resolved)
    if disk is None:
        print("warning: %s not found, so tape-only datasets cannot be recognised; nothing is skipped "
              "for that reason (run scripts/resolve_samples.py first)" % args.resolved, file=sys.stderr)
        disk = {}

    selected, tape_only, unknown = [], [], []
    for dataset, entry in sorted(index.items()):
        if args.era and entry["era"] not in args.era:
            continue
        if args.process and entry.get("process") not in args.process:
            continue
        if current is not None and entry["release"] != current:
            continue
        if not os.path.exists(os.path.join(REPO, entry["cfg"])):
            print("missing config %s for %s; rerun make_cfg.py" % (entry["cfg"], dataset), file=sys.stderr)
            continue
        on_disk = disk.get(dataset)
        if on_disk is False and not args.include_tape:
            tape_only.append(dataset)
            continue
        if on_disk is None:
            unknown.append(dataset)
        selected.append((dataset, entry))

    if tape_only:
        print("skipping %d tape-only datasets (--include-tape submits them once recalled):"
              % len(tape_only), file=sys.stderr)
        for dataset in tape_only[:SHOW_SKIPPED]:
            print("  " + dataset, file=sys.stderr)
        if len(tape_only) > SHOW_SKIPPED:
            print("  ... and %d more (see samples/resolved/tape_only.txt)" % (len(tape_only) - SHOW_SKIPPED),
                  file=sys.stderr)
    if unknown and disk:
        print("warning: no disk/tape status for %d datasets (not in %s, or resolved with --no-sites); "
              "submitting them anyway" % (len(unknown), os.path.basename(args.resolved)), file=sys.stderr)
    if not selected:
        sys.exit("Nothing to submit (check --era/--process, the CMSSW release%s)."
                 % (", and --include-tape: all selected datasets are tape-only" if tape_only else ""))

    if args.dry_run:
        user = os.environ.get("USER", "<user>")
    else:
        from CRABClient.UserUtilities import getUsername
        user = getUsername()

    failures = []
    for dataset, entry in selected:
        settings = task_settings(dataset, entry, args, user)
        if args.dry_run:
            print(json.dumps(settings, indent=1))
            continue
        from CRABAPI.RawCommand import crabCommand
        print("submitting %s" % settings["General.requestName"])
        try:
            crabCommand("submit", config=make_config(settings))
        except Exception as exc:  # keep going; report at the end
            failures.append((dataset, exc))
            print("  failed: %s" % exc, file=sys.stderr)

    print("%d tasks %s, %d failed, %d tape-only skipped" % (
        len(selected) - len(failures), "listed" if args.dry_run else "submitted", len(failures), len(tape_only)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
