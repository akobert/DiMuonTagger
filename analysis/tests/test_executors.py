"""Futures and Dask executors give the same result as the iterative one.

The Dask test uses a LocalCluster with worker *processes* that do not have
analysis/ on sys.path, as on coffea-casa: the processor reaches them only
through the zip that runner.ship_package_to_workers uploads, and the
workers write the parquet skims themselves.
"""

import glob
import os

import pytest

from dimuontagger import runner
from dimuontagger.config import load_config
from dimuontagger.processor import DiMuonProcessor


def _cutflows(output):
    return {name: dict(out["cutflow"]) for name, out in output.items()}


def _same_hist(a, b):
    if a.axes[0].traits.growth:   # growing pdgId axis: bin order depends on merge order
        return dict(zip(a.axes[0], a.values())) == dict(zip(b.axes[0], b.values()))
    return a == b


def _hide_package():
    """On a worker: drop sys.path entries that expose analysis/; True if still importable."""
    import importlib.util
    import os
    import sys

    analysis = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path[:] = [p for p in sys.path
                   if not os.path.abspath(p or os.getcwd()).startswith(analysis)]
    for name in [m for m in sys.modules if m == "dimuontagger" or m.startswith("dimuontagger.")]:
        del sys.modules[name]
    importlib.invalidate_caches()
    return importlib.util.find_spec("dimuontagger") is not None


def _run(fileset_path, executor, skim_dest):
    fileset = runner.load_fileset(fileset_path)
    proc = DiMuonProcessor(load_config(), skim_dest=str(skim_dest))
    return runner.run(fileset, proc, executor, chunksize=2)["output"]


def test_futures_matches_iterative(synthetic_fileset, iterative_run, tmp_path):
    out = _run(synthetic_fileset, runner.make_executor("futures", workers=2), tmp_path / "skims")
    assert _cutflows(out) == _cutflows(iterative_run["record"]["output"])
    ref = iterative_run["record"]["output"]
    for name in out:
        for hname, h in out[name]["hists"].items():
            assert _same_hist(h, ref[name]["hists"][hname]), hname


def test_dask_local_cluster(synthetic_fileset, iterative_run, tmp_path):
    distributed = pytest.importorskip("distributed")
    with distributed.LocalCluster(n_workers=2, threads_per_worker=1, processes=True,
                                  dashboard_address=None) as cluster, distributed.Client(cluster) as client:
        # Spawned workers inherit sys.path; strip the analysis paths so the
        # package can only arrive pickled by value, as on coffea-casa.
        assert not any(client.run(_hide_package).values())
        out = _run(synthetic_fileset, runner.make_executor("dask", client=client), tmp_path / "skims")
    assert _cutflows(out) == _cutflows(iterative_run["record"]["output"])
    written = set().union(*(o["skim_files"] for o in out.values()))
    assert written and all(os.path.exists(p.replace("file://", "")) for p in written)
    assert len(glob.glob(str(tmp_path / "skims" / "**" / "*.parquet"), recursive=True)) == len(written)


def test_probe_destination(tmp_path):
    host, rows = runner.probe_destination(str(tmp_path / "probe"))
    assert rows == 3 and not list((tmp_path / "probe").iterdir())


def test_probe_destination_on_dask_worker(tmp_path):
    distributed = pytest.importorskip("distributed")
    with distributed.LocalCluster(n_workers=1, threads_per_worker=1, processes=True,
                                  dashboard_address=None) as cluster, distributed.Client(cluster) as client:
        assert not any(client.run(_hide_package).values())
        host, rows = runner.probe_destination(str(tmp_path / "probe"), client=client)
    assert rows == 3
