"""Gen-level truth labels for DiMuonVtx candidates.

Everything here works on flat numpy arrays for one chunk. GenPart indices are
*global* within the chunk (local index + offset of the event's first GenPart),
with -1 meaning "none". The processor builds those arrays from NanoEvents.

Each leg's gen muon (``genPartIdx``) is walked up the ``genPartIdxMother``
chain for at most ``max_depth`` steps, all legs at once. A candidate's label is
the first that applies:

    signal         both legs matched to distinct gen muons that descend from the
                   same LLP
    llp_partial    an LLP is involved, but not both legs from the same one
    heavy_flavour  a leg has an open b/c-hadron ancestor (B, D, Lambda_b, ...)
    prompt         both legs are prompt (GenPart statusFlags bit 0, isPrompt;
                   optionally also prompt-quarkonium decays, see config)
    unmatched      a leg has no gen match
    other          everything else (decays in flight, light resonances, taus),
                   and pairs whose two legs match the same gen muon

Candidates in data (no GenPart) are labelled ``data``.
"""

import numpy as np

CATEGORIES = ("signal", "llp_partial", "heavy_flavour", "prompt", "unmatched", "other")
DATA_CATEGORY = "data"
ALL_CATEGORIES = CATEGORIES + (DATA_CATEGORY,)
CODES = {name: code for code, name in enumerate(ALL_CATEGORIES)}

STATUS_IS_PROMPT = 1 << 0


# ------------------------------------------------------------------ pdgIds --

