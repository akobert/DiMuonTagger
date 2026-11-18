# analysis/: coffea framework for the displaced di-muon tagger

Reads the custom NanoAOD from [`nanoaod/`](../nanoaod/README.md) (standard Run 3
NanoAOD plus `GenPart_vx/vy/vz`, `DisplacedMuon` and `DiMuonVtx`). For every muon
pair it writes:

- **histograms** per dataset and truth category (`hists.coffea`)
- a **cutflow** and the `genWeight` sum per dataset
- **parquet skims**, one row per candidate, as the first training input

It is built for [coffea-casa](https://coffea.casa) (Dask at
`tls://localhost:8786`, files read via `root://xcache//store/...`), and also runs
locally.

```
analysis/
  config.yaml              LLP pdgIds (unverified), truth options, skim destination
  dimuontagger/
    schema.py              DiMuonNanoSchema (NanoAODSchema + DisplacedMuon, DiMuonVtx)
    truth.py               vectorised gen-ancestry walk, truth labels
    processor.py           DiMuonProcessor: histograms, cutflow, skims
    fileset.py             DAS -> coffea fileset (lxplus; needs no coffea)
    runner.py              executors, run/save helpers (used by run.py and the notebook)
    plotting.py            shape plots, cutflow and ancestor tables
  scripts/
    build_fileset.py       lxplus: write filesets/<tag>.json
    run.py                 run the processor, save outputs/<tag>/hists.coffea
    plot.py                plots and tables from hists.coffea
  notebooks/quickstart.ipynb   the same run on coffea-casa
  filesets/<tag>.json      committed filesets (made by build_fileset.py)
  outputs/                 run outputs (git-ignored)
  tests/                   pytest on synthetic files
```

## Install

Locally (Python >= 3.10):

```bash
python3 -m pip install -r analysis/requirements.txt
```

**On coffea-casa, don't install coffea.** The Dask workers run the image's
coffea, and a different version in the notebook pod can break pickling between
the pod and the workers. The notebook prints the coffea version on the workers.
Everything else used here (awkward, uproot, hist, mplhep, pyarrow, fsspec,
PyYAML, pandas) is a coffea dependency. The `dimuontagger` package is zipped and uploaded to the
workers on each run (`runner.ship_package_to_workers`), so they don't need the
repo.

## 1. Build the fileset (lxplus)

After the CRAB tasks from `nanoaod/crab/submit.py --tag <tag>` are published:

```bash
source /cvmfs/cms.cern.ch/cmsset_default.sh
voms-proxy-init -voms cms -rfc -valid 192:00
python3 analysis/scripts/build_fileset.py --tag v1        # --user if your CRAB name differs from $USER
```

This queries `dataset=/*/<user>-DiMuonNano_<tag>*/USER instance=prod/phys03`.
It drops datasets the wildcard catches from other tags (`v1*` also matches
`v12`), lists the files, and writes `analysis/filesets/<tag>.json`. Each dataset
is keyed `<process>/<era>/<primary>` and has this metadata:

| key | from |
|---|---|
| `process`, `role` | `samples/catalog.yaml` run-3 patterns matched against the primary dataset name (fnmatch) |
| `era` | `DiMuonNano_<tag>_<era>` in the dataset name, else the parent MiniAOD name and the catalog era regexes |
| `das_dataset`, `primary`, `tag`, `nfiles`, `nevents` | DAS (`dataset` is a reserved key in coffea) |

Files are `root://xcache//store/...`. Use `--redirector` for elsewhere, e.g.
`root://cmsxrootd.fnal.gov/`. Problems are printed at the end: unknown
process, unknown era, empty datasets, two CRAB tasks for the same primary and era.
Commit the JSON.

## 2. Run

```bash
# local smoke test: one chunk per dataset
python3 analysis/scripts/run.py --fileset analysis/filesets/v1.json --maxchunks 1

# coffea-casa terminal, Dask workers, skims on
python3 analysis/scripts/run.py --fileset analysis/filesets/v1.json --executor dask \
    --skim-dest root://<writable endpoint>//store/user/<you>/DiMuonSkims/v1
```

