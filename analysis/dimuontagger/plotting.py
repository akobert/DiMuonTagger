"""Plots and tables from a saved run (analysis/outputs/<tag>/hists.coffea).

- Shape comparisons: per era (and all eras together), every candidate
  histogram with one normalised curve per truth category, signal first.
  Datasets are combined with weight 1/sumw each (each dataset counts equally
  per generated event); cross sections are not applied.
- Lxy residual (reco - true) for signal candidates.
- Cutflow table per dataset.
- Ancestor pdgId table per dataset, to check the configured LLP pdgIds.
"""

import csv
import os

import numpy as np

from . import truth
from .processor import CANDIDATE_AXES, LEG_AXES

# One fixed colour per category, in category order (colour follows the
# category, never its rank), plus a line style so identity is not colour-only.
# Hues: the first six slots of the dataviz reference categorical palette.
CATEGORY_STYLE = {
    "signal":        {"color": "#2a78d6", "linestyle": "-", "linewidth": 2.6},
    "llp_partial":   {"color": "#eb6834", "linestyle": (0, (6, 2)), "linewidth": 1.8},
    "heavy_flavour": {"color": "#1baf7a", "linestyle": (0, (2, 1.5)), "linewidth": 1.8},
    "prompt":        {"color": "#eda100", "linestyle": (0, (6, 2, 1.5, 2)), "linewidth": 1.8},
    "unmatched":     {"color": "#e87ba4", "linestyle": (0, (1, 1.5)), "linewidth": 1.8},
    "other":         {"color": "#008300", "linestyle": (0, (4, 1.5, 1, 1.5, 1, 1.5)), "linewidth": 1.8},
    "data":          {"color": "#0b0b0b", "linestyle": "-", "linewidth": 1.8},
}
LOG_X = {"lxy", "lxy_cp", "lxy_sig", "dca", "leg_dxy_sig"}
LOG_Y = {"mass", "lxy", "lxy_cp", "lxy_sig", "dca", "leg_dxy_sig", "norm_chi2"}
ALL_ERAS = "all"


def load(path):
    from coffea.util import load as coffea_load

    return coffea_load(path)


def era_groups(record, eras=None):
    """{era: [datasets]} plus 'all' (every dataset)."""
    groups = {}
    for name in record["output"]:
        era = record["metadata"].get(name, {}).get("era", "unknown")
        groups.setdefault(era, []).append(name)
    if eras:
        groups = {e: d for e, d in groups.items() if e in eras}
    out = {e: sorted(groups[e]) for e in sorted(groups)}
    if len(out) > 1 or not eras:
        out[ALL_ERAS] = sorted(n for names in groups.values() for n in names)
    return out


def combined(record, name, datasets):
    """Sum of hists[name] over datasets, each scaled by 1/sumw (if sumw > 0)."""
    total = None
    for ds in datasets:
        out = record["output"][ds]
        h = out["hists"][name]
        h = h * (1.0 / out["sumw"]) if out["sumw"] > 0 else h.copy()
        total = h if total is None else total + h
    return total


def _category_curve(h, category, var):
    sub = h[{"category": category}]
    if "leg" in sub.axes.name:
        sub = sub.project(var)
    values = sub.values()
    variances = sub.variances()
    return values, (variances if variances is not None else values)


def _edges(axis):
    if hasattr(axis, "edges") and axis.traits.continuous:
        return np.asarray(axis.edges)
    return np.arange(len(axis) + 1) - 0.5


