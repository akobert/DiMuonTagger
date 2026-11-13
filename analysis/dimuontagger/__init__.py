"""Coffea framework for the DiMuonTagger displaced di-muon vertex studies.

Modules:
    config     load analysis/config.yaml
    schema     DiMuonNanoSchema, NanoAODSchema plus DisplacedMuon and DiMuonVtx
    truth      gen-ancestry walk and per-candidate truth labels
    processor  DiMuonProcessor: histograms, cutflow, parquet skims
    fileset    build coffea filesets from DAS (lxplus only; needs no coffea)
    runner     helpers shared by scripts/run.py and the quickstart notebook
    plotting   shape comparisons and tables used by scripts/plot.py

This file imports nothing heavy, so `dimuontagger.fileset` works on lxplus
without coffea installed.
"""

__version__ = "0.1.0"
