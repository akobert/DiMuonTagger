// DiMuonVertexProducer
//
// Pairs every selected muon from the NanoAOD muon collection (and, if
// configured, the displaced-muon collection), fits a common vertex with the
// Kalman vertex fitter, and stores one pat::CompositeCandidate per pair with
// userFloat/userInt fields. DiMuonVertexTableProducer turns those into the
// DiMuonVtx NanoAOD table.
//
// Leg indices are positions in the input collections. The inputs are the same
// collections that feed the Muon and DisplacedMuon tables (without a cut), so
// mu1Idx/mu2Idx are row indices into those tables.
//
// Builds in CMSSW_13_0_X and CMSSW_15_0_X.

#include <cmath>
#include <exception>
#include <memory>
#include <string>
#include <vector>

#include "CommonTools/Statistics/interface/ChiSquaredProbability.h"
#include "DataFormats/BeamSpot/interface/BeamSpot.h"
#include "DataFormats/Common/interface/View.h"
#include "DataFormats/GeometryCommonDetAlgo/interface/GlobalError.h"
#include "DataFormats/GeometryVector/interface/GlobalPoint.h"
#include "DataFormats/GeometryVector/interface/GlobalVector.h"
#include "DataFormats/Math/interface/deltaR.h"
#include "DataFormats/PatCandidates/interface/CompositeCandidate.h"
#include "DataFormats/PatCandidates/interface/Muon.h"
#include "DataFormats/TrackReco/interface/Track.h"
#include "DataFormats/VertexReco/interface/Vertex.h"
#include "DataFormats/VertexReco/interface/VertexFwd.h"
#include "FWCore/Framework/interface/Event.h"
#include "FWCore/Framework/interface/EventSetup.h"
#include "FWCore/Framework/interface/MakerMacros.h"
#include "FWCore/Framework/interface/global/EDProducer.h"
#include "FWCore/ParameterSet/interface/ConfigurationDescriptions.h"
#include "FWCore/ParameterSet/interface/ParameterSet.h"
#include "FWCore/ParameterSet/interface/ParameterSetDescription.h"
#include "FWCore/Utilities/interface/ESGetToken.h"
#include "FWCore/Utilities/interface/Exception.h"
#include "FWCore/Utilities/interface/InputTag.h"
#include "RecoVertex/KalmanVertexFit/interface/KalmanVertexFitter.h"
#include "RecoVertex/VertexPrimitives/interface/TransientVertex.h"
#include "TrackingTools/PatternTools/interface/TwoTrackMinimumDistance.h"
#include "TrackingTools/Records/interface/TransientTrackRecord.h"
#include "TrackingTools/TrajectoryState/interface/TrajectoryStateClosestToPoint.h"
#include "TrackingTools/TransientTrack/interface/TransientTrack.h"
#include "TrackingTools/TransientTrack/interface/TransientTrackBuilder.h"

namespace {

  constexpr double kMuonMass = 0.1056583755;  // GeV
  constexpr int kPatMuon = 0;
  constexpr int kDisplacedMuon = 1;
  constexpr float kUnset = -1.f;

  struct Leg {
    const pat::Muon* muon;
    const reco::Track* track;
    reco::TransientTrack ttrack;
    int index;  // position in its input collection = row in its NanoAOD table
    int type;   // kPatMuon or kDisplacedMuon
  };

  // Symmetric 3x3 covariance stored as {xx, xy, xz, yy, yz, zz}.
  struct Cov3 {
    double c[6] = {0., 0., 0., 0., 0., 0.};
    Cov3& operator+=(const Cov3& o) {
      for (int i = 0; i < 6; ++i)
        c[i] += o.c[i];
      return *this;
    }
  };

  Cov3 covOf(const GlobalError& e) {
    Cov3 r;
    r.c[0] = e.cxx();
    r.c[1] = e.cyx();
    r.c[2] = e.czx();
    r.c[3] = e.cyy();
    r.c[4] = e.czy();
    r.c[5] = e.czz();
    return r;
  }

