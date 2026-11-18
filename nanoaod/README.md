# Custom NanoAOD for the displaced di-muon tagger

Standard NanoAOD has no dimuon vertex fit and no displaced standalone (DSA)
muons, and coffea cannot read MiniAOD. This directory adds a CMSSW package that
runs on Run 3 MiniAOD and writes standard NanoAOD plus these additions:

| Collection | What it adds |
|---|---|
| `GenPart` | `vx, vy, vz`: production vertex at full precision. An LLP's decay position is its daughter muons' production vertex. |
| `Muon` | `dupIdx`: the global `Muon` this muon is a copy of (−1 if none). Copies are not used in `DiMuonVtx`. |
| `DisplacedMuon` | Muons from `slimmedDisplacedMuons` with pt > 3 GeV: kinematics, muon type flags, outer-track hits, stations, χ²/ndof, pT error, gen match (`genPartIdx`, `genPartFlav`), and the match to the standard muons (`muonIdx`, `replaced`, `muonDR`). |
| `DiMuonVtx` | One row per muon pair: Muon–Muon, Muon–Displaced and Displaced–Displaced. Each row has leg indices and types, a Kalman vertex fit (position and errors, χ², probability), Lxy and its error w.r.t. beam spot and PV, 3D distance to the PV, pointing angles, the two-track DCA and crossing point (`lxyCP`, filled even when the fit fails), leg momenta at the vertex, and per-leg dxy w.r.t. the beam spot. |

`mu1Idx`/`mu2Idx` index `Muon` when `mu*Type == 0` and `DisplacedMuon` when
`mu*Type == 1`. Leg 1 is the higher-pT leg. Every branch has a doc string in the
file (`Events->GetListOfBranches()` titles). The full list is `EXPECTED` in
[`check_nano.py`](DiMuonTagger/NanoProd/test/check_nano.py).

**Event filter for backgrounds.** Background datasets are produced with
`customiseDiMuonNanoFiltered`, which writes only events with at least one
`DiMuonVtx` candidate. Events without one carry nothing for the tagger, and
there are about 18 billion background events in Run 3; in a 2022 B⁰→J/ψK*⁰
test only 0.12 candidates per event were found. Signal and control datasets
keep every event, so that efficiencies can be measured. `make_cfg.py` picks
the config from the catalog role (a process can override it with
`event_filter: true/false`; `--event-filter on/off` overrides all).
- The `Runs` tree still counts every processed event: `genWeightsTable` is put
  on its own path so that it runs on every event, not only on the kept ones.
  Normalise with `Runs` `genEventSumw`, not with the events in the file; the
  coffea processor does this.
- `check_nano.py` prints `events written: N of M processed` and fails if
  `genEventCount` is smaller than the number of events written.

**Duplicate muons.** Many displaced muons are a second reconstruction of a muon
that is also in `Muon`. In a 2022 ttbar test without handling this, half of
the Muon–Displaced pairs were a muon paired with its own copy. Those pairs, and
pairs of copies of two prompt muons, had median fitted Lxy of 7–10 cm, against
0.02 cm for Muon–Muon pairs.

The fix: duplicates are resolved before pairing, and only one copy of each
muon becomes a `DiMuonVtx` leg. The settings are on `diMuonVertices` in
`dimuonVertex_cff.py`.
- **What counts as a copy:** a displaced muon and a `Muon` with the same charge
  within ΔR < 0.2 (`displacedMatchSameCharge`, `displacedMatchDeltaR`; 0 turns
  matching off).
  - In signal, a muon's nearest `Muon` is often the *other* muon from the same
    decay, about 0.2–0.8 away and with the opposite charge. The charge
    requirement stops that partner from being taken for a copy.
  - Matching is one-to-one, closest pairs first.
- **Which copy is kept:** the `Muon` if it passes `preferredMuonCut` (default
  `isGlobalMuon`: it has a tracker track), otherwise the displaced muon.
  - A first version used ΔR < 0.1 with any charge and always kept the `Muon`.
    In a HAHM sample (20 GeV, cτ = 1 m), 40% of those replacements swapped a
    displaced muon (72% gen-matched) for a muon-system-only `Muon` (41%) or a
    tracker-only `Muon` (2%).
  - A muon-system-only `Muon` is refitted as if it came from the beam spot,
    which biases it for displaced decays.

