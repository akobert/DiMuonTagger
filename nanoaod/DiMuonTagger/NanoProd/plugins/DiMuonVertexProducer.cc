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
// Displaced muons are often a second reconstruction of a muon that is also in
// the standard collection. Pairing such a copy with its original, or with
// another prompt muon, gives spurious far-displaced vertices. So duplicates are
// resolved before pairing:
//  - a displaced and a standard muon are copies of one muon if they are within
//    displacedMatchDeltaR and (with displacedMatchSameCharge) have the same
//    charge; the charge requirement keeps the opposite-sign partner from the
//    same LLP decay, often only ~0.2-0.8 away, from being taken for a copy;
//  - matching is one-to-one, closest pairs first;
//  - of each copy pair only the better one becomes a leg: the standard muon if
//    it passes preferredMuonCut (default: global muon, i.e. with a tracker
//    track), otherwise the displaced muon. Standard muons reconstructed in the
//    muon system only are refitted as if from the beam spot, which biases them
//    for displaced decays.
// The match is written as ValueMaps for the DisplacedMuon table: dsaMuonIdx
// (matched standard muon or -1), dsaReplaced (1 = displaced copy dropped,
// 0 = kept and, if matched, the standard copy dropped) and dsaMuonDR (deltaR
// to the nearest standard muon of any charge).
//
// Builds in CMSSW_13_0_X and CMSSW_15_0_X.

#include <algorithm>
#include <cmath>
#include <exception>
#include <memory>
#include <string>
#include <tuple>
#include <vector>

#include "CommonTools/Statistics/interface/ChiSquaredProbability.h"
#include "CommonTools/Utils/interface/StringCutObjectSelector.h"
#include "DataFormats/BeamSpot/interface/BeamSpot.h"
#include "DataFormats/Common/interface/ValueMap.h"
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

  // kDm prefix: ROOT puts k-names such as kUnset (TError.h) in the global
  // namespace, which makes unprefixed names here ambiguous.
  constexpr double kDmMuonMass = 0.1056583755;  // GeV
  constexpr int kDmPatMuon = 0;
  constexpr int kDmDisplacedMuon = 1;
  constexpr float kDmUnset = -1.f;

  struct Leg {
    const pat::Muon* muon;
    const reco::Track* track;
    reco::TransientTrack ttrack;
    int index;  // position in its input collection = row in its NanoAOD table
    int type;   // kDmPatMuon or kDmDisplacedMuon
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
    float value = kDmUnset;
    float error = kDmUnset;
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
    out.error = var > 0. ? std::sqrt(var) : kDmUnset;
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
    const double e = std::sqrt(p.mag2() + kDmMuonMass * kDmMuonMass);
    return reco::Candidate::LorentzVector(p.x(), p.y(), p.z(), e);
  }

}  // namespace

