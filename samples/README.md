# MC samples for the displaced di-muon tagger

The tagger's job: given a reconstructed μ⁺μ⁻ pair with a common secondary
vertex, decide whether it came from a long-lived particle (signal) or from
something else (background). Training labels come from truth matching. A
candidate is signal when both muons match the same LLP. Background candidates
come from the background samples below, plus unmatched pairs in signal MC.

The samples are listed by physics process in [`catalog.yaml`](catalog.yaml),
for Run 2 UL (2016–2018, 13 TeV) and Run 3 (2022–2024, 13.6 TeV), in
MINIAODSIM and NANOAODSIM. Exact DAS dataset names are resolved on lxplus/LPC by
[`scripts/resolve_samples.py`](../scripts/resolve_samples.py).

## Processes

| Role | Process (catalog key) | Why it matters for the tagger | Names |
|---|---|---|---|
| signal | HAHM H→Z_D Z_D, Z_D→μμ (`hahm_zd`) | Main benchmark of CMS EXO-21-006 (Run 2) and EXO-23-014 (Run 3). Run 3: m(Z_D) from 0.5 GeV, cτ in the name; 237 samples | Run 3 confirmed |
| signal | H→XX, X→μμ (`h_to_llp_scalars`) | EXO-21-006 simplified model: heavier parents, m(X) above 10 GeV. 4μ and 2μ2jets variants; 140 samples | Run 3 confirmed |
| signal | RPV χ̃⁰→ℓℓν (`rpv_neutralino_mumunu`) | EXO-23-014 second benchmark: boosted, asymmetric vertices with missing momentum; 131 samples | Run 3 confirmed |
| signal | tt̄ + ALP, a→μμ (`ttbar_alp_mumu`) | Low-mass (0.35–60 GeV) displaced dimuons in Run 3; 118 samples | Run 3 confirmed |
| signal | B→Kφ, φ→μμ (`b_to_k_phi_mumu`) | Low-mass coverage from the scouting searches. No Run 3 MiniAOD sample exists | Run 2 only, unverified |
| background | QCD μ-enriched (`qcd_muenriched`) | Dominant: b/c semileptonic decays, b→c→μ sequential pairs with real displaced vertices, π/K decay in flight | standard |
| background | b-hadron → J/ψ(μμ) (`nonprompt_jpsi`) | Genuinely displaced (~mm) μμ vertices; irreducible at small displacement | unverified |
| background | Drell–Yan (`drell_yan`) | Prompt μμ incl. low mass and Z→ττ; keeps the tagger off prompt vertices | standard |
| background | tt̄ (`ttbar`) | Heavy-flavour vertices at higher pT, mixed prompt/non-prompt pairs | standard |
| background | prompt J/ψ, Υ (`prompt_quarkonia`) | Narrow prompt resonances; stops the tagger learning the mass | unverified |
| background | tW, W+jets, diboson | Smaller contributions; same mechanisms | standard |
| control | Uncorrelated displaced muons (`displaced_susy_uncorrelated`) | Two displaced muons that do *not* share a vertex: hard negatives. Run 3: smuon → μ gravitino (130 samples); Run 2: RPV stop (unverified) | Run 3 confirmed |

The *core* processes are the HAHM and H→XX signals plus QCD μ-enriched,
non-prompt J/ψ, Drell–Yan and tt̄. The rest are *secondary* or *optional*.

The Run 3 signal and control patterns come from a `--discover` run on
2026-11-16. Run 3 signal names changed in 2024, when parameters moved behind
`_Par-`, so most signals have two patterns. All of them require `13p6TeV`,
which keeps out 14 TeV samples. HAHM samples whose dark Higgs decays to b quarks
or photons share the `HAHM` prefix and are excluded.

**Not covered by MC:** cosmic-ray muons. The published analyses estimate them
from cosmic and NoBPTX data, and a tagger needs that too, especially at large
displacement.

## Resolving names on lxplus / LPC

```bash
source /cvmfs/cms.cern.ch/cmsset_default.sh
voms-proxy-init -voms cms -rfc -valid 192:00
```

1. **Find the signal samples.** The Run 3 signal patterns are confirmed; the
   quarkonia and non-prompt J/ψ patterns, and everything for Run 2, are still
   best guesses. Discovery mode lists every primary dataset that matches broad LLP
   patterns (`*Zd*`, `*LongLived*`, `*ctau*`, …) and shows which catalog
   process, if any, already claims each one:

   ```bash
   python3 scripts/resolve_samples.py --discover
   ```

   Edit the `patterns` in `catalog.yaml` to match what you find, then change
   `status` to `standard`.

2. **Resolve the catalog.**

   ```bash
   python3 scripts/resolve_samples.py
   ```

   It writes to `samples/resolved/`:
   - `datasets.csv` / `datasets.json`: every VALID dataset found, with era,
     tier, events, files and size.
   - `coverage.txt`: process × era table. Gaps show where a sample is
     missing, for example signal that only exists for 2022.
   - `lists/<tier>/<era>/<process>.txt`: preferred dataset names, ready for
     job submission.

   *Preferred* means the newest MiniAOD/NanoAOD format version in that era
   (e.g. NanoAODv9 over v2, MiniAODv4 over v3). Extension samples (`_ext1`)
   stay preferred because they add statistics. The script also prints patterns
   that matched nothing, datasets claimed by two processes, and datasets that
   fit no era.

Useful options: `--process`, `--role`, `--run run3`, `--tier AODSIM`,
`--no-summary` (skip event counts, much faster), `--dry-run` (print the DAS
queries), `--status PRODUCTION` (samples still being produced).

**Reruns.** `datasets.csv`, `datasets.json` and `coverage.txt` always describe
only the latest run, so a filtered run (`--process`, `--run`, …) leaves them
with just that subset. List files are updated per scope:
- **Queried this run:** each list for a queried process, run and tier is
  rewritten, and a list whose era no longer has any datasets is deleted. The
  script prints the deleted files.
- **Not queried:** lists for other processes, runs or tiers are left as they
  were.
- **DAS query failed:** that process, run and tier keeps its previous lists,
  and the script says so.

`lists/` therefore never holds a stale list for anything you just resolved.
That matters because `nanoaod/make_cfg.py --lists` turns every list file into
CRAB tasks.

## Things to check before training

- **Is the information you need in the tier?** Standard NanoAOD has per-muon
  impact parameters but no dimuon vertex fit. Muons from decays far outside the
  tracker are reconstructed as displaced standalone (DSA) muons. Run 3
  MiniAOD adds a displaced-muon collection (`slimmedDisplacedMuons`); Run 2 UL
  MiniAOD has no equivalent. Check this for your CMSSW release. A Run 2 tagger
  that covers large displacement may need AODSIM (`--tier AODSIM`) or a custom
  ntuple.
- **Cross sections** are not in DAS. Get them from XSDB or GenXSecAnalyzer when
  the backgrounds need to be mixed with physical weights.
- **Signal coverage across eras** is uneven. EXO-23-014 signal was produced
  for 2022, and other years may have none. `coverage.txt` shows this directly.
