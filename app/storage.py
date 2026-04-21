"""JSON file storage with atomic writes + ~/.loanratio_config.json management."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

CONFIG_PATH = Path.home() / ".loanratio_config.json"


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".loanratio.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"dataPath": None, "initialized": False}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return {"dataPath": None, "initialized": False}
        cfg.setdefault("dataPath", None)
        cfg["initialized"] = bool(cfg.get("dataPath")) and Path(cfg["dataPath"]).exists()
        return cfg
    except (OSError, json.JSONDecodeError):
        return {"dataPath": None, "initialized": False}


def write_config(data_path: str) -> dict[str, Any]:
    p = Path(data_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        save_state(p, empty_state(str(p)))
    cfg = {"dataPath": str(p)}
    _atomic_write(CONFIG_PATH, json.dumps(cfg, ensure_ascii=False, indent=2))
    return {"dataPath": str(p), "initialized": True}


def empty_state(data_path: str | None = None) -> dict[str, Any]:
    return {
        "config": {"dataPath": data_path},
        "payers": [],
        "loans": [],
        "downpayment": None,
        "months": [],
    }


def load_state(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        cfg = read_config()
        if not cfg.get("dataPath"):
            return empty_state(None)
        path = cfg["dataPath"]
    p = Path(path)
    if not p.exists():
        return empty_state(str(p))
    try:
        with open(p, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return empty_state(str(p))
    state.setdefault("config", {})["dataPath"] = str(p)
    state.setdefault("payers", [])
    state.setdefault("loans", [])
    state.setdefault("downpayment", None)
    state.setdefault("months", [])
    return state


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    p = Path(path)
    persisted = {
        "config": {"dataPath": str(p)},
        "payers": state.get("payers", []),
        "loans": state.get("loans", []),
        "downpayment": state.get("downpayment"),
        "months": state.get("months", []),
    }
    _atomic_write(p, json.dumps(persisted, ensure_ascii=False, indent=2))


def reset_state(path: str | Path) -> dict[str, Any]:
    state = empty_state(str(path))
    save_state(path, state)
    return state
