"""Synthetic custom-NanoAOD files with hand-built events and known truth.

Branches follow nanoaod/DiMuonTagger/NanoProd/test/check_nano.py (EXPECTED)
with NanoAOD dtypes: idx int16, type uint8, valid/momentaAtVertex bool. Files
are TTrees with nX counters and X_field branches (uproot mktree + extend; a
plain file["Events"] = {...} would write an RNTuple).

Two files:

signal (hahm_zd, 2022postEE, genWeight 2, 2, -1)
    ev 0  H -> Z_D Z_D. Z_D(a) -> mu mu at Lxy 5 cm (Muon 0, Muon 1).
          Z_D(b) -> mu at Lxy 50 cm, through an FSR copy (DisplacedMuon 0).
          cand 0 Muon0+Muon1  -> signal
          cand 1 Muon0+DSA0   -> llp_partial (different Z_D, fit invalid)
          cand 2 Muon1+DSA0   -> llp_partial
    ev 1  Z -> mu mu (prompt) + an unmatched DSA muon
          cand 0 Muon0+Muon1  -> prompt
          cand 1 Muon0+DSA0   -> unmatched
    ev 2  no muons, no candidates

background (qcd_muenriched, 2023preBPix, genWeight 1)
    ev 0  b -> B0 -> mu D-, D- -> mu ; plus an unmatched muon
          cand 0 Muon0+Muon1  -> heavy_flavour
          cand 1 Muon0+Muon2  -> heavy_flavour (beats unmatched)
    ev 1  two unmatched muons
          cand 0 Muon0+DSA0   -> unmatched
          cand 1 DSA index 3 of 1 -> dropped (bad leg index)
    ev 2  K+ -> mu, pi- -> mu decays in flight (DSA-DSA)
          cand 0 DSA0+DSA1    -> other
    ev 3  prompt J/psi -> mu mu (isPrompt false, quarkonium rule)
          cand 0              -> prompt
    ev 4  B+ -> J/psi -> mu mu
          cand 0              -> heavy_flavour
    ev 5  one prompt muon reconstructed as both Muon and DisplacedMuon
          cand 0              -> other (same gen muon)
"""

import json
import os

import awkward as ak
import numpy as np
import uproot

PV = (0.02, -0.01, 1.5)  # gen primary vertex (cm), off the origin on purpose

PROMPT = 1 << 0
HADRON_DECAY = 1 << 6
LAST_COPY = 1 << 13

GEN_FIELDS = {
    "pdgId": np.int32, "status": np.int32, "statusFlags": np.uint16, "genPartIdxMother": np.int16,
    "pt": np.float32, "eta": np.float32, "phi": np.float32, "mass": np.float32,
    "vx": np.float32, "vy": np.float32, "vz": np.float32,
}
MUON_FIELDS = {
    "pt": np.float32, "eta": np.float32, "phi": np.float32, "mass": np.float32, "charge": np.int32,
    "dxy": np.float32, "dxyErr": np.float32, "dz": np.float32, "dzErr": np.float32, "ptErr": np.float32,
    "pfRelIso04_all": np.float32, "isGlobal": np.bool_, "isStandalone": np.bool_, "isTracker": np.bool_,
    "looseId": np.bool_, "mediumId": np.bool_, "tightId": np.bool_,
    "nStations": np.uint8, "nTrackerLayers": np.uint8, "genPartIdx": np.int16, "genPartFlav": np.uint8,
}
DSA_FIELDS = {
    "pt": np.float32, "eta": np.float32, "phi": np.float32, "mass": np.float32, "charge": np.int32,
    "isGlobal": np.bool_, "isStandalone": np.bool_, "isTracker": np.bool_,
    "nValidMuonHits": np.int16, "nMuonStations": np.int16, "normChi2": np.float32, "ptErr": np.float32,
    "genPartIdx": np.int16, "genPartFlav": np.uint8,
}
VTX_FLOATS = ("pt", "eta", "phi", "mass", "vx", "vy", "vz", "vxErr", "vyErr", "vzErr", "chi2", "ndof", "prob",
              "lxyBS", "lxyBSErr", "lxyPV", "lxyPVErr", "l3dPV", "l3dPVErr", "cosAlphaXY", "cosAlpha3D", "dca",
              "cpx", "cpy", "cpz", "lxyCP", "mu1RefitPt", "mu1RefitEta", "mu1RefitPhi", "mu2RefitPt",
              "mu2RefitEta", "mu2RefitPhi", "mu1DxyBS", "mu1DxyBSErr", "mu2DxyBS", "mu2DxyBSErr", "deltaR")
