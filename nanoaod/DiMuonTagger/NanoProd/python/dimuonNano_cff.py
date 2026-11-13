"""cmsDriver customisation for the DiMuonTagger custom NanoAOD.

Use with:
    cmsDriver.py ... --step NANO \
        --customise DiMuonTagger/NanoProd/dimuonNano_cff.customiseDiMuonNano

One function for data and MC: MC-only modules go into nanoTableTaskFS, which is
scheduled only for MC. Works in CMSSW_13_0_X and CMSSW_15_0_X.
"""
from PhysicsTools.NanoAOD.common_cff import Var


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
