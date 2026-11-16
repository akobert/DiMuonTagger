#!/usr/bin/env python3
"""Resolve samples/catalog.yaml against CMS DAS with dasgoclient.

Run on lxplus or the LPC after:

    source /cvmfs/cms.cern.ch/cmsset_default.sh
    voms-proxy-init -voms cms -rfc -valid 192:00

Typical use:

    # 1. Find the real names of the signal samples (unverified patterns)
    python3 scripts/resolve_samples.py --discover

    # 2. Resolve everything in the catalog (MINIAODSIM + NANOAODSIM)
    python3 scripts/resolve_samples.py

    # Only some processes / runs / tiers, without event counts or site checks
    python3 scripts/resolve_samples.py --process hahm_zd qcd_muenriched \
        --run run3 --tier MINIAODSIM --no-summary --no-sites

Outputs (default samples/resolved/):
    datasets.csv / datasets.json   every dataset found, with era, event counts and
                                   disk/tape sites (on_disk false = tape only)
    coverage.txt                   process x era table: on-disk/total preferred datasets
    tape_only.txt                  preferred datasets with no disk copy (need a recall)
    lists/<tier>/<era>/<process>.txt   preferred dataset names, one per line
    discovery_<run>_<tier>.txt     (--discover) candidate signal primaries

datasets.csv/json and coverage.txt describe only the latest run. List files are
replaced for the processes, runs and tiers that run queried (stale ones are
removed); lists outside that scope, or whose DAS query failed, are kept.
"""

import argparse
import csv
import datetime
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: run after `cmsenv`, or `python3 -m pip install --user pyyaml`.")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CATALOG = os.path.join(REPO, "samples", "catalog.yaml")
DEFAULT_OUT = os.path.join(REPO, "samples", "resolved")
VALID_TIERS = ("MINIAODSIM", "NANOAODSIM", "AODSIM")
UNCLASSIFIED = "unclassified"


# --------------------------------------------------------------------- DAS --

class DAS:
    """Thin wrapper around the dasgoclient executable."""

    def __init__(self, exe, retries=2, timeout=300):
        self.exe = exe
        self.retries = retries
        self.timeout = timeout

    def query(self, query, as_json=False):
        cmd = [self.exe, "-query", query]
        if as_json:
            cmd.append("-json")
        err = ""
        for attempt in range(self.retries + 1):
            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      universal_newlines=True, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                err = "timed out after %ds" % self.timeout
            else:
                if proc.returncode == 0:
                    return proc.stdout
                err = (proc.stderr or proc.stdout).strip() or "exit code %d" % proc.returncode
            if attempt < self.retries:
                time.sleep(2 ** attempt)
        raise RuntimeError("dasgoclient failed for %r: %s" % (query, err))

    def datasets(self, pattern, status):
        query = "dataset=%s" % pattern
        if status != "VALID":
            query += " status=%s" % status
        out = self.query(query)
        return sorted({line.strip() for line in out.splitlines()
                       if line.startswith("/") and line.count("/") == 3})

    def summary(self, dataset):
        return parse_summary(self.query("summary dataset=%s" % dataset, as_json=True))

    def sites(self, dataset):
        """Sites holding (part of) the dataset, split into disk and tape."""
        names = sorted({line.strip() for line in self.query("site dataset=%s" % dataset).splitlines()
                        if re.match(r"^T\d_\w+$", line.strip())})
        return split_sites(names)


# Storage that cannot serve jobs directly: tape and transfer endpoints.
_NOT_DISK = re.compile(r"_(Tape|MSS|Buffer|Export)$")


def split_sites(names):
    disk = [n for n in names if not _NOT_DISK.search(n)]
    tape = [n for n in names if _NOT_DISK.search(n)]
    return {"on_disk": bool(disk), "disk_sites": disk, "tape_sites": tape}


def parse_summary(text):
    """Pull event/file/size counts out of `summary` JSON, whatever its nesting."""
    keys = {"num_event": "nevents", "nevents": "nevents",
            "num_file": "nfiles", "nfiles": "nfiles",
            "file_size": "size_bytes"}
    found = {}

    def walk(obj):
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key in keys and isinstance(val, (int, float)):
                    found.setdefault(keys[key], val)
                else:
                    walk(val)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(json.loads(text))
    return {
        "nevents": found.get("nevents"),
        "nfiles": found.get("nfiles"),
        "size_gb": round(found["size_bytes"] / 1e9, 2) if "size_bytes" in found else None,
    }


