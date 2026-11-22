"""
KaggleFleet - run local Jupyter notebooks on Kaggle GPUs across a team of
Kaggle accounts, from a CLI, a local web panel, or an AI agent.

The runtime uses only the Python standard library; it drives the official
`kaggle` CLI in a per-account sandbox.
"""

__version__ = "0.1.2"

from . import account_manager, config, kaggle_runner, models, notebook_manager, storage

__all__ = [
    "__version__",
    "config",
    "models",
    "storage",
    "account_manager",
    "notebook_manager",
    "kaggle_runner",
]
