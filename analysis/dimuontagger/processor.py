"""DiMuonProcessor: histograms, cutflow and per-candidate parquet skims.

Output of ``process`` (accumulated over chunks by coffea)::

    {dataset: {
        "hists":     {name: hist.Hist},   # most have a "category" axis
        "cutflow":   {step: int},
        "sumw":      float,               # sum of genWeight over all processed events
        "sumw_events": float,             # sum of genWeight over the events in the files
        "nevents":   int,
        "skim_files": set of str,         # parquet files written
    }}

Candidate legs: ``Muon`` and ``DisplacedMuon`` are concatenated per event, so
leg ``k`` of event ``e`` is ``Muon[k]`` for ``k < nMuon`` and
``DisplacedMuon[k - nMuon]`` otherwise. A DiMuonVtx leg therefore sits at
``muIdx + muType * nMuon`` and all legs are fetched in one indexing step.

Normalisation: background NanoAOD is written with an event filter (only events
with a DiMuonVtx candidate), so ``sumw`` comes from the Runs tree
(genEventSumw, every event the NANO job processed), added once per file with
its first chunk; ``events_generated`` in the cutflow is genEventCount. Files
without a Runs genEventSumw (data, test files) fall back to the events in the
file. With --maxchunks, files whose first chunk ran count in full.
"""

import hashlib
import posixpath
import re

import awkward as ak
import hist
import numpy as np
import uproot
from coffea import processor

from . import truth
from .config import llp_pdgid_list, load_config

PAIR_TYPES = {0: "Muon-Muon", 1: "Muon-DSA", 2: "DSA-DSA"}

# Leg variables kept as float32 in the skims; NaN where the collection lacks
# the variable (e.g. dxy for DisplacedMuon, nValidMuonHits for Muon).
LEG_FEATURES = (
    "pt", "eta", "phi", "charge", "ptErr",
    "isGlobal", "isStandalone", "isTracker",
    "dxy", "dxyErr", "dz", "dzErr", "pfRelIso04_all",
    "looseId", "mediumId", "tightId", "nStations", "nTrackerLayers",
    "nValidMuonHits", "nMuonStations", "normChi2",
)
EVENT_FIELDS = ("run", "luminosityBlock", "event")
METADATA_COLUMNS = ("process", "role", "era", "das_dataset")


# ------------------------------------------------------------- histograms --

def _log_axis(lo, hi, nbins, name, label):
    """Log-spaced bins from lo to hi, plus a first bin [0, lo)."""
    edges = np.concatenate([[0.0], np.geomspace(lo, hi, nbins + 1)])
    return hist.axis.Variable(edges, name=name, label=label)


def _category_axis():
    return hist.axis.StrCategory(list(truth.ALL_CATEGORIES), name="category", label="truth category")


def _leg_axis():
    return hist.axis.IntCategory([1, 2], name="leg", label="leg (1 = higher pT)")


# name -> value axis, for histograms with a (category, value) layout
CANDIDATE_AXES = {
    "mass": lambda: hist.axis.Regular(300, 0, 150, name="mass", label=r"$m_{\mu\mu}$ [GeV]"),
    "pt": lambda: hist.axis.Regular(100, 0, 200, name="pt", label=r"$p_{T}^{\mu\mu}$ [GeV]"),
    "lxy": lambda: _log_axis(1e-4, 1e3, 70, "lxy", r"$L_{xy}$ (fit, beam spot) [cm]"),
    "lxy_cp": lambda: _log_axis(1e-4, 1e3, 70, "lxy_cp", r"$L_{xy}$ (crossing point) [cm]"),
    "lxy_sig": lambda: _log_axis(1e-2, 1e4, 60, "lxy_sig", r"$L_{xy}/\sigma_{L_{xy}}$"),
    "norm_chi2": lambda: hist.axis.Regular(100, 0, 20, name="norm_chi2", label=r"vertex $\chi^2$/ndof"),
    "prob": lambda: hist.axis.Regular(50, 0, 1, name="prob", label="vertex fit probability"),
    "cos_alpha_xy": lambda: hist.axis.Regular(100, -1, 1, name="cos_alpha_xy", label=r"$\cos\alpha_{xy}$"),
    "dca": lambda: _log_axis(1e-5, 1e2, 70, "dca", "two-track DCA [cm]"),
    "delta_r": lambda: hist.axis.Regular(100, 0, 5, name="delta_r", label=r"$\Delta R(\mu,\mu)$"),
    "fit_valid": lambda: hist.axis.IntCategory([0, 1], name="fit_valid", label="vertex fit valid"),
    "pair_type": lambda: hist.axis.IntCategory(
        [0, 1, 2], name="pair_type", label="pair type (0 Muon-Muon, 1 Muon-DSA, 2 DSA-DSA)"),
}
LEG_AXES = {
    "leg_pt": lambda: hist.axis.Regular(100, 0, 200, name="leg_pt", label=r"leg $p_{T}$ [GeV]"),
    "leg_eta": lambda: hist.axis.Regular(60, -3, 3, name="leg_eta", label=r"leg $\eta$"),
    "leg_dxy_sig": lambda: _log_axis(1e-2, 1e4, 60, "leg_dxy_sig", r"leg $|d_{xy}|/\sigma_{d_{xy}}$ (beam spot)"),
}


