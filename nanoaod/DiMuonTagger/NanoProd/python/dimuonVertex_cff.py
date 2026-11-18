"""Modules for the DiMuonTagger custom NanoAOD: displaced muons and dimuon vertices.

Loaded by dimuonNano_cff.customiseDiMuonNano. Works in CMSSW_13_0_X and
CMSSW_15_0_X. Every table label ends in "Table" because NanoAOD output keeps
only nanoaodFlatTable_*Table_*_*. Do not set `lazyEval` anywhere: 13_0 rejects it.
"""
import FWCore.ParameterSet.Config as cms

import PhysicsTools.NanoAOD.common_cff as _common
import PhysicsTools.NanoAOD.muons_cff as _muons
from PhysicsTools.NanoAOD.common_cff import ExtVar, Var


def _uf(name, doc, precision=-1):
    return Var("userFloat('%s')" % name, float, doc=doc, precision=precision)


def _ui(name, doc, valtype="int16"):
    return Var("userInt('%s')" % name, valtype, doc=doc)


def _outer(expr, default, valtype, doc, precision=None):
    """Variable read from the muon-system (outer) track, `default` if there is none."""
    full = "?outerTrack().isNonnull()?outerTrack().%s:%s" % (expr, default)
    if precision is None:
        return Var(full, valtype, doc=doc)
    return Var(full, valtype, doc=doc, precision=precision)


# ---------------------------------------------------------------- displaced muons
# slimmedDisplacedMuons exists in Run 3 MiniAOD only. MiniAOD's
# filteredDisplacedMuons already removed displaced muons overlapping standard ones.

selectedDisplacedMuonsForNano = cms.EDFilter(
    "PATMuonSelector",
    src=cms.InputTag("slimmedDisplacedMuons"),
    cut=cms.string("pt > 3"),
)

displacedMuonTable = cms.EDProducer(
    "DisplacedMuonFlatTableProducer",
    src=cms.InputTag("selectedDisplacedMuonsForNano"),
    cut=cms.string(""),  # must stay empty: DiMuonVtx indices refer to src rows
    name=cms.string("DisplacedMuon"),
    doc=cms.string("displaced muons (slimmedDisplacedMuons, pt > 3 GeV)"),
    singleton=cms.bool(False),
    extension=cms.bool(False),
    variables=cms.PSet(
        _common.P4Vars,
        charge=Var("charge", int, doc="electric charge"),
        isGlobal=Var("isGlobalMuon", bool, doc="displaced global muon"),
        isStandalone=Var("isStandAloneMuon", bool, doc="has a muon-system (standalone) track"),
        isTracker=Var("isTrackerMuon", bool, doc="displaced tracker muon"),
        nValidMuonHits=_outer("hitPattern().numberOfValidMuonHits()", -1, "int16",
                              "valid muon-system hits on the outer track (-1: none)"),
        nMuonStations=_outer("hitPattern().muonStationsWithValidHits()", -1, "int16",
                             "muon stations with valid hits on the outer track (-1: none)"),
        normChi2=_outer("normalizedChi2()", -1, float, "outer-track chi2/ndof (-1: none)", precision=10),
        ptErr=_outer("ptError()", -1, float, "outer-track pT uncertainty (-1: none)", precision=10),
    ),
    # Filled by diMuonVertices, which resolves displaced/standard duplicates
    # (same charge, deltaR < displacedMatchDeltaR) before pairing.
    externalVariables=cms.PSet(
        muonIdx=ExtVar(cms.InputTag("diMuonVertices", "dsaMuonIdx"), int,
                       doc="index of the Muon that is a copy of this displaced muon, -1 if none"),
        replaced=ExtVar(cms.InputTag("diMuonVertices", "dsaReplaced"), int,
                        doc="1: this displaced muon is replaced by Muon[muonIdx] in DiMuonVtx; "
                            "0: it is kept (and Muon[muonIdx], if any, is not used)"),
        muonDR=ExtVar(cms.InputTag("diMuonVertices", "dsaMuonDR"), float,
                      doc="deltaR to the nearest Muon of any charge (-1 if the event has none)", precision=10),
    ),
)

