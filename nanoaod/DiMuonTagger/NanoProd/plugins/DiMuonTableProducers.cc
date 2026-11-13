// Flat-table producers for the DiMuonTagger custom NanoAOD.
//
// The table template is header-only and identical in CMSSW_13_0_X and
// CMSSW_15_0_X, so instantiating it here keeps this package off the
// nanoaod::FlatTable API. The names are our own: 15_0 already ships a
// SimpleCompositeCandidateFlatTableProducer and 13_0 does not, so reusing that
// name would clash in 15_0.

#include "DataFormats/PatCandidates/interface/CompositeCandidate.h"
#include "DataFormats/PatCandidates/interface/Muon.h"
#include "FWCore/Framework/interface/MakerMacros.h"
#include "PhysicsTools/NanoAOD/interface/SimpleFlatTableProducer.h"

typedef SimpleFlatTableProducer<pat::CompositeCandidate> DiMuonVertexTableProducer;
typedef SimpleFlatTableProducer<pat::Muon> DisplacedMuonFlatTableProducer;

DEFINE_FWK_MODULE(DiMuonVertexTableProducer);
DEFINE_FWK_MODULE(DisplacedMuonFlatTableProducer);