Options: `--executor iterative|futures|dask`, `--workers` (futures),
`--scheduler` (dask, default `tls://localhost:8786`), `--chunksize`,
`--maxchunks`, `--max-files`, `--process`, `--era`, `--role`, `--dataset` (glob),
`--skim-dest`, `--config`, `--skipbadfiles`, `--output`. The output is
`analysis/outputs/<tag>/hists.coffea`, where `<tag>` is the fileset's file name.

Or open [`notebooks/quickstart.ipynb`](notebooks/quickstart.ipynb) on
coffea-casa. It runs the same thing and shows the cutflow, the ancestor table
and some plots.

## 3. Plot

```bash
python3 analysis/scripts/plot.py analysis/outputs/v1/hists.coffea
```

| Output | Contents |
|---|---|
| `plots/<era>/<var>.png` | Normalised shapes, one curve per truth category, per era and for all eras together (`plots/all/`). Datasets are combined with weight 1/sumw each (`sumw` over every processed event, see below); cross sections are not applied |
| `plots/<era>/lxy_residual.png` | Reco − true Lxy for `signal` candidates: vertex fit vs two-track crossing point |
| `cutflow.md`, `cutflow.csv` | Per-dataset counts |
| `ancestors.md` | Most frequent ancestor pdgIds of gen-matched muons per dataset, with the configured LLP pdgIds marked. Warns when a signal dataset has none of them |

## Truth labels

Each leg's gen muon (`Muon_genPartIdx` / `DisplacedMuon_genPartIdx`) is walked
up `GenPart_genPartIdxMother`, all legs of a chunk at once in numpy, for at most
`truth.max_depth` steps. A candidate gets the first label that applies:

| label | condition |
|---|---|
| `signal` | both legs matched to *distinct* gen muons that descend from the same LLP |
| `llp_partial` | an LLP is involved, but not both legs from the same one (incl. one leg unmatched) |
| `heavy_flavour` | a leg has an open b/c hadron ancestor (B, D, Λb, …; not quarkonia) |
| `prompt` | both legs `isPrompt` (statusFlags bit 0), or from a prompt quarkonium (see below) |
| `unmatched` | a leg has no gen match |
| `other` | the rest: decays in flight, light resonances, τ decays, and pairs whose two legs match the same gen muon |
| `data` | no GenPart in the file |

Details:
- **LLP pdgIds** come from `truth.llp_pdgids` in [`config.yaml`](config.yaml).
  The union over all processes is used for every dataset. They are
  **unverified** (1023 Z_D, 6000113 X, 1000022 χ̃⁰, 9000005 ALP, 9000006 φ,
  1000006 t̃, 1000013/2000013 μ̃). Check
  them with `ancestors.md` on the first real files. Also confirm that
  NanoAOD's GenPart pruning keeps the LLP. If it is dropped, the muons' mother
  chain skips it and nothing is labelled `signal`.
- **The LLP index** is moved up to the top of its run of same-pdgId copies, so
  two muons attached to different copies of one LLP still count as the same LLP.
- **Prompt quarkonia:** CMSSW's `isPrompt` is false for muons from any hadron
  decay, prompt J/ψ and Υ included. With `truth.prompt_quarkonia: true` (the
  default), a leg with a quarkonium ancestor, no open b/c hadron and no LLP also
  counts as prompt. Without that, the `prompt_quarkonia` sample would land in
  `other`.
- **True Lxy** of a leg is the transverse distance from the LLP's own
  production vertex (`GenPart_vx/vy`) to the leg's gen production vertex, which
  is the LLP decay point for direct daughters. A candidate's `gen_lxy` is the
  mean over the legs that have an LLP. Reco Lxy (`lxyBS`) is measured from the
  beam spot, so the residual includes the PV's offset from the beam spot
  (~10 µm).

## Histograms

Per dataset, in `record["output"][dataset]["hists"]`. Most have a `category`
axis and are weighted by `genWeight`:

- candidate: `mass`, `pt`, `lxy` (fit, valid only), `lxy_cp` (crossing
  point), `lxy_sig`, `norm_chi2`, `prob`, `cos_alpha_xy`, `dca`, `delta_r`,
  `fit_valid`, `pair_type` (0 Muon–Muon, 1 Muon–DSA, 2 DSA–DSA)