def hadron_class(abs_pdg):
    """(open_heavy_flavour, quarkonium) boolean arrays for |pdgId| values.

    Uses the PDG numbering scheme n nr nL nq1 nq2 nq3 nJ. Only n = 0 (and 9,
    used for some excited states) are Standard Model hadrons, so SUSY and other
    BSM codes (1000022, 6000113, R-hadrons, ...) are never hadrons here.
    """
    a = np.asarray(abs_pdg, dtype=np.int64)
    n = (a // 1_000_000) % 10
    nq1 = (a // 1000) % 10
    nq2 = (a // 100) % 10
    nq3 = (a // 10) % 10
    hadron = ((n == 0) | (n == 9)) & (nq2 > 0) & (nq3 > 0)
    meson = hadron & (nq1 == 0)
    heaviest = np.where(meson, nq2, nq1)
    onium = meson & (nq2 == nq3) & ((nq2 == 4) | (nq2 == 5))
    open_hf = hadron & ((heaviest == 4) | (heaviest == 5)) & ~onium
    return open_hf, onium


# ----------------------------------------------------------------- indices --

def event_starts(counts):
    """Index of each event's first element in the flattened array."""
    counts = np.asarray(counts, dtype=np.int64)
    return np.cumsum(counts) - counts


def globalise(local, event_of_item, gen_starts, gen_counts):
    """Local GenPart indices -> global ones; -1 for negative or out-of-range."""
    local = np.asarray(local, dtype=np.int64)
    event_of_item = np.asarray(event_of_item, dtype=np.int64)
    ok = (local >= 0) & (local < gen_counts[event_of_item])
    return np.where(ok, local + gen_starts[event_of_item], -1)


def mother_table(mother_local, gen_counts):
    """Global mother index for every GenPart of the chunk (-1: none)."""
    gen_counts = np.asarray(gen_counts, dtype=np.int64)
    starts = event_starts(gen_counts)
    event = np.repeat(np.arange(len(gen_counts)), gen_counts)
    mother = globalise(mother_local, event, starts, gen_counts)
    # A particle that is its own mother would make the walk spin until max_depth.
    return np.where(mother == np.arange(len(mother)), -1, mother)


# ------------------------------------------------------------------- walk --

def walk_ancestors(start, mother, abs_pdg, llp_pdgids, max_depth=32):
    """Walk up the mother chain of every particle in `start` (global indices).

    Returns a dict of arrays aligned with `start`:

    - ``llp``: index of the nearest LLP ancestor, moved up to the top of its
      run of same-pdgId copies (so two muons attached to different copies of
      one LLP still agree), or -1
    - ``open_hf``: an open b/c hadron among the ancestors
    - ``onium``: a quarkonium among the ancestors
    - ``mother_pdg``: |pdgId| of the first ancestor whose pdgId differs from the
      particle's own (skips FSR copies), 0 if none
    - ``truncated``: the walk reached ``max_depth`` with ancestors left

    plus ``anc_item`` / ``anc_pdg`` / ``anc_depth``: one entry per ancestor,
    with consecutive copies (same pdgId as the previous step) collapsed. These
    feed the ancestor-pdgId histogram.
    """
    start = np.asarray(start, dtype=np.int64)
    mother = np.asarray(mother, dtype=np.int64)
    abs_pdg = np.asarray(abs_pdg, dtype=np.int64)
    llp_ids = np.asarray(sorted({abs(int(i)) for i in llp_pdgids}), dtype=np.int64)
    n = len(start)
    if len(mother) == 0:  # no GenPart in this chunk
        start = np.full(n, -1, dtype=np.int64)
        mother = abs_pdg = np.zeros(1, dtype=np.int64)

    llp = np.full(n, -1, dtype=np.int64)
    in_llp_run = np.zeros(n, dtype=bool)
    llp_run_pdg = np.zeros(n, dtype=np.int64)
    open_hf = np.zeros(n, dtype=bool)
    onium = np.zeros(n, dtype=bool)
    mother_pdg = np.zeros(n, dtype=np.int64)
    anc_item, anc_pdg, anc_depth = [], [], []

    cur = start.copy()
    prev_pdg = np.where(cur >= 0, abs_pdg[np.maximum(cur, 0)], 0)
    items = np.arange(n)
    for depth in range(1, max_depth + 1):
        alive = cur >= 0
        if not alive.any():
            break
        cur = np.where(alive, mother[np.maximum(cur, 0)], -1)
        alive = cur >= 0
        pdg = np.where(alive, abs_pdg[np.maximum(cur, 0)], 0)

        hf, oni = hadron_class(pdg)
        open_hf |= alive & hf
        onium |= alive & oni

        # nearest LLP, then up through its copies
        is_llp = alive & np.isin(pdg, llp_ids)
        first = is_llp & (llp < 0)
        cont = in_llp_run & alive & (pdg == llp_run_pdg)
        llp = np.where(first | cont, cur, llp)
        llp_run_pdg = np.where(first, pdg, llp_run_pdg)
        in_llp_run = first | cont

        new = alive & (pdg != prev_pdg)
        mother_pdg = np.where(new & (mother_pdg == 0), pdg, mother_pdg)
        anc_item.append(items[new])
        anc_pdg.append(pdg[new])
        anc_depth.append(np.full(int(new.sum()), depth, dtype=np.int64))
        prev_pdg = np.where(alive, pdg, prev_pdg)

    truncated = cur >= 0
    if anc_item:
        anc_item, anc_pdg, anc_depth = (np.concatenate(x) for x in (anc_item, anc_pdg, anc_depth))
    else:
        anc_item = anc_pdg = anc_depth = np.zeros(0, dtype=np.int64)
    return {
        "llp": llp,
        "open_hf": open_hf,
        "onium": onium,
        "mother_pdg": mother_pdg,
        "truncated": truncated,
        "anc_item": anc_item,
        "anc_pdg": anc_pdg,
        "anc_depth": anc_depth,
    }


# ----------------------------------------------------------------- labels --

def leg_truth(gen, walk, status_flags, abs_pdg, vx=None, vy=None, vz=None, prompt_quarkonia=True):
    """Per-leg truth quantities from the walk.

    `gen` are the legs' global gen indices (-1: unmatched). ``gen_lxy`` /
    ``gen_l3d`` are the distances of the leg's gen production vertex from its
    LLP's own production vertex (NaN without an LLP or without GenPart_vx/vy/vz).
    """
    gen = np.asarray(gen, dtype=np.int64)
    matched = gen >= 0
    safe = np.maximum(gen, 0)
    have_gen = len(abs_pdg) > 0
    flags = np.asarray(status_flags, dtype=np.int64)[safe] if have_gen else np.zeros(len(gen), np.int64)
    pdg = np.asarray(abs_pdg, dtype=np.int64)[safe] if have_gen else np.zeros(len(gen), np.int64)

    llp = walk["llp"]
    prompt = matched & ((flags & STATUS_IS_PROMPT) != 0)
    if prompt_quarkonia:
        prompt |= matched & walk["onium"] & ~walk["open_hf"] & (llp < 0)

    has_llp = llp >= 0
    gen_lxy = np.full(len(gen), np.nan)
    gen_l3d = np.full(len(gen), np.nan)
    if vx is not None and vy is not None and have_gen:
        vx, vy = np.asarray(vx, dtype=np.float64), np.asarray(vy, dtype=np.float64)
        lsafe = np.maximum(llp, 0)
        dx = vx[safe] - vx[lsafe]
        dy = vy[safe] - vy[lsafe]
        gen_lxy = np.where(has_llp, np.hypot(dx, dy), np.nan)
        if vz is not None:
            dz = np.asarray(vz, dtype=np.float64)[safe] - np.asarray(vz, dtype=np.float64)[lsafe]
            gen_l3d = np.where(has_llp, np.sqrt(dx * dx + dy * dy + dz * dz), np.nan)

    llp_pdg = np.where(has_llp, np.asarray(abs_pdg, dtype=np.int64)[np.maximum(llp, 0)] if have_gen else 0, 0)
    return {
        "gen": gen,
        "matched": matched,
        "pdg": np.where(matched, pdg, 0),
        "mother_pdg": walk["mother_pdg"],
        "llp": llp,
        "llp_pdg": llp_pdg,
        "open_hf": walk["open_hf"] & matched,
        "onium": walk["onium"] & matched,
        "prompt": prompt,
        "gen_lxy": gen_lxy,
        "gen_l3d": gen_l3d,
    }


def label_pairs(leg1, leg2):
    """Category code per candidate from two per-candidate leg-truth dicts."""
    both = leg1["matched"] & leg2["matched"]
    same_muon = both & (leg1["gen"] == leg2["gen"])
    signal = both & ~same_muon & (leg1["llp"] >= 0) & (leg1["llp"] == leg2["llp"])
    llp_partial = (leg1["llp"] >= 0) | (leg2["llp"] >= 0)
    heavy = leg1["open_hf"] | leg2["open_hf"]
    prompt = leg1["prompt"] & leg2["prompt"]
    unmatched = ~both
    code = np.select(
        [signal, llp_partial, heavy, prompt, unmatched],
        [CODES["signal"], CODES["llp_partial"], CODES["heavy_flavour"], CODES["prompt"], CODES["unmatched"]],
        default=CODES["other"],
    )
    code = np.where(same_muon, CODES["other"], code)
    return code.astype(np.int8), same_muon


def code_names(codes):
    """Category names (numpy str array) for an array of codes."""
    return np.asarray(ALL_CATEGORIES)[np.asarray(codes, dtype=np.int64)]


def pair_mean(a, b):
    """Elementwise mean of the finite values of a and b (NaN if neither is)."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    fa, fb = np.isfinite(a), np.isfinite(b)
    total = np.where(fa, a, 0.0) + np.where(fb, b, 0.0)
    count = fa.astype(np.int64) + fb
    return np.where(count > 0, total / np.maximum(count, 1), np.nan)
