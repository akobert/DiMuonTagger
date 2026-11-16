"""Tests for scripts/resolve_samples.py against a fake dasgoclient (no DAS needed).

    python3 -m unittest discover -s scripts/tests -v
"""
import contextlib
import csv
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import resolve_samples  # noqa: E402

FAKE_DASGOCLIENT = textwrap.dedent('''\
    #!%s
    """Answers `dataset=`, `summary dataset=` and `site dataset=` queries.

    $FAKE_DAS_DATASETS is a JSON list of datasets (all on disk at
    T2_US_Nebraska) or a JSON object {dataset: [sites]}.
    """
    import json, os, re, sys
    query = sys.argv[sys.argv.index("-query") + 1]
    if os.environ.get("FAKE_DAS_FAIL") and os.environ["FAKE_DAS_FAIL"] in query:
        sys.exit("simulated DAS failure")
    with open(os.environ["FAKE_DAS_DATASETS"]) as handle:
        data = json.load(handle)
    datasets = list(data)
    sites = data if isinstance(data, dict) else {}
    if query.startswith("summary dataset="):
        print(json.dumps([{"summary": [{"num_event": 1000, "num_file": 2, "file_size": 5e9}]}]))
    elif query.startswith("site dataset="):
        print("\\n".join(sites.get(query[len("site dataset="):], ["T2_US_Nebraska"])))
    else:
        glob = query.split()[0][len("dataset="):]
        regex = re.compile("^" + "[^/]*".join(map(re.escape, glob.split("*"))) + "$")
        print("\\n".join(d for d in datasets if regex.match(d)))
    ''') % sys.executable

QCD_22 = ("/QCD_PT-15to20_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8/"
          "Run3Summer22MiniAODv4-130X_mcRun3_2022_realistic_v5-v2/MINIAODSIM")
QCD_23 = ("/QCD_PT-15to20_MuEnrichedPt5_TuneCP5_13p6TeV_pythia8/"
          "Run3Summer23MiniAODv4-130X_mcRun3_2023_realistic_v14-v2/MINIAODSIM")
TT_22 = ("/TTto2L2Nu_TuneCP5_13p6TeV_powheg-pythia8/"
         "Run3Summer22MiniAODv4-130X_mcRun3_2022_realistic_v5-v2/MINIAODSIM")


