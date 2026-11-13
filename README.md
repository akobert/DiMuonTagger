# DiMuonTagger
Development space for creating/training di-muon tagger

The tagger identifies displaced μ⁺μ⁻ vertices from long-lived particles,
using CMS Run 2 UL and Run 3 simulation.

- [`samples/`](samples/README.md): signal and background MC catalog, and how
  to resolve it to DAS dataset names
- [`scripts/resolve_samples.py`](scripts/resolve_samples.py): dasgoclient
  resolver (run on lxplus/LPC)
- [`nanoaod/`](nanoaod/README.md): CMSSW custom NanoAOD (Run 3) with displaced
  muons and dimuon vertex fits, plus cmsDriver and CRAB tooling
- [`analysis/`](analysis/README.md): coffea framework (coffea-casa) for truth-labelled
  histograms and per-candidate parquet skims from that NanoAOD
