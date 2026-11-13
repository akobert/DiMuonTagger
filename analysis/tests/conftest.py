import os
import subprocess
import sys

import pytest

TESTS = os.path.dirname(os.path.abspath(__file__))
ANALYSIS = os.path.dirname(TESTS)
sys.path.insert(0, ANALYSIS)
sys.path.insert(0, TESTS)

import synthetic  # noqa: E402


def run_script(name, *args):
    """Run analysis/scripts/<name> with this interpreter; fail with its output."""
    cmd = [sys.executable, os.path.join(ANALYSIS, "scripts", name)] + [str(a) for a in args]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    assert proc.returncode == 0, "%s failed:\n%s" % (" ".join(cmd), proc.stdout)
    return proc.stdout


@pytest.fixture(scope="session")
def synthetic_fileset(tmp_path_factory):
    return synthetic.write_all(str(tmp_path_factory.mktemp("synthetic")))


@pytest.fixture(scope="session")
def iterative_run(synthetic_fileset, tmp_path_factory):
    """scripts/run.py --executor iterative on the synthetic files, 2 events per chunk."""
    out = tmp_path_factory.mktemp("run")
    hists = out / "hists.coffea"
    skims = out / "skims"
    log = run_script("run.py", "--fileset", synthetic_fileset, "--executor", "iterative", "--chunksize", 2,
                     "--skim-dest", skims, "--output", hists)
    from coffea.util import load

    return {"record": load(str(hists)), "hists_path": hists, "skims": skims, "log": log, "dir": out}
