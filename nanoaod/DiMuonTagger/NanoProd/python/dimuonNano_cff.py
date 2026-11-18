"""cmsDriver customisation for the DiMuonTagger custom NanoAOD.

Use with:
    cmsDriver.py ... --step NANO \
        --customise DiMuonTagger/NanoProd/dimuonNano_cff.customiseDiMuonNano

customiseDiMuonNanoFiltered does the same and also writes only events with at
least one DiMuonVtx candidate (used for backgrounds; see make_cfg.py).

One function for data and MC: MC-only modules go into nanoTableTaskFS, which is
scheduled only for MC. Works in CMSSW_13_0_X and CMSSW_15_0_X.
"""
import FWCore.ParameterSet.Config as cms
from PhysicsTools.NanoAOD.common_cff import Var

FILTER_PATH = "dimuonFilterPath"
GEN_WEIGHTS_PATH = "dimuonGenWeightsPath"


def customiseDiMuonNano(process):
    # 15_0's muons_cff no longer loads it; needed by DiMuonVertexProducer.
    process.load("TrackingTools.TransientTrack.TransientTrackBuilder_cfi")
    process.load("DiMuonTagger.NanoProd.dimuonVertex_cff")

    process.nanoTableTaskCommon.add(process.dimuonTask, process.dimuonTablesTask)
    process.nanoTableTaskFS.add(process.dimuonMCTask)

    # Production vertex of every stored gen particle, at full precision: the
    # LLP decay position is the production vertex of its daughter muons.
    for coord in ("vx", "vy", "vz"):
        setattr(process.genParticleTable.variables, coord,
                Var(coord, float, doc="production vertex %s (cm)" % coord[1], precision=-1))
    return process


def customiseDiMuonNanoFiltered(process):
    """customiseDiMuonNano, writing only events with >= 1 DiMuonVtx candidate.

    Events without a candidate carry nothing for the tagger. The Runs tree
    still counts every processed event (genEventCount, genEventSumw, ...), so
    normalise with it rather than with the events in the file.
    """
    process = customiseDiMuonNano(process)
    process.diMuonVertexFilter = cms.EDFilter(
        "CandViewCountFilter", src=cms.InputTag("diMuonVertices"), minNumber=cms.uint32(1))
    setattr(process, FILTER_PATH, cms.Path(process.diMuonVertexFilter))
    paths = [getattr(process, FILTER_PATH)]

    # genWeightsTable fills the Runs-tree counters from the events it runs on.
    # In a Task it runs only when the output module asks for its product, i.e.
    # only for kept events; on a Path it runs for every event.
    if hasattr(process, "genWeightsTable"):
        setattr(process, GEN_WEIGHTS_PATH, cms.Path(process.genWeightsTable))
        paths.append(getattr(process, GEN_WEIGHTS_PATH))
    else:
        print("customiseDiMuonNanoFiltered: no genWeightsTable (data?); Runs counters are not scheduled")

    schedule = getattr(process, "schedule", None)
    if schedule is not None:
        for path in reversed(paths):
            schedule.insert(0, path)
    for output in process.outputModules_().values():
        output.SelectEvents = cms.untracked.PSet(SelectEvents=cms.vstring(FILTER_PATH))
    return process
