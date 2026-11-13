"""Execute the quickstart notebook's code cells on the synthetic files (iterative executor)."""

import json
import os

from conftest import ANALYSIS

NOTEBOOK = os.path.join(ANALYSIS, "notebooks", "quickstart.ipynb")


def test_quickstart_notebook(synthetic_fileset, tmp_path, monkeypatch):
    with open(NOTEBOOK) as handle:
        nb = json.load(handle)
    monkeypatch.chdir(os.path.dirname(NOTEBOOK))
    overrides = {"FILESET": synthetic_fileset, "EXECUTOR": "iterative", "CHUNKSIZE": 2, "MAXCHUNKS": None,
                 "SKIM_DEST": str(tmp_path / "skims"), "OUTPUT": str(tmp_path / "out" / "hists.coffea")}
    ns = {}
    seen_parameters = False
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        exec(compile("".join(cell["source"]), "quickstart.ipynb", "exec"), ns)
        if "parameters" in cell["metadata"].get("tags", []):
            ns.update(overrides)
            seen_parameters = True
    assert seen_parameters
    out = ns["record"]["output"]
    assert sum(o["cutflow"]["cat_signal"] for o in out.values()) == 1
    assert (tmp_path / "out" / "hists.coffea").exists()
    assert (tmp_path / "out" / "ancestors.md").exists()
    assert (tmp_path / "out" / "plots" / "all" / "lxy.png").exists()
    assert len(ns["files"]) == 4      # skim files: 1 signal chunk + 3 background chunks with candidates
