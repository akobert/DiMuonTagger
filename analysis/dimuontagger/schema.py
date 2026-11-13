"""NanoEvents schema for the DiMuonTagger custom NanoAOD.

Standard Run 3 NanoAOD plus (see nanoaod/DiMuonTagger/NanoProd/test/check_nano.py):

- ``GenPart_vx/vy/vz``: production vertex (cm)
- ``DisplacedMuon``: displaced standalone/global muons (slimmedDisplacedMuons)
- ``DiMuonVtx``: one row per muon pair with a vertex fit. ``mu1Idx/mu2Idx`` are
  local indices into ``Muon`` (``muNType == 0``) or ``DisplacedMuon``
  (``muNType == 1``). They are not cross-referenced here, because the target
  collection depends on the row; the processor resolves them itself.
"""

from coffea.nanoevents.schemas import NanoAODSchema


class DiMuonNanoSchema(NanoAODSchema):
    """NanoAODSchema with the DisplacedMuon and DiMuonVtx collections.

    Follows the PFNanoAODSchema pattern: extend the class-level ``mixins`` and
    ``all_cross_references`` mappings.
    """

    __graphed_capable__ = True  # not inherited from NanoAODSchema

    mixins = {
        **NanoAODSchema.mixins,
        "DisplacedMuon": "PtEtaPhiMCandidate",
        "DiMuonVtx": "PtEtaPhiMCollection",
    }
    all_cross_references = {
        **NanoAODSchema.all_cross_references,
        "DisplacedMuon_genPartIdx": "GenPart",
    }