Branches:
- `DisplacedMuon_muonIdx`: the `Muon` copy, −1 if none.
- `DisplacedMuon_replaced`: 1 if the `Muon` was kept, 0 if the displaced muon was.
- `DisplacedMuon_muonDR`: ΔR to the nearest `Muon` of any charge, for retuning.

**Copies within the standard collection.** This step runs before the
displaced-muon step.
- **The finding:** the standard `Muon` collection itself can hold two copies of
  one muon. In the 2022 ttbar test, 13 of 18 same-sign Muon–Muon pairs with
  ΔR < 0.2 were a global muon plus a muon that is neither global, tracker nor
  muon-system-only, most likely tagged only by the RPC or GEM detectors on the
  same tracker track. The pairs had a median ΔR of 0.04 and a median pT ratio of
  0.82, and only one muon of each pair was gen-matched.
- **The rule:** a standard muon failing `preferredMuonCut` with the same charge
  within `standardMatchDeltaR` (default 0.2; `standardMatchSameCharge`) of one
  passing it is a copy. It's dropped as a leg, and `Muon_dupIdx` points to the
  kept muon. One global muon can absorb several copies. Two global or two
  non-global muons are both kept. Muons dropped here are also skipped in the
  displaced-muon step.

`check_nano.py` reports how many displaced muons had a copy and which copy was
kept. It fails if a dropped copy appears in any pair. Its "dup-like" column
(same-sign, ΔR < 0.2) should be near zero for Muon–Displaced pairs.

In simulation the definitive check is the "same-gen" column: the fraction of
pairs whose two muons match the same generated muon, i.e. one muon
reconstructed twice, whatever the charges. "same-gen OS" is the opposite-sign
part of those, for example a copy whose charge was measured wrongly, which the
same-charge rule doesn't catch. It applies only to Muon–Displaced pairs and
shows "n/a" for the others. NanoAOD's gen matching is one-to-one within a
collection, so the second copy of a muon in the same collection is always left
unmatched, and two copies there never share a generated muon.
`check_nano.py` also reports the fraction of `Muon`s that are copies, and fails
if a dropped copy appears in any pair.

Run 3 only: Run 2 UL MiniAOD has no displaced-muon collection.

## Layout

```
nanoaod/
  DiMuonTagger/NanoProd/                 CMSSW package (symlinked into $CMSSW_BASE/src)
    plugins/DiMuonVertexProducer.cc      pairs muons, Kalman fit, two-track DCA -> pat::CompositeCandidate
    plugins/DiMuonTableProducers.cc      flat-table producers for DiMuonVtx and DisplacedMuon
    python/dimuonVertex_cff.py           modules and tasks
    python/dimuonNano_cff.py             customiseDiMuonNano (cmsDriver --customise)
    test/check_nano.py                   branch / index / summary check of an output file
  eras.yaml                              era -> release and cmsDriver --era per MiniAOD version
  make_cfg.py                            cmsDriver config generation + cfg/index.json
  crab/submit.py                         CRAB submission from cfg/index.json
  setup.sh                               creates the CMSSW area and builds
  test_sample.sh                         one-dataset test: disk file, config, cmsRun, check_nano
  tests/                                 offline tests (no CMSSW needed)
```

| Era | Release | `--era` (by input MiniAOD) |
|---|---|---|
| 2022preEE, 2022postEE | CMSSW_13_0_X | v3 (124X): `Run3,run3_nanoAOD_124`; v4 (130X): `Run3` |
| 2023preBPix, 2023postBPix | CMSSW_13_0_X | v4: `Run3_2023` |
| 2024 | CMSSW_15_0_X | v6: `Run3_2024` |

The global tag comes from the MiniAOD processed name, e.g.
`130X_mcRun3_2022_realistic_postEE_v6`. Override it with
`make_cfg.py --conditions`.

## Setup (lxplus)

Both releases are used in their production architecture, el8:
`el8_amd64_gcc11` for CMSSW_13_0_X and `el8_amd64_gcc12` for CMSSW_15_0_X.
CMSSW_15_0_X also has el9 builds, but scram flags them as non-production, and
official 2024 NanoAOD used el8. On lxplus (el9), start the el8 container
first, for either release:

```bash
cmssw-el8
```
```bash
bash nanoaod/setup.sh 2022_23 ~/work
```
```bash
bash nanoaod/setup.sh 2024 ~/work
```

Each run creates `~/work/CMSSW_<series>_N`, links the package and runs
`scram b`. Repeat `scram b` in that `src/` after editing C++.

## Test locally (do this first in each release)