def check_proxy():
    if not shutil.which("voms-proxy-info"):
        print("warning: voms-proxy-info not found; assuming a valid grid proxy exists",
              file=sys.stderr)
        return
    ok = subprocess.run(["voms-proxy-info", "-exists", "-valid", "0:30"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not ok:
        sys.exit("No grid proxy valid for 30 more minutes. Run:\n"
                 "  voms-proxy-init -voms cms -rfc -valid 192:00")


# ----------------------------------------------------------------- catalog --

def load_catalog(path):
    with open(path) as handle:
        cat = yaml.safe_load(handle)
    problems = []
    for rdef in cat.get("runs", {}).values():
        # YAML turns bare era names like 2018 into ints; eras are used as path names.
        rdef["eras"] = {str(era): tiers for era, tiers in rdef.get("eras", {}).items()}
    for run, rdef in cat.get("runs", {}).items():
        if not rdef.get("campaign_globs"):
            problems.append("runs.%s has no campaign_globs" % run)
        for era, tiers in rdef.get("eras", {}).items():
            for tier, regex in tiers.items():
                try:
                    re.compile(regex)
                except re.error as exc:
                    problems.append("runs.%s.eras.%s.%s: bad regex (%s)" % (run, era, tier, exc))
    for name, proc in cat.get("processes", {}).items():
        if proc.get("role") not in ("signal", "background", "control"):
            problems.append("processes.%s: role must be signal/background/control" % name)
        for run in proc.get("patterns", {}):
            if run not in cat["runs"]:
                problems.append("processes.%s: unknown run %r" % (name, run))
    if problems:
        sys.exit("Catalog errors in %s:\n  %s" % (path, "\n  ".join(problems)))
    return cat


def classify_era(cat, run, tier, processed):
    for era, tiers in cat["runs"][run]["eras"].items():
        regex = tiers.get(tier)
        if regex and re.search(regex, processed):
            return era
    return UNCLASSIFIED


def format_version(processed):
    """MiniAOD/NanoAOD format version from the processed name (RECO -> 0)."""
    match = re.search(r"(?:MiniAOD|NanoAOD)(?:APV)?(?:v(\d+))?-", processed)
    if not match:
        return 0
    return int(match.group(1) or 1)


# --------------------------------------------------------------- resolving --

def run_parallel(func, items, workers):
    """Apply func to items in a thread pool; return ({item: result}, {item: error})."""
    results, errors = {}, {}

    def wrapped(item):
        try:
            return item, func(item), None
        except Exception as exc:  # report and carry on with the rest
            return item, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for item, result, error in pool.map(wrapped, items):
            if error is None:
                results[item] = result
            else:
                errors[item] = error
    return results, errors


def build_queries(cat, processes, runs, tiers):
    """One DAS pattern per (process, run, tier, primary pattern, campaign glob)."""
    jobs = []
    for name in processes:
        for run in runs:
            for primary in cat["processes"][name].get("patterns", {}).get(run, []):
                for glob in cat["runs"][run]["campaign_globs"]:
                    for tier in tiers:
                        jobs.append((name, run, tier, primary, "/%s/%s/%s" % (primary, glob, tier)))
    return jobs


def resolve(cat, das, args, processes, runs, tiers):
    jobs = build_queries(cat, processes, runs, tiers)
    if args.dry_run:
        for job in jobs:
            print("dataset=%s" % job[4])
        return 0

    patterns = sorted({job[4] for job in jobs})
    print("Querying DAS: %d patterns, %d workers ..." % (len(patterns), args.workers))
    found, failures = run_parallel(lambda p: das.datasets(p, args.status), patterns, args.workers)
    # (process, run, tier) whose dataset list may be incomplete
    failed_scopes = {(name, run, tier) for name, run, tier, _, pattern in jobs if pattern in failures}

    records = {}          # (process, dataset) -> record
    pattern_hits = defaultdict(int)  # (process, run, primary pattern) -> n datasets
    for name, run, tier, primary, pattern in jobs:
        for dataset in found.get(pattern, []):
            pattern_hits[(name, run, primary)] += 1
            _, prim, processed, _ = dataset.split("/")
            proc = cat["processes"][name]
            records[(name, dataset)] = {
                "role": proc["role"], "priority": proc.get("priority", ""),
                "process": name, "run": run, "era": classify_era(cat, run, tier, processed),
                "tier": tier, "primary": prim, "dataset": dataset,
                "format_version": format_version(processed),
            }

    # Preferred = newest MiniAOD/NanoAOD format within (process, primary, era, tier).
    newest = defaultdict(int)
    for rec in records.values():
        key = (rec["process"], rec["primary"], rec["era"], rec["tier"])
        newest[key] = max(newest[key], rec["format_version"])
    for rec in records.values():
        rec["preferred"] = rec["era"] != UNCLASSIFIED and \
            rec["format_version"] == newest[(rec["process"], rec["primary"], rec["era"], rec["tier"])]

    unique = sorted({rec["dataset"] for rec in records.values()})
    if args.no_summary:
        summaries = {}
    else:
        print("Fetching event counts for %d datasets ..." % len(unique))
        summaries, sum_failures = run_parallel(das.summary, unique, args.workers)
        failures.update({"summary " + k: v for k, v in sum_failures.items()})
    for rec in records.values():
        rec.update(summaries.get(rec["dataset"], {"nevents": None, "nfiles": None, "size_gb": None}))

    if args.no_sites:
        sites = {}
    else:
        print("Checking disk/tape sites for %d datasets ..." % len(unique))
        sites, site_failures = run_parallel(das.sites, unique, args.workers)
        failures.update({"sites " + k: v for k, v in site_failures.items()})
    for rec in records.values():
        rec.update(sites.get(rec["dataset"], {"on_disk": None, "disk_sites": [], "tape_sites": []}))

    rows = sorted(records.values(), key=lambda r: (
        ["signal", "background", "control"].index(r["role"]), r["process"], r["run"],
        era_index(cat, r["run"], r["era"]), r["tier"], r["dataset"]))
    write_outputs(cat, args, rows, processes, runs, tiers, failed_scopes)

    empty = [(n, r, p) for n, r, t, p, _ in jobs if pattern_hits[(n, r, p)] == 0]
    if empty:
        print("\nPatterns that matched nothing (fix names in the catalog or check --discover):")
        for name, run, primary in sorted(set(empty)):
            print("  %-28s %-5s %s" % (name, run, primary))
    owners = defaultdict(set)
    for rec in rows:
        owners[rec["dataset"]].add(rec["process"])
    shared = {ds: names for ds, names in owners.items() if len(names) > 1}
    if shared:
        print("\nDatasets claimed by more than one process (tighten the patterns):")
        for dataset, names in sorted(shared.items()):
            print("  %s: %s" % (",".join(sorted(names)), dataset))
    unclassified = [r["dataset"] for r in rows if r["era"] == UNCLASSIFIED]
    if unclassified:
        print("\n%d datasets did not match any era regex (listed in datasets.csv as '%s')."
              % (len(unclassified), UNCLASSIFIED))
    return report_failures(failures)


def era_index(cat, run, era):
    eras = list(cat["runs"][run]["eras"])
    return eras.index(era) if era in eras else len(eras)


def write_outputs(cat, args, rows, processes, runs, tiers, failed_scopes=frozenset()):
    os.makedirs(args.out, exist_ok=True)
    fields = ["role", "priority", "process", "run", "era", "tier", "preferred",
              "nevents", "nfiles", "size_gb", "on_disk", "disk_sites", "tape_sites", "dataset"]
    with open(os.path.join(args.out, "datasets.csv"), "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(rec, disk_sites=" ".join(rec["disk_sites"]),
                              tape_sites=" ".join(rec["tape_sites"])) for rec in rows)
    with open(os.path.join(args.out, "datasets.json"), "w") as handle:
        json.dump({"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                   "catalog": os.path.relpath(args.catalog, REPO),
                   "status": args.status, "datasets": rows}, handle, indent=1)

    write_lists(cat, args.out, rows, processes, runs, tiers, failed_scopes)
    write_tape_only(args.out, rows)

    table = coverage_table(cat, rows, processes, runs, tiers)
    with open(os.path.join(args.out, "coverage.txt"), "w") as handle:
        handle.write(table)
    print("\n" + table)
    print("Wrote %d datasets to %s" % (len(rows), display_path(args.out)))
    if args.process or args.role or args.run or args.tier:
        print("Filtered run: datasets.csv, datasets.json and coverage.txt cover only the "
              "processes, runs and tiers queried this time.")


def write_tape_only(out, rows):
    """tape_only.txt: preferred datasets with no disk copy (they need a recall
    before jobs can read them). Removed when sites were not checked, so it never
    describes an older run."""
    path = os.path.join(out, "tape_only.txt")
    checked = [rec for rec in rows if rec["preferred"] and rec["on_disk"] is not None]
    if not checked:
        if os.path.exists(path):
            os.remove(path)
        return
    tape = [rec for rec in checked if not rec["on_disk"]]
    with open(path, "w") as handle:
        handle.write("# preferred datasets without a disk copy: process era tier size_gb tape_sites dataset\n")
        for rec in tape:
            handle.write("%s %s %s %s %s %s\n" % (rec["process"], rec["era"], rec["tier"], rec["size_gb"],
                                                  ",".join(rec["tape_sites"]) or "-", rec["dataset"]))
    size = sum(rec["size_gb"] or 0 for rec in tape)
    print("\n%d of %d preferred datasets have no disk copy (%.0f GB): %s"
          % (len(tape), len(checked), size, display_path(path)))


def list_path(out, tier, era, process):
    return os.path.join(out, "lists", tier, era, process + ".txt")


def write_lists(cat, out, rows, processes, runs, tiers, failed_scopes):
    """Rewrite lists/<tier>/<era>/<process>.txt for everything this run queried.

    Old list files in the queried (process, run, tier) scope are removed first,
    so an era that no longer has datasets for a process loses its list instead
    of keeping a stale one. Lists outside the scope (processes, runs or tiers
    not queried this time) are left alone. A scope with a failed DAS query keeps
    its old lists untouched, since the new result may be incomplete.
    """
    lists = defaultdict(list)
    for rec in rows:
        if rec["preferred"] and (rec["process"], rec["run"], rec["tier"]) not in failed_scopes:
            lists[list_path(out, rec["tier"], rec["era"], rec["process"])].append(rec["dataset"])

    removed = set()
    for name in processes:
        for run in runs:
            if run not in cat["processes"][name].get("patterns", {}):
                continue
            for tier in tiers:
                if (name, run, tier) in failed_scopes:
                    continue
                for era in cat["runs"][run]["eras"]:
                    path = list_path(out, tier, era, name)
                    if os.path.exists(path):
                        os.remove(path)
                        removed.add(path)

    for path, datasets in lists.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write("\n".join(datasets) + "\n")

    stale = sorted(removed - set(lists))
    if stale:
        print("\nRemoved %d stale list file(s), no datasets found this time:" % len(stale))
        for path in stale:
            print("  " + display_path(path))
    if failed_scopes:
        print("\nKept the previous lists (DAS query failed) for:")
        for name, run, tier in sorted(failed_scopes):
            print("  %-28s %-5s %s" % (name, run, tier))


def display_path(path):
    rel = os.path.relpath(path)
    return path if rel.startswith("..") else rel


def coverage_table(cat, rows, processes, runs, tiers):
    """Per process and era: preferred datasets and events (M) for each tier.

    With site information each cell reads `on-disk/total (events)`.
    """
    counts = defaultdict(lambda: [0, 0, 0, 0])  # n, events, n on disk, n with site info
    for rec in rows:
        if rec["preferred"]:
            cell = counts[(rec["process"], rec["era"], rec["tier"])]
            cell[0] += 1
            cell[1] += rec["nevents"] or 0
            if rec.get("on_disk") is not None:
                cell[3] += 1
                cell[2] += int(rec["on_disk"])
    with_sites = any(c[3] for c in counts.values())
    lines = ["Coverage: preferred datasets per era%s (events in millions); '-' = none found"
             % (" as on-disk/total" if with_sites else ""), ""]
    width = 17
    for run in runs:
        eras = list(cat["runs"][run]["eras"])
        for tier in tiers:
            header = "%-28s" % ("%s %s" % (run, tier)) + "".join("%*s" % (width, e) for e in eras)
            lines += [header, "-" * len(header)]
            for name in processes:
                if run not in cat["processes"][name].get("patterns", {}):
                    continue
                cells = []
                for era in eras:
                    n, ev, n_disk, n_known = counts.get((name, era, tier), (0, 0, 0, 0))
                    if n == 0:
                        text = "-"
                    else:
                        text = "%d/%d" % (n_disk, n) if n_known == n else "%d" % n
                        if ev:
                            text += " (%.1f)" % (ev / 1e6)
                    cells.append("%*s" % (width, text))
                lines.append("%-28s" % name + "".join(cells))
            lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------- discovery --

def discover(cat, das, args, runs, tiers):
    jobs = [(run, tier, "/%s/%s/%s" % (pat, glob, tier))
            for run in runs for tier in tiers
            for pat in cat.get("discovery_patterns", [])
            for glob in cat["runs"][run]["campaign_globs"]]
    if args.dry_run:
        for job in jobs:
            print("dataset=%s" % job[2])
        return 0
    print("Discovery: %d DAS patterns, %d workers ..." % (len(jobs), args.workers))
    found, failures = run_parallel(lambda p: das.datasets(p, args.status),
                                   sorted({j[2] for j in jobs}), args.workers)

    known = [(name, pat) for name, proc in cat["processes"].items()
             for pats in proc.get("patterns", {}).values() for pat in pats]
    os.makedirs(args.out, exist_ok=True)
    for run in runs:
        for tier in tiers:
            eras_by_primary = defaultdict(set)
            for jrun, jtier, pattern in jobs:
                if (jrun, jtier) != (run, tier):
                    continue
                for dataset in found.get(pattern, []):
                    _, prim, processed, _ = dataset.split("/")
                    eras_by_primary[prim].add(classify_era(cat, run, tier, processed))
            lines = []
            for prim in sorted(eras_by_primary):
                owners = sorted({n for n, p in known if fnmatch.fnmatchcase(prim, p)})
                eras = sorted(eras_by_primary[prim], key=lambda e: era_index(cat, run, e))
                lines.append("%-90s  %-40s  %s" % (prim, ",".join(eras),
                                                   ",".join(owners) or "(not in catalog)"))
            path = os.path.join(args.out, "discovery_%s_%s.txt" % (run, tier))
            with open(path, "w") as handle:
                handle.write("# primary dataset  eras  catalog process\n")
                handle.write("\n".join(lines) + ("\n" if lines else ""))
            print("%s %s: %d candidate primary datasets -> %s"
                  % (run, tier, len(lines), display_path(path)))
    return report_failures(failures)


def report_failures(failures):
    if not failures:
        return 0
    sys.stdout.flush()
    print("\n%d DAS queries failed (results above are incomplete):" % len(failures),
          file=sys.stderr)
    for item, error in sorted(failures.items()):
        print("  %s\n    %s" % (item, error), file=sys.stderr)
    return 1


# -------------------------------------------------------------------- main --

def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--out", default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--discover", action="store_true",
                        help="list primary datasets matching the catalog's discovery_patterns")
    parser.add_argument("--process", nargs="+", help="only these catalog processes")
    parser.add_argument("--role", nargs="+", choices=["signal", "background", "control"])
    parser.add_argument("--run", nargs="+", help="only these runs (e.g. run2 run3)")
    parser.add_argument("--tier", nargs="+", choices=VALID_TIERS,
                        help="data tiers (default: catalog default_tiers; MINIAODSIM for --discover)")
    parser.add_argument("--status", default="VALID",
                        help="DAS dataset status, e.g. VALID, PRODUCTION or '*' (default VALID)")
    parser.add_argument("--no-summary", action="store_true",
                        help="skip per-dataset event/file counts (much faster)")
    parser.add_argument("--no-sites", action="store_true",
                        help="skip the per-dataset disk/tape site check")
    parser.add_argument("--workers", type=int, default=6, help="parallel dasgoclient calls")
    parser.add_argument("--dry-run", action="store_true", help="print DAS queries and exit")
    parser.add_argument("--dasgoclient", default="dasgoclient", help="dasgoclient executable")
    parser.add_argument("--skip-proxy-check", action="store_true")
    args = parser.parse_args(argv)
    args.catalog = os.path.abspath(args.catalog)
    args.out = os.path.abspath(args.out)

    cat = load_catalog(args.catalog)
    runs = args.run or list(cat["runs"])
    unknown = [r for r in runs if r not in cat["runs"]]
    if unknown:
        parser.error("unknown run(s) %s; catalog has %s" % (unknown, list(cat["runs"])))
    tiers = args.tier or (["MINIAODSIM"] if args.discover else cat.get("default_tiers", ["MINIAODSIM"]))

    processes = list(cat["processes"])
    if args.process:
        unknown = [p for p in args.process if p not in cat["processes"]]
        if unknown:
            parser.error("unknown process(es) %s; catalog has %s" % (unknown, processes))
        processes = [p for p in processes if p in args.process]
    if args.role:
        processes = [p for p in processes if cat["processes"][p]["role"] in args.role]

    if not args.dry_run:
        if not shutil.which(args.dasgoclient):
            sys.exit("%s not found. On lxplus/LPC: source /cvmfs/cms.cern.ch/cmsset_default.sh"
                     % args.dasgoclient)
        if not args.skip_proxy_check:
            check_proxy()

    das = DAS(args.dasgoclient)
    if args.discover:
        return discover(cat, das, args, runs, tiers)
    return resolve(cat, das, args, processes, runs, tiers)


if __name__ == "__main__":
    sys.exit(main())
