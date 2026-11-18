#!/usr/bin/env python3
"""Generate cmsRun configs for the DiMuonTagger custom NanoAOD with cmsDriver.py.

Run inside a CMSSW area made by nanoaod/setup.sh, after `cmsenv`.

Each MiniAOD dataset gets mapped as follows:
- its era, from the samples/catalog.yaml regexes;
- its MiniAOD format version and global tag, from the processed name;
- a cmsDriver --era, from nanoaod/eras.yaml.

One config is generated per distinct (era, MiniAOD version, global tag, event
filter). Background datasets get the event filter: only events with at least
one DiMuonVtx candidate are written (the Runs tree still counts every event).
Signal and control datasets keep every event, for efficiencies. A catalog
process can override this with `event_filter: true/false`; --event-filter
on/off overrides it for every dataset.
nanoaod/cfg/index.json maps every dataset to its config, for
nanoaod/crab/submit.py. Datasets whose era needs the other CMSSW release are
skipped; run this once in each release area.

    # every dataset in the resolver's MiniAOD lists that this release handles
    python3 nanoaod/make_cfg.py --lists samples/resolved/lists/MINIAODSIM

    # a test config for one dataset: 200 events from a given file (not indexed)
    python3 nanoaod/make_cfg.py --dataset /TTto2L2Nu_.../Run3Summer22MiniAODv4-.../MINIAODSIM \
        --filein root://cms-xrd-global.cern.ch//store/mc/... -n 200
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

import yaml  # noqa: E402

from resolve_samples import UNCLASSIFIED, classify_era, format_version, load_catalog  # noqa: E402

DEFAULT_CATALOG = os.path.join(REPO, "samples", "catalog.yaml")
DEFAULT_ERAS = os.path.join(HERE, "eras.yaml")
DEFAULT_OUT = os.path.join(HERE, "cfg")
CUSTOMISE = "DiMuonTagger/NanoProd/dimuonNano_cff.customiseDiMuonNano"
CUSTOMISE_FILTERED = "DiMuonTagger/NanoProd/dimuonNano_cff.customiseDiMuonNanoFiltered"
RUN = "run3"
TIER = "MINIAODSIM"

_GT_RE = re.compile(r"^[^-]+-(?P<gt>.+?)(?:_ext\d+)?-v\d+$")


def load_eras(path):
    with open(path) as handle:
        cfg = yaml.safe_load(handle)
    cfg["eras"] = {str(era): {**info, "cmsdriver_era": {int(v): e for v, e in info["cmsdriver_era"].items()}}
                   for era, info in cfg["eras"].items()}
    cfg["releases"] = {str(k): v for k, v in cfg["releases"].items()}
    return cfg


def split_dataset(dataset):
    parts = dataset.split("/")
    if len(parts) != 4 or parts[0] or not all(parts[1:]):
        raise ValueError("not a DAS dataset name: %r" % dataset)
    return parts[1], parts[2], parts[3]


def global_tag(processed):
    """GT from a processed name: Run3Summer22MiniAODv4-<GT>[_extN]-vN."""
    match = _GT_RE.match(processed)
    if not match:
        raise ValueError("cannot read a global tag from %r" % processed)
    return match.group("gt")


def describe(dataset, catalog, eras, conditions=None):
    """Everything needed to build the NANO config for one MiniAOD dataset."""
    primary, processed, tier = split_dataset(dataset)
    if tier != TIER:
        raise ValueError("%s is %s, expected %s" % (dataset, tier, TIER))
    era = classify_era(catalog, RUN, TIER, processed)
    if era == UNCLASSIFIED or era not in eras["eras"]:
        raise ValueError("%s: no Run 3 era matches %r" % (dataset, processed))
    version = format_version(processed)
    era_cfg = eras["eras"][era]
    if version not in era_cfg["cmsdriver_era"]:
        raise ValueError("%s: era %s has no cmsDriver era for MiniAODv%d (known: %s)"
                         % (dataset, era, version, sorted(era_cfg["cmsdriver_era"])))
    return {
        "dataset": dataset,
        "primary": primary,
        "era": era,
        "release": era_cfg["release"],
        "miniaod_version": version,
        "global_tag": conditions or global_tag(processed),
        "cmsdriver_era": era_cfg["cmsdriver_era"][version],
    }


def catalog_process(primary, catalog):
    """Catalog process whose Run 3 patterns match a primary dataset name, or None."""
    for name, proc in sorted(catalog["processes"].items()):
        if any(fnmatch.fnmatchcase(primary, pattern) for pattern in proc["patterns"].get(RUN) or []):
            return name
    return None


def event_filter_for(process, catalog, mode="auto"):
    """Whether a dataset's config writes only events with a DiMuonVtx candidate.

    mode "on"/"off" forces it. "auto": the catalog process's `event_filter`
    if set, else on for backgrounds; off for signal, control and datasets of
    no known process.
    """
    if mode != "auto":
        return mode == "on"
    proc = catalog["processes"].get(process) if process else None
    if proc is None:
        return False
    return bool(proc.get("event_filter", proc["role"] == "background"))


def config_name(info, test_inputs=None):
    """Config file name. Production configs are shared by every dataset with
    the same era, MiniAOD version, GT and event filter (CRAB sets the input).

    test_inputs=(filein, nevents) names a test config. The name then also
    identifies the sample and input, so tests of different samples in the
    same era never share (and silently reuse) a config.
    """
    base = "nano_%s_MiniAODv%d_%s" % (info["era"], info["miniaod_version"], info["global_tag"])
    if info.get("event_filter"):
        base += "_filtered"
    if test_inputs is None:
        return base + "_cfg.py"
    key = "|".join([info["dataset"], test_inputs[0], str(test_inputs[1])])
    digest = hashlib.sha1(key.encode()).hexdigest()[:6]
    primary = re.sub(r"[^A-Za-z0-9_-]", "_", info["primary"])[:40]
    return "%s_test_%s_%s_cfg.py" % (base, primary, digest)


def cmsdriver_command(info, cfg_path, filein, nevents):
    return [
        "cmsDriver.py", "nano",
        "--python_filename", cfg_path,
        "--fileout", "file:nano.root",
        "--filein", filein,
        "--step", "NANO",
        "--mc",
        "--eventcontent", "NANOAODSIM",
        "--datatier", "NANOAODSIM",
        "--era", info["cmsdriver_era"],
        "--conditions", info["global_tag"],
        "--customise", CUSTOMISE_FILTERED if info.get("event_filter") else CUSTOMISE,
        "-n", str(nevents),
        "--no_exec",
    ]


def release_series(version):
    """'CMSSW_13_0_17' -> '13_0' (None if not a CMSSW version string)."""
    match = re.match(r"^CMSSW_(\d+)_(\d+)_", version or "")
    return "%s_%s" % match.groups() if match else None


def read_lists(lists_dir):
    """(dataset, process) pairs from <lists_dir>/<era>/<process>.txt."""
    pairs = []
    for era in sorted(os.listdir(lists_dir)):
        era_dir = os.path.join(lists_dir, era)
        if not os.path.isdir(era_dir):
            continue
        for name in sorted(os.listdir(era_dir)):
            if not name.endswith(".txt"):
                continue
            with open(os.path.join(era_dir, name)) as handle:
                for line in handle:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        pairs.append((line, name[:-len(".txt")]))
    return pairs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--lists", help="MINIAODSIM list directory written by scripts/resolve_samples.py")
    source.add_argument("--dataset", nargs="+", help="MiniAOD dataset name(s)")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--eras", default=DEFAULT_ERAS)
    parser.add_argument("--out-dir", default=DEFAULT_OUT)
    parser.add_argument("--conditions", help="override the global tag read from the dataset name")
    parser.add_argument("--filein", help="input file for a test config (default: placeholder CRAB replaces)")
    parser.add_argument("-n", "--nevents", type=int, default=-1, help="events for a test config")
    parser.add_argument("--event-filter", choices=["auto", "on", "off"], default="auto",
                        help="write only events with a DiMuonVtx candidate; auto: backgrounds only")
    parser.add_argument("--any-release", action="store_true",
                        help="do not skip datasets that need the other CMSSW release (with --dry-run)")
    parser.add_argument("--force", action="store_true", help="regenerate configs that already exist")
    parser.add_argument("--dry-run", action="store_true", help="print cmsDriver commands only")
    args = parser.parse_args(argv)

    catalog = load_catalog(args.catalog)
    eras = load_eras(args.eras)
    test = args.filein is not None or args.nevents != -1
    filein = args.filein or "file:input.root"
    current = release_series(os.environ.get("CMSSW_VERSION"))
    if current is None and not (args.dry_run and args.any_release):
        sys.exit("Run inside a CMSSW area (cmsenv), or use --dry-run --any-release.")

    # strip(): names pasted or cut from files can carry a trailing \r or spaces
    pairs = read_lists(args.lists) if args.lists else [(d.strip(), None) for d in args.dataset]
    entries, skipped, errors = [], 0, []
    for dataset, process in pairs:
        try:
            info = describe(dataset, catalog, eras, args.conditions)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not args.any_release and info["release"] != current:
            skipped += 1
            continue
        info["process"] = process or catalog_process(info["primary"], catalog)
        info["event_filter"] = event_filter_for(info["process"], catalog, args.event_filter)
        info["cfg"] = os.path.join(args.out_dir, config_name(info, (filein, args.nevents) if test else None))
        entries.append(info)

    os.makedirs(args.out_dir, exist_ok=True)
    commands = {}
    for info in entries:
        commands.setdefault(info["cfg"], cmsdriver_command(info, info["cfg"], filein, args.nevents))
    failed = []
    for cfg_path, command in sorted(commands.items()):
        if args.dry_run:
            print(" ".join(command))
            continue
        if os.path.exists(cfg_path) and not args.force:
            print("exists, keeping: %s" % os.path.relpath(cfg_path))
            continue
        print("generating %s" % os.path.relpath(cfg_path))
        if subprocess.run(command).returncode != 0:
            failed.append(cfg_path)

    if not args.dry_run and not test:
        index_path = os.path.join(args.out_dir, "index.json")
        index = {}
        if os.path.exists(index_path):
            with open(index_path) as handle:
                index = json.load(handle)
        for info in entries:
            if info["cfg"] not in failed:
                index[info["dataset"]] = dict(info, cfg=os.path.relpath(info["cfg"], REPO))
        with open(index_path, "w") as handle:
            json.dump(index, handle, indent=1, sort_keys=True)
        print("index: %d datasets in %s" % (len(index), os.path.relpath(index_path)))

    print("%d datasets -> %d configs (%d datasets with the event filter); %d skipped (other release)"
          % (len(entries), len(commands), sum(info["event_filter"] for info in entries), skipped))
    for message in errors:
        print("error: " + message, file=sys.stderr)
    for cfg_path in failed:
        print("error: cmsDriver failed for %s" % cfg_path, file=sys.stderr)
    return 1 if errors or failed else 0


if __name__ == "__main__":
    sys.exit(main())