  Cov3 covOf(const reco::Vertex& v) {
    Cov3 r;
    r.c[0] = v.covariance(0, 0);
    r.c[1] = v.covariance(0, 1);
    r.c[2] = v.covariance(0, 2);
    r.c[3] = v.covariance(1, 1);
    r.c[4] = v.covariance(1, 2);
    r.c[5] = v.covariance(2, 2);
    return r;
  }

  // Beam-spot position uncertainty plus the transverse beam width, i.e. where
  // a primary vertex could sit in the transverse plane.
  Cov3 covOf(const reco::BeamSpot& bs) {
    Cov3 r;
    r.c[0] = bs.covariance(0, 0) + bs.BeamWidthX() * bs.BeamWidthX();
    r.c[1] = bs.covariance(0, 1);
    r.c[3] = bs.covariance(1, 1) + bs.BeamWidthY() * bs.BeamWidthY();
    return r;
  }

  struct Distance {
    float value = kUnset;
    float error = kUnset;
  };

  // Length of d (transverse or 3D) and its uncertainty, propagating cov.
  Distance distance(double dx, double dy, double dz, const Cov3& cov, bool transverse) {
    if (transverse)
      dz = 0.;
    const double l2 = dx * dx + dy * dy + dz * dz;
    Distance out;
    if (!(l2 > 0.))
      return out;
    const double* c = cov.c;
    double var = dx * dx * c[0] + dy * dy * c[3] + 2. * dx * dy * c[1];
    if (!transverse)
      var += dz * dz * c[5] + 2. * dx * dz * c[2] + 2. * dy * dz * c[4];
    var /= l2;
    out.value = std::sqrt(l2);
    out.error = var > 0. ? std::sqrt(var) : kUnset;
    return out;
  }

  float cosAngle(double ax, double ay, double az, double bx, double by, double bz) {
    const double na = std::sqrt(ax * ax + ay * ay + az * az);
    const double nb = std::sqrt(bx * bx + by * by + bz * bz);
    if (!(na > 0.) || !(nb > 0.))
      return -2.f;  // undefined
    return float((ax * bx + ay * by + az * bz) / (na * nb));
  }

  reco::Candidate::LorentzVector muonP4(const GlobalVector& p) {
    const double e = std::sqrt(p.mag2() + kMuonMass * kMuonMass);
    return reco::Candidate::LorentzVector(p.x(), p.y(), p.z(), e);
  }

}  // namespace

class DiMuonVertexProducer : public edm::global::EDProducer<> {
public:
  explicit DiMuonVertexProducer(const edm::ParameterSet&);
  void produce(edm::StreamID, edm::Event&, const edm::EventSetup&) const override;
  static void fillDescriptions(edm::ConfigurationDescriptions&);

private:
  void collectLegs(const edm::View<pat::Muon>& muons,
                   int type,
                   const TransientTrackBuilder& builder,
                   std::vector<Leg>& legs) const;
  pat::CompositeCandidate makeCandidate(const Leg& a,
                                        const Leg& b,
                                        const reco::BeamSpot& bs,
                                        const reco::Vertex* pv,
                                        const KalmanVertexFitter& fitter) const;

  const edm::EDGetTokenT<edm::View<pat::Muon>> muonToken_;
  const edm::InputTag displacedTag_;
  edm::EDGetTokenT<edm::View<pat::Muon>> displacedToken_;
  const edm::EDGetTokenT<reco::VertexCollection> pvToken_;
  const edm::EDGetTokenT<reco::BeamSpot> bsToken_;
  const edm::ESGetToken<TransientTrackBuilder, TransientTrackRecord> ttbToken_;
  const double minPt_;
  const double maxAbsEta_;
  const std::string displacedTrack_;
  const unsigned int maxCandidates_;
};

