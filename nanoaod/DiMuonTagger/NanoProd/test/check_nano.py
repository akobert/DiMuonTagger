#!/usr/bin/env python3
"""Sanity-check a DiMuonTagger custom NanoAOD file.

    python3 check_nano.py nano.root [more.root ...]

Checks that the custom branches exist, that DiMuonVtx leg indices point at
existing Muon/DisplacedMuon rows, and that no leg is the dropped copy of a
displaced/standard duplicate. Prints a short summary (candidates per event,
duplicates resolved, pair types, fit success, Lxy quantiles, gen-match rate).
In simulation the pair table also counts pairs whose two legs match the same
generated muon ("same-gen": one muon reconstructed twice, whatever the
charges), and the opposite-sign part of those ("same-gen OS", e.g. a copy
with a wrongly measured charge). Only for Muon-Displaced pairs: NanoAOD gen
matching is one-to-one within a collection, so two copies in the same
collection never share a gen muon (the second is left unmatched).
Exits non-zero if a branch is missing or a check fails. Needs uproot and
awkward (both in CMSSW's Python 3).
"""

import argparse
import sys

import awkward as ak
import numpy as np
import uproot

# The custom branch spec shared with the coffea framework in analysis/.
EXPECTED = {
    "GenPart": ["vx", "vy", "vz"],
    "Muon": ["dupIdx"],
    "DisplacedMuon": ["pt", "eta", "phi", "mass", "charge", "isGlobal", "isStandalone", "isTracker",
                      "nValidMuonHits", "nMuonStations", "normChi2", "ptErr", "genPartIdx", "genPartFlav",
                      "muonIdx", "replaced", "muonDR"],
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

    DiMuonVtx legs must point at an existing Muon/DisplacedMuon row;
    DisplacedMuon_muonIdx and Muon_dupIdx must be -1 or an existing Muon row.
    """
    bad = 0
    for leg in ("mu1", "mu2"):
        idx = ev["DiMuonVtx_%sIdx" % leg]
        typ = ev["DiMuonVtx_%sType" % leg]
        size = ak.where(typ == 0, ev["nMuon"], ev["nDisplacedMuon"])
        bad += int(ak.sum((idx < 0) | (idx >= size) | ((typ != 0) & (typ != 1))))
    for name in ("DisplacedMuon_muonIdx", "Muon_dupIdx"):
        idx = ev[name]
        bad += int(ak.sum((idx < -1) | (idx >= ev["nMuon"])))
    return bad


def _offsets(counts):
    counts = ak.to_numpy(counts).astype(np.int64)
    return np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.int64), counts


def dropped_copy_legs(ev):
    """Number of DiMuonVtx legs that are the dropped copy of a duplicate.

    A Muon with dupIdx >= 0 (copy of a global Muon) must not be a leg; nor a
    displaced muon with replaced == 1; nor the Muon a displaced muon duplicates
    (muonIdx) when the displaced muon was kept (replaced == 0).
    Assumes index_errors(ev) == 0.
    """
    off_mu, n_mu = _offsets(ev["nMuon"])
    off_dsa, n_dsa = _offsets(ev["nDisplacedMuon"])
    replaced = ak.to_numpy(ak.flatten(ev["DisplacedMuon_replaced"])).astype(bool)
    match = ak.to_numpy(ak.flatten(ev["DisplacedMuon_muonIdx"])).astype(np.int64)
    dsa_event = np.repeat(np.arange(len(n_dsa)), n_dsa)
    keep_dsa = (match >= 0) & ~replaced
    dropped_mu = set((off_mu[dsa_event[keep_dsa]] + match[keep_dsa]).tolist())
    dup = ak.to_numpy(ak.flatten(ev["Muon_dupIdx"])).astype(np.int64)
    dropped_mu |= set(np.nonzero(dup >= 0)[0].tolist())  # flat Muon positions
    _, n_vtx = _offsets(ev["nDiMuonVtx"])
    vtx_event = np.repeat(np.arange(len(n_vtx)), n_vtx)
    bad = 0
    for leg in ("mu1", "mu2"):
        idx = ak.to_numpy(ak.flatten(ev["DiMuonVtx_%sIdx" % leg])).astype(np.int64)
        typ = ak.to_numpy(ak.flatten(ev["DiMuonVtx_%sType" % leg])).astype(np.int64)
        dsa = typ == 1
        bad += int(replaced[off_dsa[vtx_event[dsa]] + idx[dsa]].sum())
        bad += sum(g in dropped_mu for g in (off_mu[vtx_event[~dsa]] + idx[~dsa]).tolist())
    return bad


def same_gen_muon(ev):
    """Per DiMuonVtx row: both legs gen-matched to the same GenPart.

    None without gen information. Assumes index_errors(ev) == 0.
    """
    if "Muon_genPartIdx" not in ev.fields:
        return None
    gen = {0: ak.to_numpy(ak.flatten(ev["Muon_genPartIdx"])).astype(np.int64),
           1: ak.to_numpy(ak.flatten(ev["DisplacedMuon_genPartIdx"])).astype(np.int64)}
    offsets = {0: _offsets(ev["nMuon"])[0], 1: _offsets(ev["nDisplacedMuon"])[0]}
    _, n_vtx = _offsets(ev["nDiMuonVtx"])
    vtx_event = np.repeat(np.arange(len(n_vtx)), n_vtx)
    legs = []
    for leg in ("mu1", "mu2"):
        idx = ak.to_numpy(ak.flatten(ev["DiMuonVtx_%sIdx" % leg])).astype(np.int64)
        typ = ak.to_numpy(ak.flatten(ev["DiMuonVtx_%sType" % leg])).astype(np.int64)
        g = np.full(len(idx), -1, dtype=np.int64)
        for t in (0, 1):
            sel = typ == t
            g[sel] = gen[t][offsets[t][vtx_event[sel]] + idx[sel]]
        legs.append(g)
    return (legs[0] >= 0) & (legs[0] == legs[1])


def summarize(ev, indices_ok=True):
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
    matched = ak.to_numpy(ak.flatten(ev["DisplacedMuon_muonIdx"])) >= 0
    replaced = ak.to_numpy(ak.flatten(ev["DisplacedMuon_replaced"])).astype(bool)
    print("events: %d   candidates: %d (%.2f / event)" % (n_events, n_cand, n_cand / max(n_events, 1)))
    print("muons / event: %.2f   displaced muons / event: %.2f"
          % (ak.mean(ev["nMuon"]), ak.mean(ev["nDisplacedMuon"])))
    dup = ak.to_numpy(ak.flatten(ev["Muon_dupIdx"]))
    if len(dup):
        print("Muons that are copies of a global Muon (not used as legs): %.1f%%" % (100.0 * (dup >= 0).mean()))
    if len(matched):
        pct = lambda sel: 100.0 * sel.sum() / len(matched)
        print("displaced muons duplicating a Muon: %.1f%% (replaced by the Muon: %.1f%%, "
              "kept instead of the Muon: %.1f%%)"
              % (pct(matched), pct(matched & replaced), pct(matched & ~replaced)))
    if n_cand == 0:
        return
    same_gen = same_gen_muon(ev) if indices_ok else None
    # "dup-like": same-sign legs within dR < 0.2, i.e. probably one muon reconstructed twice
    header = "\n%-22s %8s %10s %8s %10s %14s" % ("pair type", "pairs", "fit valid", "has CP", "dup-like",
                                                  "median lxyBS")
    if same_gen is not None:
        header += " %10s %12s" % ("same-gen", "same-gen OS")
    print(header)
    for name in ("Muon-Muon", "Muon-Displaced", "Displaced-Displaced"):
        sel = np.array([PAIR_TYPES[(a, b)] == name for a, b in zip(t1, t2)], dtype=bool)
        if sel.any():
            fitted = sel & valid
            median = "%.3g cm" % np.median(lxy[fitted]) if fitted.any() else "-"
            line = "%-22s %8d %9.1f%% %7.1f%% %9.1f%% %14s" % (
                name, sel.sum(), 100 * valid[sel].mean(), 100 * (lxy_cp[sel] >= 0).mean(),
                100 * (same_sign[sel] & (dr[sel] < 0.2)).mean(), median)
            if same_gen is not None and name != "Muon-Displaced":
                # one-to-one gen matching within a collection: always 0, not informative
                line += " %10s %12s" % ("n/a", "n/a")
            elif same_gen is not None:
                line += " %9.1f%% %11.1f%%" % (100 * same_gen[sel].mean(),
                                               100 * (same_gen[sel] & ~same_sign[sel]).mean())
            print(line)
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
        names = ["nMuon", "nDisplacedMuon", "DisplacedMuon_muonIdx", "DisplacedMuon_replaced", "Muon_dupIdx"] + \
            [k for k in keys if k.startswith(("DiMuonVtx_", "nDiMuonVtx"))]
        if "Muon_genPartIdx" in keys:
            names += ["Muon_genPartIdx", "DisplacedMuon_genPartIdx"]
        ev = tree.arrays(names)
        bad = index_errors(ev)
        if bad:
            print("ERROR: %d indices point outside Muon/DisplacedMuon" % bad)
            status = 1
        else:
            dropped = dropped_copy_legs(ev)
            if dropped:
                print("ERROR: %d DiMuonVtx legs are the dropped copy of a duplicate muon" % dropped)
                status = 1
        summarize(ev, indices_ok=not bad)
        print()
    return status


if __name__ == "__main__":
    sys.exit(main())