def shape_plot(record, var, datasets, era_label, path, categories=None):
    """One normalised curve per non-empty category; returns path or None."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import mplhep as hep

    h = combined(record, var, datasets)
    if h is None:
        return None
    axis = h.axes[var]
    edges = _edges(axis)
    hep.style.use("CMS")
    fig, ax = plt.subplots(figsize=(10, 8))
    drawn = 0
    for cat in categories or truth.ALL_CATEGORIES:
        values, variances = _category_curve(h, cat, var)
        total = values.sum()
        if total <= 0:
            continue
        hep.histplot(values / total, edges, yerr=np.sqrt(np.abs(variances)) / total, ax=ax,
                     label="%s (%.0f%%)" % (cat, 100 * total / max(h.values().sum(), 1e-300)),
                     histtype="step", **CATEGORY_STYLE.get(cat, {}))
        drawn += 1
    if not drawn:
        plt.close(fig)
        return None
    if var in LOG_X:
        ax.set_xscale("log")
        ax.set_xlim(edges[1], edges[-1])
    if not axis.traits.continuous:
        ax.set_xticks(np.arange(len(axis)))
        ax.set_xticklabels([str(v) for v in axis])
    if var in LOG_Y:
        ax.set_yscale("log")
    ax.set_xlabel(axis.label)
    ax.set_ylabel("fraction of candidates per bin")
    ax.legend(title="%s\ntruth category (share)" % era_label, fontsize="small", title_fontsize="small")
    hep.cms.label(text="Private Work", data=False, rlabel="(13.6 TeV)", ax=ax)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def residual_plot(record, datasets, era_label, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import mplhep as hep

    h = combined(record, "lxy_residual", datasets)
    if h is None or h.values().sum() <= 0:
        return None
    hep.style.use("CMS")
    fig, ax = plt.subplots(figsize=(10, 8))
    styles = {"fit": CATEGORY_STYLE["signal"], "crossing": CATEGORY_STYLE["llp_partial"]}
    for method in ("fit", "crossing"):
        sub = h[{"method": method}].project("residual")
        total = sub.values().sum()
        if total <= 0:
            continue
        hep.histplot(sub.values() / total, sub.axes[0].edges, ax=ax, histtype="step",
                     label={"fit": "vertex fit", "crossing": "two-track crossing point"}[method],
                     **styles[method])
    ax.set_xlabel(h.axes["residual"].label)
    ax.set_ylabel("fraction of signal candidates per bin")
    ax.set_yscale("log")
    ax.legend(title="%s, signal" % era_label, fontsize="small", title_fontsize="small")
    hep.cms.label(text="Private Work", data=False, rlabel="(13.6 TeV)", ax=ax)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def all_plots(record, outdir, eras=None, formats=("png",)):
    """Every shape and residual plot; returns the written paths."""
    written = []
    variables = list(CANDIDATE_AXES) + list(LEG_AXES)
    for era, datasets in era_groups(record, eras).items():
        label = "all eras" if era == ALL_ERAS else era
        for fmt in formats:
            for var in variables:
                path = shape_plot(record, var, datasets, label, os.path.join(outdir, era, "%s.%s" % (var, fmt)))
                if path:
                    written.append(path)
            path = residual_plot(record, datasets, label, os.path.join(outdir, era, "lxy_residual.%s" % fmt))
            if path:
                written.append(path)
    return written


# ------------------------------------------------------------------ tables --

CUTFLOW_COLUMNS = ("events_generated", "sumw", "events", "sumw_events", "events_with_candidate", "candidates", "candidates_bad_leg_index",
                   "candidates_used", "candidates_fit_valid") + tuple("cat_" + c for c in truth.CATEGORIES) \
    + ("legs_gen_matched", "legs_walk_truncated", "skim_rows")


def cutflow_rows(record):
    rows = []
    for name in sorted(record["output"]):
        out = record["output"][name]
        meta = record["metadata"].get(name, {})
        row = {"dataset": name, "process": meta.get("process", ""), "era": meta.get("era", "")}
        for col in CUTFLOW_COLUMNS:
            row[col] = out.get(col, 0.0) if col.startswith("sumw") else out["cutflow"].get(col, 0)
        rows.append(row)
    return rows


def markdown_table(header, rows):
    def fmt(value):
        return "%.6g" % value if isinstance(value, float) else str(value)
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(fmt(row[h]) for h in header) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def write_cutflow(record, outdir):
    rows = cutflow_rows(record)
    header = ["dataset", "process", "era"] + list(CUTFLOW_COLUMNS)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "cutflow.csv"), "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")  # no \r for shell tools
        writer.writeheader()
        writer.writerows(rows)
    text = "# Cutflow\n\nRaw counts. `events_generated` and `sumw` (genWeight) count every event the NANO " \
        "job processed (Runs tree); `events` and `sumw_events` count the events in the files, which for " \
        "backgrounds are only events with a candidate.\n\n" \
        + markdown_table(header, rows)
    with open(os.path.join(outdir, "cutflow.md"), "w") as handle:
        handle.write(text)
    return rows


def ancestor_rows(record, top=12):
    """Per dataset: the most frequent ancestor |pdgId|s of gen-matched muons."""
    from .config import llp_pdgid_list

    llp_ids = set(llp_pdgid_list(record["config"])) if record.get("config") else set()
    out = {}
    for name in sorted(record["output"]):
        res = record["output"][name]
        h = res["hists"]["ancestor_pdgid"]
        mothers = res["hists"]["mother_pdgid"]
        nlegs = res["cutflow"].get("legs_gen_matched", 0)
        mother_counts = dict(zip((int(v) for v in mothers.axes[0]), mothers.values()))
        entries = [(int(pdg), float(count)) for pdg, count in zip(h.axes[0], h.values())]
        entries.sort(key=lambda e: (-e[1], e[0]))
        out[name] = {
            "role": record["metadata"].get(name, {}).get("role", ""),
            "legs": nlegs,
            "llp_found": sorted(p for p, c in entries if p in llp_ids and c > 0),
            "rows": [{"pdgid": p, "count": c, "fraction": c / nlegs if nlegs else 0.0,
                      "as_mother": float(mother_counts.get(p, 0.0)), "configured_llp": p in llp_ids}
                     for p, c in entries[:top]],
        }
    return out


def write_ancestors(record, outdir, top=12):
    from .config import llp_pdgid_list

    table = ancestor_rows(record, top)
    llp_ids = llp_pdgid_list(record["config"]) if record.get("config") else []
    lines = ["# Ancestors of gen-matched muons", "",
             "Configured LLP pdgIds: %s (verified: %s). `fraction` = ancestor entries / gen-matched legs; "
             "copies of the same particle count once, so it is roughly the fraction of muons with that "
             "ancestor. **LLP** marks configured LLP pdgIds." % (
                 ", ".join(map(str, llp_ids)) or "none",
                 record.get("config", {}).get("truth", {}).get("llp_pdgids_verified", False)), ""]
    warnings = []
    for name, info in table.items():
        lines += ["## %s" % name, "", "gen-matched legs: %d, role: %s" % (info["legs"], info["role"] or "?"), ""]
        header = ["pdgid", "count", "fraction", "as_mother", "configured_llp"]
        rows = [dict(r, configured_llp="**LLP**" if r["configured_llp"] else "") for r in info["rows"]]
        lines.append(markdown_table(header, rows))
        if info["role"] == "signal" and not info["llp_found"]:
            warnings.append(name)
    if warnings:
        lines += ["## Warning", "", "Signal datasets with no configured LLP pdgId among the muon ancestors "
                  "(fix truth.llp_pdgids in analysis/config.yaml, or check GenPart pruning):", ""]
        lines += ["- %s" % w for w in warnings]
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "ancestors.md"), "w") as handle:
        handle.write("\n".join(lines) + "\n")
    return table, warnings