def make_hists():
    weight = hist.storage.Weight()
    out = {name: hist.Hist(_category_axis(), axis(), storage=weight) for name, axis in CANDIDATE_AXES.items()}
    out.update({name: hist.Hist(_category_axis(), _leg_axis(), axis(), storage=weight)
                for name, axis in LEG_AXES.items()})
    out["lxy_residual"] = hist.Hist(
        hist.axis.StrCategory(["fit", "crossing"], name="method", label="reco Lxy"),
        _log_axis(1e-3, 1e3, 30, "gen_lxy", r"true $L_{xy}$ [cm]"),
        hist.axis.Regular(200, -10, 10, name="residual", label=r"$L_{xy}^{reco} - L_{xy}^{true}$ [cm]"),
        storage=weight,
    )
    # Unweighted: one entry per gen-matched reco muon (Muon or DisplacedMuon)
    # and per distinct ancestor pdgId run (FSR copies collapsed).
    out["ancestor_pdgid"] = hist.Hist(hist.axis.IntCategory([], growth=True, name="pdgid", label="|pdgId| of ancestor"))
    out["mother_pdgid"] = hist.Hist(hist.axis.IntCategory([], growth=True, name="pdgid", label="|pdgId| of mother"))
    return out


CUTFLOW_STEPS = (
    "events_generated", "events", "events_with_candidate", "candidates", "candidates_bad_leg_index", "candidates_used",
    "candidates_fit_valid", "legs", "legs_gen_matched", "legs_walk_truncated",
) + tuple("cat_" + c for c in truth.ALL_CATEGORIES) + ("skim_rows",)


# -------------------------------------------------------------- utilities --

_RUNS_CACHE = {}  # filename -> (genEventSumw, genEventCount) or None; per worker


def runs_counts(filename):
    """(sum of genEventSumw, sum of genEventCount) from a file's Runs tree.

    None if the file has no Runs tree with those branches (data, test files).
    Cached per file, since every chunk of a file asks.
    """
    if filename not in _RUNS_CACHE:
        counts = None
        with uproot.open(filename) as handle:
            if "Runs" in handle:
                runs = handle["Runs"]
                if {"genEventSumw", "genEventCount"} <= set(runs.keys()):
                    counts = (float(np.sum(runs["genEventSumw"].array(library="np"))),
                              int(np.sum(runs["genEventCount"].array(library="np"))))
        _RUNS_CACHE[filename] = counts
    return _RUNS_CACHE[filename]


def _np(array, dtype=None):
    out = ak.to_numpy(array)
    return out if dtype is None else out.astype(dtype)


def _flat(array, dtype=None):
    return _np(ak.flatten(array, axis=1), dtype)


def _leg_record(collection, kind):
    """Common record for Muon (kind 0) and DisplacedMuon (kind 1) legs."""
    fields = set(collection.fields)
    template = ak.values_astype(collection.pt, np.float32)
    rec = {}
    for name in LEG_FEATURES:
        rec[name] = (ak.values_astype(collection[name], np.float32) if name in fields
                     else ak.full_like(template, np.nan))
    if "genPartIdx" in fields:
        rec["genPartIdx"] = ak.values_astype(collection.genPartIdx, np.int64)
    else:
        rec["genPartIdx"] = ak.values_astype(ak.full_like(template, -1), np.int64)
    rec["genPartFlav"] = (ak.values_astype(collection.genPartFlav, np.int64) if "genPartFlav" in fields
                          else ak.values_astype(ak.full_like(template, -1), np.int64))
    rec["type"] = ak.values_astype(ak.full_like(template, kind), np.int64)
    return ak.zip(rec, depth_limit=2)


