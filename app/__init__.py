"""LoanRatio backend package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("loanratio")
except PackageNotFoundError:  # running from source without install
    __version__ = "0.0.0+dev"

REPO_URL = "https://github.com/mycloudai/LoanRatio-Master"