# Same matcher as the standard Muon table, with a looser pT match because
# muon-system-only tracks have poor momentum resolution.
displacedMuonsMCMatchForTable = _muons.muonsMCMatchForTable.clone(
    src=displacedMuonTable.src,
    maxDPtRel=cms.double(1.0),
)

displacedMuonMCTable = _muons.muonMCTable.clone(
    src=displacedMuonTable.src,
    mcMap=cms.InputTag("displacedMuonsMCMatchForTable"),
    objName=displacedMuonTable.name,
    docString=cms.string("MC matching to status==1 muons (dR < 0.3, dpT/pT < 1)"),
)

# ---------------------------------------------------------------- dimuon vertices

diMuonVertices = cms.EDProducer(
    "DiMuonVertexProducer",
    muons=cms.InputTag("linkedObjects", "muons"),  # feeds the Muon table, no cut
    displacedMuons=displacedMuonTable.src,
    primaryVertices=cms.InputTag("offlineSlimmedPrimaryVertices"),
    beamSpot=cms.InputTag("offlineBeamSpot"),
    minPt=cms.double(3.0),
    maxAbsEta=cms.double(2.4),
    displacedTrack=cms.string("outer"),
    # A displaced and a standard muon with the same charge within this deltaR
    # are copies of one muon (0 disables). Only one copy becomes a leg: the
    # standard muon if it passes preferredMuonCut, else the displaced muon.
    # Same charge keeps the opposite-sign partner from one LLP decay apart.
    displacedMatchDeltaR=cms.double(0.2),
    displacedMatchSameCharge=cms.bool(True),
    # Within the standard collection: a non-global muon (e.g. RPC/GEM-tagged or
    # muon-system-only) with the same charge within this deltaR of a global one
    # is a copy of it and is not used as a leg (0 disables).
    standardMatchDeltaR=cms.double(0.2),
    standardMatchSameCharge=cms.bool(True),
    preferredMuonCut=cms.string("isGlobalMuon"),
    maxCandidates=cms.uint32(50),
)