1. Check that the input has displaced muons, especially for 124X MiniAODv3:

   ```bash
   edmDumpEventContent root://cms-xrd-global.cern.ch//store/mc/<file>.root | grep -i displacedmuon
   ```

2. Run the custom NanoAOD on one dataset and check the output, in one step:

   ```bash
   bash nanoaod/test_sample.sh <MiniAOD dataset>
   ```

   [`test_sample.sh`](test_sample.sh):
   - refuses a tape-only dataset and takes an input file stored at a disk site;
   - makes a test config with `make_cfg.py`;
   - runs `cmsRun` on 500 events (log in `cmsRun.log`);
   - runs `check_nano.py` on the output.

   It stops with DAS's or cmsRun's own message when a step fails. The output
   goes to `${DIMUON_TEST_DIR:-~/dimuon_tests}/<primary>__<campaign>/nano.root`
   (one folder per sample and campaign), and the script prints the `scp`
   command to copy it.

   Options: `-n EVENTS`, `--out DIR`, `--file LFN` to use a given file and skip
   DAS, and `--redirector URL`. For example, to keep tests on EOS:

   ```bash
   export DIMUON_TEST_DIR=/eos/user/<u>/<username>/cmssw/test
   ```

   Running the steps by hand works too: `make_cfg.py --dataset … --filein …
   -n 200` prints the test config path, then run `cmsRun <config>` and
   `check_nano.py nano.root` in a folder of its own. Test configs are named
   per sample and input file, so different samples never share one.

Checked by running in CMSSW_13_0_25 on 2022 ttbar MiniAODv4:
- the package builds;
- MiniAOD keeps the displaced muons' `outerTrack`, so Displaced legs are fitted;
- the customisation adds every custom branch.

Also checked:
- CMSSW_15_0 (el8) builds and runs on 2024 HAHM MiniAODv6;
- 124X MiniAODv3 inputs work: on 2022 B⁰→J/ψK*⁰ the Muon–Muon pairs have a
  median Lxy of 0.1 cm (the B flight distance; 0.02 cm in ttbar) and all fits
  are valid.

Still to check: how often Kalman fits fail far outside the tracker on signal
(the "fit valid" column of `check_nano.py`).

## Production with CRAB

1. Resolve the samples (see [`samples/README.md`](../samples/README.md)). This gives
   `samples/resolved/lists/MINIAODSIM/<era>/<process>.txt`.

2. In each release area, generate the configs and the index:

   ```bash
   python3 nanoaod/make_cfg.py --lists samples/resolved/lists/MINIAODSIM
   ```

   Datasets that belong to the other release are skipped, so run this once in
   CMSSW_13_0_X and once in CMSSW_15_0_X. Background datasets get the
   `_filtered` configs (see "Event filter for backgrounds" above).

3. Submit, after `cmsenv` and `source /cvmfs/cms.cern.ch/common/crab-setup.sh`:

   ```bash
   python3 nanoaod/crab/submit.py --tag v1 --dry-run
   ```
   ```bash
   python3 nanoaod/crab/submit.py --tag v1
   ```

Output goes to `/store/user/<user>/DiMuonNano/<tag>/` on T2_US_Nebraska. It is
published in DBS `prod/phys03` as
`/<primary>/<user>-DiMuonNano_<tag>_<era>-<hash>/USER`, which is what the
coffea fileset builder looks up. Extension samples of the same primary and era
publish into one output dataset. `--era`, `--process`, `--splitting` and
`--units-per-job` narrow or tune a submission.

**Tape-only datasets are skipped.** `submit.py` reads each dataset's disk
status from the resolver's `samples/resolved/datasets.json` (`--resolved`) and
skips datasets that exist only on tape, which jobs can't read. It lists the
skipped ones and counts them in the final line. `--include-tape` submits them
anyway, once they have been recalled to disk with a Rucio rule. Datasets
without disk status are submitted with a warning: either they're missing from
`datasets.json`, which describes only the latest resolver run, or the resolver
ran with `--no-sites`. Rerun the resolver without `--no-sites` and without
filters before a full submission.

## Offline tests

The tests run without CMSSW:

```bash
python3 -m unittest discover -s nanoaod/tests -v
```

They load the cff and customise files against a stub of `FWCore.ParameterSet.Config`
and check that the table variables match `check_nano.EXPECTED`. They also cover
dataset → era/GT/cmsDriver mapping and CRAB task naming. The `check_nano` test
runs only if uproot and awkward are installed.
