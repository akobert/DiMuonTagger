# Custom NanoAOD for the displaced di-muon tagger

Standard NanoAOD has no dimuon vertex fit and no displaced standalone (DSA)
muons, and coffea cannot read MiniAOD. This directory adds a CMSSW package that
runs on Run 3 MiniAOD and writes standard NanoAOD plus three additions:

| Collection | What it adds |
|---|---|
| `GenPart` | `vx, vy, vz`: production vertex at full precision. An LLP's decay position is its daughter muons' production vertex. |
| `DisplacedMuon` | Muons from `slimmedDisplacedMuons` with pt > 3 GeV: kinematics, muon type flags, outer-track hits, stations, χ²/ndof, pT error, gen match (`genPartIdx`, `genPartFlav`), and the match to the standard muons (`muonIdx`, `replaced`, `muonDR`). |
| `DiMuonVtx` | One row per muon pair: Muon–Muon, Muon–Displaced and Displaced–Displaced. Each row has leg indices and types, a Kalman vertex fit (position and errors, χ², probability), Lxy and its error w.r.t. beam spot and PV, 3D distance to the PV, pointing angles, the two-track DCA and crossing point (`lxyCP`, filled even when the fit fails), leg momenta at the vertex, and per-leg dxy w.r.t. the beam spot. |

`mu1Idx`/`mu2Idx` index `Muon` when `mu*Type == 0` and `DisplacedMuon` when
`mu*Type == 1`. Leg 1 is the higher-pT leg. Every branch has a doc string in the
file (`Events->GetListOfBranches()` titles). The full list is `EXPECTED` in
[`check_nano.py`](DiMuonTagger/NanoProd/test/check_nano.py).

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

`check_nano.py` reports how many displaced muons had a copy and which copy was
kept. It fails if a dropped copy appears in any pair. Its "dup-like" column
(same-sign, ΔR < 0.2) should be near zero for Muon–Displaced pairs.

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

CMSSW_13_0_X is an el8 release, so on lxplus (el9) start the el8 container first:

```bash
cmssw-el8
```
```bash
bash nanoaod/setup.sh 2022_23 ~/work
```

For 2024, on el9 directly:

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

Still to check:
- the CMSSW_15_0 build and 2024 inputs;
- 124X MiniAODv3 inputs;
- how often Kalman fits fail far outside the tracker on signal (the "fit valid" column of `check_nano.py`).

## Production with CRAB

1. Resolve the samples (see [`samples/README.md`](../samples/README.md)). This gives
   `samples/resolved/lists/MINIAODSIM/<era>/<process>.txt`.

2. In each release area, generate the configs and the index:

   ```bash
   python3 nanoaod/make_cfg.py --lists samples/resolved/lists/MINIAODSIM
   ```

   Datasets that belong to the other release are skipped, so run this once in
   CMSSW_13_0_X and once in CMSSW_15_0_X.

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

## Offline tests

The tests run without CMSSW:

```bash
python3 -m unittest discover -s nanoaod/tests -v
```

They load the cff and customise files against a stub of `FWCore.ParameterSet.Config`
and check that the table variables match `check_nano.EXPECTED`. They also cover
dataset → era/GT/cmsDriver mapping and CRAB task naming. The `check_nano` test
runs only if uproot and awkward are installed.