- per leg (`leg` axis 1/2): `leg_pt`, `leg_eta`, `leg_dxy_sig`
- `lxy_residual` (method × true Lxy × residual), signal only
- `ancestor_pdgid`, `mother_pdgid`: unweighted, one entry per gen-matched reco
  muon and per distinct ancestor; FSR copies are collapsed

Also in `record["output"][dataset]`: `cutflow`, `sumw`, `sumw_events`,
`nevents` and `skim_files`.
- `sumw` is the sum of genWeight over every event the NANO job processed,
  read from the `Runs` tree (`genEventSumw`) once per file. Use it for
  normalisation: background NanoAOD keeps only events with a `DiMuonVtx`
  candidate. The cutflow's `events_generated` is `genEventCount`.
- `sumw_events` is the sum over the events in the files.
- Files without a `Runs` `genEventSumw` (data, synthetic files) use the events
  in the file for both. With `--maxchunks`, a file whose first chunk ran is
  counted in full, so `sumw` is only approximate there. The saved record also holds the fileset
metadata, config, metrics and versions.

## Skims

One parquet file per chunk, at
`<skim-dest>/<process>/<era>/<primary>/<fileuuid>_<start>-<stop>.parquet`.
The path is deterministic, so a retried chunk overwrites its own file. The
destination is any fsspec URL (local path, `root://` with fsspec-xrootd,
`s3://`, …); `skim.storage_options` in the config is passed to fsspec. One row
per candidate:

- event: `run`, `luminosityBlock`, `event`, `nMuon`, `nDisplacedMuon`,
  `nDiMuonVtx`, `cand_index` (the DiMuonVtx row)
- every `DiMuonVtx` branch under its own name (`mass`, `lxyBS`, `valid`, …),
  plus `lxySig`, `normChi2`, `pairType`, `mu1DxySig`, `mu2DxySig`
- legs, `mu1_*` / `mu2_*`: `pt, eta, phi, charge, ptErr, isGlobal,
  isStandalone, isTracker, dxy, dxyErr, dz, dzErr, pfRelIso04_all, looseId,
  mediumId, tightId, nStations, nTrackerLayers, nValidMuonHits, nMuonStations,
  normChi2, genPartIdx, genPartFlav, type`. These are float32, NaN where the
  leg's collection lacks the variable (e.g. `dxy` for DisplacedMuon).
- truth: `label`, `label_code` (index in the table above), `gen_lxy`,
  `gen_same_muon`, and per leg `mu*_gen_pdg`, `_mother_pdg`, `_llp_pdg`,
  `_open_hf`, `_prompt`, `_gen_lxy`, `_gen_l3d`
- `weight` (genWeight), `dataset`, `process`, `role`, `era`, `das_dataset`

## Tests

```bash
cd analysis && python3 -m pytest
```

[`tests/synthetic.py`](tests/synthetic.py) writes two custom-NanoAOD files with
uproot. They follow the branch list and dtypes in
`nanoaod/DiMuonTagger/NanoProd/test/check_nano.py` and contain hand-built events:
H → Z_D Z_D → μμ + μ (with an FSR copy), prompt Z → μμ, b → c → μ, B → J/ψ,
prompt J/ψ, K/π decays in flight, unmatched muons, a bad leg index, and one gen
muon reconstructed twice. The tests check:

- every candidate's truth label in the skims
- histogram contents, the cutflow, `sumw` (from a `Runs` tree in the
  background file, counted once over its three chunks) and the ancestor tables
- `run.py`, `plot.py` and the notebook, end to end
- that the futures executor and Dask (a LocalCluster whose workers cannot
  import the package from disk) give the same output, with the skims written
  by the workers
- the DAS fileset builder, against a fake DAS

## Not yet checked on real files

- The LLP pdgIds and GenPart pruning (see `ancestors.md`).
- Whether coffea-casa workers can write to your chosen `--skim-dest`. Your
  home directory there is not visible to the workers, and XCache is read-only.
  Run the probe cell in the notebook (`runner.probe_destination`) first. If no
  worker-writable destination exists, write the skims with `--executor futures`
  in the notebook pod to a local path.
- The worker image's coffea version against the notebook pod's.
- Histogram ranges on real distributions (e.g. `mass` stops at 150 GeV, Lxy at 10 m).
