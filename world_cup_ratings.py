"""
FIFA 风格综合能力(OVR)估算：优先使用 data/team_ovr_overrides.json（可为全量 220 队），
未在 JSON 中出现的队名再按「全局串联名单」排名曲线估算。
数值范围约 46–93，映射到比赛用 power，拉强弱队差距。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict

_DATA_DIR = Path(__file__).resolve().parent / "data"
_OVERRIDE_PATH = _DATA_DIR / "team_ovr_overrides.json"
_WORLD_RANK_PATH = _DATA_DIR / "team_world_ranks.json"


def load_world_ranks() -> Dict[str, int]:
    """
    世界排名：数字越小越强。与 OVR 独立，用于洲际附加赛分档、积分榜同分规则等。
    未在 JSON 中出现的队名不会出现在返回值中（由 game 层给默认大数字）。
    """
    if not _WORLD_RANK_PATH.is_file():
        return {}
    try:
        raw = json.loads(_WORLD_RANK_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: Dict[str, int] = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def load_ovr_overrides() -> Dict[str, float]:
    if not _OVERRIDE_PATH.is_file():
        return {}
    try:
        raw = json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: Dict[str, float] = {}
    for k, v in raw.items():
        if isinstance(k, str) and k.startswith("_"):
            continue
        key = str(k).strip()
        if not key or key.startswith("_"):
            continue
        try:
            out[key] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def ovr_from_rank_curve(rank: int, total: int) -> float:
    """指数曲线：头部队 88+，尾部 48 左右。rank 1 最强。"""
    if total <= 1:
        return 75.0
    t = (rank - 1) / max(1, total - 1)
    base = 48.0 + 44.0 * math.exp(-2.8 * t)
    return max(46.0, min(93.0, base))


def ovr_for_team(name: str, rank: int, total: int, overrides: Dict[str, float]) -> float:
    if name in overrides:
        # JSON 设定值为准；允许明显弱队（如 30），不再抬到 40
        v = float(overrides[name])
        return max(12.0, min(99.0, v))
    return ovr_from_rank_curve(rank, total)


def power_from_ovr(ovr: float) -> float:
    """映射到比赛引擎：约 650–1850，弱队与强队差距明显。"""
    return 400.0 + ovr * 15.5