VTX_FIELDS = dict({name: np.float32 for name in VTX_FLOATS}, charge=np.int32, mu1Idx=np.int16, mu2Idx=np.int16,
                  mu1Type=np.uint8, mu2Type=np.uint8, valid=np.bool_, momentaAtVertex=np.bool_)


# ---------------------------------------------------------------- builders --

def gen(pdg, mother, status=1, flags=0, pt=10.0, eta=0.5, phi=0.1, mass=0.0, vtx=(0.0, 0.0, 0.0)):
    """GenPart row; `vtx` is the production vertex relative to PV."""
    if mass == 0.0 and abs(pdg) == 13:
        mass = 0.10566
    return {"pdgId": pdg, "status": status, "statusFlags": flags, "genPartIdxMother": mother,
            "pt": pt, "eta": eta, "phi": phi, "mass": mass,
            "vx": PV[0] + vtx[0], "vy": PV[1] + vtx[1], "vz": PV[2] + vtx[2]}


def muon(pt, gen_idx, charge=-1, eta=0.5, phi=0.1, dxy=0.001):
    return {"pt": pt, "eta": eta, "phi": phi, "mass": 0.10566, "charge": charge,
            "dxy": dxy, "dxyErr": 0.002, "dz": 0.01, "dzErr": 0.003, "ptErr": 0.02 * pt,
            "pfRelIso04_all": 0.05, "isGlobal": True, "isStandalone": True, "isTracker": True,
            "looseId": True, "mediumId": True, "tightId": gen_idx >= 0, "nStations": 3, "nTrackerLayers": 12,
            "genPartIdx": gen_idx, "genPartFlav": 1 if gen_idx >= 0 else 0}


def dsa(pt, gen_idx, charge=-1, eta=0.6, phi=0.2):
    return {"pt": pt, "eta": eta, "phi": phi, "mass": 0.10566, "charge": charge,
            "isGlobal": False, "isStandalone": True, "isTracker": False,
            "nValidMuonHits": 24, "nMuonStations": 3, "normChi2": 1.1, "ptErr": 0.1 * pt,
            "genPartIdx": gen_idx, "genPartFlav": 1 if gen_idx >= 0 else 0}


def vtx(leg1, leg2, mass, pt, valid=True, lxy=0.01, lxy_err=0.005, lxy_cp=None, chi2=1.0, ndof=1.0,
        prob=0.3, cos_alpha=0.99, dca=0.002, delta_r=0.3, dxy1=0.01, dxy2=0.02, charge=0):
    """DiMuonVtx row. leg = (type, idx): type 0 = Muon, 1 = DisplacedMuon."""
    row = {name: 0.0 for name in VTX_FLOATS}
    row.update(mass=mass, pt=pt, eta=0.4, phi=0.2, deltaR=delta_r, dca=dca,
               lxyCP=lxy if lxy_cp is None else lxy_cp, cosAlphaXY=cos_alpha, cosAlpha3D=cos_alpha,
               mu1DxyBS=dxy1, mu1DxyBSErr=0.005, mu2DxyBS=dxy2, mu2DxyBSErr=0.005,
               mu1RefitPt=pt / 2, mu2RefitPt=pt / 2)
    if valid:
        row.update(lxyBS=lxy, lxyBSErr=lxy_err, lxyPV=lxy, lxyPVErr=lxy_err, l3dPV=lxy, l3dPVErr=lxy_err,
                   chi2=chi2, ndof=ndof, prob=prob, vxErr=0.01, vyErr=0.01, vzErr=0.01)
    else:
        row.update(lxyBS=-1.0, lxyBSErr=-1.0, lxyPV=-1.0, lxyPVErr=-1.0, l3dPV=-1.0, l3dPVErr=-1.0,
                   chi2=-1.0, ndof=-1.0, prob=-1.0, vxErr=-1.0, vyErr=-1.0, vzErr=-1.0)
    row.update(mu1Type=leg1[0], mu1Idx=leg1[1], mu2Type=leg2[0], mu2Idx=leg2[1], valid=valid,
               momentaAtVertex=valid, charge=charge)
    return row


