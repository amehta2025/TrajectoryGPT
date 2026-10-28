"""Load preloaded datasets as MDAnalysis Universes, and resolve named groups."""
import warnings
from functools import lru_cache

import MDAnalysis as mda

from app.config import DATA_DIR, dataset_config

HEAVY = "protein and not name H*"


def _paths(loader: str) -> tuple[str, str]:
    if loader == "mdanalysistests_psf_dcd":
        from MDAnalysisTests.datafiles import DCD, PSF
        return PSF, DCD
    if loader == "mdanalysisdata_adk_equilibrium":
        from MDAnalysisData import datasets
        d = datasets.fetch_adk_equilibrium(data_home=str(DATA_DIR))
        return d.topology, d.trajectory
    raise ValueError(f"Unknown loader {loader!r}")


def dataset_files(dataset: str) -> tuple[str, str]:
    """(topology, trajectory) paths; downloads the equilibrium set on first use."""
    return _paths(dataset_config(dataset)["loader"])


@lru_cache(maxsize=4)
def load_universe(dataset: str) -> mda.Universe:
    top, traj = dataset_files(dataset)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return mda.Universe(top, traj)


def group_selection(dataset: str, group: str) -> str:
    """MDAnalysis selection string for a named domain, e.g. 'resid 30:59'."""
    domains = dataset_config(dataset)["domains"]
    if group not in domains:
        raise ValueError(f"Unknown group {group!r} for {dataset}. Known: {sorted(domains)}")
    return " or ".join(f"resid {a}:{b}" for a, b in domains[group])


def group_heavy_atoms(dataset: str, group: str):
    u = load_universe(dataset)
    return u.select_atoms(f"({HEAVY}) and ({group_selection(dataset, group)})")