DiMuonVertexProducer::DiMuonVertexProducer(const edm::ParameterSet& cfg)
    : muonToken_(consumes<edm::View<pat::Muon>>(cfg.getParameter<edm::InputTag>("muons"))),
      displacedTag_(cfg.getParameter<edm::InputTag>("displacedMuons")),
      pvToken_(consumes<reco::VertexCollection>(cfg.getParameter<edm::InputTag>("primaryVertices"))),
      bsToken_(consumes<reco::BeamSpot>(cfg.getParameter<edm::InputTag>("beamSpot"))),
      ttbToken_(esConsumes<TransientTrackBuilder, TransientTrackRecord>(edm::ESInputTag("", "TransientTrackBuilder"))),
      minPt_(cfg.getParameter<double>("minPt")),
      maxAbsEta_(cfg.getParameter<double>("maxAbsEta")),
      displacedTrack_(cfg.getParameter<std::string>("displacedTrack")),
      maxCandidates_(cfg.getParameter<unsigned int>("maxCandidates")) {
  if (displacedTrack_ != "outer" && displacedTrack_ != "best")
    throw cms::Exception("Configuration") << "displacedTrack must be 'outer' or 'best', got '" << displacedTrack_
                                          << "'";
  if (!displacedTag_.label().empty())
    displacedToken_ = consumes<edm::View<pat::Muon>>(displacedTag_);
  produces<pat::CompositeCandidateCollection>();
}

void DiMuonVertexProducer::collectLegs(const edm::View<pat::Muon>& muons,
                                       int type,
                                       const TransientTrackBuilder& builder,
                                       std::vector<Leg>& legs) const {
  for (size_t i = 0; i < muons.size(); ++i) {
    const pat::Muon& mu = muons[i];
    if (mu.pt() < minPt_ || std::abs(mu.eta()) > maxAbsEta_)
      continue;
    const reco::Track* track = nullptr;
    if (type == kDisplacedMuon && displacedTrack_ == "outer") {
      if (mu.outerTrack().isNonnull())
        track = mu.outerTrack().get();
    } else {
      track = mu.bestTrack();
    }
    if (track == nullptr)
      continue;
    legs.push_back(Leg{&mu, track, builder.build(track), static_cast<int>(i), type});
  }
}

