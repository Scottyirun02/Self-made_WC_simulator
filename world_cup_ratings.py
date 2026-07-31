"""
FIFA 风格综合能力(OVR)估算：优先使用 data/team_ovr_overrides.json（可为全量 220 队），
未在 JSON 中出现的队名再按「全局串联名单」排名曲线估算。
数值范围约 46–93，映射到比赛用 power，拉强弱队差距。

世界排名：
- team_world_ranks_original.json / team_world_ranks.json：开局回溯用原始库
- team_world_ranks_cycle.json：Part A（洲际杯）结束后写入，供 Part B（世界杯周期）使用
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

_DATA_DIR = Path(__file__).resolve().parent / "data"
_OVERRIDE_PATH = _DATA_DIR / "team_ovr_overrides.json"
_WORLD_RANK_PATH = _DATA_DIR / "team_world_ranks.json"
_WORLD_RANK_ORIGINAL = _DATA_DIR / "team_world_ranks_original.json"
_WORLD_RANK_CYCLE = _DATA_DIR / "team_world_ranks_cycle.json"


def _parse_rank_dict(raw: dict) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for k, v in raw.items():
        if str(k).startswith("_"):
            continue
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def load_world_ranks(path: Optional[Path] = None) -> Dict[str, int]:
    """
    世界排名：数字越小越强。与 OVR 独立，用于洲际附加赛分档、积分榜同分规则等。
    未在 JSON 中出现的队名不会出现在返回值中（由 game 层给默认大数字）。
    """
    p = path or _WORLD_RANK_PATH
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return _parse_rank_dict(raw)


def ensure_rank_databases() -> None:
    """保证 original / cycle 存在；original 与主库同步作开局回溯源。"""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _WORLD_RANK_PATH.is_file():
        if not _WORLD_RANK_ORIGINAL.is_file():
            shutil.copy2(_WORLD_RANK_PATH, _WORLD_RANK_ORIGINAL)
        if not _WORLD_RANK_CYCLE.is_file():
            shutil.copy2(_WORLD_RANK_PATH, _WORLD_RANK_CYCLE)
    elif _WORLD_RANK_ORIGINAL.is_file() and not _WORLD_RANK_PATH.is_file():
        shutil.copy2(_WORLD_RANK_ORIGINAL, _WORLD_RANK_PATH)
        if not _WORLD_RANK_CYCLE.is_file():
            shutil.copy2(_WORLD_RANK_ORIGINAL, _WORLD_RANK_CYCLE)


def reset_cycle_ranks_from_original() -> Dict[str, int]:
    """新开局：用原始库覆盖周期库并返回排名。"""
    ensure_rank_databases()
    src = _WORLD_RANK_ORIGINAL if _WORLD_RANK_ORIGINAL.is_file() else _WORLD_RANK_PATH
    if not src.is_file():
        return {}
    shutil.copy2(src, _WORLD_RANK_CYCLE)
    if src.resolve() != _WORLD_RANK_PATH.resolve():
        # 保持主库与 original 一致，便于手工编辑主库后下次同步
        pass
    return load_world_ranks(_WORLD_RANK_CYCLE)


def load_cycle_ranks() -> Dict[str, int]:
    ensure_rank_databases()
    return load_world_ranks(_WORLD_RANK_CYCLE)


def load_original_ranks() -> Dict[str, int]:
    ensure_rank_databases()
    return load_world_ranks(_WORLD_RANK_ORIGINAL if _WORLD_RANK_ORIGINAL.is_file() else _WORLD_RANK_PATH)


def save_world_ranks(ranks: Mapping[str, int], path: Optional[Path] = None, comment: str = "") -> None:
    p = path or _WORLD_RANK_CYCLE
    ordered = sorted(ranks.items(), key=lambda kv: (kv[1], kv[0]))
    payload = {
        "_comment": comment
        or "周期世界排名（数字越小越强）。由洲际杯赛果更新；开局可从 original 回溯。",
    }
    for name, rk in ordered:
        payload[name] = int(rk)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rank_to_elo(rank: int, total: int) -> float:
    """排名 → FIFA 风格积分初值：第 1 约 1800，末位约 900（贴近常见 FIFA 积分量级）。"""
    if total <= 1:
        return 1400.0
    t = (rank - 1) / max(1, total - 1)
    return 1800.0 - 900.0 * t


# 大洲强度（官方 SUM 已取消；此处仅作极轻度修正，避免完全抹平强弱洲差异）
CONFED_STRENGTH: Dict[str, float] = {
    "UEFA": 1.00,
    "CONMEBOL": 1.00,
    "CONCACAF": 0.97,
    "CAF": 0.96,
    "AFC": 0.95,
    "OFC": 0.94,
}

# 比赛重要性 I — 对齐 FIFA World Ranking（2018–）量级
IMPORTANCE_FRIENDLY = 10.0
IMPORTANCE_PRELIM = 15.0
IMPORTANCE_QUALIFIER = 15.0
IMPORTANCE_PLAYOFF = 25.0
IMPORTANCE_FINALS_GS = 25.0
IMPORTANCE_R16 = 35.0
IMPORTANCE_QF = 35.0
IMPORTANCE_SF = 50.0
IMPORTANCE_FINAL = 50.0
IMPORTANCE_GLOBAL_GS = 35.0
IMPORTANCE_GLOBAL_PO = 35.0

# 淘汰赛不再另加固定奖励：重要性 I 已覆盖（与 FIFA SUM 一致）
KO_WIN_BONUS_BASE = 0.0

# 兼容旧调用；FIFA SUM 公式本身不含独立 K
BASE_K = 1.0
# FIFA 讨论中常见的主场加成（加在积分差 dr 上）
HOME_ADV_POINTS = 100.0
# 期望值分档：FIFA 用 600（比经典 Elo 400 更钝）
FIFA_SCALE = 600.0


def elo_expected(ra: float, rb: float, *, scale: float = FIFA_SCALE) -> float:
    """We = 1 / (10^(-dr/scale) + 1)，dr = ra - rb。"""
    dr = ra - rb
    return 1.0 / (10 ** (-dr / scale) + 1.0)


def confed_strength(confed: str) -> float:
    return CONFED_STRENGTH.get(confed, 0.95)


def effective_rating(rating: float, confed: str) -> float:
    """轻度大洲折算：eff = 1500 + (R-1500)*w。"""
    w = confed_strength(confed)
    return 1500.0 + (rating - 1500.0) * w


def match_importance(comp: str, stage: str, kind: str = "league") -> float:
    """根据赛事代码与轮次返回 FIFA 风格重要性 I。"""
    stage = stage or ""
    comp = comp or ""
    kind = kind or "league"

    def _world_ko_importance(st: str) -> Optional[float]:
        # 必须先匹配 1/8、1/4、半决赛，再匹配「决赛」——否则「1/8决赛」会被误判成决赛
        if "半决赛" in st:
            return IMPORTANCE_SF
        if "1/4" in st or "八强" in st:
            return IMPORTANCE_QF
        if "1/8" in st or "十六" in st:
            return IMPORTANCE_R16
        if st == "决赛" or (st.endswith("决赛") and "半" not in st and "1/" not in st and "附加" not in st):
            return IMPORTANCE_FINAL
        return None

    if comp.startswith("WORLD-") or comp.startswith("WCC"):
        if "24强" in stage or comp.endswith("-PO"):
            return IMPORTANCE_GLOBAL_PO
        if "-GS-" in comp or "小组" in stage:
            return IMPORTANCE_GLOBAL_GS
        ko_i = _world_ko_importance(stage)
        if ko_i is not None:
            return ko_i
        if kind == "knockout":
            return IMPORTANCE_R16
        return IMPORTANCE_GLOBAL_GS

    if any(comp.startswith(p) for p in ("EURO", "AFCON", "APAC", "AMERICA")):
        if comp.endswith("-KO") or "-KO" in comp:
            if stage == "决赛":
                return IMPORTANCE_FINAL
            if stage == "半决赛":
                return IMPORTANCE_SF
            if "1/4" in stage:
                return IMPORTANCE_QF
            if "1/8" in stage:
                return IMPORTANCE_R16
            return IMPORTANCE_R16
        if "-PO" in comp or "附加赛" in stage:
            return IMPORTANCE_PLAYOFF
        if "-GS-" in comp or "正赛小组" in stage:
            return IMPORTANCE_FINALS_GS
        if "-QUAL-" in comp or "预选" in stage:
            return IMPORTANCE_QUALIFIER
        return IMPORTANCE_QUALIFIER

    if comp.endswith("-PRE") or stage == "Preliminary":
        return IMPORTANCE_PRELIM
    if comp.endswith("-QUAL") or "联赛第" in stage:
        return IMPORTANCE_QUALIFIER
    if comp in ("WC-PO", "WL-PO", "WA-PO") or "单场附加赛" in stage:
        return IMPORTANCE_PLAYOFF

    if kind == "knockout":
        ko_i = _world_ko_importance(stage)
        return ko_i if ko_i is not None else IMPORTANCE_R16
    if kind == "two_leg":
        return IMPORTANCE_PLAYOFF
    return IMPORTANCE_FRIENDLY


def is_knockout_decisive(comp: str, stage: str, kind: str, round_num: int = 1) -> bool:
    """标记淘汰赛决胜场（仅用于明细展示；不再单独加分）。"""
    if kind == "knockout":
        return True
    if kind == "two_leg" and round_num >= 2:
        return True
    if "-KO" in (comp or "") or "决赛" in (stage or "") or "1/" in (stage or ""):
        return True
    if "24强附加赛" in (stage or ""):
        return True
    return False


def apply_elo(
    ratings: Dict[str, float],
    home: str,
    away: str,
    hg: int,
    ag: int,
    *,
    k: float = BASE_K,
    home_adv: float = HOME_ADV_POINTS,
    home_confed: str = "",
    away_confed: str = "",
    importance: float = 10.0,
    knockout: bool = False,
    winner_name: Optional[str] = None,
) -> Dict[str, float]:
    """
    FIFA World Ranking（SUM）风格结算：ΔP = I × w_对手 × (W − We)
    - We 使用 600 分档
    - 主场在积分差上 +100
    - 大洲强度仅轻度修正对手有效积分与得分权重
    - 无净胜球放大、无淘汰赛定额奖励（由 I 体现）
    - 淘汰赛（knockout=True）：败方不扣分（Δ 截断为 ≥0），胜方照常加分
    """
    del k  # SUM 不含独立 K；保留参数兼容旧调用
    rh = ratings[home]
    ra = ratings[away]
    rh_eff = effective_rating(rh, home_confed) + home_adv
    ra_eff = effective_rating(ra, away_confed)
    ea = elo_expected(rh_eff, ra_eff, scale=FIFA_SCALE)
    eb = 1.0 - ea

    if hg > ag:
        sa, sb = 1.0, 0.0
    elif hg < ag:
        sa, sb = 0.0, 1.0
    else:
        # 点球决胜：比分可能仍平，必须以 winner 计胜负
        sa, sb = 0.5, 0.5
        if winner_name == home:
            sa, sb = 1.0, 0.0
        elif winner_name == away:
            sa, sb = 0.0, 1.0

    i = max(5.0, float(importance))
    w_vs_away = confed_strength(away_confed)
    w_vs_home = confed_strength(home_confed)

    dh = i * w_vs_away * (sa - ea)
    da = i * w_vs_home * (sb - eb)

    # 淘汰赛：败方不扣国际积分
    if knockout:
        if sa < sb:
            dh = max(0.0, dh)
        elif sb < sa:
            da = max(0.0, da)

    ratings[home] = rh + dh
    ratings[away] = ra + da
    return {
        "importance": round(i, 2),
        "home_confed_w": round(w_vs_home, 3),
        "away_confed_w": round(w_vs_away, 3),
        "delta_home": round(dh, 2),
        "delta_away": round(da, 2),
        "ko_bonus": 0.0,
        "ko_bonus_team": "",
        "expected_home": round(ea, 3),
        "knockout": bool(knockout),
        "result_W_home": sa,
        "result_W_away": sb,
    }


def ranks_from_ratings(
    ratings: Mapping[str, float],
    *,
    tiebreak_ranks: Optional[Mapping[str, int]] = None,
) -> Dict[str, int]:
    """积分高者排名靠前（1 最强）；同分按原排名、队名。"""
    tb = tiebreak_ranks or {}
    ordered = sorted(
        ratings.items(),
        key=lambda kv: (-kv[1], tb.get(kv[0], 9999), kv[0]),
    )
    return {name: i for i, (name, _) in enumerate(ordered, start=1)}


def init_ratings_from_ranks(ranks: Mapping[str, int]) -> Dict[str, float]:
    total = max(len(ranks), max(ranks.values(), default=1))
    return {n: rank_to_elo(int(r), total) for n, r in ranks.items()}


def update_ranks_from_match_results(
    base_ranks: Mapping[str, int],
    results: Iterable[Tuple[str, str, int, int, bool]],
    *,
    k: float = BASE_K,
    confeds: Optional[Mapping[str, str]] = None,
) -> Dict[str, int]:
    """
    根据赛果更新排名。
    results 元素: (home, away, hg, ag, neutral)
    """
    ratings = init_ratings_from_ranks(base_ranks)
    conf = confeds or {}
    for home, away, hg, ag, neutral in results:
        if home not in ratings or away not in ratings:
            continue
        apply_elo(
            ratings,
            home,
            away,
            hg,
            ag,
            k=k,
            home_adv=0.0 if neutral else HOME_ADV_POINTS,
            home_confed=conf.get(home, ""),
            away_confed=conf.get(away, ""),
            importance=IMPORTANCE_QUALIFIER,
            knockout=False,
        )
    return ranks_from_ratings(ratings, tiebreak_ranks=base_ranks)


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
        v = float(overrides[name])
        return max(12.0, min(99.0, v))
    return ovr_from_rank_curve(rank, total)


def power_from_ovr(ovr: float) -> float:
    """映射到比赛引擎：约 650–1850，弱队与强队差距明显。"""
    return 400.0 + ovr * 15.5
