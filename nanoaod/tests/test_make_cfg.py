"""Tests for nanoaod/make_cfg.py and nanoaod/crab/submit.py (no CMSSW needed)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
NANOAOD = os.path.dirname(HERE)
sys.path.insert(0, NANOAOD)
sys.path.insert(0, os.path.join(NANOAOD, "crab"))

import make_cfg  # noqa: E402
import submit  # noqa: E402

DATASETS = {
    # dataset: (era, miniaod version, cmsDriver era, global tag, release)
    "/QCD_PT-15to20_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8/Run3Summer22MiniAODv4-130X_mcRun3_2022_realistic_v5-v2/MINIAODSIM":
        ("2022preEE", 4, "Run3", "130X_mcRun3_2022_realistic_v5", "13_0"),
    "/HTo2ZdTo2mu2x_MZd-20_TuneCP5_13p6TeV_madgraph-pythia8/Run3Summer22MiniAODv3-124X_mcRun3_2022_realistic_v12-v2/MINIAODSIM":
        ("2022preEE", 3, "Run3,run3_nanoAOD_124", "124X_mcRun3_2022_realistic_v12", "13_0"),
    "/QCD_PT-15to20_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8/Run3Summer22EEMiniAODv4-130X_mcRun3_2022_realistic_postEE_v6-v2/MINIAODSIM":
        ("2022postEE", 4, "Run3", "130X_mcRun3_2022_realistic_postEE_v6", "13_0"),
    "/TTto2L2Nu_TuneCP5_13p6TeV_powheg-pythia8/Run3Summer23MiniAODv4-130X_mcRun3_2023_realistic_v14_ext1-v2/MINIAODSIM":
        ("2023preBPix", 4, "Run3_2023", "130X_mcRun3_2023_realistic_v14", "13_0"),
    "/TTto2L2Nu_TuneCP5_13p6TeV_powheg-pythia8/Run3Summer23BPixMiniAODv4-130X_mcRun3_2023_realistic_postBPix_v2-v3/MINIAODSIM":
        ("2023postBPix", 4, "Run3_2023", "130X_mcRun3_2023_realistic_postBPix_v2", "13_0"),
    "/QCD_PT-15to20_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8/RunIII2024Summer24MiniAODv6-150X_mcRun3_2024_realistic_v2-v2/MINIAODSIM":
        ("2024", 6, "Run3_2024", "150X_mcRun3_2024_realistic_v2", "15_0"),
}


class TestDescribe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = make_cfg.load_catalog(make_cfg.DEFAULT_CATALOG)
        cls.eras = make_cfg.load_eras(make_cfg.DEFAULT_ERAS)

    def test_known_datasets(self):
        for dataset, (era, version, driver_era, gt, release) in DATASETS.items():
            info = make_cfg.describe(dataset, self.catalog, self.eras)
            self.assertEqual((info["era"], info["miniaod_version"], info["cmsdriver_era"],
                              info["global_tag"], info["release"]),
                             (era, version, driver_era, gt, release), dataset)

    def test_conditions_override(self):
        dataset = next(iter(DATASETS))
        info = make_cfg.describe(dataset, self.catalog, self.eras, conditions="MY_GT")
        self.assertEqual(info["global_tag"], "MY_GT")

    def test_rejects_unsupported_inputs(self):
        bad = [
            # Run 2 UL: no Run 3 era
            "/QCD_Pt-15To20_MuEnrichedPt5_TuneCP5_13TeV-pythia8/RunIISummer20UL18MiniAODv2-106X_upgrade2018_realistic_v16_L1v1-v2/MINIAODSIM",
            # 2023 has no MiniAODv3 entry
            "/X/Run3Summer23MiniAODv3-124X_mcRun3_2023_realistic_v1-v1/MINIAODSIM",
            # wrong tier
            "/X/Run3Summer22NanoAODv12-130X_mcRun3_2022_realistic_v5-v2/NANOAODSIM",
            "not/a/dataset",
        ]
        for dataset in bad:
            with self.assertRaises(ValueError, msg=dataset):
                make_cfg.describe(dataset, self.catalog, self.eras)

    def test_global_tag_drops_processing_prefix(self):
        cases = {
            "Run3Summer22EEMiniAODv4-FixLLSleptonSim_130X_mcRun3_2022_realistic_postEE_v6-v2":
                "130X_mcRun3_2022_realistic_postEE_v6",
            "Run3Summer23MiniAODv4-MUO_POG_130X_mcRun3_2023_realistic_v14_ext1-v2": "130X_mcRun3_2023_realistic_v14",
            "RunIII2024Summer24MiniAODv6-FS_150X_mcRun3_2024_realistic_v2-v2": "150X_mcRun3_2024_realistic_v2",
            "Run3Summer22MiniAODv4-130X_mcRun3_2022_realistic_v5-v2": "130X_mcRun3_2022_realistic_v5",
        }
        for processed, gt in cases.items():
            self.assertEqual(make_cfg.global_tag(processed), gt, processed)

    def test_global_tag_needs_version_suffix(self):
        with self.assertRaises(ValueError):
            make_cfg.global_tag("Run3Summer22MiniAODv4")

    def test_cmsdriver_command(self):
        dataset = next(iter(DATASETS))
        info = make_cfg.describe(dataset, self.catalog, self.eras)
        cmd = make_cfg.cmsdriver_command(info, "/tmp/x_cfg.py", "file:input.root", -1)
        joined = " ".join(cmd)
        for piece in ("--step NANO", "--mc", "--era Run3", "--conditions 130X_mcRun3_2022_realistic_v5",
                      "--customise " + make_cfg.CUSTOMISE, "--no_exec", "-n -1", "--eventcontent NANOAODSIM"):
            self.assertIn(piece, joined)
        self.assertEqual(make_cfg.config_name(info),
                         "nano_2022preEE_MiniAODv4_130X_mcRun3_2022_realistic_v5_cfg.py")
        test_name = make_cfg.config_name(info, ("root://x//store/a.root", 200))
        self.assertTrue(test_name.startswith("nano_2022preEE_MiniAODv4_130X_mcRun3_2022_realistic_v5_test_QCD_PT"))
        self.assertTrue(test_name.endswith("_cfg.py"))

    def test_test_configs_are_per_sample_and_input(self):
        # Same era / MiniAOD version / GT: the production config is shared,
        # but test configs for different samples or inputs must not be.
        tt = "/TTto2L2Nu_TuneCP5_13p6TeV_powheg-pythia8/Run3Summer22MiniAODv4-130X_mcRun3_2022_realistic_v5-v2/MINIAODSIM"
        zd = ("/HTo2ZdTo2mu2x_MZd-20_Epsilon-5e-08_TuneCP5_13p6TeV_madgraph-pythia8/"
              "Run3Summer22MiniAODv4-130X_mcRun3_2022_realistic_v5-v2/MINIAODSIM")
        a, b = (make_cfg.describe(d, self.catalog, self.eras) for d in (tt, zd))
        self.assertEqual(make_cfg.config_name(a), make_cfg.config_name(b))
        inputs = ("root://x//store/f.root", 200)
        names = {make_cfg.config_name(a, inputs), make_cfg.config_name(b, inputs),
                 make_cfg.config_name(b, ("root://x//store/g.root", 200)),
                 make_cfg.config_name(b, ("root://x//store/f.root", 500))}
        self.assertEqual(len(names), 4)
        self.assertEqual(make_cfg.config_name(b, inputs), make_cfg.config_name(b, inputs))  # stable
        self.assertIn("_test_HTo2ZdTo2mu2x_MZd-20_Epsilon-5e-08", make_cfg.config_name(b, inputs))

    def test_release_series(self):
        self.assertEqual(make_cfg.release_series("CMSSW_13_0_17"), "13_0")
        self.assertEqual(make_cfg.release_series("CMSSW_15_0_6_patch1"), "15_0")
        self.assertIsNone(make_cfg.release_series(None))
        self.assertIsNone(make_cfg.release_series("garbage"))


class TestMainDryRun(unittest.TestCase):
    def test_lists_dry_run_dedups_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            lists = os.path.join(tmp, "lists")
            for dataset, (era, *_rest) in DATASETS.items():
                os.makedirs(os.path.join(lists, era), exist_ok=True)
                with open(os.path.join(lists, era, "qcd_muenriched.txt"), "a") as handle:
                    handle.write(dataset + "\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = make_cfg.main(["--lists", lists, "--out-dir", os.path.join(tmp, "cfg"),
                                      "--dry-run", "--any-release"])
            self.assertEqual(code, 0)
            commands = [line for line in out.getvalue().splitlines() if line.startswith("cmsDriver.py")]
            self.assertEqual(len(commands), len(DATASETS))  # one distinct (era, version, GT) each
            self.assertIn("%d datasets -> %d configs" % (len(DATASETS), len(DATASETS)), out.getvalue())

    def test_dataset_argument_is_stripped(self):
        dataset = next(iter(DATASETS)) + "\r"  # as cut from a CRLF file
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(out):
            code = make_cfg.main(["--dataset", dataset, "--out-dir", tmp, "--dry-run", "--any-release"])
        self.assertEqual(code, 0)
        self.assertIn("1 datasets -> 1 configs", out.getvalue())

    def test_release_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CMSSW_VERSION"] = "CMSSW_15_0_2"
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    make_cfg.main(["--dataset", *DATASETS, "--out-dir", tmp, "--dry-run"])
            finally:
                del os.environ["CMSSW_VERSION"]
            self.assertIn("1 datasets -> 1 configs (1 datasets with the event filter); 5 skipped", out.getvalue())


SIGNAL_2022 = ("/HTo2ZdTo2mu2x_MZd-20_Epsilon-5e-08_TuneCP5_13p6TeV_madgraph-pythia8/"
               "Run3Summer22MiniAODv4-130X_mcRun3_2022_realistic_v5-v2/MINIAODSIM")
QCD_2022 = next(iter(DATASETS))


class TestEventFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = make_cfg.load_catalog(make_cfg.DEFAULT_CATALOG)
        cls.eras = make_cfg.load_eras(make_cfg.DEFAULT_ERAS)

    def test_catalog_process(self):
        cases = {
            "QCD_PT-15to20_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8": "qcd_muenriched",
            "QCD_Bin-PT-15to20_Fil-MuEnriched_TuneCP5_13p6TeV_pythia8": "qcd_muenriched",
            "HTo2ZdTo2mu2x_MZd-20_Epsilon-5e-08_TuneCP5_13p6TeV_madgraph-pythia8": "hahm_zd",
            "SMuonToMuGravitino_M-100_ctau-10mm_TuneCP5_13p6TeV_pythia8": "displaced_susy_uncorrelated",
            "ButoJpsiK-Jpsito2Mu_Fil-Mu_TuneCP5_13p6TeV_pythia8-evtgen": "nonprompt_jpsi",
            "SomethingElse_TuneCP5_13p6TeV_pythia8": None,
        }
        for primary, process in cases.items():
            self.assertEqual(make_cfg.catalog_process(primary, self.catalog), process, primary)

    def test_backgrounds_are_filtered_by_default(self):
        f = lambda process, mode="auto": make_cfg.event_filter_for(process, self.catalog, mode)
        self.assertTrue(f("qcd_muenriched"))
        self.assertTrue(f("drell_yan"))
        self.assertFalse(f("hahm_zd"))                       # signal: keep every event
        self.assertFalse(f("displaced_susy_uncorrelated"))   # control
        self.assertFalse(f(None))                            # unknown process: keep every event
        self.assertTrue(f("hahm_zd", "on"))
        self.assertFalse(f("qcd_muenriched", "off"))

    def test_catalog_override(self):
        catalog = {"processes": {"x": {"role": "background", "event_filter": False},
                                 "y": {"role": "signal", "event_filter": True}}}
        self.assertFalse(make_cfg.event_filter_for("x", catalog))
        self.assertTrue(make_cfg.event_filter_for("y", catalog))

    def test_filtered_config_is_separate(self):
        info = make_cfg.describe(QCD_2022, self.catalog, self.eras)
        plain = make_cfg.config_name(info)
        plain_cmd = " ".join(make_cfg.cmsdriver_command(info, "/tmp/x_cfg.py", "file:input.root", -1))
        info["event_filter"] = True
        self.assertEqual(make_cfg.config_name(info), plain.replace("_cfg.py", "_filtered_cfg.py"))
        cmd = " ".join(make_cfg.cmsdriver_command(info, "/tmp/x_cfg.py", "file:input.root", -1))
        self.assertIn("--customise " + make_cfg.CUSTOMISE_FILTERED, cmd)
        self.assertNotIn("Filtered", plain_cmd)
        self.assertTrue(make_cfg.CUSTOMISE_FILTERED.endswith(".customiseDiMuonNanoFiltered"))

    def _dry_run(self, *args):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(out):
            code = make_cfg.main(["--out-dir", tmp, "--dry-run", "--any-release"] + list(args))
        self.assertEqual(code, 0)
        return out.getvalue()

    def test_dataset_mode_uses_the_catalog_role(self):
        # test_sample.sh passes --dataset: the process comes from the catalog patterns
        out = self._dry_run("--dataset", QCD_2022, SIGNAL_2022)
        self.assertIn("customiseDiMuonNanoFiltered", out)
        self.assertIn("2 datasets -> 2 configs (1 datasets with the event filter)", out)
        out = self._dry_run("--dataset", SIGNAL_2022, "--event-filter", "on")
        self.assertIn("(1 datasets with the event filter)", out)

    def test_lists_mode_uses_the_list_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            lists = os.path.join(tmp, "lists", "2022preEE")
            os.makedirs(lists)
            for process, dataset in (("qcd_muenriched", QCD_2022), ("hahm_zd", SIGNAL_2022)):
                with open(os.path.join(lists, process + ".txt"), "w") as handle:
                    handle.write(dataset + "\n")
            out = self._dry_run("--lists", os.path.join(tmp, "lists"))
        self.assertIn("2 datasets -> 2 configs (1 datasets with the event filter)", out)


class TestSubmit(unittest.TestCase):
    def test_request_names(self):
        names = set()
        for dataset, (era, *_rest) in DATASETS.items():
            name = submit.request_name("v1", era, dataset)
            self.assertLessEqual(len(name), submit.MAX_REQUEST_NAME)
            self.assertRegex(name, r"^[A-Za-z0-9_-]+$")
            self.assertTrue(name.startswith("v1_%s_" % era))
            names.add(name)
        self.assertEqual(len(names), len(DATASETS))
        long_primary = "/" + "X" * 150 + "/Run3Summer22MiniAODv4-GT-v1/MINIAODSIM"
        self.assertLessEqual(len(submit.request_name("v1", "2022preEE", long_primary)), submit.MAX_REQUEST_NAME)

    # Two 2022preEE datasets sharing one config.
    QCD = next(iter(DATASETS))
    HAHM = [d for d in DATASETS if d.startswith("/HTo2Zd")][0]

    def dry_run(self, on_disk, *extra):
        """Run submit.py --dry-run on QCD + HAHM; on_disk: {dataset: bool or None}, or None for no
        datasets.json. Returns (exit code or SystemExit message, stdout, stderr)."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "nano_cfg.py")
            open(cfg, "w").close()
            index = {d: {"era": "2022preEE", "release": "13_0", "process": "x",
                         "cfg": os.path.relpath(cfg, submit.REPO)} for d in (self.QCD, self.HAHM)}
            index_path = os.path.join(tmp, "index.json")
            with open(index_path, "w") as handle:
                json.dump(index, handle)
            resolved = os.path.join(tmp, "datasets.json")
            if on_disk is not None:
                with open(resolved, "w") as handle:
                    json.dump({"datasets": [{"dataset": d, "on_disk": v} for d, v in on_disk.items()]}, handle)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = submit.main(["--tag", "v1", "--index", index_path, "--resolved", resolved,
                                        "--dry-run", *extra])
                except SystemExit as exc:
                    code = exc.code
            return code, out.getvalue(), err.getvalue()

    @staticmethod
    def tasks(out):
        """Settings dicts printed by --dry-run."""
        decoder, tasks, text = json.JSONDecoder(), [], out
        while text.lstrip().startswith("{"):
            obj, end = decoder.raw_decode(text.lstrip())
            tasks.append(obj)
            text = text.lstrip()[end:]
        return tasks

    def test_tape_only_skipped(self):
        code, out, err = self.dry_run({self.QCD: True, self.HAHM: False})
        self.assertEqual(code, 0)
        self.assertEqual([t["Data.inputDataset"] for t in self.tasks(out)], [self.QCD])
        self.assertIn("skipping 1 tape-only datasets", err)
        self.assertIn(self.HAHM, err)
        self.assertIn("1 tasks listed, 0 failed, 1 tape-only skipped", out)

    def test_include_tape(self):
        code, out, err = self.dry_run({self.QCD: True, self.HAHM: False}, "--include-tape")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.tasks(out)), 2)
        self.assertNotIn("skipping", err)

    def test_all_tape_only(self):
        code, out, _ = self.dry_run({self.QCD: False, self.HAHM: False})
        self.assertIn("all selected datasets are tape-only", str(code))

    def test_unknown_status_submitted_with_warning(self):
        code, out, err = self.dry_run({self.QCD: True, self.HAHM: None})  # HAHM: sites not checked
        self.assertEqual(code, 0)
        self.assertEqual(len(self.tasks(out)), 2)
        self.assertIn("no disk/tape status for 1 datasets", err)

    def test_missing_resolved_file(self):
        code, out, err = self.dry_run(None)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.tasks(out)), 2)
        self.assertIn("not found, so tape-only datasets cannot be recognised", err)

    def test_dry_run(self):
        code, out, _ = self.dry_run({self.QCD: True, self.HAHM: True})
        self.assertEqual(code, 0)
        settings = {t["Data.inputDataset"]: t for t in self.tasks(out)}[self.QCD]
        self.assertEqual(settings["Site.storageSite"], "T2_US_Nebraska")
        self.assertEqual(settings["Data.outputDatasetTag"], "DiMuonNano_v1_2022preEE")
        self.assertTrue(settings["Data.publication"])
        self.assertEqual(settings["Data.inputDataset"], self.QCD)
        self.assertTrue(settings["Data.outLFNDirBase"].endswith("/DiMuonNano/v1"))

    def test_bad_tag(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            submit.main(["--tag", "v1-bad", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
