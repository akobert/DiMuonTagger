"""Build coffea filesets from the published DiMuonNano datasets.

Runs on lxplus (or the LPC), which has dasgoclient and a grid proxy. Needs only
the standard library and PyYAML, not coffea.

CRAB (nanoaod/crab/submit.py) publishes each task in DBS ``prod/phys03`` as::

    /<primary>/<user>-DiMuonNano_<tag>_<era>-<pset hash>/USER

For each such dataset this module lists its files, prefixes the LFNs with a
redirector (``root://xcache/`` on coffea-casa), and attaches metadata:

- ``process`` and ``role``: the samples/catalog.yaml process whose run-3
  patterns match the primary dataset name (fnmatch, case-sensitive)
- ``era``: from the dataset name; if it is missing there, from the parent
  MiniAOD dataset, classified with the catalog's MINIAODSIM era regexes
- ``das_dataset``, ``primary``, ``tag``, ``nfiles`` and, unless skipped,
  ``nevents``

The output is a coffea fileset,
``{name: {"files": {url: "Events"}, "metadata": {...}}}``, keyed
``<process>/<era>/<primary>``. (``dataset`` is a reserved metadata key in
coffea, hence ``das_dataset``.)
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
import time

from .config import REPO_DIR

DAS_INSTANCE = "prod/phys03"
DEFAULT_REDIRECTOR = "root://xcache/"
DEFAULT_CATALOG = os.path.join(REPO_DIR, "samples", "catalog.yaml")
TREE = "Events"
RUN = "run3"
UNKNOWN = "unknown"


# --------------------------------------------------------------------- DAS --

def dasgoclient_query(query, as_json=False, exe="dasgoclient", retries=2, timeout=300):
    """Run one dasgoclient query; return its non-empty output lines."""
    cmd = [exe, "-query", query] + (["-json"] if as_json else [])
    err = ""
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  universal_newlines=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            err = "timed out after %ds" % timeout
        else:
            if proc.returncode == 0:
                return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            err = (proc.stderr or proc.stdout).strip() or "exit code %d" % proc.returncode
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise RuntimeError("dasgoclient failed for %r: %s" % (query, err))


# ----------------------------------------------------------------- catalog --

def load_catalog(path=DEFAULT_CATALOG):
    import yaml

    with open(path) as handle:
        cat = yaml.safe_load(handle)
    for rdef in cat.get("runs", {}).values():
        # YAML turns bare era names like 2024 into ints
        rdef["eras"] = {str(era): tiers for era, tiers in rdef.get("eras", {}).items()}
    return cat


def match_processes(primary, catalog, run=RUN):
    """Catalog processes whose `run` patterns match the primary dataset name."""
    return [name for name, proc in catalog.get("processes", {}).items()
            if any(fnmatch.fnmatchcase(primary, pat) for pat in proc.get("patterns", {}).get(run, []))]


def era_from_name(processed, tag, eras):
    """Era from '<user>-DiMuonNano_<tag>_<era>-<hash>', if it is a known era."""
    match = re.search(r"-DiMuonNano_%s_([^-_]+)-[0-9a-fA-F]+$" % re.escape(tag), processed)
    if match and match.group(1) in eras:
        return match.group(1)
    return None


def era_from_parent(parent_processed, catalog, run=RUN, tier="MINIAODSIM"):
    for era, tiers in catalog["runs"][run]["eras"].items():
        regex = tiers.get(tier)
        if regex and re.search(regex, parent_processed):
            return era
    return None


def file_url(lfn, redirector=DEFAULT_REDIRECTOR):
    """'/store/...' -> 'root://xcache//store/...' (redirector + LFN)."""
    return redirector.rstrip("/") + "/" + lfn


def parse_nevents(summary_json):
    """nevents from `summary` JSON, whatever its nesting."""
    def walk(obj):
        if isinstance(obj, dict):
            for key in ("nevents", "num_event"):
                if isinstance(obj.get(key), (int, float)):
                    return int(obj[key])
            obj = list(obj.values())
        if isinstance(obj, list):
            for item in obj:
                found = walk(item)
                if found is not None:
                    return found
        return None
    return walk(json.loads(summary_json))


# --------------------------------------------------------------- building --

def has_tag(processed, tag):
    """True for '<user>-DiMuonNano_<tag>[_<era>]-<hash>' (so tag v1 does not take v12)."""
    return re.search(r"-DiMuonNano_%s(_[^-_]+)?-[0-9a-fA-F]+$" % re.escape(tag), processed) is not None


def find_datasets(tag, user, query):
    """Published datasets of this tag; also returns those the wildcard caught for other tags."""
    pattern = "/*/%s-DiMuonNano_%s*/USER" % (user, tag)
    lines = query("dataset=%s instance=%s" % (pattern, DAS_INSTANCE))
    found = sorted({line for line in lines if line.startswith("/") and line.count("/") == 3})
    keep = [d for d in found if has_tag(d.split("/")[2], tag)]
    return keep, [d for d in found if d not in keep]


def build_fileset(tag, user, catalog, query, redirector=DEFAULT_REDIRECTOR, summary=True, log=None):
    """Return (fileset, problems).

    `query(text, as_json=False) -> list of lines` runs one DAS query
    (``dasgoclient_query`` on lxplus, a fake in the tests).
    """
    log = log or (lambda msg: print(msg, file=sys.stderr))
    eras = set(catalog["runs"][RUN]["eras"])
    datasets, other_tags = find_datasets(tag, user, query)
    problems = ["%s: different tag; skipped" % d for d in other_tags]
    if not datasets:
        return {}, problems + ["no datasets match /*/%s-DiMuonNano_%s*/USER in %s" % (user, tag, DAS_INSTANCE)]

    fileset = {}
    for das in datasets:
        _, primary, processed, _ = das.split("/")
        procs = match_processes(primary, catalog)
        if not procs:
            problems.append("%s: primary matches no catalog process; process=%s" % (das, UNKNOWN))
            process, role = UNKNOWN, UNKNOWN
        else:
            if len(procs) > 1:
                problems.append("%s: matches several processes %s; using %s" % (das, procs, procs[0]))
            process = procs[0]
            role = catalog["processes"][process].get("role", UNKNOWN)

        era = era_from_name(processed, tag, eras)
        if era is None:
            parents = query("parent dataset=%s instance=%s" % (das, DAS_INSTANCE))
            parents = [p for p in parents if p.count("/") == 3]
            era = era_from_parent(parents[0].split("/")[2], catalog) if parents else None
            if era is None:
                problems.append("%s: era not found in the name or parent; era=%s" % (das, UNKNOWN))
                era = UNKNOWN

        lfns = sorted(f for f in query("file dataset=%s instance=%s" % (das, DAS_INSTANCE))
                      if f.startswith("/store/"))
        if not lfns:
            problems.append("%s: no files; skipped" % das)
            continue
        meta = {
            "process": process, "role": role, "era": era,
            "das_dataset": das, "primary": primary, "tag": tag, "nfiles": len(lfns),
        }
        if summary:
            text = "\n".join(query("summary dataset=%s instance=%s" % (das, DAS_INSTANCE), as_json=True))
            try:
                meta["nevents"] = parse_nevents(text)
            except ValueError:
                problems.append("%s: could not parse event count" % das)

        name = "%s/%s/%s" % (process, era, primary)
        if name in fileset:
            digest = processed.rsplit("-", 1)[-1][:8]
            problems.append("%s: second dataset for %s (another CRAB task?); stored as %s_%s"
                            % (das, name, name, digest))
            name = "%s_%s" % (name, digest)
        fileset[name] = {"files": {file_url(lfn, redirector): TREE for lfn in lfns}, "metadata": meta}
        log("%-70s %4d files  %s" % (name, len(lfns), das))
    return fileset, problems


def write_fileset(fileset, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as handle:
        json.dump(fileset, handle, indent=1, sort_keys=True)
        handle.write("\n")