class DiMuonVertexProducer : public edm::global::EDProducer<> {
public:
  explicit DiMuonVertexProducer(const edm::ParameterSet&);
  void produce(edm::StreamID, edm::Event&, const edm::EventSetup&) const override;
  static void fillDescriptions(edm::ConfigurationDescriptions&);

private:
  // `drop` (optional): per muon, nonzero if it must not be used as a leg
  // (the other copy of a duplicate pair is used instead).
  void collectLegs(const edm::View<pat::Muon>& muons,
                   int type,
                   const TransientTrackBuilder& builder,
                   std::vector<Leg>& legs,
                   const std::vector<char>* drop = nullptr) const;
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
  const double displacedMatchDeltaR_;
  const bool displacedMatchSameCharge_;
  const StringCutObjectSelector<pat::Muon> preferredMuon_;
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
      displacedMatchDeltaR_(cfg.getParameter<double>("displacedMatchDeltaR")),
      displacedMatchSameCharge_(cfg.getParameter<bool>("displacedMatchSameCharge")),
      preferredMuon_(cfg.getParameter<std::string>("preferredMuonCut")),
      maxCandidates_(cfg.getParameter<unsigned int>("maxCandidates")) {
  if (displacedTrack_ != "outer" && displacedTrack_ != "best")
    throw cms::Exception("Configuration") << "displacedTrack must be 'outer' or 'best', got '" << displacedTrack_
                                          << "'";
  produces<pat::CompositeCandidateCollection>();
  if (!displacedTag_.label().empty()) {
    displacedToken_ = consumes<edm::View<pat::Muon>>(displacedTag_);
    produces<edm::ValueMap<int>>("dsaMuonIdx");
    produces<edm::ValueMap<int>>("dsaReplaced");
    produces<edm::ValueMap<float>>("dsaMuonDR");
  }
}

void DiMuonVertexProducer::collectLegs(const edm::View<pat::Muon>& muons,
                                       int type,
                                       const TransientTrackBuilder& builder,
                                       std::vector<Leg>& legs,
                                       const std::vector<char>* drop) const {
  for (size_t i = 0; i < muons.size(); ++i) {
    const pat::Muon& mu = muons[i];
    if (mu.pt() < minPt_ || std::abs(mu.eta()) > maxAbsEta_)
      continue;
    if (drop != nullptr && (*drop)[i])
      continue;
    const reco::Track* track = nullptr;
    if (type == kDmDisplacedMuon && displacedTrack_ == "outer") {
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
  float dca = kDmUnset;
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
                    cpValid ? std::hypot(cp.x() - bs.x(cp.z()), cp.y() - bs.y(cp.z())) : kDmUnset);

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
  float vxErr = kDmUnset, vyErr = kDmUnset, vzErr = kDmUnset;
  float chi2 = kDmUnset, ndof = kDmUnset, prob = kDmUnset;

  if (valid) {
    pos = tv.position();
    havePos = true;
    const GlobalError err = tv.positionError();
    vxErr = std::sqrt(err.cxx());
    vyErr = std::sqrt(err.cyy());
    vzErr = std::sqrt(err.czz());
    chi2 = tv.totalChiSquared();
    ndof = tv.degreesOfFreedom();
    prob = ndof > 0.f ? ChiSquaredProbability(chi2, ndof) : kDmUnset;

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

  const edm::View<pat::Muon>& muons = iEvent.get(muonToken_);
  std::vector<Leg> legs;

  if (displacedTag_.label().empty()) {
    collectLegs(muons, kDmPatMuon, builder, legs);
  } else {
    const edm::Handle<edm::View<pat::Muon>> displaced = iEvent.getHandle(displacedToken_);
    const size_t nDsa = displaced->size();

    // Candidate copy pairs (dR, displaced j, standard i), and the nearest
    // standard muon of any charge for every displaced muon.
    std::vector<float> nearestDR(nDsa, kDmUnset);
    std::vector<std::tuple<double, size_t, size_t>> pairs;
    for (size_t j = 0; j < nDsa; ++j) {
      const pat::Muon& dsa = (*displaced)[j];
      for (size_t i = 0; i < muons.size(); ++i) {
        const double dr = reco::deltaR(dsa, muons[i]);
        if (nearestDR[j] < 0.f || dr < nearestDR[j])
          nearestDR[j] = dr;
        if (dr < displacedMatchDeltaR_ && (!displacedMatchSameCharge_ || dsa.charge() == muons[i].charge()))
          pairs.emplace_back(dr, j, i);
      }
    }

    // One-to-one matching, closest pairs first; keep the better copy of each.
    std::sort(pairs.begin(), pairs.end());
    std::vector<int> matchedMuon(nDsa, -1);
    std::vector<int> replaced(nDsa, 0);
    std::vector<char> dropDsa(nDsa, 0), dropMuon(muons.size(), 0), muonUsed(muons.size(), 0);
    for (const auto& pair : pairs) {
      const size_t j = std::get<1>(pair);
      const size_t i = std::get<2>(pair);
      if (matchedMuon[j] >= 0 || muonUsed[i])
        continue;
      matchedMuon[j] = static_cast<int>(i);
      muonUsed[i] = 1;
      if (preferredMuon_(muons[i])) {
        replaced[j] = 1;
        dropDsa[j] = 1;
      } else {
        dropMuon[i] = 1;
      }
    }

    collectLegs(muons, kDmPatMuon, builder, legs, &dropMuon);
    collectLegs(*displaced, kDmDisplacedMuon, builder, legs, &dropDsa);

    auto idxMap = std::make_unique<edm::ValueMap<int>>();
    edm::ValueMap<int>::Filler idxFiller(*idxMap);
    idxFiller.insert(displaced, matchedMuon.begin(), matchedMuon.end());
    idxFiller.fill();
    iEvent.put(std::move(idxMap), "dsaMuonIdx");

    auto replacedMap = std::make_unique<edm::ValueMap<int>>();
    edm::ValueMap<int>::Filler replacedFiller(*replacedMap);
    replacedFiller.insert(displaced, replaced.begin(), replaced.end());
    replacedFiller.fill();
    iEvent.put(std::move(replacedMap), "dsaReplaced");

    auto drMap = std::make_unique<edm::ValueMap<float>>();
    edm::ValueMap<float>::Filler drFiller(*drMap);
    drFiller.insert(displaced, nearestDR.begin(), nearestDR.end());
    drFiller.fill();
    iEvent.put(std::move(drMap), "dsaMuonDR");
  }

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
  desc.add<double>("displacedMatchDeltaR", 0.2)
      ->setComment("displaced and standard muon within this deltaR are copies of one muon (0 disables)");
  desc.add<bool>("displacedMatchSameCharge", true)
      ->setComment("copies must have the same charge (keeps the opposite-sign partner muon apart)");
  desc.add<std::string>("preferredMuonCut", "isGlobalMuon")
      ->setComment("standard muons passing this cut are kept over their displaced copy; otherwise the "
                   "displaced copy is kept and the standard muon dropped");
  desc.add<unsigned int>("maxCandidates", 50)->setComment("maximum pairs stored per event");
  descriptions.add("diMuonVertexProducer", desc);
}

DEFINE_FWK_MODULE(DiMuonVertexProducer);
