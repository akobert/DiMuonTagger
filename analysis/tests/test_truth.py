"""Unit tests of the vectorised ancestry walk and the label precedence."""

import numpy as np
import pytest

from dimuontagger import truth


def test_hadron_class():
    pdg = np.array([511, 421, 5122, 4122, 541, 443, 553, 100443, 211, 321, 2212, 13, 22, 1023, 6000113,
                    1000022, 9000006, 25, 23])
    open_hf, onium = truth.hadron_class(pdg)
    assert list(pdg[open_hf]) == [511, 421, 5122, 4122, 541]
    assert list(pdg[onium]) == [443, 553, 100443]


def test_mother_table_handles_bad_indices():
    # event 0: 3 particles, event 1: 2 particles
    mother_local = np.array([-1, 0, 7, -1, 1])   # 7 out of range; event 1 row 1 points to itself
    mother = truth.mother_table(mother_local, np.array([3, 2]))
    assert list(mother) == [-1, 0, -1, -1, -1]


def test_globalise():
    starts, counts = np.array([0, 3]), np.array([3, 2])
    out = truth.globalise(np.array([2, -1, 1, 2]), np.array([0, 0, 1, 1]), starts, counts)
    assert list(out) == [2, -1, 4, -1]


def _chain():
    """0 H, 1 Z_D, 2 Z_D copy, 3 mu, 4 mu-copy(FSR) -> 3, 5 B0, 6 D-, 7 mu<-D-."""
    pdg = np.array([25, 1023, 1023, 13, 13, 511, 411, 13])
    mother = np.array([-1, 0, 1, 2, 3, -1, 5, 6])
    return pdg, mother


def test_walk_llp_top_copy_and_mother_pdg():
    pdg, mother = _chain()
    walk = truth.walk_ancestors(np.array([3, 4, 7, -1]), mother, pdg, [1023], max_depth=10)
    assert list(walk["llp"]) == [1, 1, -1, -1]            # top of the Z_D copies
    assert list(walk["mother_pdg"]) == [1023, 1023, 411, 0]
    assert list(walk["open_hf"]) == [False, False, True, False]
    assert not walk["truncated"].any()
    # ancestors of leg 1 (the FSR copy): 1023 once, then 25
    anc = sorted(zip(walk["anc_item"], walk["anc_pdg"]))
    assert anc == [(0, 25), (0, 1023), (1, 25), (1, 1023), (2, 411), (2, 511)]


def test_walk_max_depth():
    pdg, mother = _chain()
    walk = truth.walk_ancestors(np.array([4]), mother, pdg, [1023], max_depth=2)
    assert walk["llp"][0] == 2       # reached the first Z_D only
    assert walk["truncated"][0]


def test_walk_without_genpart():
    walk = truth.walk_ancestors(np.array([-1, -1]), np.zeros(0, int), np.zeros(0, int), [1023])
    assert list(walk["llp"]) == [-1, -1]


def _leg(gen, llp=-1, hf=False, prompt=False):
    gen = np.atleast_1d(gen)
    return {"gen": gen, "matched": gen >= 0, "llp": np.atleast_1d(llp),
            "open_hf": np.atleast_1d(hf), "prompt": np.atleast_1d(prompt)}


@pytest.mark.parametrize("leg1, leg2, expected", [
    (_leg(1, llp=9), _leg(2, llp=9), "signal"),
    (_leg(1, llp=9), _leg(2, llp=8), "llp_partial"),
    (_leg(1, llp=9), _leg(-1), "llp_partial"),
    (_leg(1, llp=9, hf=True), _leg(2, hf=True), "llp_partial"),
    (_leg(1, hf=True), _leg(-1), "heavy_flavour"),
    (_leg(1, hf=True), _leg(2, prompt=True), "heavy_flavour"),
    (_leg(1, prompt=True), _leg(2, prompt=True), "prompt"),
    (_leg(1, prompt=True), _leg(-1), "unmatched"),
    (_leg(-1), _leg(-1), "unmatched"),
    (_leg(1), _leg(2, prompt=True), "other"),
    (_leg(1, llp=9), _leg(1, llp=9), "other"),        # both legs are the same gen muon
    (_leg(1, prompt=True), _leg(1, prompt=True), "other"),
])
def test_label_precedence(leg1, leg2, expected):
    codes, _ = truth.label_pairs(leg1, leg2)
    assert truth.code_names(codes)[0] == expected


def test_leg_truth_gen_lxy_and_prompt_quarkonia():
    # 0 g at PV, 1 J/psi (prompt), 2 mu <- J/psi, 3 Z_D at PV, 4 mu <- Z_D at (3, 4)
    pdg = np.array([21, 443, 13, 1023, 13])
    mother = np.array([-1, 0, 1, 0, 3])
    vx = np.array([1.0, 1.0, 1.0, 1.0, 4.0])
    vy = np.array([2.0, 2.0, 2.0, 2.0, 6.0])
    vz = np.zeros(5)
    flags = np.array([0, 0, 0, 0, 1])
    gen = np.array([2, 4])
    walk = truth.walk_ancestors(gen, mother, pdg, [1023])
    lt = truth.leg_truth(gen, walk, flags, pdg, vx, vy, vz, prompt_quarkonia=True)
    assert list(lt["prompt"]) == [True, True]
    assert np.isnan(lt["gen_lxy"][0]) and lt["gen_lxy"][1] == pytest.approx(5.0)
    assert list(lt["llp_pdg"]) == [0, 1023]
    lt = truth.leg_truth(gen, walk, flags, pdg, vx, vy, vz, prompt_quarkonia=False)
    assert list(lt["prompt"]) == [False, True]


def test_pair_mean():
    out = truth.pair_mean(np.array([1.0, np.nan, np.nan]), np.array([3.0, 2.0, np.nan]))
    assert out[0] == 2.0 and out[1] == 2.0 and np.isnan(out[2])
