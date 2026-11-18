"""End to end: synthetic files -> scripts/run.py (iterative) -> hists, cutflow, parquet -> plot.py."""

import glob
import os

import awkward as ak
import hist
import numpy as np
import pytest

import synthetic
from conftest import run_script

SIG = synthetic.dataset_name("signal")
BKG = synthetic.dataset_name("background")


def _skim(run, key):
    files = sorted(glob.glob(os.path.join(str(run["skims"]), synthetic.dataset_name(key), "*.parquet")))
    assert files, "no parquet files for %s" % key
    table = ak.concatenate([ak.from_parquet(f) for f in files])
    order = np.lexsort((ak.to_numpy(table.cand_index), ak.to_numpy(table.event)))
    return table[order], files


def test_output_layout(iterative_run):
    rec = iterative_run["record"]
    assert set(rec["output"]) == {SIG, BKG}
    assert rec["metadata"][SIG]["process"] == "hahm_zd"
    assert rec["metadata"][BKG]["era"] == "2023preBPix"


def test_sumw_and_cutflow(iterative_run):
    out = iterative_run["record"]["output"]
    assert out[SIG]["sumw"] == pytest.approx(3.0)       # 2 + 2 - 1; no Runs tree: from the events
    assert out[SIG]["sumw_events"] == pytest.approx(3.0)
    assert out[BKG]["sumw"] == pytest.approx(10.0)      # Runs genEventSumw, once for the file's 3 chunks
    assert out[BKG]["sumw_events"] == pytest.approx(6.0)
    sig, bkg = out[SIG]["cutflow"], out[BKG]["cutflow"]
    assert (sig["events_generated"], bkg["events_generated"]) == (3, 10)
    assert (sig["events"], sig["events_with_candidate"], sig["candidates"], sig["candidates_used"]) == (3, 2, 5, 5)
    assert sig["candidates_fit_valid"] == 4
    assert (bkg["events"], bkg["candidates"], bkg["candidates_bad_leg_index"], bkg["candidates_used"]) == (6, 8, 1, 7)
    assert (sig["cat_signal"], sig["cat_llp_partial"], sig["cat_prompt"], sig["cat_unmatched"]) == (1, 2, 1, 1)
    assert (bkg["cat_heavy_flavour"], bkg["cat_unmatched"], bkg["cat_other"], bkg["cat_prompt"]) == (3, 1, 2, 1)
    assert bkg["cat_signal"] == 0 and bkg["cat_llp_partial"] == 0
    assert sig["legs"] == 6 and sig["legs_gen_matched"] == 5
    assert sig["skim_rows"] == 5 and bkg["skim_rows"] == 7


@pytest.mark.parametrize("key", ["signal", "background"])
def test_truth_labels_in_skim(iterative_run, key):
    table, _ = _skim(iterative_run, key)
    first = 1000 if key == "signal" else 2000
    got = [(int(e) - first, int(c), str(lab)) for e, c, lab in zip(table.event, table.cand_index, table.label)]
    assert got == synthetic.EXPECTED[key]


def test_skim_columns(iterative_run):
    _, bkg_files = _skim(iterative_run, "background")
    assert len(bkg_files) == 3                              # 6 events, chunksize 2: one file per chunk
    table, _ = _skim(iterative_run, "signal")
    for col in ("mass", "lxyBS", "lxyCP", "valid", "momentaAtVertex", "mu1Idx", "mu2Type", "lxySig", "normChi2",
                "pairType", "mu1_pt", "mu2_nValidMuonHits", "mu1_dxy", "label", "label_code", "weight", "gen_lxy",
                "mu1_gen_llp_pdg", "dataset", "process", "role", "era", "das_dataset", "run", "event"):
        assert col in table.fields, col
    sig = table[table.label == "signal"]
    assert len(sig) == 1
    row = sig[0]
    assert row.weight == pytest.approx(2.0)
    assert row.gen_lxy == pytest.approx(5.0, abs=1e-4)
    assert row.mu1_gen_llp_pdg == 1023 and row.mu2_gen_llp_pdg == 1023
    assert row.mu1_pt == pytest.approx(35.0) and row.mu2_pt == pytest.approx(22.0)
    assert row.pairType == 0
    assert row.process == "hahm_zd" and row.era == "2022postEE" and row.role == "signal"
    # Muon-only variables are NaN for DisplacedMuon legs and vice versa
    partial = table[(table.label == "llp_partial") & (table.mu2_type == 1)]
    assert np.all(np.isnan(ak.to_numpy(partial.mu2_dxy)))
    assert np.all(ak.to_numpy(partial.mu2_nValidMuonHits) == 24)
    assert np.all(np.isnan(ak.to_numpy(table[table.mu1_type == 0].mu1_nValidMuonHits)))