def _safe_path(name):
    """Dataset name -> relative path; keeps '/' so process/era/primary nest."""
    parts = [re.sub(r"[^A-Za-z0-9._+-]", "_", p) for p in str(name).split("/") if p not in ("", ".", "..")]
    return "/".join(parts) or "dataset"


def _ratio(num, den, valid):
    out = np.full(len(num), np.nan)
    ok = valid & (den > 0)
    out[ok] = num[ok] / den[ok]
    return out


# -------------------------------------------------------------- processor --

class DiMuonProcessor(processor.ProcessorABC):
    """Displaced di-muon candidates: truth labels, histograms and skims.

    Parameters
    ----------
    config : dict or str or None
        Config dict (from ``load_config``), a YAML path, or None for
        analysis/config.yaml.
    skim_dest : str or None
        fsspec URL / path for parquet skims; overrides ``config["skim"]["dest"]``.
        None and no config value: no skims.
    skim_storage_options : dict or None
        Overrides ``config["skim"]["storage_options"]``.
    """

    def __init__(self, config=None, skim_dest=None, skim_storage_options=None):
        if config is None or isinstance(config, str):
            config = load_config(config)
        self.config = config
        self.llp_pdgids = llp_pdgid_list(config)
        self.max_depth = int(config["truth"]["max_depth"])
        self.prompt_quarkonia = bool(config["truth"]["prompt_quarkonia"])
        self.skim_dest = skim_dest if skim_dest is not None else config["skim"]["dest"]
        self.skim_storage_options = dict(
            skim_storage_options if skim_storage_options is not None else config["skim"]["storage_options"])

    # ------------------------------------------------------------ process --
    def process(self, events):
        md = events.metadata
        dataset = md["dataset"]
        out = {
            "hists": make_hists(),
            "cutflow": dict.fromkeys(CUTFLOW_STEPS, 0),
            "sumw": 0.0,
            "sumw_events": 0.0,
            "nevents": 0,
            "skim_files": set(),
        }
        cut = out["cutflow"]
        nev = len(events)
        is_mc = "GenPart" in events.fields
        w_evt = _np(events.genWeight, np.float64) if "genWeight" in events.fields else np.ones(nev)
        out["nevents"] = nev
        out["sumw_events"] = float(w_evt.sum())
        cut["events"] = nev
        runs = runs_counts(md["filename"]) if md.get("filename") else None
        if runs is None:
            out["sumw"], cut["events_generated"] = out["sumw_events"], nev
        elif md.get("entrystart", 0) == 0:  # the file's first chunk counts the whole file
            out["sumw"], cut["events_generated"] = runs

        # ---- legs: Muon then DisplacedMuon, per event, flattened
        n_mu = _np(ak.num(events.Muon, axis=1), np.int64)
        n_dsa = _np(ak.num(events.DisplacedMuon, axis=1), np.int64)
        n_legs = n_mu + n_dsa
        legs = ak.concatenate([_leg_record(events.Muon, 0), _leg_record(events.DisplacedMuon, 1)], axis=1)
        legs = {name: _flat(legs[name]) for name in legs.fields}
        leg_start = truth.event_starts(n_legs)
        cut["legs"] = int(n_legs.sum())

        # ---- candidates, with leg positions in the flat leg arrays
        vtx = events.DiMuonVtx
        n_vtx = _np(ak.num(vtx, axis=1), np.int64)
        cand_evt = np.repeat(np.arange(nev), n_vtx)
        cand_row = np.arange(len(cand_evt)) - truth.event_starts(n_vtx)[cand_evt]  # DiMuonVtx row in event
        pos, ok = {}, np.ones(len(cand_evt), dtype=bool)
        for leg in ("mu1", "mu2"):
            idx = _flat(vtx[leg + "Idx"], np.int64)
            typ = _flat(vtx[leg + "Type"], np.int64)
            size = np.where(typ == 0, n_mu[cand_evt], n_dsa[cand_evt])
            ok &= ((typ == 0) | (typ == 1)) & (idx >= 0) & (idx < size)
            pos[leg] = leg_start[cand_evt] + idx + typ * n_mu[cand_evt]
        cut["candidates"] = len(cand_evt)
        cut["events_with_candidate"] = int((n_vtx > 0).sum())
        cut["candidates_bad_leg_index"] = int((~ok).sum())
        cand_evt, cand_row = cand_evt[ok], cand_row[ok]
        p1, p2 = pos["mu1"][ok], pos["mu2"][ok]
        cut["candidates_used"] = len(cand_evt)
        v = {name: _flat(vtx[name])[ok] for name in vtx.fields}
        valid = v["valid"].astype(bool)
        cut["candidates_fit_valid"] = int(valid.sum())
        weight = w_evt[cand_evt]

        # ---- truth
        if is_mc:
            lt, walk = self._leg_truth(events, legs, n_legs)
            cut["legs_gen_matched"] = int(lt["matched"].sum())
            cut["legs_walk_truncated"] = int((walk["truncated"] & lt["matched"]).sum())
            t1 = {k: a[p1] for k, a in lt.items()}
            t2 = {k: a[p2] for k, a in lt.items()}
            codes, same_muon = truth.label_pairs(t1, t2)
            self._fill_ancestors(out["hists"], lt, walk)
        else:
            t1 = t2 = None
            codes = np.full(len(cand_evt), truth.CODES[truth.DATA_CATEGORY], dtype=np.int8)
            same_muon = np.zeros(len(cand_evt), dtype=bool)
        labels = truth.code_names(codes)
        for name in truth.ALL_CATEGORIES:
            cut["cat_" + name] = int((codes == truth.CODES[name]).sum())

        derived = self._derived(v, valid)
        # true Lxy of the candidate: mean over the legs that descend from an LLP
        gen_lxy = (truth.pair_mean(t1["gen_lxy"], t2["gen_lxy"]) if is_mc
                   else np.full(len(cand_evt), np.nan))
        self._fill(out["hists"], v, valid, derived, legs, p1, p2, labels, weight, codes, gen_lxy)

        # ---- skim
        if self.skim_dest and len(cand_evt):
            table = self._skim_table(events, md, v, derived, legs, p1, p2, t1, t2, cand_evt, cand_row,
                                     labels, codes, same_muon, weight, gen_lxy)
            out["skim_files"].add(self._write_skim(table, md))
            cut["skim_rows"] = len(cand_evt)
        return {dataset: out}

    def postprocess(self, accumulator):
        return accumulator

    # ------------------------------------------------------------- truth --
    def _leg_truth(self, events, legs, n_legs):
        gp = events.GenPart
        gen_counts = _np(ak.num(gp, axis=1), np.int64)
        gen_starts = truth.event_starts(gen_counts)
        mother = truth.mother_table(_flat(gp.genPartIdxMother, np.int64), gen_counts)
        abs_pdg = np.abs(_flat(gp.pdgId, np.int64))
        flags = _flat(gp.statusFlags, np.int64)
        coords = [(_flat(gp[c], np.float64) if c in gp.fields else None) for c in ("vx", "vy", "vz")]

        leg_evt = np.repeat(np.arange(len(n_legs)), n_legs)
        gen = truth.globalise(legs["genPartIdx"], leg_evt, gen_starts, gen_counts)
        walk = truth.walk_ancestors(gen, mother, abs_pdg, self.llp_pdgids, self.max_depth)
        lt = truth.leg_truth(gen, walk, flags, abs_pdg, *coords, prompt_quarkonia=self.prompt_quarkonia)
        return lt, walk

    @staticmethod
    def _fill_ancestors(hists, lt, walk):
        matched = lt["matched"]
        keep = matched[walk["anc_item"]]
        hists["ancestor_pdgid"].fill(pdgid=walk["anc_pdg"][keep])
        hists["mother_pdgid"].fill(pdgid=walk["mother_pdg"][matched & (walk["mother_pdg"] > 0)])

    # ------------------------------------------------------------- hists --
    @staticmethod
    def _derived(v, valid):
        d = {
            "lxySig": _ratio(v["lxyBS"], v["lxyBSErr"], valid),
            "normChi2": _ratio(v["chi2"], v["ndof"], valid),
            "pairType": (v["mu1Type"].astype(np.int64) + v["mu2Type"].astype(np.int64)),
        }
        for leg in ("mu1", "mu2"):
            d[leg + "DxySig"] = _ratio(np.abs(v[leg + "DxyBS"]), v[leg + "DxyBSErr"], np.ones(len(valid), bool))
        return d

    @staticmethod
    def _fill(h, v, valid, d, legs, p1, p2, labels, weight, codes, gen_lxy):
        def fill(name, values, mask=None, **extra):
            values = np.asarray(values)
            sel = np.isfinite(values.astype(np.float64))
            if mask is not None:
                sel &= mask
            h[name].fill(category=labels[sel], weight=weight[sel], **{name: values[sel]}, **extra)

        fill("mass", v["mass"])
        fill("pt", v["pt"])
        fill("lxy", v["lxyBS"], valid & (v["lxyBS"] >= 0))
        fill("lxy_cp", v["lxyCP"], v["lxyCP"] >= 0)
        fill("lxy_sig", d["lxySig"], valid)
        fill("norm_chi2", d["normChi2"], valid)
        fill("prob", v["prob"], valid & (v["prob"] >= 0))
        fill("cos_alpha_xy", v["cosAlphaXY"], v["cosAlphaXY"] >= -1)
        fill("dca", v["dca"], v["dca"] >= 0)
        fill("delta_r", v["deltaR"])
        fill("fit_valid", valid.astype(np.int64))
        fill("pair_type", d["pairType"])
        for i, (leg, pos) in enumerate((("mu1", p1), ("mu2", p2)), start=1):
            fill("leg_pt", legs["pt"][pos], leg=i)
            fill("leg_eta", legs["eta"][pos], leg=i)
            fill("leg_dxy_sig", d[leg + "DxySig"], leg=i)

        sig = codes == truth.CODES["signal"]
        for method, reco, mask in (("fit", v["lxyBS"], sig & valid & (v["lxyBS"] >= 0)),
                                   ("crossing", v["lxyCP"], sig & (v["lxyCP"] >= 0))):
            mask = mask & np.isfinite(gen_lxy)
            h["lxy_residual"].fill(method=method, gen_lxy=gen_lxy[mask], residual=(reco - gen_lxy)[mask],
                                   weight=weight[mask])

    # ------------------------------------------------------------- skims --
    def _skim_table(self, events, md, v, d, legs, p1, p2, t1, t2, cand_evt, cand_row, labels, codes,
                    same_muon, weight, gen_lxy):
        n = len(cand_evt)
        cols = {}
        for name in EVENT_FIELDS:
            if name in events.fields:
                cols[name] = _np(events[name])[cand_evt]
        cols["nMuon"] = _np(ak.num(events.Muon, axis=1), np.int64)[cand_evt]
        cols["nDisplacedMuon"] = _np(ak.num(events.DisplacedMuon, axis=1), np.int64)[cand_evt]
        cols["nDiMuonVtx"] = _np(ak.num(events.DiMuonVtx, axis=1), np.int64)[cand_evt]
        cols["cand_index"] = cand_row
        cols.update(v)
        cols.update(d)
        for leg, pos in (("mu1", p1), ("mu2", p2)):
            for name, values in legs.items():
                cols["%s_%s" % (leg, name)] = values[pos]
        cols["label"] = labels.astype(str)
        cols["label_code"] = codes
        cols["weight"] = weight
        if t1 is not None:
            for leg, t in (("mu1", t1), ("mu2", t2)):
                for name in ("pdg", "mother_pdg", "llp_pdg", "open_hf", "prompt", "gen_lxy", "gen_l3d"):
                    cols["%s_gen_%s" % (leg, name)] = t[name]
            cols["gen_lxy"] = gen_lxy
            cols["gen_same_muon"] = same_muon
        cols["dataset"] = np.full(n, md["dataset"], dtype=object).astype(str)
        for name in METADATA_COLUMNS:
            cols[name] = np.full(n, str(md.get(name, "")), dtype=object).astype(str)
        return ak.zip({k: ak.Array(np.asarray(a)) for k, a in cols.items()}, depth_limit=1)

    def _write_skim(self, table, md):
        import fsspec

        fs, root = fsspec.core.url_to_fs(self.skim_dest, **self.skim_storage_options)
        stem = md.get("fileuuid") or hashlib.sha1(md["filename"].encode()).hexdigest()[:16]
        dirpath = posixpath.join(root.rstrip("/"), _safe_path(md["dataset"]))
        fs.makedirs(dirpath, exist_ok=True)
        path = posixpath.join(dirpath, "%s_%d-%d.parquet" % (stem, md["entrystart"], md["entrystop"]))
        url = fs.unstrip_protocol(path)
        ak.to_parquet(table, url, storage_options=self.skim_storage_options)
        return url
