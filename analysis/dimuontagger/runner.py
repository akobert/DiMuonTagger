"""Running DiMuonProcessor: shared by scripts/run.py and the quickstart notebook."""

import copy
import fnmatch
import json
import os

from .config import ANALYSIS_DIR

DEFAULT_SCHEDULER = "tls://localhost:8786"  # coffea-casa's Dask scheduler
TREE = "Events"


def load_fileset(fileset, processes=None, eras=None, roles=None, datasets=None, max_files=None):
    """Read a fileset (path or dict) and keep the selected datasets.

    `processes`, `eras`, `roles` match the metadata exactly; `datasets` are
    fnmatch patterns on the dataset name. `max_files` keeps the first N files
    of each dataset. Plain file lists are turned into {file: "Events"}.
    """
    if isinstance(fileset, (str, os.PathLike)):
        with open(fileset) as handle:
            fileset = json.load(handle)
    out = {}
    for name, entry in fileset.items():
        if isinstance(entry, (list, tuple)):
            entry = {"files": {f: TREE for f in entry}, "metadata": {}}
        entry = copy.deepcopy(entry)
        if isinstance(entry["files"], (list, tuple)):
            entry["files"] = {f: TREE for f in entry["files"]}
        meta = entry.setdefault("metadata", {})
        if processes and meta.get("process") not in processes:
            continue
        if eras and meta.get("era") not in eras:
            continue
        if roles and meta.get("role") not in roles:
            continue
        if datasets and not any(fnmatch.fnmatchcase(name, pat) for pat in datasets):
            continue
        if max_files:
            entry["files"] = dict(list(entry["files"].items())[:max_files])
        out[name] = entry
    return out


def ship_package_to_workers(client):
    """Zip dimuontagger and upload it to the Dask workers; returns the zip path.

    Workers on coffea-casa don't see your home directory, so they can't import
    the package from the repo. The zip goes in through an ``UploadFile`` worker
    plugin with a fixed name: workers that join later get it too, and a re-run
    after an edit replaces it. Unlike ``Client.upload_file`` this skips the
    scheduler, which with a LocalCluster lives in this process and would
    re-import the package here from a temporary copy. (cloudpickle's
    register_pickle_by_value is not enough: distributed sends some objects,
    e.g. the schema class, with plain pickle, by reference.)
    """
    import tempfile
    import zipfile

    from distributed.diagnostics.plugin import UploadFile

    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(tempfile.mkdtemp(prefix="dimuontagger-"), "dimuontagger.zip")
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(os.listdir(pkg_dir)):
            if name.endswith(".py"):
                archive.write(os.path.join(pkg_dir, name), "dimuontagger/" + name)
    client.register_plugin(UploadFile(path, load=True), name="dimuontagger-package")
    return path


def make_executor(kind, workers=4, scheduler=None, client=None):
    """IterativeExecutor, FuturesExecutor(workers) or DaskExecutor(client).

    For dask, pass a `client`, or a `scheduler` address (default
    tls://localhost:8786, coffea-casa's scheduler).
    """
    from coffea import processor

    if kind == "iterative":
        return processor.IterativeExecutor()
    if kind == "futures":
        return processor.FuturesExecutor(workers=workers)
    if kind == "dask":
        if client is None:
            from dask.distributed import Client

            client = Client(scheduler or DEFAULT_SCHEDULER)
        ship_package_to_workers(client)
        return processor.DaskExecutor(client=client)
    raise ValueError("unknown executor %r (iterative, futures, dask)" % kind)


def probe_destination(dest, storage_options=None, client=None):
    """Write, read back and delete a small parquet file under `dest`.

    With a Dask `client` this runs on a worker, which is where the skims are
    written. Returns (hostname, rows read back); raises if the destination is
    not writable from there.
    """
    opts = dict(storage_options or {})

    def probe():
        import posixpath
        import socket
        import uuid

        import awkward as ak
        import fsspec

        fs, root = fsspec.core.url_to_fs(dest, **opts)
        fs.makedirs(root, exist_ok=True)
        url = fs.unstrip_protocol(posixpath.join(root.rstrip("/"), "_probe_%s.parquet" % uuid.uuid4().hex[:8]))
        ak.to_parquet(ak.Array({"x": [1, 2, 3]}), url, storage_options=opts)
        rows = len(ak.from_parquet(url, storage_options=opts))
        fs.rm(fs._strip_protocol(url))
        return socket.gethostname(), rows

    if client is None:
        return probe()
    return client.submit(probe, pure=False).result()


def run(fileset, processor_instance, executor, chunksize=100_000, maxchunks=None,
        skipbadfiles=False, savemetrics=True):
    """Run the processor; return {"output", "metrics"}."""
    from coffea import processor

    from .schema import DiMuonNanoSchema

    runner = processor.Runner(
        executor=executor,
        schema=DiMuonNanoSchema,
        chunksize=chunksize,
        maxchunks=maxchunks,
        skipbadfiles=skipbadfiles,
        savemetrics=savemetrics,
    )
    result = runner(fileset, processor_instance=processor_instance)
    if savemetrics:
        output, metrics = result
    else:
        output, metrics = result, None
    return {"output": output, "metrics": metrics}


def result_record(run_result, fileset, config, **extra):
    """What gets saved: output plus the metadata plot.py needs."""
    import coffea

    record = {
        "output": run_result["output"],
        "metrics": run_result.get("metrics"),
        "metadata": {name: dict(entry.get("metadata", {})) for name, entry in fileset.items()},
        "config": config,
        "versions": {"coffea": getattr(coffea, "__version__", "unknown")},
    }
    record.update(extra)
    return record


def default_output_path(tag):
    return os.path.join(ANALYSIS_DIR, "outputs", tag, "hists.coffea")


def save(record, path):
    from coffea.util import save as coffea_save

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    coffea_save(record, path)
    return path
