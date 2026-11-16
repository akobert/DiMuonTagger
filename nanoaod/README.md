# Custom NanoAOD for the displaced di-muon tagger

Standard NanoAOD has no dimuon vertex fit and no displaced standalone (DSA)
muons, and coffea cannot read MiniAOD. This directory adds a CMSSW package that
runs on Run 3 MiniAOD and writes standard NanoAOD plus three additions:

| Collection | What it adds |
|---|---|
| `GenPart` | `vx, vy, vz`: production vertex at full precision. An LLP's decay position is its daughter muons' production vertex. |
| `DisplacedMuon` | Muons from `slimmedDisplacedMuons` with pt > 3 GeV: kinematics, muon type flags, outer-track hits, stations, χ²/ndof, pT error, gen match (`genPartIdx`, `genPartFlav`), and the match to the standard muons (`muonIdx`, `muonDR`). |
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

The fix: each displaced muon is matched to the nearest `Muon` in ΔR. If that
muon is within `displacedMatchDeltaR` (default 0.1, set on `diMuonVertices` in
`dimuonVertex_cff.py`; 0 turns it off), the displaced muon is replaced by it and
never used as a `DiMuonVtx` leg. `DisplacedMuon_muonIdx` holds the replacing
`Muon` (−1 if none), and `DisplacedMuon_muonDR` the distance to the nearest
`Muon`, so the cut can be re-tuned later. `check_nano.py` reports the
replaced fraction and a "dup-like" column per pair type (same-sign, ΔR < 0.2),
which should be near zero for Muon–Displaced pairs.

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

2. Make a 200-event test config and run it:

   ```bash
   python3 nanoaod/make_cfg.py --dataset <MiniAOD dataset> --filein root://cms-xrd-global.cern.ch//store/mc/<file>.root -n 200
   ```
   ```bash
   cmsRun nanoaod/cfg/nano_<era>_MiniAODv<N>_<GT>_test_cfg.py
   ```

3. Check the output:

   ```bash
   python3 nanoaod/DiMuonTagger/NanoProd/test/check_nano.py nano.root
   ```

Things the offline checks here could not confirm, which compiling and running will settle:
- the 13_0 `TransientVertex` interface;
- that MiniAOD's slimmer keeps the displaced muons' `outerTrack`;
- how often Kalman fits fail far outside the tracker (watch the "fit valid" column of `check_nano.py`);
- that cmsDriver applies `--customise` after the standard NanoAOD customisation.

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