pat::CompositeCandidate DiMuonVertexProducer::makeCandidate(const Leg& a,
                                                            const Leg& b,
                                                            const reco::BeamSpot& bs,
                                                            const reco::Vertex* pv,
                                                            const KalmanVertexFitter& fitter) const {
  // mu1 is the leg with the higher pT.
  const bool aFirst = a.muon->pt() >= b.muon->pt();
  const Leg& l1 = aFirst ? a : b;
  const Leg& l2 = aFirst ? b : a;

  pat::CompositeCandidate cand;
  cand.addUserInt("mu1Idx", l1.index);
  cand.addUserInt("mu2Idx", l2.index);
  cand.addUserInt("mu1Type", l1.type);
  cand.addUserInt("mu2Type", l2.type);
  cand.setCharge(l1.muon->charge() + l2.muon->charge());
  cand.addUserFloat("deltaR", reco::deltaR(*l1.muon, *l2.muon));

  // Per-leg transverse impact parameter w.r.t. the beam spot. The error adds
  // the mean transverse beam width to the track's own dxy error.
  const double beamWidth = 0.5 * (bs.BeamWidthX() + bs.BeamWidthY());
  cand.addUserFloat("mu1DxyBS", l1.track->dxy(bs));
  cand.addUserFloat("mu1DxyBSErr", std::hypot(l1.track->dxyError(), beamWidth));
  cand.addUserFloat("mu2DxyBS", l2.track->dxy(bs));
  cand.addUserFloat("mu2DxyBSErr", std::hypot(l2.track->dxyError(), beamWidth));

  // Two-track point of closest approach: always attempted, so candidates whose
  // Kalman fit fails (e.g. far outside the tracker) still get a position.
  float dca = kUnset;
  bool cpValid = false;
  GlobalPoint cp;
  {
    TwoTrackMinimumDistance ttmd;
    if (ttmd.calculate(l1.ttrack.initialFreeState(), l2.ttrack.initialFreeState()) && ttmd.status()) {
      dca = ttmd.distance();
      cp = ttmd.crossingPoint();
      cpValid = true;
    }
  }
  cand.addUserFloat("dca", dca);
  cand.addUserFloat("cpx", cpValid ? cp.x() : 0.f);
  cand.addUserFloat("cpy", cpValid ? cp.y() : 0.f);
  cand.addUserFloat("cpz", cpValid ? cp.z() : 0.f);
  cand.addUserFloat("lxyCP",
                    cpValid ? std::hypot(cp.x() - bs.x(cp.z()), cp.y() - bs.y(cp.z())) : kUnset);

  // Kalman vertex fit.
  TransientVertex tv;
  bool valid = false;
  try {
    tv = fitter.vertex(std::vector<reco::TransientTrack>{l1.ttrack, l2.ttrack});
    valid = tv.isValid();
  } catch (const std::exception&) {
    valid = false;
  }
  cand.addUserInt("valid", valid ? 1 : 0);

  // Leg momenta at the vertex (or the plain muon momenta without one).
  GlobalVector p1(l1.muon->px(), l1.muon->py(), l1.muon->pz());
  GlobalVector p2(l2.muon->px(), l2.muon->py(), l2.muon->pz());
  bool atVertex = false;
  // Position used for the pointing angle: fitted vertex, else crossing point.
  bool havePos = false;
  GlobalPoint pos;

  Distance lxyBS, lxyPV, l3dPV;
  float vxErr = kUnset, vyErr = kUnset, vzErr = kUnset;
  float chi2 = kUnset, ndof = kUnset, prob = kUnset;

  if (valid) {
    pos = tv.position();
    havePos = true;
    const GlobalError err = tv.positionError();
    vxErr = std::sqrt(err.cxx());
    vyErr = std::sqrt(err.cyy());
    vzErr = std::sqrt(err.czz());
    chi2 = tv.totalChiSquared();
    ndof = tv.degreesOfFreedom();
    prob = ndof > 0.f ? ChiSquaredProbability(chi2, ndof) : kUnset;

    const TrajectoryStateClosestToPoint s1 = l1.ttrack.trajectoryStateClosestToPoint(pos);
    const TrajectoryStateClosestToPoint s2 = l2.ttrack.trajectoryStateClosestToPoint(pos);
    if (s1.isValid() && s2.isValid()) {
      p1 = s1.momentum();
      p2 = s2.momentum();
      atVertex = true;
    }

    Cov3 bsCov = covOf(err);
    bsCov += covOf(bs);
    lxyBS = distance(pos.x() - bs.x(pos.z()), pos.y() - bs.y(pos.z()), 0., bsCov, true);
    if (pv != nullptr) {
      Cov3 pvCov = covOf(err);
      pvCov += covOf(*pv);
      lxyPV = distance(pos.x() - pv->x(), pos.y() - pv->y(), 0., pvCov, true);
      l3dPV = distance(pos.x() - pv->x(), pos.y() - pv->y(), pos.z() - pv->z(), pvCov, false);
    }
  } else if (cpValid) {
    pos = cp;
    havePos = true;
  }

  cand.addUserFloat("vx", valid ? pos.x() : 0.f);
  cand.addUserFloat("vy", valid ? pos.y() : 0.f);
  cand.addUserFloat("vz", valid ? pos.z() : 0.f);
  cand.addUserFloat("vxErr", vxErr);
  cand.addUserFloat("vyErr", vyErr);
  cand.addUserFloat("vzErr", vzErr);
  cand.addUserFloat("chi2", chi2);
  cand.addUserFloat("ndof", ndof);
  cand.addUserFloat("prob", prob);
  cand.addUserFloat("lxyBS", lxyBS.value);
  cand.addUserFloat("lxyBSErr", lxyBS.error);
  cand.addUserFloat("lxyPV", lxyPV.value);
  cand.addUserFloat("lxyPVErr", lxyPV.error);
  cand.addUserFloat("l3dPV", l3dPV.value);
  cand.addUserFloat("l3dPVErr", l3dPV.error);
  cand.addUserInt("momentaAtVertex", atVertex ? 1 : 0);

  cand.addUserFloat("mu1RefitPt", p1.perp());
  cand.addUserFloat("mu1RefitEta", p1.eta());
  cand.addUserFloat("mu1RefitPhi", p1.phi());
  cand.addUserFloat("mu2RefitPt", p2.perp());
  cand.addUserFloat("mu2RefitEta", p2.eta());
  cand.addUserFloat("mu2RefitPhi", p2.phi());

  const reco::Candidate::LorentzVector p4 = atVertex ? muonP4(p1) + muonP4(p2) : l1.muon->p4() + l2.muon->p4();
  cand.setP4(p4);

  // Pointing angle between the dimuon momentum and the displacement from the
  // beam spot (transverse) or from the PV (3D).
  float cosXY = -2.f, cos3D = -2.f;
  if (havePos) {
    cosXY = cosAngle(pos.x() - bs.x(pos.z()), pos.y() - bs.y(pos.z()), 0., p4.px(), p4.py(), 0.);
    if (pv != nullptr)
      cos3D = cosAngle(pos.x() - pv->x(), pos.y() - pv->y(), pos.z() - pv->z(), p4.px(), p4.py(), p4.pz());
    cand.setVertex(reco::Candidate::Point(pos.x(), pos.y(), pos.z()));
  }
  cand.addUserFloat("cosAlphaXY", cosXY);
  cand.addUserFloat("cosAlpha3D", cos3D);
  return cand;
}