diMuonVertexTable = cms.EDProducer(
    "DiMuonVertexTableProducer",
    src=cms.InputTag("diMuonVertices"),
    cut=cms.string(""),
    name=cms.string("DiMuonVtx"),
    doc=cms.string("muon pairs with a common-vertex fit (Kalman) and two-track closest approach; "
                   "lengths in cm, Lxy w.r.t. beam spot (BS) or first PV"),
    singleton=cms.bool(False),
    extension=cms.bool(False),
    variables=cms.PSet(
        _common.P4Vars,
        charge=Var("charge", int, doc="sum of the leg charges"),
        mu1Idx=_ui("mu1Idx", "index of the higher-pT leg in Muon (mu1Type 0) or DisplacedMuon (mu1Type 1)"),
        mu2Idx=_ui("mu2Idx", "index of the lower-pT leg in Muon (mu2Type 0) or DisplacedMuon (mu2Type 1)"),
        mu1Type=_ui("mu1Type", "collection of leg 1: 0 = Muon, 1 = DisplacedMuon", "uint8"),
        mu2Type=_ui("mu2Type", "collection of leg 2: 0 = Muon, 1 = DisplacedMuon", "uint8"),
        valid=Var("userInt('valid')", bool, doc="Kalman vertex fit succeeded"),
        momentaAtVertex=Var("userInt('momentaAtVertex')", bool,
                            doc="p4 and mu*Refit* use leg momenta at the fitted vertex"),
        vx=_uf("vx", "fitted vertex x (0 if not valid)"),
        vy=_uf("vy", "fitted vertex y (0 if not valid)"),
        vz=_uf("vz", "fitted vertex z (0 if not valid)"),
        vxErr=_uf("vxErr", "vertex x uncertainty (-1 if not valid)", 10),
        vyErr=_uf("vyErr", "vertex y uncertainty (-1 if not valid)", 10),
        vzErr=_uf("vzErr", "vertex z uncertainty (-1 if not valid)", 10),
        chi2=_uf("chi2", "vertex fit chi2 (-1 if not valid)", 10),
        ndof=_uf("ndof", "vertex fit degrees of freedom (-1 if not valid)", 10),
        prob=_uf("prob", "vertex fit chi2 probability (-1 if not valid)", 10),
        lxyBS=_uf("lxyBS", "transverse distance of the vertex from the beam spot (-1 if not valid)"),
        lxyBSErr=_uf("lxyBSErr", "uncertainty on lxyBS, incl. beam width (-1 if not valid)", 10),
        lxyPV=_uf("lxyPV", "transverse distance of the vertex from the first PV (-1 if not valid)"),
        lxyPVErr=_uf("lxyPVErr", "uncertainty on lxyPV (-1 if not valid)", 10),
        l3dPV=_uf("l3dPV", "3D distance of the vertex from the first PV (-1 if not valid)"),
        l3dPVErr=_uf("l3dPVErr", "uncertainty on l3dPV (-1 if not valid)", 10),
        cosAlphaXY=_uf("cosAlphaXY", "cos of transverse angle between dimuon p and (vertex - BS); "
                       "crossing point used if the fit failed; -2 if undefined", 12),
        cosAlpha3D=_uf("cosAlpha3D", "cos of 3D angle between dimuon p and (vertex - PV); -2 if undefined", 12),
        dca=_uf("dca", "two-track distance of closest approach (-1 if not computed)", 10),
        cpx=_uf("cpx", "two-track crossing point x (0 if not computed)"),
        cpy=_uf("cpy", "two-track crossing point y (0 if not computed)"),
        cpz=_uf("cpz", "two-track crossing point z (0 if not computed)"),
        lxyCP=_uf("lxyCP", "transverse distance of the crossing point from the beam spot (-1 if not computed)"),
        mu1RefitPt=_uf("mu1RefitPt", "leg 1 pT at the vertex (muon pT if momentaAtVertex is false)", 12),
        mu1RefitEta=_uf("mu1RefitEta", "leg 1 eta at the vertex", 12),
        mu1RefitPhi=_uf("mu1RefitPhi", "leg 1 phi at the vertex", 12),
        mu2RefitPt=_uf("mu2RefitPt", "leg 2 pT at the vertex (muon pT if momentaAtVertex is false)", 12),
        mu2RefitEta=_uf("mu2RefitEta", "leg 2 eta at the vertex", 12),
        mu2RefitPhi=_uf("mu2RefitPhi", "leg 2 phi at the vertex", 12),
        mu1DxyBS=_uf("mu1DxyBS", "leg 1 track dxy w.r.t. the beam spot", 12),
        mu1DxyBSErr=_uf("mu1DxyBSErr", "leg 1 dxy uncertainty incl. beam width", 10),
        mu2DxyBS=_uf("mu2DxyBS", "leg 2 track dxy w.r.t. the beam spot", 12),
        mu2DxyBSErr=_uf("mu2DxyBSErr", "leg 2 dxy uncertainty incl. beam width", 10),
        deltaR=_uf("deltaR", "deltaR between the two legs", 10),
    ),
)

# Extension of the standard Muon table (same source, no cut, so rows line up):
# Muon_dupIdx from the within-collection duplicate step of diMuonVertices.
muonDupTable = cms.EDProducer(
    "DisplacedMuonFlatTableProducer",  # SimpleFlatTableProducer<pat::Muon>
    src=diMuonVertices.muons,
    cut=cms.string(""),
    name=cms.string("Muon"),
    doc=cms.string("DiMuonTagger additions to the Muon table"),
    singleton=cms.bool(False),
    extension=cms.bool(True),
    variables=cms.PSet(),
    externalVariables=cms.PSet(
        dupIdx=ExtVar(cms.InputTag("diMuonVertices", "muonDupIdx"), int,
                      doc="index of the global Muon this muon is a copy of (same charge, close in deltaR); "
                          "such muons are not used in DiMuonVtx; -1 if none"),
    ),
)

dimuonTask = cms.Task(selectedDisplacedMuonsForNano, diMuonVertices)
dimuonTablesTask = cms.Task(displacedMuonTable, diMuonVertexTable, muonDupTable)
dimuonMCTask = cms.Task(displacedMuonsMCMatchForTable, displacedMuonMCTable)
