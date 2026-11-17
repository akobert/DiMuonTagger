"""nanoaod/test_sample.sh end to end, with stand-ins for dasgoclient, cmsDriver.py and cmsRun.

The fake cmsRun writes a small synthetic custom-NanoAOD file, so check_nano.py
runs for real (skipped without uproot/awkward).
"""
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

try:
    import awkward  # noqa: F401
    import uproot  # noqa: F401
except ImportError:  # pragma: no cover
    uproot = None

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "test_sample.sh")
DATASET = ("/HTo2ZdTo2mu2x_MZd-20p0_ctau-1000mm_TuneCP5_13p6TeV_madgraph-pythia8/"
           "Run3Summer22EEMiniAODv4-130X_mcRun3_2022_realistic_postEE_v6-v2/MINIAODSIM")
LFN = "/store/mc/Run3Summer22EEMiniAODv4/HTo2Zd/MINIAODSIM/x/50000/abc.root"

STUBS = {
    # $FAKE_SITES: newline-separated sites; $FAKE_SITE_FILES=0: no per-site file list
    "dasgoclient": '''
        import os, sys
        # dasgoclient takes `-query=Q` or `-query Q`
        args = sys.argv[1:]
        q = next((a[len("-query="):] for a in args if a.startswith("-query=")), None)
        if q is None:
            q = args[args.index("-query") + 1]
        with open(os.environ["FAKE_DAS_LOG"], "a") as log:
            log.write(q + "\\n")
        if q.startswith("site dataset="):
            print(os.environ.get("FAKE_SITES", "T2_US_Nebraska"))
        elif q.startswith("file dataset=") and " site=" in q:
            if os.environ.get("FAKE_SITE_FILES", "1") == "1":
                print(os.environ["FAKE_LFN"])
        elif q.startswith("file dataset="):
            print(os.environ["FAKE_LFN"])
        ''',
    "voms-proxy-info": '''
        import sys
        sys.exit(0)
        ''',
    "cmsDriver.py": '''
        import sys
        path = sys.argv[sys.argv.index("--python_filename") + 1]
        with open(path, "w") as handle:
            handle.write("# fake config\\n# " + " ".join(sys.argv[1:]) + "\\n")
        ''',
    "cmsRun": '''
        import os, sys
        if os.environ.get("FAKE_CMSRUN_FAIL") == "1":
            print("----- Begin Fatal Exception (fake) -----")
            sys.exit(65)
        sys.path.insert(0, os.environ["FAKE_TESTS_DIR"])
        from test_check_nano import write_file
        write_file("nano.root")
        ''',
}


@unittest.skipIf(uproot is None, "needs uproot and awkward")
class TestTestSample(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.bin = os.path.join(root, "bin")
        os.makedirs(self.bin)
        for name, body in STUBS.items():
            path = os.path.join(self.bin, name)
            with open(path, "w") as handle:
                handle.write("#!%s\n%s" % (sys.executable, textwrap.dedent(body)))
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        self.cfg_dir = os.path.join(root, "cfg")
        self.test_dir = os.path.join(root, "tests")
        self.das_log = os.path.join(root, "das.log")
        self.env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"],
                        CMSSW_BASE=root, CMSSW_VERSION="CMSSW_13_0_25",
                        DIMUON_CFG_DIR=self.cfg_dir, DIMUON_TEST_DIR=self.test_dir,
                        FAKE_LFN=LFN, FAKE_DAS_LOG=self.das_log, FAKE_TESTS_DIR=HERE)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args, **env):
        proc = subprocess.run(["bash", SCRIPT, *args], env=dict(self.env, **env),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return proc.returncode, proc.stdout

    def das_queries(self):
        if not os.path.exists(self.das_log):
            return []
        with open(self.das_log) as handle:
            return handle.read().splitlines()

    def test_runs_end_to_end(self):
        code, out = self.run_script(DATASET + "\r", "-n", "300")  # stray \r is stripped
        self.assertEqual(code, 0, out)
        self.assertIn("file:    root://cms-xrd-global.cern.ch//store/mc/", out)
        self.assertIn("site:    T2_US_Nebraska", out)
        self.assertIn("candidates: 2", out)  # check_nano.py ran on the output
        out_dir = os.path.join(self.test_dir, DATASET.split("/")[1] + "__Run3Summer22EEMiniAODv4")
        self.assertTrue(os.path.exists(os.path.join(out_dir, "nano.root")))
        self.assertTrue(os.path.exists(os.path.join(out_dir, "cmsRun.log")))
        configs = os.listdir(self.cfg_dir)
        self.assertEqual(len(configs), 1)
        self.assertIn("_test_", configs[0])
        with open(os.path.join(self.cfg_dir, configs[0])) as handle:
            text = handle.read()
        self.assertIn("-n 300", text)
        self.assertIn("--era Run3 ", text)
        self.assertIn("130X_mcRun3_2022_realistic_postEE_v6", text)
        self.assertIn("file dataset=%s site=T2_US_Nebraska" % DATASET, self.das_queries())

    def test_picks_a_disk_site(self):
        code, out = self.run_script(DATASET, FAKE_SITES="T1_DE_KIT_Tape\nT1_UK_RAL_Disk")
        self.assertEqual(code, 0, out)
        self.assertIn("site:    T1_UK_RAL_Disk", out)
        self.assertNotIn("file dataset=%s site=T1_DE_KIT_Tape" % DATASET, self.das_queries())

    def test_refuses_tape_only(self):
        code, out = self.run_script(DATASET, FAKE_SITES="T1_DE_KIT_Tape")
        self.assertEqual(code, 1, out)
        self.assertIn("only on tape (T1_DE_KIT_Tape)", out)

    def test_falls_back_to_dataset_file_list(self):
        code, out = self.run_script(DATASET, FAKE_SITE_FILES="0")
        self.assertEqual(code, 0, out)
        self.assertIn("using the dataset's first file", out)

    def test_given_file_skips_das(self):
        code, out = self.run_script(DATASET, "--file", "root://eoscms.cern.ch//eos/x.root")
        self.assertEqual(code, 0, out)
        self.assertIn("file:    root://eoscms.cern.ch//eos/x.root", out)
        self.assertEqual(self.das_queries(), [])

    def test_cmsrun_failure_shows_log(self):
        code, out = self.run_script(DATASET, FAKE_CMSRUN_FAIL="1")
        self.assertEqual(code, 1, out)
        self.assertIn("Begin Fatal Exception (fake)", out)
        self.assertIn("cmsRun failed", out)

    def test_needs_cmssw_env_and_valid_name(self):
        env = {k: v for k, v in self.env.items() if k != "CMSSW_BASE"}
        proc = subprocess.run(["bash", SCRIPT, DATASET], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no CMSSW environment", proc.stdout)
        code, out = self.run_script("/X/Y/NANOAODSIM")
        self.assertEqual(code, 1)
        self.assertIn("not a MINIAODSIM dataset name", out)


if __name__ == "__main__":
    unittest.main()
