"""Paths and dataset config. Reads config/dataset.yaml once."""
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "dataset.yaml"
CACHE_DIR = REPO_ROOT / "cache"
DATA_DIR = CACHE_DIR / "mdanalysis_data"


@lru_cache(maxsize=1)
def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def dataset_config(dataset: str) -> dict:
    datasets = load_config()["datasets"]
    if dataset not in datasets:
        raise ValueError(f"Unknown dataset {dataset!r}. Known: {sorted(datasets)}")
    return datasets[dataset]