def test_histogram_contents(iterative_run):
    out = iterative_run["record"]["output"]
    h = out[SIG]["hists"]
    mass = h["mass"]
    assert mass[{"category": "signal"}].values().sum() == pytest.approx(2.0)
    assert mass[{"category": "signal", "mass": hist.loc(20.0)}].value == pytest.approx(2.0)
    assert mass[{"category": "llp_partial"}].values().sum() == pytest.approx(4.0)
    assert mass[{"category": "prompt", "mass": hist.loc(91.0)}].value == pytest.approx(2.0)
    # lxy only for valid fits: the invalid llp_partial candidate is not filled
    assert h["lxy"][{"category": "llp_partial"}].values(flow=True).sum() == pytest.approx(2.0)
    assert h["lxy_cp"][{"category": "llp_partial"}].values(flow=True).sum() == pytest.approx(4.0)
    assert h["fit_valid"][{"category": "llp_partial", "fit_valid": hist.loc(0)}].value == pytest.approx(2.0)
    assert h["pair_type"][{"category": "llp_partial", "pair_type": hist.loc(1)}].value == pytest.approx(4.0)
    assert h["leg_pt"][{"category": "signal"}].values().sum() == pytest.approx(4.0)   # 2 legs x weight 2
    assert h["lxy_sig"][{"category": "signal", "lxy_sig": hist.loc(53.5)}].value == pytest.approx(2.0)
    assert h["norm_chi2"][{"category": "signal", "norm_chi2": hist.loc(1.3)}].value == pytest.approx(2.0)
    # Lxy residual for signal: fit 5.35 - 5.0, crossing point 5.15 - 5.0 (bin centres)
    res = h["lxy_residual"]
    fit = res[{"method": "fit"}].project("residual")
    cp = res[{"method": "crossing"}].project("residual")
    assert fit[hist.loc(0.35)].value == pytest.approx(2.0) and fit.values().sum() == pytest.approx(2.0)
    assert cp[hist.loc(0.15)].value == pytest.approx(2.0)
    bkg = out[BKG]["hists"]
    assert bkg["mass"][{"category": "heavy_flavour"}].values().sum() == pytest.approx(3.0)
    assert bkg["lxy_residual"].values().sum() == 0
    # nothing lands outside the category axis
    assert mass.values(flow=True).sum() == pytest.approx(10.0)


def test_ancestor_histograms(iterative_run):
    out = iterative_run["record"]["output"]
    anc = out[SIG]["hists"]["ancestor_pdgid"]
    counts = dict(zip((int(p) for p in anc.axes[0]), anc.values()))
    assert counts[1023] == 3          # three muons below a Z_D, copies counted once
    assert counts[25] == 3 and counts[23] == 2
    mothers = out[SIG]["hists"]["mother_pdgid"]
    mcounts = dict(zip((int(p) for p in mothers.axes[0]), mothers.values()))
    assert mcounts == {1023: 3, 23: 2}   # the FSR muon copy is skipped
    bkg = out[BKG]["hists"]["ancestor_pdgid"]
    bcounts = dict(zip((int(p) for p in bkg.axes[0]), bkg.values()))
    assert bcounts[511] == 2 and bcounts[411] == 1 and bcounts[443] == 4 and bcounts[521] == 2


def test_selection_options(synthetic_fileset, tmp_path):
    out = tmp_path / "h.coffea"
    run_script("run.py", "--fileset", synthetic_fileset, "--process", "qcd_muenriched", "--maxchunks", 1,
               "--chunksize", 2, "--output", out)
    from coffea.util import load

    rec = load(str(out))
    assert set(rec["output"]) == {BKG}
    assert rec["output"][BKG]["nevents"] == 2
    assert rec["skim_dest"] is None


def test_plot(iterative_run):
    plotdir = iterative_run["dir"] / "plotout"
    log = run_script("plot.py", iterative_run["hists_path"], "--outdir", plotdir)
    assert "WARNING" not in log
    for era in ("2022postEE", "2023preBPix", "all"):
        assert (plotdir / "plots" / era / "mass.png").stat().st_size > 10_000
    assert (plotdir / "plots" / "2022postEE" / "lxy_residual.png").exists()
    assert not (plotdir / "plots" / "2023preBPix" / "lxy_residual.png").exists()   # no signal there
    cutflow = (plotdir / "cutflow.md").read_text()
    assert SIG in cutflow and BKG in cutflow
    ancestors = (plotdir / "ancestors.md").read_text()
    assert "| 1023 |" in ancestors and "**LLP**" in ancestors