# ------------------------------------------------------------------ events --

def signal_events():
    llp_a = (3.0, 4.0, 2.0)      # Lxy 5 cm from the LLP production point (PV)
    llp_b = (30.0, 40.0, 10.0)   # Lxy 50 cm
    flags = PROMPT | LAST_COPY   # Z_D is not a hadron, so its muons are isPrompt in CMSSW
    ev0 = {
        "GenPart": [
            gen(21, -1, status=21, pt=0.0),
            gen(25, 0, status=22, pt=30.0, mass=125.0),
            gen(1023, 1, status=22, pt=40.0, mass=20.0),
            gen(1023, 2, status=2, pt=40.0, mass=20.0),           # last copy of Z_D(a)
            gen(1023, 1, status=22, pt=25.0, mass=20.0),
            gen(1023, 4, status=2, pt=25.0, mass=20.0),           # last copy of Z_D(b)
            gen(13, 3, flags=flags, pt=35.0, vtx=llp_a),          # 6
            gen(-13, 3, flags=flags, pt=22.0, vtx=llp_a),         # 7
            gen(13, 5, status=23, flags=PROMPT, pt=19.0, vtx=llp_b),  # 8: before FSR
            gen(13, 8, flags=flags, pt=18.0, vtx=llp_b),          # 9: after FSR
        ],
        "Muon": [muon(35.0, 6, charge=-1), muon(22.0, 7, charge=+1)],
        "DisplacedMuon": [dsa(18.0, 9, charge=-1)],
        "DiMuonVtx": [
            vtx((0, 0), (0, 1), mass=20.0, pt=40.0, lxy=5.35, lxy_err=0.1, lxy_cp=5.15, chi2=1.3, prob=0.27,
                cos_alpha=0.999, dca=0.01, delta_r=0.4, dxy1=2.0, dxy2=3.0),
            vtx((0, 0), (1, 0), mass=45.0, pt=30.0, valid=False, lxy_cp=30.0, dca=5.0, delta_r=1.5,
                charge=-2),
            vtx((0, 1), (1, 0), mass=38.0, pt=28.0, lxy=25.0, lxy_err=2.0, chi2=8.0, prob=0.005,
                dca=3.0, delta_r=1.2),
        ],
        "genWeight": 2.0,
    }
    ev1 = {
        "GenPart": [
            gen(23, -1, status=62, pt=5.0, mass=91.2),
            gen(13, 0, flags=PROMPT | LAST_COPY, pt=45.0),
            gen(-13, 0, flags=PROMPT | LAST_COPY, pt=40.0),
        ],
        "Muon": [muon(45.0, 1, charge=-1), muon(40.0, 2, charge=+1)],
        "DisplacedMuon": [dsa(10.0, -1, charge=+1)],
        "DiMuonVtx": [
            vtx((0, 0), (0, 1), mass=91.0, pt=5.0, lxy=0.002, lxy_err=0.002),
            vtx((0, 0), (1, 0), mass=12.0, pt=40.0, lxy=0.8, lxy_err=0.3, chi2=4.0, prob=0.05),
        ],
        "genWeight": 2.0,
    }
    ev2 = {"GenPart": [gen(21, -1, status=21)], "Muon": [], "DisplacedMuon": [], "DiMuonVtx": [],
           "genWeight": -1.0}
    return [ev0, ev1, ev2]


