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


def write_file(path, leg_idx=(0, 0), dsa_muon_idx=-1, dsa_replaced=0, drop=None,
               muon_gen=(0, 1, 0), dsa_gen=0, charges=(0, 0)):
    """Two events: 2 muons + 1 displaced muon, then 1 muon + 0 displaced; one pair each.

    The event-0 pair is Muon[leg_idx[0]] + DisplacedMuon[0]; the event-1 pair
    is Muon[0] + Muon[leg_idx[1]]. dsa_muon_idx / dsa_replaced are
    DisplacedMuon_muonIdx / _replaced of the event-0 displaced muon.
    muon_gen: Muon_genPartIdx of the three muons (event 0: two, event 1: one);
    dsa_gen: DisplacedMuon_genPartIdx of the displaced muon; charges:
    DiMuonVtx_charge of the two pairs (0 = opposite sign, +-2 = same sign).
    """
    n_mu, n_dsa, n_vtx, n_gen = [2, 1], [1, 0], [1, 1], [3, 2]
    from check_nano import EXPECTED

    def fields(collection, counts):
        out = {}
        for name in EXPECTED[collection]:
            dtype = np.int16 if name.endswith(("Idx", "Type", "charge", "Hits", "Stations")) else np.float32
            out[name] = _jagged(counts, 0, dtype)
        return out

    dsa = fields("DisplacedMuon", n_dsa)
    dsa["muonIdx"] = ak.unflatten(np.array([dsa_muon_idx], np.int32), n_dsa)
    dsa["replaced"] = ak.unflatten(np.array([dsa_replaced], np.int32), n_dsa)
    dsa["genPartIdx"] = ak.unflatten(np.array([dsa_gen], np.int16), n_dsa)
    vtx = fields("DiMuonVtx", n_vtx)
    vtx["charge"] = ak.unflatten(np.array(charges, np.int16), n_vtx)
    # event 0: Muon leg_idx[0] + DisplacedMuon 0; event 1: Muon 0 + Muon leg_idx[1]
    vtx["mu1Idx"] = ak.unflatten(np.array([leg_idx[0], 0], np.int16), n_vtx)
    vtx["mu2Idx"] = ak.unflatten(np.array([0, leg_idx[1]], np.int16), n_vtx)
    vtx["mu1Type"] = ak.unflatten(np.array([0, 0], np.uint8), n_vtx)
    vtx["mu2Type"] = ak.unflatten(np.array([1, 0], np.uint8), n_vtx)
    vtx["valid"] = ak.unflatten(np.array([True, False]), n_vtx)
    vtx["lxyBS"] = ak.unflatten(np.array([2.5, -1.0], np.float32), n_vtx)
    vtx["lxyCP"] = ak.unflatten(np.array([2.4, 0.01], np.float32), n_vtx)
    branches = {
        "Muon": ak.zip({"pt": _jagged(n_mu, 10.0, np.float32),
                        "genPartIdx": ak.unflatten(np.array(muon_gen, np.int16), n_mu)}),
        "DisplacedMuon": ak.zip(dsa),
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
        self.assertIn("1 indices point outside", out)
        self.assertNotIn("same-gen", out)  # gen lookup skipped when indices are bad

    @staticmethod
    def same_gen(out, pair_type):
        """(same-gen %, same-gen OS %) of a pair-type row."""
        line = next(l for l in out.splitlines() if l.startswith(pair_type + " "))
        return tuple(float(tok.rstrip("%")) for tok in line.split()[-2:])

    def test_same_gen_columns(self):
        # default: the Muon-Displaced pair (Muon[0], DisplacedMuon[0]) both match
        # gen 0, opposite sign; the Muon-Muon pair is Muon[0] twice
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("same-gen OS", out)
        self.assertEqual(self.same_gen(out, "Muon-Displaced"), (100.0, 100.0))
        self.assertEqual(self.same_gen(out, "Muon-Muon"), (100.0, 100.0))
        # same-sign copy: counted as same-gen, not as same-gen OS
        _, out = self.run_check(charges=(2, 0))
        self.assertEqual(self.same_gen(out, "Muon-Displaced"), (100.0, 0.0))
        # different gen muons, or an unmatched leg: not same-gen
        _, out = self.run_check(dsa_gen=1)
        self.assertEqual(self.same_gen(out, "Muon-Displaced"), (0.0, 0.0))
        _, out = self.run_check(dsa_gen=-1, muon_gen=(-1, 1, 0))
        self.assertEqual(self.same_gen(out, "Muon-Displaced"), (0.0, 0.0))  # -1 == -1 is not a match

    def test_displaced_muon_match_index(self):
        # displaced muon kept, its copy Muon[1] dropped; Muon[1] is not a leg: fine
        code, out = self.run_check(dsa_muon_idx=1, dsa_replaced=0)
        self.assertEqual(code, 0, out)
        self.assertIn("duplicating a Muon: 100.0% (replaced by the Muon: 0.0%, kept instead of the Muon: 100.0%)", out)
        code, out = self.run_check(dsa_muon_idx=2)  # out of range
        self.assertEqual(code, 1, out)
        self.assertIn("1 indices point outside", out)

    def test_replaced_displaced_muon_used_as_leg(self):
        # DisplacedMuon[0] was replaced by Muon[1] but is still a leg of the event-0 pair
        code, out = self.run_check(dsa_muon_idx=1, dsa_replaced=1)
        self.assertEqual(code, 1, out)
        self.assertIn("1 DiMuonVtx legs are the dropped copy", out)

    def test_dropped_muon_used_as_leg(self):
        # DisplacedMuon[0] was kept instead of Muon[0], but Muon[0] is a leg of the event-0 pair
        code, out = self.run_check(dsa_muon_idx=0, dsa_replaced=0)
        self.assertEqual(code, 1, out)
        self.assertIn("1 DiMuonVtx legs are the dropped copy", out)

    def test_missing_branch(self):
        code, out = self.run_check(drop=("DiMuonVtx", "lxyCP"))
        self.assertEqual(code, 1, out)
        self.assertIn("DiMuonVtx_lxyCP", out)


if __name__ == "__main__":
    unittest.main()
