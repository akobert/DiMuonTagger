#!/usr/bin/env python3
"""Sanity-check a DiMuonTagger custom NanoAOD file.

    python3 check_nano.py nano.root [more.root ...]

Checks that the custom branches exist, that DiMuonVtx leg indices point at
existing Muon/DisplacedMuon rows, and prints a short summary (candidates per
event, pair types, fit success, Lxy quantiles, gen-match rate). Exits non-zero
if a branch is missing or an index is out of range. Needs uproot and awkward
(both in CMSSW's Python 3).
"""

import argparse
import sys

import awkward as ak
import numpy as np
import uproot

# The custom branch spec shared with the coffea framework in analysis/.
EXPECTED = {
    "GenPart": ["vx", "vy", "vz"],
    "DisplacedMuon": ["pt", "eta", "phi", "mass", "charge", "isGlobal", "isStandalone", "isTracker",
                      "nValidMuonHits", "nMuonStations", "normChi2", "ptErr", "genPartIdx", "genPartFlav",
                      "muonIdx", "muonDR"],
    "DiMuonVtx": ["pt", "eta", "phi", "mass", "charge", "mu1Idx", "mu2Idx", "mu1Type", "mu2Type",
                  "valid", "momentaAtVertex", "vx", "vy", "vz", "vxErr", "vyErr", "vzErr",
                  "chi2", "ndof", "prob", "lxyBS", "lxyBSErr", "lxyPV", "lxyPVErr", "l3dPV", "l3dPVErr",
                  "cosAlphaXY", "cosAlpha3D", "dca", "cpx", "cpy", "cpz", "lxyCP",
                  "mu1RefitPt", "mu1RefitEta", "mu1RefitPhi", "mu2RefitPt", "mu2RefitEta", "mu2RefitPhi",
                  "mu1DxyBS", "mu1DxyBSErr", "mu2DxyBS", "mu2DxyBSErr", "deltaR"],
}
PAIR_TYPES = {(0, 0): "Muon-Muon", (0, 1): "Muon-Displaced", (1, 0): "Muon-Displaced", (1, 1): "Displaced-Displaced"}


def missing_branches(keys):
    is_mc = "nGenPart" in keys
    missing = []
    for collection, fields in EXPECTED.items():
        if collection == "GenPart" and not is_mc:
            continue
        missing += ["%s_%s" % (collection, f) for f in fields if "%s_%s" % (collection, f) not in keys]
        if "n" + collection not in keys:
            missing.append("n" + collection)
    return missing


def index_errors(ev):
    """Number of indices pointing outside their target collection.

    DiMuonVtx legs must point at an existing Muon/DisplacedMuon row, and
    DisplacedMuon_muonIdx must be -1 or an existing Muon row.
    """
    bad = 0
    for leg in ("mu1", "mu2"):
        idx = ev["DiMuonVtx_%sIdx" % leg]
        typ = ev["DiMuonVtx_%sType" % leg]
        size = ak.where(typ == 0, ev["nMuon"], ev["nDisplacedMuon"])
        bad += int(ak.sum((idx < 0) | (idx >= size) | ((typ != 0) & (typ != 1))))
    idx = ev["DisplacedMuon_muonIdx"]
    bad += int(ak.sum((idx < -1) | (idx >= ev["nMuon"])))
    return bad


def summarize(ev):
    vtx_valid = ak.flatten(ev["DiMuonVtx_valid"])
    t1 = ak.to_numpy(ak.flatten(ev["DiMuonVtx_mu1Type"])).astype(int)
    t2 = ak.to_numpy(ak.flatten(ev["DiMuonVtx_mu2Type"])).astype(int)
    valid = ak.to_numpy(vtx_valid).astype(bool)
    lxy = ak.to_numpy(ak.flatten(ev["DiMuonVtx_lxyBS"]))
    lxy_cp = ak.to_numpy(ak.flatten(ev["DiMuonVtx_lxyCP"]))
    dr = ak.to_numpy(ak.flatten(ev["DiMuonVtx_deltaR"]))
    same_sign = np.abs(ak.to_numpy(ak.flatten(ev["DiMuonVtx_charge"]))) == 2
    n_events = len(ev)
    n_cand = len(valid)
    replaced = ak.flatten(ev["DisplacedMuon_muonIdx"]) >= 0
    print("events: %d   candidates: %d (%.2f / event)" % (n_events, n_cand, n_cand / max(n_events, 1)))
    print("muons / event: %.2f   displaced muons / event: %.2f (%.1f%% replaced by a Muon)"
          % (ak.mean(ev["nMuon"]), ak.mean(ev["nDisplacedMuon"]),
             100 * ak.mean(replaced) if len(replaced) else 0.))
    if n_cand == 0:
        return
    # "dup-like": same-sign legs within dR < 0.2, i.e. probably one muon reconstructed twice
    print("\n%-22s %8s %10s %8s %10s %14s"
          % ("pair type", "pairs", "fit valid", "has CP", "dup-like", "median lxyBS"))
    for name in ("Muon-Muon", "Muon-Displaced", "Displaced-Displaced"):
        sel = np.array([PAIR_TYPES[(a, b)] == name for a, b in zip(t1, t2)], dtype=bool)
        if sel.any():
            fitted = sel & valid
            median = "%.3g cm" % np.median(lxy[fitted]) if fitted.any() else "-"
            print("%-22s %8d %9.1f%% %7.1f%% %9.1f%% %14s"
                  % (name, sel.sum(), 100 * valid[sel].mean(), 100 * (lxy_cp[sel] >= 0).mean(),
                     100 * (same_sign[sel] & (dr[sel] < 0.2)).mean(), median))
    if valid.any():
        q = np.percentile(lxy[valid], [10, 50, 90, 99])
        print("\nlxyBS of valid fits [cm]: 10%%=%.3g 50%%=%.3g 90%%=%.3g 99%%=%.3g" % tuple(q))
    if "Muon_genPartIdx" in ev.fields:
        for coll in ("Muon", "DisplacedMuon"):
            idx = ak.flatten(ev["%s_genPartIdx" % coll])
            if len(idx):
                print("%s gen-matched: %.1f%%" % (coll, 100 * ak.mean(idx >= 0)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+")
    parser.add_argument("--tree", default="Events")
    args = parser.parse_args(argv)

    status = 0
    for path in args.files:
        print("== %s" % path)
        tree = uproot.open(path)[args.tree]
        keys = set(tree.keys())
        missing = missing_branches(keys)
        if missing:
            print("MISSING branches: %s" % ", ".join(missing))
            status = 1
            continue
        names = ["nMuon", "nDisplacedMuon", "DisplacedMuon_muonIdx"] + \
            [k for k in keys if k.startswith(("DiMuonVtx_", "nDiMuonVtx"))]
        if "Muon_genPartIdx" in keys:
            names += ["Muon_genPartIdx", "DisplacedMuon_genPartIdx"]
        ev = tree.arrays(names)
        bad = index_errors(ev)
        if bad:
            print("ERROR: %d indices point outside Muon/DisplacedMuon" % bad)
            status = 1
        summarize(ev)
        print()
    return status


if __name__ == "__main__":
    sys.exit(main())