def background_events():
    b_decay = (0.03, 0.04, 0.05)   # B flight
    d_decay = (0.06, 0.08, 0.10)   # B then D flight
    ev0 = {
        "GenPart": [
            gen(5, -1, status=23, pt=20.0),
            gen(511, 0, status=2, pt=18.0, mass=5.28),
            gen(-411, 1, status=2, pt=9.0, mass=1.87, vtx=b_decay),
            gen(13, 1, flags=HADRON_DECAY | LAST_COPY, pt=12.0, vtx=b_decay),
            gen(-13, 2, flags=HADRON_DECAY | LAST_COPY, pt=8.0, vtx=d_decay),
        ],
        "Muon": [muon(12.0, 3, charge=-1, dxy=0.05), muon(8.0, 4, charge=+1, dxy=0.09), muon(5.0, -1)],
        "DisplacedMuon": [],
        "DiMuonVtx": [
            vtx((0, 0), (0, 1), mass=2.5, pt=19.0, lxy=0.07, lxy_err=0.01),
            vtx((0, 0), (0, 2), mass=4.0, pt=15.0, lxy=0.2, lxy_err=0.1),
        ],
        "genWeight": 1.0,
    }
    ev1 = {
        "GenPart": [gen(2212, -1, status=4, pt=0.0)],
        "Muon": [muon(6.0, -1, charge=+1)],
        "DisplacedMuon": [dsa(9.0, -1)],
        "DiMuonVtx": [
            vtx((1, 0), (0, 0), mass=7.0, pt=12.0, valid=False, lxy_cp=60.0, dca=20.0),
            vtx((0, 0), (1, 3), mass=1.0, pt=1.0),   # DisplacedMuon index 3 does not exist
        ],
        "genWeight": 1.0,
    }
    ev2 = {
        "GenPart": [
            gen(21, -1, status=21),
            gen(321, 0, status=2, pt=15.0, mass=0.494),
            gen(-211, 0, status=2, pt=10.0, mass=0.140),
            gen(-13, 1, flags=HADRON_DECAY | LAST_COPY, pt=14.0, vtx=(30.0, 20.0, 5.0)),
            gen(13, 2, flags=HADRON_DECAY | LAST_COPY, pt=9.0, vtx=(-40.0, 10.0, 8.0)),
        ],
        "Muon": [],
        "DisplacedMuon": [dsa(14.0, 3, charge=+1), dsa(9.0, 4, charge=-1)],
        "DiMuonVtx": [vtx((1, 0), (1, 1), mass=3.0, pt=20.0, valid=False, lxy_cp=45.0, dca=12.0)],
        "genWeight": 1.0,
    }
    ev3 = {
        "GenPart": [
            gen(21, -1, status=21),
            gen(443, 0, status=2, pt=12.0, mass=3.097),
            gen(13, 1, flags=HADRON_DECAY | LAST_COPY, pt=7.0),
            gen(-13, 1, flags=HADRON_DECAY | LAST_COPY, pt=6.0),
        ],
        "Muon": [muon(7.0, 2, charge=-1), muon(6.0, 3, charge=+1)],
        "DisplacedMuon": [],
        "DiMuonVtx": [vtx((0, 0), (0, 1), mass=3.1, pt=12.0, lxy=0.003, lxy_err=0.003)],
        "genWeight": 1.0,
    }
    ev4 = {
        "GenPart": [
            gen(5, -1, status=23, pt=25.0),
            gen(521, 0, status=2, pt=22.0, mass=5.28),
            gen(443, 1, status=2, pt=15.0, mass=3.097, vtx=(0.1, 0.0, 0.05)),
            gen(13, 2, flags=HADRON_DECAY | LAST_COPY, pt=9.0, vtx=(0.1, 0.0, 0.05)),
            gen(-13, 2, flags=HADRON_DECAY | LAST_COPY, pt=7.0, vtx=(0.1, 0.0, 0.05)),
        ],
        "Muon": [muon(9.0, 3, charge=-1, dxy=0.1), muon(7.0, 4, charge=+1, dxy=0.1)],
        "DisplacedMuon": [],
        "DiMuonVtx": [vtx((0, 0), (0, 1), mass=3.1, pt=15.0, lxy=0.11, lxy_err=0.01)],
        "genWeight": 1.0,
    }
    ev5 = {
        "GenPart": [gen(23, -1, status=62, mass=91.2), gen(13, 0, flags=PROMPT | LAST_COPY, pt=30.0)],
        "Muon": [muon(30.0, 1, charge=-1)],
        "DisplacedMuon": [dsa(29.0, 1, charge=-1)],
        "DiMuonVtx": [vtx((0, 0), (1, 0), mass=0.5, pt=59.0, lxy=0.01, lxy_err=0.01, charge=-2)],
        "genWeight": 1.0,
    }
    return [ev0, ev1, ev2, ev3, ev4, ev5]