void DiMuonVertexProducer::produce(edm::StreamID, edm::Event& iEvent, const edm::EventSetup& iSetup) const {
  auto out = std::make_unique<pat::CompositeCandidateCollection>();

  const TransientTrackBuilder& builder = iSetup.getData(ttbToken_);
  const reco::BeamSpot& bs = iEvent.get(bsToken_);
  const reco::VertexCollection& pvs = iEvent.get(pvToken_);
  const reco::Vertex* pv = pvs.empty() ? nullptr : &pvs.front();

  std::vector<Leg> legs;
  collectLegs(iEvent.get(muonToken_), kPatMuon, builder, legs);
  if (!displacedTag_.label().empty())
    collectLegs(iEvent.get(displacedToken_), kDisplacedMuon, builder, legs);

  const KalmanVertexFitter fitter(true);
  for (size_t i = 0; i < legs.size() && out->size() < maxCandidates_; ++i)
    for (size_t j = i + 1; j < legs.size() && out->size() < maxCandidates_; ++j)
      out->push_back(makeCandidate(legs[i], legs[j], bs, pv, fitter));

  iEvent.put(std::move(out));
}

void DiMuonVertexProducer::fillDescriptions(edm::ConfigurationDescriptions& descriptions) {
  edm::ParameterSetDescription desc;
  desc.add<edm::InputTag>("muons", edm::InputTag("linkedObjects", "muons"))
      ->setComment("collection feeding the NanoAOD Muon table (indices must match)");
  desc.add<edm::InputTag>("displacedMuons", edm::InputTag(""))
      ->setComment("collection feeding the DisplacedMuon table; empty = standard muons only");
  desc.add<edm::InputTag>("primaryVertices", edm::InputTag("offlineSlimmedPrimaryVertices"));
  desc.add<edm::InputTag>("beamSpot", edm::InputTag("offlineBeamSpot"));
  desc.add<double>("minPt", 3.0)->setComment("leg pT threshold (GeV)");
  desc.add<double>("maxAbsEta", 2.4)->setComment("leg |eta| limit");
  desc.add<std::string>("displacedTrack", "outer")
      ->setComment("track for displaced muons: 'outer' (muon-system only) or 'best'");
  desc.add<unsigned int>("maxCandidates", 50)->setComment("maximum pairs stored per event");
  descriptions.add("diMuonVertexProducer", desc);
}

DEFINE_FWK_MODULE(DiMuonVertexProducer);
