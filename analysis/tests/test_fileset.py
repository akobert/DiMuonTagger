"""fileset.build_fileset against a fake DAS (the real one needs lxplus)."""

import json

from dimuontagger import fileset as fs

TAG = "v1"
HAHM = "/HTo2ZdTo2mu2x_MZd-20_ctau-100mm_TuneCP5_13p6TeV_madgraph-pythia8/jdoe-DiMuonNano_v1_2022postEE-" \
       "0123456789abcdef0123456789abcdef/USER"
QCD = "/QCD_PT-50to80_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8/jdoe-DiMuonNano_v1-" \
      "fedcba9876543210fedcba9876543210/USER"      # no era in the name: taken from the parent
QCD_PARENT = "/QCD_PT-50to80_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8/" \
             "Run3Summer23BPixMiniAODv4-130X_mcRun3_2023_realistic_postBPix_v2-v2/MINIAODSIM"
ODD = "/SomethingElse_13p6TeV/jdoe-DiMuonNano_v1-00ff/USER"        # no process, no era anywhere
EMPTY = "/TTto2L2Nu_TuneCP5_13p6TeV_powheg-pythia8/jdoe-DiMuonNano_v1_2024-00aa/USER"
OTHER_TAG = "/DYto2Mu_MLL-50to120_TuneCP5_13p6TeV_powheg-pythia8/jdoe-DiMuonNano_v12_2024-11aa/USER"
RUN2 = "/QCD_Pt-50To80_MuEnrichedPt5_TuneCP5_13TeV-pythia8/jdoe-DiMuonNano_v1_2016preVFP-22bb/USER"


def fake_das(calls):
    answers = {
        "dataset=/*/jdoe-DiMuonNano_v1*/USER instance=prod/phys03": [HAHM, QCD, ODD, EMPTY, OTHER_TAG, RUN2],
        "file dataset=%s instance=prod/phys03" % RUN2: ["/store/user/jdoe/DiMuonNano/v1/d/out_1.root"],
        "file dataset=%s instance=prod/phys03" % HAHM: ["/store/user/jdoe/DiMuonNano/v1/a/out_2.root",
                                                        "/store/user/jdoe/DiMuonNano/v1/a/out_1.root"],
        "file dataset=%s instance=prod/phys03" % QCD: ["/store/user/jdoe/DiMuonNano/v1/b/out_1.root"],
        "file dataset=%s instance=prod/phys03" % ODD: ["/store/user/jdoe/DiMuonNano/v1/c/out_1.root"],
        "parent dataset=%s instance=prod/phys03" % QCD: [QCD_PARENT],
        "summary dataset=%s instance=prod/phys03" % HAHM: ['[{"summary": [{"nevents": 1200, "nfiles": 2}]}]'],
        "summary dataset=%s instance=prod/phys03" % QCD: ['[{"summary": [{"num_event": 300}]}]'],
    }

    def query(text, as_json=False):
        calls.append((text, as_json))
        return answers.get(text, [])
    return query


def test_build_fileset(tmp_path):
    calls = []
    fileset, problems = fs.build_fileset(TAG, "jdoe", fs.load_catalog(), fake_das(calls), log=lambda msg: None)
    assert sorted(fileset) == [
        "hahm_zd/2022postEE/HTo2ZdTo2mu2x_MZd-20_ctau-100mm_TuneCP5_13p6TeV_madgraph-pythia8",
        "qcd_muenriched/2016preVFP/QCD_Pt-50To80_MuEnrichedPt5_TuneCP5_13TeV-pythia8",   # Run 2 UL
        "qcd_muenriched/2023postBPix/QCD_PT-50to80_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8",
        "unknown/unknown/SomethingElse_13p6TeV",
    ]
    hahm = fileset["hahm_zd/2022postEE/HTo2ZdTo2mu2x_MZd-20_ctau-100mm_TuneCP5_13p6TeV_madgraph-pythia8"]
    assert hahm["files"] == {"root://xcache//store/user/jdoe/DiMuonNano/v1/a/out_1.root": "Events",
                             "root://xcache//store/user/jdoe/DiMuonNano/v1/a/out_2.root": "Events"}
    assert hahm["metadata"] == {"process": "hahm_zd", "role": "signal", "era": "2022postEE",
                                "das_dataset": HAHM, "nfiles": 2, "nevents": 1200, "tag": TAG,
                                "primary": "HTo2ZdTo2mu2x_MZd-20_ctau-100mm_TuneCP5_13p6TeV_madgraph-pythia8"}
    qcd = fileset["qcd_muenriched/2023postBPix/QCD_PT-50to80_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8"]
    assert qcd["metadata"]["role"] == "background" and qcd["metadata"]["nevents"] == 300
    assert ("summary dataset=%s instance=prod/phys03" % HAHM, True) in calls

    text = "\n".join(problems)
    assert "%s: different tag; skipped" % OTHER_TAG in text
    assert "%s: no files; skipped" % EMPTY in text
    assert "%s: primary matches no catalog process" % ODD in text
    assert "%s: era not found" % ODD in text
    assert "could not parse event count" in text      # ODD has no summary

    out = tmp_path / "v1.json"
    fs.write_fileset(fileset, str(out))
    assert json.loads(out.read_text()) == fileset


def test_no_datasets():
    fileset, problems = fs.build_fileset(TAG, "nobody", fs.load_catalog(), lambda q, as_json=False: [])
    assert fileset == {} and "no datasets match" in problems[-1]


def test_helpers():
    eras = {"2022preEE", "2024"}
    assert fs.era_from_name("jdoe-DiMuonNano_v1_2024-abc123", "v1", eras) == "2024"
    assert fs.era_from_name("jdoe-DiMuonNano_my_tag_2022preEE-abc123", "my_tag", eras) == "2022preEE"
    assert fs.era_from_name("jdoe-DiMuonNano_v12_2024-abc123", "v1", eras) is None
    assert fs.has_tag("jdoe-DiMuonNano_v1_2024-abc123", "v1")
    assert fs.has_tag("jdoe-DiMuonNano_v1-abc123", "v1")
    assert not fs.has_tag("jdoe-DiMuonNano_v12_2024-abc123", "v1")
    assert not fs.has_tag("jdoe-DiMuonNano_my_tag_2024-abc123", "my")
    assert fs.file_url("/store/a.root") == "root://xcache//store/a.root"
    assert fs.file_url("/store/a.root", "root://cmsxrootd.fnal.gov") == "root://cmsxrootd.fnal.gov//store/a.root"
    cat = fs.load_catalog()
    assert fs.match_processes("QCD_PT-15to20_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8", cat) == ["qcd_muenriched"]
    assert fs.match_processes("NotInCatalog", cat) == []
    assert fs.era_from_parent("Run3Summer22EEMiniAODv4-130X_mcRun3_2022_realistic_postEE_v6-v2", cat) == "2022postEE"
    assert fs.era_from_parent("RunIISummer20UL17MiniAODv2-106X_mc2017_realistic_v9-v2", cat) == "2017"
    assert fs.match_processes("QCD_Pt-15To20_MuEnrichedPt5_TuneCP5_13TeV-pythia8", cat) == ["qcd_muenriched"]
    assert fs.match_processes("QCD_Pt-15To20_MuEnrichedPt5_TuneCP5_13TeV-pythia8", cat, run="run3") == []
    assert {"2016preVFP", "2018", "2022preEE", "2024"} <= fs.all_eras(cat)