# Expected labels of the candidates that survive, per file, in order.
EXPECTED = {
    "signal": [(0, 0, "signal"), (0, 1, "llp_partial"), (0, 2, "llp_partial"),
               (1, 0, "prompt"), (1, 1, "unmatched")],
    "background": [(0, 0, "heavy_flavour"), (0, 1, "heavy_flavour"), (1, 0, "unmatched"), (2, 0, "other"),
                   (3, 0, "prompt"), (4, 0, "heavy_flavour"), (5, 0, "other")],
}
FILES = {
    "signal": {"events": signal_events, "process": "hahm_zd", "role": "signal", "era": "2022postEE",
               "primary": "HTo2ZdTo2mu2x_MZd-20_ctau-100mm_TuneCP5_13p6TeV_madgraph-pythia8"},
    "background": {"events": background_events, "process": "qcd_muenriched", "role": "background",
                   "era": "2023preBPix", "primary": "QCD_PT-50to80_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8"},
}


# ----------------------------------------------------------------- writing --

def _collection(events, key, fields):
    counts = np.array([len(ev[key]) for ev in events], dtype=np.int64)
    columns = {}
    for name, dtype in fields.items():
        flat = np.array([row[name] for ev in events for row in ev[key]], dtype=dtype)
        columns[name] = ak.unflatten(flat, counts)
    return ak.zip(columns)


def write_file(path, events, first_event=1):
    n = len(events)
    branches = {
        "run": np.ones(n, dtype=np.uint32),
        "luminosityBlock": np.full(n, 7, dtype=np.uint32),
        "event": np.arange(first_event, first_event + n, dtype=np.uint64),
        "genWeight": np.array([ev["genWeight"] for ev in events], dtype=np.float32),
        "GenPart": _collection(events, "GenPart", GEN_FIELDS),
        "Muon": _collection(events, "Muon", MUON_FIELDS),
        "DisplacedMuon": _collection(events, "DisplacedMuon", DSA_FIELDS),
        "DiMuonVtx": _collection(events, "DiMuonVtx", VTX_FIELDS),
    }
    types = {k: (v.type if isinstance(v, ak.Array) else v.dtype) for k, v in branches.items()}
    with uproot.recreate(path) as handle:
        tree = handle.mktree("Events", types)
        tree.extend(branches)
    return path


def write_all(directory, tag="synthetic"):
    """Write both files and a fileset JSON; return the fileset path."""
    os.makedirs(directory, exist_ok=True)
    fileset = {}
    for i, (key, spec) in enumerate(FILES.items()):
        path = write_file(os.path.join(directory, "%s.root" % key), spec["events"](), first_event=1000 * (i + 1))
        name = "%s/%s/%s" % (spec["process"], spec["era"], spec["primary"])
        fileset[name] = {
            "files": {os.path.abspath(path): "Events"},
            "metadata": {"process": spec["process"], "role": spec["role"], "era": spec["era"],
                         "das_dataset": "/%s/user-DiMuonNano_%s_%s-0123abcd/USER" % (spec["primary"], tag, spec["era"]),
                         "primary": spec["primary"], "tag": tag},
        }
    out = os.path.join(directory, "%s.json" % tag)
    with open(out, "w") as handle:
        json.dump(fileset, handle, indent=1)
    return out


def dataset_name(key):
    spec = FILES[key]
    return "%s/%s/%s" % (spec["process"], spec["era"], spec["primary"])


if __name__ == "__main__":
    import sys

    print(write_all(sys.argv[1] if len(sys.argv) > 1 else "synthetic"))