class _ResolverCase(unittest.TestCase):
    """Runs resolve_samples.main against a fake dasgoclient in a temp dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.exe = os.path.join(self.dir, "dasgoclient")
        with open(self.exe, "w") as handle:
            handle.write(FAKE_DASGOCLIENT)
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IXUSR)
        self.out = os.path.join(self.dir, "resolved")
        self.env = {"FAKE_DAS_DATASETS": os.path.join(self.dir, "das.json")}

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, datasets, *extra, fail=None):
        with open(self.env["FAKE_DAS_DATASETS"], "w") as handle:
            json.dump(datasets, handle)
        env = dict(self.env, FAKE_DAS_FAIL=fail or "")
        args = ["--dasgoclient", self.exe, "--skip-proxy-check", "--out", self.out,
                "--run", "run3", "--tier", "MINIAODSIM", "--no-summary", *extra]
        old_env = dict(os.environ)
        os.environ.update(env)
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = resolve_samples.main(args)
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        return code, out.getvalue()

    def list_file(self, era, process):
        return os.path.join(self.out, "lists", "MINIAODSIM", era, process + ".txt")

    def read_list(self, era, process):
        with open(self.list_file(era, process)) as handle:
            return handle.read().split()


class TestListFiles(_ResolverCase):
    def test_full_run_writes_lists(self):
        code, _ = self.resolve([QCD_22, QCD_23, TT_22])
        self.assertEqual(code, 0)
        self.assertEqual(self.read_list("2022preEE", "qcd_muenriched"), [QCD_22])
        self.assertEqual(self.read_list("2023preBPix", "qcd_muenriched"), [QCD_23])
        self.assertEqual(self.read_list("2022preEE", "ttbar"), [TT_22])

    def test_rerun_removes_stale_list_in_scope_only(self):
        self.resolve([QCD_22, QCD_23, TT_22])
        # QCD 2023 disappears; ttbar is not queried in the second run
        code, out = self.resolve([QCD_22], "--process", "qcd_muenriched")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.list_file("2023preBPix", "qcd_muenriched")))
        self.assertIn("Removed 1 stale list file", out)
        self.assertEqual(self.read_list("2022preEE", "qcd_muenriched"), [QCD_22])
        self.assertEqual(self.read_list("2022preEE", "ttbar"), [TT_22])  # outside the scope: kept
        self.assertIn("Filtered run", out)

    def test_failed_query_keeps_previous_lists(self):
        self.resolve([QCD_22, QCD_23, TT_22])
        code, out = self.resolve([], "--process", "qcd_muenriched", fail="QCD")
        self.assertEqual(code, 1)
        self.assertEqual(self.read_list("2022preEE", "qcd_muenriched"), [QCD_22])
        self.assertEqual(self.read_list("2023preBPix", "qcd_muenriched"), [QCD_23])
        self.assertIn("Kept the previous lists", out)

    def test_other_tier_untouched(self):
        nano = os.path.join(self.out, "lists", "NANOAODSIM", "2022preEE", "qcd_muenriched.txt")
        os.makedirs(os.path.dirname(nano))
        with open(nano, "w") as handle:
            handle.write("/X/Y/NANOAODSIM\n")
        self.resolve([QCD_22])  # MINIAODSIM only
        self.assertTrue(os.path.exists(nano))


class TestDiskSites(_ResolverCase):
    def read(self, name):
        with open(os.path.join(self.out, name)) as handle:
            return handle.read()

    def test_split_sites(self):
        split = resolve_samples.split_sites(
            ["T1_DE_KIT_Tape", "T1_US_FNAL_Disk", "T2_US_Nebraska", "T0_CH_CERN_MSS", "T1_IT_CNAF_Buffer"])
        self.assertTrue(split["on_disk"])
        self.assertEqual(split["disk_sites"], ["T1_US_FNAL_Disk", "T2_US_Nebraska"])
        self.assertEqual(split["tape_sites"], ["T1_DE_KIT_Tape", "T0_CH_CERN_MSS", "T1_IT_CNAF_Buffer"])
        self.assertFalse(resolve_samples.split_sites(["T1_DE_KIT_Tape"])["on_disk"])
        self.assertFalse(resolve_samples.split_sites([])["on_disk"])

    def test_tape_only_datasets_are_reported(self):
        code, out = self.resolve({QCD_22: ["T2_US_Nebraska", "T1_US_FNAL_Tape"],
                                  QCD_23: ["T1_DE_KIT_Tape"], TT_22: ["T2_CH_CERN"]})
        self.assertEqual(code, 0)
        rows = {r["dataset"]: r for r in csv.DictReader(io.StringIO(self.read("datasets.csv")))}
        self.assertEqual(rows[QCD_22]["on_disk"], "True")
        self.assertEqual(rows[QCD_22]["disk_sites"], "T2_US_Nebraska")
        self.assertEqual(rows[QCD_22]["tape_sites"], "T1_US_FNAL_Tape")
        self.assertEqual(rows[QCD_23]["on_disk"], "False")
        tape_only = self.read("tape_only.txt")
        self.assertIn(QCD_23, tape_only)
        self.assertNotIn(QCD_22, tape_only)
        self.assertIn("1 of 3 preferred datasets have no disk copy", out)
        coverage = self.read("coverage.txt")
        qcd = next(l for l in coverage.splitlines() if l.startswith("qcd_muenriched"))
        self.assertEqual(qcd.split()[1:3], ["1/1", "-"])   # 2022preEE on disk, 2022postEE none
        self.assertEqual(qcd.split()[3], "0/1")             # 2023preBPix tape only
        # lists (CRAB input) still contain the tape-only dataset
        self.assertEqual(self.read_list("2023preBPix", "qcd_muenriched"), [QCD_23])

    def test_no_sites_skips_check_and_drops_old_report(self):
        self.resolve({QCD_23: ["T1_DE_KIT_Tape"]})
        self.assertTrue(os.path.exists(os.path.join(self.out, "tape_only.txt")))
        code, out = self.resolve({QCD_23: ["T1_DE_KIT_Tape"]}, "--no-sites")
        self.assertEqual(code, 0)
        self.assertNotIn("disk/tape", out)
        self.assertFalse(os.path.exists(os.path.join(self.out, "tape_only.txt")))
        qcd = next(l for l in self.read("coverage.txt").splitlines() if l.startswith("qcd_muenriched"))
        self.assertEqual(qcd.split()[3], "1")  # plain count without site info


if __name__ == "__main__":
    unittest.main()
