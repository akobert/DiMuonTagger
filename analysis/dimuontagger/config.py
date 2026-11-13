"""Load and validate analysis/config.yaml."""

import copy
import os

ANALYSIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(ANALYSIS_DIR)
DEFAULT_CONFIG = os.path.join(ANALYSIS_DIR, "config.yaml")

DEFAULTS = {
    "truth": {
        "llp_pdgids": {},
        "llp_pdgids_verified": False,
        "max_depth": 32,
        "prompt_quarkonia": True,
    },
    "skim": {
        "dest": None,
        "storage_options": {},
    },
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path=None, overrides=None):
    """Return the config dict: DEFAULTS, then the YAML file, then `overrides`.

    `path=None` reads analysis/config.yaml. The result is a plain dict, so a
    processor holding it pickles cleanly to Dask workers.
    """
    import yaml

    with open(path or DEFAULT_CONFIG) as handle:
        user = yaml.safe_load(handle) or {}
    cfg = _merge(_merge(DEFAULTS, user), overrides)
    validate(cfg)
    return cfg


def validate(cfg):
    truth = cfg["truth"]
    if not isinstance(truth["llp_pdgids"], dict):
        raise ValueError("truth.llp_pdgids must map process name -> list of pdgIds")
    for name, ids in truth["llp_pdgids"].items():
        if not isinstance(ids, (list, tuple)) or not all(isinstance(i, int) for i in ids):
            raise ValueError("truth.llp_pdgids.%s must be a list of integers" % name)
    if int(truth["max_depth"]) < 1:
        raise ValueError("truth.max_depth must be >= 1")
    if not isinstance(cfg["skim"]["storage_options"], dict):
        raise ValueError("skim.storage_options must be a mapping")


def llp_pdgid_list(cfg):
    """Sorted union of all configured LLP pdgIds (absolute values)."""
    ids = set()
    for values in cfg["truth"]["llp_pdgids"].values():
        ids.update(abs(int(v)) for v in values)
    return sorted(ids)
