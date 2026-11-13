"""check_nano.py on small synthetic files (skipped without uproot/awkward)."""
import ast
import contextlib
import io
import os
import sys
import tempfile
import unittest

try:
    import awkward as ak
    import numpy as np
    import uproot
except ImportError:  # pragma: no cover
    uproot = None

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "DiMuonTagger", "NanoProd", "test"))


def _jagged(counts, value, dtype):
    return ak.unflatten(np.full(int(sum(counts)), value, dtype=dtype), counts)


def write_file(path, leg_idx=(0, 0), drop=None):
    """Two events: 2 muons + 1 displaced muon, then 1 muon + 0 displaced; one pair each.

    leg_idx = (mu1Idx of the event-0 pair, mu2Idx of the event-1 pair).
    """
    n_mu, n_dsa, n_vtx, n_gen = [2, 1], [1, 0], [1, 1], [3, 2]
    from check_nano import EXPECTED

    def fields(collection, counts):
        out = {}
        for name in EXPECTED[collection]:
            dtype = np.int16 if name.endswith(("Idx", "Type", "charge", "Hits", "Stations")) else np.float32
            out[name] = _jagged(counts, 0, dtype)
        return out

    vtx = fields("DiMuonVtx", n_vtx)
    # event 0: Muon leg_idx[0] + DisplacedMuon 0; event 1: Muon 0 + Muon leg_idx[1]
    vtx["mu1Idx"] = ak.unflatten(np.array([leg_idx[0], 0], np.int16), n_vtx)
    vtx["mu2Idx"] = ak.unflatten(np.array([0, leg_idx[1]], np.int16), n_vtx)
    vtx["mu1Type"] = ak.unflatten(np.array([0, 0], np.uint8), n_vtx)
    vtx["mu2Type"] = ak.unflatten(np.array([1, 0], np.uint8), n_vtx)
    vtx["valid"] = ak.unflatten(np.array([True, False]), n_vtx)
    vtx["lxyBS"] = ak.unflatten(np.array([2.5, -1.0], np.float32), n_vtx)
    vtx["lxyCP"] = ak.unflatten(np.array([2.4, 0.01], np.float32), n_vtx)
    branches = {
        "Muon": ak.zip({"pt": _jagged(n_mu, 10.0, np.float32), "genPartIdx": _jagged(n_mu, 0, np.int16)}),
        "DisplacedMuon": ak.zip(fields("DisplacedMuon", n_dsa)),
        "DiMuonVtx": ak.zip(vtx),
        "GenPart": ak.zip(fields("GenPart", n_gen)),
    }
    if drop:
        collection, field = drop
        branches[collection] = ak.zip({k: branches[collection][k] for k in branches[collection].fields
                                       if k != field})
    with uproot.recreate(path) as handle:
        # mktree: a TTree (nX counter + X_field branches), not the RNTuple that
        # `handle[name] = ...` writes in recent uproot.
        tree = handle.mktree("Events", {name: array.type for name, array in branches.items()})
        tree.extend(branches)


@unittest.skipIf(uproot is None, "needs uproot and awkward")
class TestCheckNano(unittest.TestCase):
    def run_check(self, **kwargs):
        import check_nano
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nano.root")
            write_file(path, **kwargs)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = check_nano.main([path])
        return code, out.getvalue()

    def test_good_file(self):
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("candidates: 2", out)
        self.assertIn("Muon-Displaced", out)
        self.assertIn("Muon-Muon", out)

    def test_index_out_of_range(self):
        code, out = self.run_check(leg_idx=(0, 1))  # event 1 has a single muon: index 1 is bad
        self.assertEqual(code, 1, out)
        self.assertIn("1 DiMuonVtx legs point outside", out)

    def test_missing_branch(self):
        code, out = self.run_check(drop=("DiMuonVtx", "lxyCP"))
        self.assertEqual(code, 1, out)
        self.assertIn("DiMuonVtx_lxyCP", out)


if __name__ == "__main__":
    unittest.main()
