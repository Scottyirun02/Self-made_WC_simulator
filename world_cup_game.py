"""
四年周期模拟：Part A 四大洲际杯（含超级杯比赛日）→ 友谊赛 → 正赛 →
Part B 洲内附加 → 联赛上半 → 联合会杯 → 联赛下半 → 三大杯。
淘汰赛：加时 + 点球；战力采用 FIFA 风格 OVR（见 world_cup_ratings.py 与 data/team_ovr_overrides.json）。
比赛日与开球见 fifa_calendar.py。
"""
from __future__ import annotations

import argparse
import hashlib
import math
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from fifa_calendar import (
    LEAGUE_LONG_ROUNDS,
    LEAGUE_SPLIT_AFTER,
    N_MATCHDAYS,
    QUAL_EARLY_SLOTS,
    assign_kickoffs,
    cycle_label,
    cycle_start_year,
    draw_friendly_pairs,
    format_kickoff,
    league_dates_for_rounds,
    matchday_date,
    matchday_info,
    qual_dates_for_rounds,
)

from continental_cups import (
    CONTINENTAL_CODES,
    CONTINENTAL_CUPS,
    CONTINENTAL_LABELS,
    FINAL_GROUP_LABELS,
    QF_FROM_R16,
    R16_PAIRINGS,
    SF_FROM_QF,
    cup_by_code,
    draw_finals_groups,
    draw_playoff_ties,
    draw_qual_groups,
    fair_fourth_place_stats,
    fourth_place_sort_key,
    pool_teams,
    round_robin_double_any,
    select_playoff_fourths,
    traditional_r16_slots,
)
from world_cup_challenger import (
    QF_PAIR_IDX,
    SF_PAIR_IDX,
    WCC_GROUP_LABELS,
    compute_bracket_state,
    compute_draw_strength,
    draw_groups_from_pots,
    draw_six_pots_into_groups,
    get_r16_slots,
    gs_comp_label,
    gs_tables_ready,
    round_robin_single_even,
    sorted_group_table,
)
from world_cup_ratings import (
    BASE_K,
    HOME_ADV_POINTS,
    apply_elo,
    init_ratings_from_ranks,
    is_knockout_decisive,
    load_original_ranks,
    load_ovr_overrides,
    load_world_ranks,
    elo_treat_as_cup_knockout,
    match_importance,
    ovr_for_team,
    power_from_ovr,
    ranks_from_ratings,
    reset_cycle_ranks_from_original,
    save_world_ranks,
)

CONFEDS = ["UEFA", "AFC", "CONCACAF", "CAF", "OFC", "CONMEBOL"]
FINAL_CUPS = ["WORLD-CHAMPIONS", "WORLD-LEAGUE", "WORLD-ASSOCIATION"]
PRELIM_LEAGUE_N = {
    "UEFA": 48,
    "AFC": 36,
    "CONCACAF": 30,
    "CAF": 48,
    "OFC": 12,
    "CONMEBOL": 10,
}
PRELIM_DIRECT_N = {
    "UEFA": 44,
    "AFC": 31,
    "CONCACAF": 25,
    "CAF": 42,
    "OFC": 11,
    "CONMEBOL": 10,
}
CONFED_CUP_FOR_BYE = {
    "UEFA": "EURO",
    "AFC": "APAC",
    "CONCACAF": "AMERICA",
    "CAF": "AFCON",
}
CUP_FINAL_RANK_ORDER = [
    "EURO",
    "AFCON",
    "APAC",
    "AMERICA",
    "CC",
    "WCC",
    "WORLD-CHAMPIONS",
    "WORLD-LEAGUE",
    "WORLD-ASSOCIATION",
    "WSC",
]
CUP_FINAL_RANK_LABELS = {
    "EURO": "欧洲杯",
    "AFCON": "非洲杯",
    "APAC": "亚太杯",
    "AMERICA": "美洲杯",
    "CC": "联合会杯",
    "WCC": "世界挑战者杯",
    "WORLD-CHAMPIONS": "世界冠军杯",
    "WORLD-LEAGUE": "世界联赛杯",
    "WORLD-ASSOCIATION": "世界协会杯",
    "WSC": "世界超级杯",
}

# 联合会杯（Confederations Cup）：Part A 末尾，四大洲杯四强共 16 队，
# 一~四档 = 欧洲/美洲/非洲/亚太杯四强；每档抽一队落入 A–D 组，
# 小组单循环前二进八强，随后 1/4决赛 → 半决赛 → 决赛（中立场单场决胜）。
CC_GROUP_LABELS = list("ABCD")
CC_POT_CUP_ORDER = ["EURO", "AMERICA", "AFCON", "APAC"]
CC_QF_PAIRINGS = [("A1", "B2"), ("C1", "D2"), ("B1", "A2"), ("D1", "C2")]

# 世界超级杯（World Super Cup）：三大杯结束后，8 队单场淘汰。
# 录取：世界冠军杯四强 + 世界联赛杯冠亚军 + 世界协会杯冠军
#      + 上述 7 队之外世界排名最高的 1 队。
# 开赛一次性锁死全部签位：
#   附加赛第一轮 一档=联赛冠亚军（主场）vs 二档=协会冠军+排名递补（客场）；
#   附加赛第二轮 一档=冠军杯 3–4 名（主场）vs 第一轮两名胜者（客场）；
#   半决赛/决赛中立场：冠军杯冠、亚军分镇上下半区，迎战第二轮胜者。
WSC_CODE = "WSC"

# 单场战力：在球队基准 OVR（JSON/曲线）附近小幅波动；整届大赛内基准不变
MATCH_OVR_JITTER = 0.8

# OVR 比分模拟参数（有效评分差 → 进球份额 → Poisson 采样）
OVR_LOW_TIER_AMP = 0.45
OVR_WEAK_PENALTY = 0.35
OVR_TOTAL_GOAL_COEFF = 0.085
OVR_WEAK_EXTRA_GOAL = 0.06
OVR_TOTAL_GOALS_MIN = 2.05
OVR_TOTAL_GOALS_MAX = 10.50
OVR_COLLAPSE_PROB_CAP = 0.45
OVR_HOME_OVR_PER_POWER = 1.0 / 15.5
# 双方均属中上/一流时，略放大有效分差（压低二流爆冷一流的概率）
OVR_ELITE_FLOOR = 78.0
OVR_ELITE_DIFF_MULT = 1.2

# 积分榜划线：(名次下限, 名次上限, 标签) — 用于 UI 展示晋级区间
TABLE_ZONES: Dict[str, List[Tuple[int, int, str]]] = {
    "UEFA-QUAL": [
        (1, 14, "世界冠军杯正赛"),
        (15, 18, "世界冠军杯附加赛"),
        (19, 26, "世界联赛杯正赛"),
        (27, 30, "世界联赛杯附加赛"),
        (31, 33, "世界协会杯正赛"),
        (34, 37, "世界协会杯附加赛"),
    ],
    "AFC-QUAL": [
        (1, 4, "世界冠军杯正赛"),
        (5, 6, "世界冠军杯附加赛"),
        (7, 12, "世界联赛杯正赛"),
        (13, 14, "世界联赛杯附加赛"),
        (15, 21, "世界协会杯正赛"),
        (22, 26, "世界协会杯附加赛"),
    ],
    "CONCACAF-QUAL": [
        (1, 3, "世界冠军杯正赛"),
        (4, 5, "世界冠军杯附加赛"),
        (6, 7, "世界联赛杯正赛"),
        (8, 9, "世界联赛杯附加赛"),
        (10, 10, "世界协会杯正赛"),
        (11, 13, "世界协会杯附加赛"),
    ],
    "CAF-QUAL": [
        (1, 4, "世界冠军杯正赛"),
        (5, 6, "世界冠军杯附加赛"),
        (7, 12, "世界联赛杯正赛"),
        (13, 14, "世界联赛杯附加赛"),
        (15, 22, "世界协会杯正赛"),
        (23, 28, "世界协会杯附加赛"),
    ],
    "OFC-QUAL": [
        (1, 1, "世界冠军杯附加赛"),
        (2, 2, "世界联赛杯附加赛"),
        (3, 3, "世界协会杯正赛"),
        (4, 4, "世界协会杯附加赛"),
    ],
    "CONMEBOL-QUAL": [
        (1, 5, "世界冠军杯正赛"),
        (6, 6, "世界冠军杯附加赛"),
        (7, 8, "世界联赛杯正赛"),
        (9, 9, "世界联赛杯附加赛"),
        (10, 10, "世界协会杯附加赛"),
    ],
}

_CHALLENGER_GS_ZONES: List[Tuple[int, int, str]] = [
    (1, 1, "16强直通（小组第一）"),
    (2, 2, "第二名（S7/S8 槽位：前二均值最优两组）"),
    (3, 3, "24强附加赛"),
    (4, 4, "24强附加赛"),
    (5, 6, "未晋级淘汰赛"),
]
for _cup in FINAL_CUPS:
    for _lab in WCC_GROUP_LABELS:
        TABLE_ZONES[f"{_cup}-GS-{_lab}"] = _CHALLENGER_GS_ZONES
TABLE_ZONES["WCC-GS-A"] = _CHALLENGER_GS_ZONES
for _lab in WCC_GROUP_LABELS[1:]:
    TABLE_ZONES[f"WCC-GS-{_lab}"] = _CHALLENGER_GS_ZONES

_CONTINENTAL_QUAL_ZONES: List[Tuple[int, int, str]] = [
    (1, 3, "正赛直通"),
    (4, 4, "附加赛（第四名）"),
    (5, 7, "未晋级"),
]
_CONTINENTAL_FINALS_GS_ZONES: List[Tuple[int, int, str]] = [
    (1, 2, "16强"),
    (3, 4, "小组出局"),
]
for _cc in CONTINENTAL_CODES:
    for _lab in "ABCDEFGHI":
        TABLE_ZONES[f"{_cc}-QUAL-{_lab}"] = _CONTINENTAL_QUAL_ZONES
    for _lab in FINAL_GROUP_LABELS:
        TABLE_ZONES[f"{_cc}-GS-{_lab}"] = _CONTINENTAL_FINALS_GS_ZONES

_CC_GS_ZONES: List[Tuple[int, int, str]] = [
    (1, 2, "八强（晋级淘汰赛）"),
    (3, 4, "小组出局"),
]
for _lab in CC_GROUP_LABELS:
    TABLE_ZONES[f"CC-GS-{_lab}"] = _CC_GS_ZONES


def zone_label_for_rank(comp: str, rank: int) -> str:
    for lo, hi, lab in TABLE_ZONES.get(comp, []):
        if lo <= rank <= hi:
            return lab
    return "—"

UEFA_TEAMS = [
    "France", "England", "Spain", "Portugal", "Netherlands", "Belgium", "Italy", "Germany", "Croatia", "Switzerland",
    "Denmark", "Austria", "Ukraine", "Sweden", "Poland", "Serbia", "Türkiye", "Czechia", "Hungary", "Romania",
    "Scotland", "Slovakia", "Slovenia", "Greece", "Norway", "Wales", "Ireland", "Northern Ireland", "Iceland", "Finland",
    "Bosnia and Herzegovina", "Albania", "Montenegro", "North Macedonia", "Bulgaria", "Georgia", "Belarus", "Kosovo", "Armenia", "Kazakhstan",
    "Luxembourg", "Azerbaijan", "Estonia", "Latvia", "Lithuania", "Faroe Islands", "Moldova", "Malta", "Cyprus", "Andorra",
    "San Marino", "Liechtenstein", "Gibraltar", "Monaco", "Vatican City",
]

AFC_TEAMS = [
    "Japan", "IR Iran", "South Korea", "Australia", "Saudi Arabia", "Qatar", "Iraq", "UAE", "Uzbekistan", "Jordan",
    "Oman", "Bahrain", "China PR", "Syria", "Palestine", "Kyrgyz Republic", "Vietnam", "India", "Tajikistan", "Lebanon",
    "Thailand", "North Korea", "Indonesia", "Malaysia", "Philippines", "Turkmenistan", "Hong Kong", "Singapore", "Yemen", "Afghanistan",
    "Myanmar", "Kuwait", "Nepal", "Cambodia", "Mongolia", "Chinese Taipei", "Bhutan", "Maldives", "Bangladesh",
    "Macau", "Laos", "Brunei Darussalam", "Timor-Leste", "Pakistan", "Sri Lanka", "Guam",
    "Northern Mariana Islands",
]

CONCACAF_TEAMS = [
    "USA", "Mexico", "Canada", "Costa Rica", "Panama", "Jamaica", "Honduras", "El Salvador", "Haiti", "Trinidad and Tobago",
    "Guatemala", "Curaçao", "Suriname", "Nicaragua", "Dominican Republic", "Antigua and Barbuda", "Grenada", "Guyana", "St. Kitts and Nevis", "St. Lucia",
    "St. Vincent and the Grenadines", "Barbados", "Cuba", "Puerto Rico", "Bermuda", "Belize", "Dominica", "Montserrat", "Aruba", "Bahamas",
    "Cayman Islands", "Turks and Caicos Islands", "US Virgin Islands", "British Virgin Islands", "Anguilla", "Sint Maarten", "Martinique", "Guadeloupe", "French Guiana", "Bonaire",
    "Greenland",
]

CAF_TEAMS = [
    "Morocco", "Senegal", "Nigeria", "Egypt", "Algeria", "Tunisia", "Cameroon", "Mali", "Ivory Coast", "Ghana",
    "DR Congo", "South Africa", "Burkina Faso", "Guinea", "Cape Verde", "Zambia", "Uganda", "Benin", "Gabon", "Angola",
    "Equatorial Guinea", "Mauritania", "Libya", "Namibia", "Madagascar", "Mozambique", "Kenya", "Zimbabwe", "Tanzania", "Botswana",
    "Ethiopia", "Rwanda", "Burundi", "Togo", "Sierra Leone", "Malawi", "Niger", "Sudan", "Congo", "Gambia",
    "Comoros", "Central African Republic", "Eswatini", "Lesotho", "Liberia", "South Sudan", "Mauritius", "Chad", "Sao Tome and Principe", "Seychelles",
    "Djibouti", "Somalia", "Eritrea", "Guinea-Bissau",
]

OFC_TEAMS = [
    "New Zealand", "Solomon Islands", "Tahiti", "New Caledonia", "Fiji", "Papua New Guinea", "Vanuatu", "Samoa", "Tonga",
    "Cook Islands", "American Samoa", "Kiribati", "Tuvalu",
]

CONMEBOL_TEAMS = [
    "Argentina", "Brazil", "Uruguay", "Colombia", "Ecuador", "Peru", "Chile", "Paraguay", "Venezuela", "Bolivia",
]


@dataclass
class Team:
    name: str
    confed: str
    world_rank: int
    ovr: float
    power: float


@dataclass
class Match:
    comp: str
    stage: str
    day: int
    round_num: int
    home: Team
    away: Team
    played: bool = False
    hg: int = 0
    ag: int = 0
    # league: 允许平局 | knockout: 必分胜负(加时/点球)
    kind: str = "league"
    score_note: str = ""
    winner: Optional[Team] = None
    # True：中立场（三大杯正赛联赛/淘汰赛/杯内附加赛）；不计主场战力加成
    neutral: bool = False
    # 本场实际采用的 OVR（基准 + 单场抖动）；未赛时为 None
    home_match_ovr: Optional[float] = None
    away_match_ovr: Optional[float] = None
    # 两回合附加赛配对键（同 tie_id 的两场）
    tie_id: str = ""
    # 常规时间比分（淘汰赛加时前）；排名表只用这两项
    reg_hg: Optional[int] = None
    reg_ag: Optional[int] = None
    # 开球时间（ISO 8601，含时区）；未排期为空
    kickoff: str = ""


def venue_caption(neutral: bool, home_name: str) -> str:
    if neutral:
        return f"中立球场（记名主队 {home_name}）"
    return f"主场 {home_name}"


def assign_balanced_home_away(
    pots: List[List[Team]],
    edges: List[Tuple[Team, Team]],
    rng: Optional[random.Random] = None,
) -> List[Tuple[Team, Team]]:
    """
    按现行欧冠/欧联/欧协联联赛阶段规则定向主客场：
    每队对每一档的 2 个对手必须一主一客（含同档的 2 场）。
    每个「档对」子图均为 2-正则图（同档为长度≥3 的环，跨档为偶环），
    沿环同向定向即得每队每档 1 主 1 客；环方向由 rng 逐环随机。
    每队总主场数 = 总客场数 = 档数（6 档联赛为 6 主 6 客，4 档为 4 主 4 客）。
    """
    if not edges:
        return []

    pot_of: Dict[str, int] = {}
    for pi, pot in enumerate(pots):
        for t in pot:
            pot_of[t.name] = pi

    blocks: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for ei, (a, b) in enumerate(edges):
        pa, pb = pot_of[a.name], pot_of[b.name]
        blocks[(pa, pb) if pa <= pb else (pb, pa)].append(ei)

    home_of: Dict[int, str] = {}
    for (pi, pj), eidxs in sorted(blocks.items()):
        adj: Dict[str, List[int]] = defaultdict(list)
        for ei in eidxs:
            a, b = edges[ei]
            adj[a.name].append(ei)
            adj[b.name].append(ei)
        for name, idxs in adj.items():
            if len(idxs) != 2:
                raise RuntimeError(f"档对({pi + 1},{pj + 1})中 {name} 有 {len(idxs)} 场对阵，应为 2")

        unused: Set[int] = set(eidxs)
        while unused:
            e0 = next(iter(unused))
            start = edges[e0][0].name
            cycle: List[int] = []
            cur, ei = start, e0
            while True:
                cycle.append(ei)
                unused.discard(ei)
                a, b = edges[ei]
                nxt = b.name if a.name == cur else a.name
                if nxt == start:
                    break
                ei = next(e for e in adj[nxt] if e != ei)
                cur = nxt
            flip = rng is not None and rng.random() < 0.5
            cur = start
            for ei in cycle:
                a, b = edges[ei]
                nxt = b.name if a.name == cur else a.name
                home_of[ei] = nxt if flip else cur
                cur = nxt

    oriented: List[Tuple[Team, Team]] = []
    for i, (a, b) in enumerate(edges):
        if home_of.get(i, a.name) == a.name:
            oriented.append((a, b))
        else:
            oriented.append((b, a))

    home_vs_pot: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for h, a in oriented:
        home_vs_pot[h.name][pot_of[a.name]] += 1
    for pot in pots:
        for t in pot:
            for pj in range(len(pots)):
                if home_vs_pot[t.name].get(pj, 0) != 1:
                    raise RuntimeError(f"{t.name} 对 {pj + 1} 档主场数应为 1，实际 {home_vs_pot[t.name].get(pj, 0)}")
    return oriented


def split_into_pots(teams: List[Team], n_pots: int) -> List[List[Team]]:
    ordered = sorted(teams, key=lambda t: t.world_rank)
    n = len(ordered)
    if n % n_pots != 0:
        raise ValueError(f"球队数 {n} 无法均分为 {n_pots} 档")
    m = n // n_pots
    return [ordered[i * m : (i + 1) * m] for i in range(n_pots)]


def split_into_pots_banded(bands: Sequence[Sequence[Team]], n_pots: int) -> List[List[Team]]:
    """三带来源依次入档：每带内按世界排名，再按档容量切开。"""
    ordered: List[Team] = []
    seen: Set[str] = set()
    for band in bands:
        for t in sorted(band, key=lambda x: x.world_rank):
            if t.name in seen:
                continue
            seen.add(t.name)
            ordered.append(t)
    n = len(ordered)
    if n % n_pots != 0:
        raise ValueError(f"球队数 {n} 无法均分为 {n_pots} 档")
    m = n // n_pots
    return [ordered[i * m : (i + 1) * m] for i in range(n_pots)]


def match_regular_score(m: Match) -> Tuple[int, int]:
    if m.reg_hg is not None and m.reg_ag is not None:
        return int(m.reg_hg), int(m.reg_ag)
    return int(m.hg), int(m.ag)


def _dedupe_edges(edges: List[Tuple[Team, Team]]) -> List[Tuple[Team, Team]]:
    seen: Set[Tuple[str, str]] = set()
    out: List[Tuple[Team, Team]] = []
    for a, b in edges:
        x, y = sorted([a.name, b.name])
        if (x, y) in seen:
            continue
        seen.add((x, y))
        out.append((a, b))
    return out


def build_pot_league_edges(pots: List[List[Team]]) -> List[Tuple[Team, Team]]:
    """
    旧版固定轮转配对（已弃用）：同档内 k 与 k+1 成环、跨档亦为 k/k+1，
    会导致「档内序号」与对手档内序号强相关。请使用 simulate_uefa_style_league_draw。
    """
    n_pots = len(pots)
    m = len(pots[0])
    for p in pots:
        if len(p) != m:
            raise ValueError("各档人数必须相同")
    edges: List[Tuple[Team, Team]] = []
    for i in range(n_pots):
        for j in range(i, n_pots):
            pi, pj = pots[i], pots[j]
            if i == j:
                for k in range(m):
                    edges.append((pi[k], pi[(k + 1) % m]))
            else:
                for k in range(m):
                    edges.append((pi[k], pj[k]))
                    edges.append((pi[k], pj[(k + 1) % m]))
    return _dedupe_edges(edges)


def _count_neighbors_in_pot(adj: Dict[str, Set[str]], name: str, pot_members: Set[str]) -> int:
    return len(adj[name] & pot_members)


def simulate_uefa_style_league_draw(
    pots: List[List[Team]],
    rng: random.Random,
    *,
    max_attempts: int = 8000,
) -> Tuple[List[Tuple[Team, Team]], List[Dict[str, Any]], Dict[str, Dict[str, List[str]]]]:
    """
    模拟欧冠/欧联/欧协联式「联赛阶段」抽签（本游戏为每档 2 个对手，含同档 2 场）：
    自最后一档（序号最大、实力最弱档）到第一档，档内按世界排名从低到高（弱队先抽）；
    对每个抽中的球队，自最低档向最高档依次补足与各档的 2 场对阵；
    跨档时若某候选队与「本档」的已配对次数已达 2，则不得再抽中（移出池）；
    同档则在尚未连满 2 场的队友中随机抽选。

    返回：边列表、逐步抽签记录、各队「按档」对手名单（档号 1 为最强档）。
    """
    n_pots = len(pots)
    m = len(pots[0])
    for p in pots:
        if len(p) != m:
            raise ValueError("各档人数必须相同")

    pot_members: List[Set[str]] = [{t.name for t in pot} for pot in pots]
    pot_of: Dict[str, int] = {}
    for pi, pot in enumerate(pots):
        for t in pot:
            pot_of[t.name] = pi

    base_salt = rng.randrange(1, 10**9)

    def try_once(attempt_rng: random.Random) -> Optional[Tuple[List[Tuple[Team, Team]], List[Dict[str, Any]]]]:
        adj: Dict[str, Set[str]] = defaultdict(set)
        steps: List[Dict[str, Any]] = []
        team_map: Dict[str, Team] = {t.name: t for pot in pots for t in pot}

        draw_order: List[Team] = []
        for pi in range(n_pots - 1, -1, -1):
            draw_order.extend(sorted(pots[pi], key=lambda t: t.world_rank, reverse=True))

        for T in draw_order:
            pi = pot_of[T.name]
            for pj in range(n_pots - 1, -1, -1):
                need = 2 - _count_neighbors_in_pot(adj, T.name, pot_members[pj])
                while need > 0:
                    cands: List[Team] = []
                    for C in pots[pj]:
                        if C.name == T.name:
                            continue
                        if C.name in adj[T.name]:
                            continue
                        if pi == pj:
                            if len(adj[C.name] & pot_members[pi]) >= 2:
                                continue
                        else:
                            if _count_neighbors_in_pot(adj, C.name, pot_members[pi]) >= 2:
                                continue
                        cands.append(C)
                    if not cands:
                        return None
                    attempt_rng.shuffle(cands)
                    C = cands[0]
                    adj[T.name].add(C.name)
                    adj[C.name].add(T.name)
                    steps.append(
                        {
                            "抽中球队": T.name,
                            "该队所在档": pi + 1,
                            "从档抽选": pj + 1,
                            "对手": C.name,
                        }
                    )
                    need -= 1
        edges: List[Tuple[Team, Team]] = []
        seen: Set[Tuple[str, str]] = set()
        for a in adj:
            for bn in adj[a]:
                x, y = sorted([a, bn])
                if (x, y) in seen:
                    continue
                seen.add((x, y))
                edges.append((team_map[x], team_map[y]))
        return edges, steps

    last_err: Optional[str] = None
    for att in range(max_attempts):
        r2 = random.Random(base_salt + att * 7919)
        got = try_once(r2)
        if got is not None:
            edges, steps = got
            by_team_pot: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
            for a, b in edges:
                pa, pb = pot_of[a.name], pot_of[b.name]
                by_team_pot[a.name][f"{pb + 1}档"].append(b.name)
                by_team_pot[b.name][f"{pa + 1}档"].append(a.name)
            flat: Dict[str, Dict[str, List[str]]] = {}
            for nm, d in by_team_pot.items():
                flat[nm] = {k: sorted(v) for k, v in sorted(d.items())}
            return edges, steps, flat
        last_err = "greedy_dead_end"

    raise RuntimeError(f"联赛阶段抽签多次失败（{last_err}），请更换随机种子或增大 max_attempts")


def build_ofc_league_edges(pots: List[List[Team]]) -> List[Tuple[Team, Team]]:
    """旧版大洋洲固定配对（已弃用；现与其它洲相同使用 simulate_uefa_style_league_draw）。"""
    if len(pots) != 4:
        raise ValueError("OFC 需要 4 档")
    m = len(pots[0])
    for p in pots:
        if len(p) != m:
            raise ValueError("各档人数必须相同")
    edges: List[Tuple[Team, Team]] = []
    for i in range(4):
        for j in range(i, 4):
            pi, pj = pots[i], pots[j]
            if i == j:
                for a in range(m):
                    for b in range(a + 1, m):
                        edges.append((pi[a], pi[b]))
            else:
                for k in range(m):
                    edges.append((pi[k], pj[k]))
                    edges.append((pi[k], pj[(k + 1) % m]))
    return _dedupe_edges(edges)


def _verify_regular(edges: List[Tuple[Team, Team]], team_list: List[Team], degree_expected: int) -> None:
    cnt: Dict[str, int] = {t.name: 0 for t in team_list}
    for a, b in edges:
        cnt[a.name] += 1
        cnt[b.name] += 1
    bad = [(n, c) for n, c in cnt.items() if c != degree_expected]
    if bad:
        raise RuntimeError(f"场次不一致: 期望每队{degree_expected}场, 异常样例: {bad[:8]}")


def assign_rounds_greedy(
    matches: List[Tuple[Team, Team]],
    n_rounds: int,
    rng: random.Random,
    max_attempts: int = 600,
) -> Optional[List[List[int]]]:
    n = len(matches)
    if n == 0:
        return []
    for _ in range(max_attempts):
        order = list(range(n))
        rng.shuffle(order)
        round_of = [-1] * n
        occ: Set[Tuple[str, int]] = set()
        ok = True
        for mi in order:
            a, b = matches[mi]
            placed = False
            rs = list(range(n_rounds))
            rng.shuffle(rs)
            for r in rs:
                if (a.name, r) in occ or (b.name, r) in occ:
                    continue
                round_of[mi] = r
                occ.add((a.name, r))
                occ.add((b.name, r))
                placed = True
                break
            if not placed:
                ok = False
                break
        if not ok:
            continue
        buckets: List[List[int]] = [[] for _ in range(n_rounds)]
        for mi in range(n):
            buckets[round_of[mi]].append(mi)
        return buckets
    return None


def assign_rounds_with_restarts(
    matches: List[Tuple[Team, Team]],
    n_rounds: int,
    rng: random.Random,
    max_outer: int = 80,
) -> Optional[List[List[int]]]:
    """多次换随机种子重试贪心。"""
    base = rng.randint(1, 10**9)
    for salt in range(max_outer):
        r2 = random.Random(base + salt)
        res = assign_rounds_greedy(matches, n_rounds, r2, max_attempts=400)
        if res is not None:
            return res
    return None


def assign_rounds_cp_sat(matches: List[Tuple[Team, Team]], n_rounds: int) -> Optional[List[List[int]]]:
    """
    将每场安排在唯一一轮，且同一轮内每支球队至多一场 —— CP-SAT 可行解（通常极快）。
    """
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return None

    n_m = len(matches)
    if n_m == 0:
        return []

    teams: Set[str] = set()
    for a, b in matches:
        teams.add(a.name)
        teams.add(b.name)
    inv: Dict[str, List[int]] = {t: [] for t in teams}
    for mi, (a, b) in enumerate(matches):
        inv[a.name].append(mi)
        inv[b.name].append(mi)

    model = cp_model.CpModel()
    x: Dict[Tuple[int, int], Any] = {}
    for mi in range(n_m):
        for r in range(n_rounds):
            x[mi, r] = model.NewBoolVar(f"x_{mi}_{r}")
    for mi in range(n_m):
        model.Add(sum(x[mi, r] for r in range(n_rounds)) == 1)
    for r in range(n_rounds):
        for t in teams:
            mis = inv[t]
            if mis:
                model.Add(sum(x[mi, r] for mi in mis) <= 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30.0
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    assign_r = [0] * n_m
    for mi in range(n_m):
        for r in range(n_rounds):
            if solver.Value(x[mi, r]):
                assign_r[mi] = r
                break
    buckets: List[List[int]] = [[] for _ in range(n_rounds)]
    for mi, r in enumerate(assign_r):
        buckets[r].append(mi)
    return buckets


def assign_rounds_auto(
    matches: List[Tuple[Team, Team]], n_rounds: int, rng: random.Random
) -> Optional[List[List[int]]]:
    r = assign_rounds_cp_sat(matches, n_rounds)
    if r is not None:
        return r
    r = assign_rounds_with_restarts(matches, n_rounds, rng, max_outer=200)
    if r is not None:
        return r
    return assign_rounds_greedy(matches, n_rounds, rng, max_attempts=5000)


def round_robin_double(teams: List[Team], rng: random.Random) -> List[List[Tuple[Team, Team]]]:
    n = len(teams)
    if n % 2 == 1:
        raise ValueError("南美预选赛须为偶数队")
    s = teams[:]
    rng.shuffle(s)
    rounds_first: List[List[Tuple[Team, Team]]] = []
    for _ in range(n - 1):
        day: List[Tuple[Team, Team]] = []
        for i in range(n // 2):
            a, b = s[i], s[n - 1 - i]
            if rng.random() < 0.5:
                day.append((a, b))
            else:
                day.append((b, a))
        rounds_first.append(day)
        s = [s[0]] + [s[-1]] + s[1 : n - 1]
    rounds_second: List[List[Tuple[Team, Team]]] = []
    for day in rounds_first:
        rounds_second.append([(b, a) for a, b in day])
    return rounds_first + rounds_second


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _sample_poisson(rng: random.Random, lam: float) -> int:
    lam = max(0.0, lam)
    if lam <= 0.0:
        return 0
    lim = math.exp(-lam)
    k = 0
    p = 1.0
    while p > lim:
        k += 1
        p *= rng.random()
    return k - 1


def _ovr_effective_diff(ra: float, rb: float) -> Tuple[float, float, float, float]:
    abs_diff = abs(ra - rb)
    avg = (ra + rb) / 2.0
    low_team = min(ra, rb)
    effective_diff = (
        abs_diff
        + max(0.0, 70.0 - avg) * OVR_LOW_TIER_AMP
        + max(0.0, 55.0 - low_team) * OVR_WEAK_PENALTY
    )
    # 高分段对局：略拉开有效分差，避免一流 vs 二流+过于“黏”
    if min(ra, rb) >= OVR_ELITE_FLOOR:
        effective_diff *= OVR_ELITE_DIFF_MULT
    return effective_diff, abs_diff, avg, low_team


def _ovr_rating_scale(avg: float) -> float:
    # 高分段用更小 scale → 同等 OVR 差下强队进球份额更高
    if avg >= 75.0:
        return 12.0
    if avg >= 60.0:
        return 14.0
    return 11.0


def _ovr_match_lambdas(ra: float, rb: float, rng: random.Random) -> Tuple[float, float]:
    """按 OVR 算法计算两队 Poisson λ；ra/rb 为已含主场等效的 OVR。"""
    effective_diff, abs_diff, avg, low_team = _ovr_effective_diff(ra, rb)
    rating_scale = _ovr_rating_scale(avg)
    strong_share = _sigmoid(effective_diff / rating_scale)
    weak_share = 1.0 - strong_share

    total_goals = 2.25 + OVR_TOTAL_GOAL_COEFF * max(0.0, effective_diff - 8.0)
    if low_team < 45.0:
        total_goals += (45.0 - low_team) * OVR_WEAK_EXTRA_GOAL
    total_goals = max(OVR_TOTAL_GOALS_MIN, min(OVR_TOTAL_GOALS_MAX, total_goals))

    lam_strong = total_goals * strong_share
    lam_weak = total_goals * weak_share

    if abs_diff >= 30.0 and low_team <= 45.0:
        collapse_p = min(OVR_COLLAPSE_PROB_CAP, max(0.0, (abs_diff - 30.0) / 50.0 * OVR_COLLAPSE_PROB_CAP))
        if rng.random() < collapse_p:
            lam_strong *= 1.0 + rng.uniform(0.20, 0.65)
            lam_weak *= max(0.05, 1.0 - rng.uniform(0.25, 0.55))

    if ra >= rb:
        return lam_strong, lam_weak
    return lam_weak, lam_strong


def _goals_from_ovr(rng: random.Random, home_ovr: float, away_ovr: float) -> Tuple[int, int]:
    lam_h, lam_a = _ovr_match_lambdas(home_ovr, away_ovr, rng)
    return _sample_poisson(rng, lam_h), _sample_poisson(rng, lam_a)


def _p_win(home: Team, away: Team, home_adv: float = 52.0) -> float:
    d = (home.power + home_adv) - away.power
    return 1.0 / (1.0 + 10 ** (-d / 315.0))


def _p_win_neutral(a: Team, b: Team) -> float:
    d = a.power - b.power
    return 1.0 / (1.0 + 10 ** (-d / 300.0))


def _pen_score_prob(t: Team) -> float:
    return max(0.64, min(0.93, 0.72 + (t.ovr - 58.0) * 0.0038))


def confed_ranks_from(teams: Sequence[Team], live_ranks: Dict[str, int]) -> Dict[str, int]:
    """各大洲内部名次（1 最好），按给定世界排名排序。"""
    grouped: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
    for t in teams:
        grouped[t.confed].append((int(live_ranks.get(t.name, t.world_rank)), t.name))
    out: Dict[str, int] = {}
    for rows in grouped.values():
        rows.sort()
        for i, (_, name) in enumerate(rows, 1):
            out[name] = i
    return out


def _opening_rank_map(teams: Sequence[Team]) -> Dict[str, int]:
    wr_map = load_original_ranks()
    total = len(teams)
    base_fb = (max(wr_map.values(), default=0) + 1) if wr_map else 0
    base: Dict[str, int] = {}
    for i, t in enumerate(teams, 1):
        if not wr_map:
            base[t.name] = i
        else:
            base[t.name] = int(wr_map.get(t.name, base_fb + i))
    return base


def rebuild_rank_history(sim: Any) -> List[Tuple[int, Dict[str, int], Dict[str, int]]]:
    """
    从开局积分库 + 已赛赛果回放国际积分，重建逐日世界/大洲排名。
    不改动 sim 当前 live_ranks / fifa_points，供旧会话补历史。
    """
    teams = list(sim.teams)
    base = _opening_rank_map(teams)
    pts = init_ratings_from_ranks(base)
    ranks = ranks_from_ratings(pts, tiebreak_ranks=base)
    hist: List[Tuple[int, Dict[str, int], Dict[str, int]]] = [
        (0, dict(ranks), confed_ranks_from(teams, ranks))
    ]
    by_day: Dict[int, List[Match]] = defaultdict(list)
    for m in sim.all_results:
        if getattr(m, "played", False):
            by_day[int(m.day)].append(m)
    for day in sorted(by_day):
        before = dict(ranks)
        for m in by_day[day]:
            if m.home.name not in pts or m.away.name not in pts:
                continue
            imp = match_importance(m.comp, m.stage, m.kind)
            cup_knockout = elo_treat_as_cup_knockout(m.comp, m.stage, m.kind)
            ko = is_knockout_decisive(m.comp, m.stage, m.kind, m.round_num)
            winner_name = None
            if m.winner is not None:
                winner_name = m.winner.name
            elif ko and m.hg != m.ag:
                winner_name = m.home.name if m.hg > m.ag else m.away.name
            hg, ag = m.hg, m.ag
            if cup_knockout and winner_name and hg == ag:
                if winner_name == m.home.name:
                    hg, ag = 1, 0
                elif winner_name == m.away.name:
                    hg, ag = 0, 1
            apply_elo(
                pts,
                m.home.name,
                m.away.name,
                hg,
                ag,
                k=BASE_K,
                home_adv=0.0 if m.neutral else HOME_ADV_POINTS,
                home_confed=m.home.confed,
                away_confed=m.away.confed,
                importance=imp,
                knockout=cup_knockout,
                winner_name=winner_name,
            )
        pts = {t.name: pts[t.name] for t in teams}
        ranks = ranks_from_ratings(pts, tiebreak_ranks=before)
        hist.append((day, dict(ranks), confed_ranks_from(teams, ranks)))
    return hist


def ensure_rank_history(sim: Any) -> None:
    """若会话里没有完整逐日排名，则从赛果回放补上（结束页无需重置）。"""
    hist = getattr(sim, "rank_history", None)
    last_played = max((int(m.day) for m in sim.all_results if getattr(m, "played", False)), default=0)
    if hist and hist[-1][0] >= last_played:
        return
    sim.rank_history = rebuild_rank_history(sim)


def team_rank_series(sim: Any, name: str) -> List[Dict[str, int]]:
    ensure_rank_history(sim)
    rows: List[Dict[str, int]] = []
    for day, world, confed in getattr(sim, "rank_history", []) or []:
        if name not in world:
            continue
        rows.append(
            {
                "比赛日": day,
                "世界排名": world[name],
                "大洲排名": int(confed.get(name, 0)),
            }
        )
    return rows


class Simulator:
    def __init__(
        self,
        seed: int,
        hosts: Optional[Dict[str, str]] = None,
        carry: Optional[Dict[str, Any]] = None,
        cycle_index: int = 1,
    ) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.cycle_index = max(1, int(cycle_index or 1))
        self.cycle_start_year = cycle_start_year(self.cycle_index)
        # 东道主：cup_code -> team name；缺省时各大区取排名最前的队
        self.hosts: Dict[str, str] = dict(hosts or {})
        self.cycle_part = "A"  # A=洲际杯, B=世界杯周期
        self.day = 0
        self._cal_segment = "QUAL_EARLY"
        self._league_tail: List[List[Match]] = []
        self._friendly_recent: Set[frozenset] = set()
        self._wsc_pending: Optional[Dict[str, Any]] = None
        self.phase_idx = 0
        self.phase_name = ""
        self.phase_matchdays: List[List[Match]] = []
        self.phase_results: List[Match] = []
        self.all_results: List[Match] = []
        self.tables: Dict[str, Dict[str, Dict[str, int]]] = {}

        self.draw_log: List[Dict[str, Any]] = []
        self.league_schedule_by_confed: Dict[str, List[List[Tuple[str, str, str, str]]]] = {}
        self.league_play_plan: Dict[str, List[List[Tuple[Team, Team]]]] = {}
        self.league_opponents_by_comp: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
        self.qual_slots: Dict[str, List[Team]] = {
            "WC": [], "WC_PO": [], "WL": [], "WL_PO": [], "WA": [], "WA_PO": [],
        }
        self._prelim_pairs_meta: Dict[str, Dict[str, Any]] = {}
        self._prelim_state: Dict[str, Dict[str, Any]] = {}
        self._prelim_live_round: int = 0
        self._po_pairs: Dict[str, List[Tuple[Team, Team]]] = {}
        self._ko_sub: str = ""
        self._last_day_matches: List[Match] = []
        self.cup_champions: Dict[str, str] = {}
        self.continental_champions: Dict[str, str] = {}
        self._wcc_prelim_losers: List[Team] = []
        self._p1_days_completed: int = 0
        self._wcc_inject_flags: Dict[str, bool] = {}
        self._wcc_draw_groups: List[List[Team]] = []
        self.wcc_champion: str = ""
        self._cup_draw_groups: Dict[str, List[List[Team]]] = {}
        self._cup_fixed_pots: Dict[str, Dict[int, List[Team]]] = {}
        self._challenger_bracket_state: Dict[str, Dict[str, Any]] = {}
        self._challenger_draw_strength: Dict[str, Dict[str, Any]] = {}

        # 洲际杯状态
        self._cont_qual_groups: Dict[str, List[List[Team]]] = {}
        self._cont_qual_plan: Dict[str, Dict[str, List[List[Tuple[Team, Team]]]]] = {}
        self._cont_playoff_ties: Dict[str, List[Dict[str, Any]]] = {}
        self._cont_finalists: Dict[str, List[Team]] = {}
        self._cont_po_winners: Dict[str, List[Team]] = {}
        self._cont_finals_groups: Dict[str, List[List[Team]]] = {}
        self._cont_ko_sub: str = ""

        # 联合会杯状态（Part A 末尾：洲际杯后、世界杯周期前）
        self._cc_pots: List[List[Team]] = []
        self._cc_groups: List[List[Team]] = []
        self._cc_ko_sub: str = ""
        self.cc_champion: str = ""

        # 世界超级杯状态（Part B 末尾：三大杯决赛后）
        self._wsc_slots: Dict[str, Any] = {}
        self._wsc_ko_sub: str = ""
        self.wsc_champion: str = ""
        self._cup_final_rankings: Dict[str, List[Dict[str, Any]]] = {}

        self.fifa_points: Dict[str, float] = {}
        self.live_ranks: Dict[str, int] = {}
        self.last_day_ranking_delta: List[Dict[str, Any]] = []
        self.last_day_rating_details: List[Dict[str, Any]] = []
        self.rank_history: List[Tuple[int, Dict[str, int], Dict[str, int]]] = []

        if carry:
            self._apply_carry_opening(carry)
        else:
            # 开局：从原始排名库覆盖周期库
            self._rank_source = "original"
            wr_map = reset_cycle_ranks_from_original()
            self.teams = self._build_teams(wr_map)
            self.team_map = {t.name: t for t in self.teams}
            self._init_live_rankings()
        self._resolve_default_hosts()
        self._bootstrap_continental_qual()
        if carry:
            self.draw_log.append(
                {
                    "type": "cycle_continue",
                    "周期": self.cycle_index,
                    "说明": "继承上一周期结束时的世界排名、国际积分与 OVR；本周期重新抽签。",
                }
            )

    def carry_snapshot(self) -> Dict[str, Any]:
        """供下一四年周期继承：排名、国际积分、OVR，以及下届超级杯名单。"""
        snap: Dict[str, Any] = {
            "ranks": {t.name: int(self.live_ranks.get(t.name, t.world_rank)) for t in self.teams},
            "points": {t.name: float(self.fifa_points.get(t.name, 1200.0)) for t in self.teams},
            "ovr": {t.name: float(t.ovr) for t in self.teams},
            "cycle_index": int(getattr(self, "cycle_index", 1)),
        }
        pending = self._wsc_pending_from_results()
        if pending:
            snap["wsc_pending"] = pending
        return snap

    def _apply_carry_opening(self, carry: Dict[str, Any]) -> None:
        ranks = {str(k): int(v) for k, v in (carry.get("ranks") or {}).items()}
        points = {str(k): float(v) for k, v in (carry.get("points") or {}).items()}
        ovr_map = {str(k): float(v) for k, v in (carry.get("ovr") or {}).items()}
        self._rank_source = "cycle"
        self.teams = self._build_teams(ranks)
        for t in self.teams:
            if t.name in ranks:
                t.world_rank = ranks[t.name]
            if t.name in ovr_map:
                t.ovr = ovr_map[t.name]
                t.power = power_from_ovr(t.ovr)
        self.team_map = {t.name: t for t in self.teams}
        self.live_ranks = {t.name: int(ranks.get(t.name, t.world_rank)) for t in self.teams}
        self.fifa_points = {t.name: float(points[t.name]) if t.name in points else 1200.0 for t in self.teams}
        save_world_ranks(
            self.live_ranks,
            comment="新四年周期开局：继承上一周期结束时的国际积分所对应排名。",
        )
        self.last_day_ranking_delta = []
        self.last_day_rating_details = []
        self.rank_history = []
        pending = carry.get("wsc_pending")
        self._wsc_pending = dict(pending) if pending else None
        self._record_rank_snapshot()

    def cycle_years_label(self) -> str:
        return cycle_label(self.cycle_start_year)

    def next_kickoff_display(self) -> str:
        if self.phase_matchdays:
            for m in self.phase_matchdays[0]:
                if getattr(m, "kickoff", ""):
                    return format_kickoff(m.kickoff)
            return matchday_date(self.cycle_start_year, self.day + 1).isoformat()
        if self.day > 0:
            return matchday_date(self.cycle_start_year, min(self.day, N_MATCHDAYS)).isoformat()
        return matchday_date(self.cycle_start_year, 1).isoformat()

    def _restamp_queued_kickoffs(self) -> None:
        for i, day in enumerate(self.phase_matchdays):
            d = matchday_date(self.cycle_start_year, self.day + 1 + i)
            assign_kickoffs(day, d)

    def _set_matchdays(self, days: List[List[Match]]) -> None:
        self.phase_matchdays = days
        self._restamp_queued_kickoffs()

    def _wsc_pending_from_results(self) -> Optional[Dict[str, Any]]:
        try:
            wc_ch, wc_ru = self._cup_champion_and_runner("WORLD-CHAMPIONS")
            wl_ch, wl_ru = self._cup_champion_and_runner("WORLD-LEAGUE")
            wa_ch, _wa_ru = self._cup_champion_and_runner("WORLD-ASSOCIATION")
            sf = self._cup_sf_losers("WORLD-CHAMPIONS")
        except RuntimeError:
            return None
        names = [wc_ch.name, wc_ru.name, wl_ch.name, wl_ru.name, wa_ch.name] + [t.name for t in sf]
        if len(set(names)) != 7:
            return None
        return {
            "wc_champion": wc_ch.name,
            "wc_runner_up": wc_ru.name,
            "wl_champion": wl_ch.name,
            "wl_runner_up": wl_ru.name,
            "wa_champion": wa_ch.name,
            "wc_sf_losers": [t.name for t in sf],
        }

    def _init_live_rankings(self) -> None:
        base = {t.name: t.world_rank for t in self.teams}
        self.fifa_points = init_ratings_from_ranks(base)
        self.live_ranks = ranks_from_ratings(self.fifa_points, tiebreak_ranks=base)
        for t in self.teams:
            t.world_rank = self.live_ranks[t.name]
        self.last_day_ranking_delta = []
        self.last_day_rating_details = []
        self._record_rank_snapshot()

    def _confed_ranks(self) -> Dict[str, int]:
        """各大洲内部名次（1 最好），按当前世界排名排序。"""
        return confed_ranks_from(self.teams, self.live_ranks)

    def _record_rank_snapshot(self) -> None:
        world = {t.name: int(self.live_ranks.get(t.name, t.world_rank)) for t in self.teams}
        self.rank_history.append((int(self.day), world, self._confed_ranks()))

    def rank_series_for(self, name: str) -> List[Dict[str, int]]:
        """某队按比赛日的世界排名与大洲排名序列（含开局第 0 日）。"""
        return team_rank_series(self, name)

    def ranking_snapshot(self) -> Dict[str, Tuple[int, float]]:
        """当前世界排名与国际积分快照。"""
        return {t.name: (self.live_ranks[t.name], self.fifa_points[t.name]) for t in self.teams}

    def ranking_delta_from(
        self,
        before: Dict[str, Tuple[int, float]],
        *,
        only_played: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """相对快照的排名/积分变化；默认只列有变化的队，可限为本轮参赛队。"""
        rows: List[Dict[str, Any]] = []
        for t in self.teams:
            name = t.name
            if only_played is not None and name not in only_played:
                continue
            old_r, old_p = before.get(name, (self.live_ranks[name], self.fifa_points[name]))
            new_r, new_p = self.live_ranks[name], self.fifa_points[name]
            dr = int(old_r) - int(new_r)  # 正数=排名上升（名次数字变小）
            dp = float(new_p) - float(old_p)
            if abs(dr) < 1 and abs(dp) < 0.05:
                if only_played is None:
                    continue
            rows.append(
                {
                    "球队": name,
                    "大洲": t.confed,
                    "原排名": int(old_r),
                    "新排名": int(new_r),
                    "排名变化": dr,
                    "原积分": round(float(old_p), 1),
                    "新积分": round(float(new_p), 1),
                    "积分变化": round(dp, 1),
                }
            )
        rows.sort(key=lambda r: (-abs(r["排名变化"]), -abs(r["积分变化"]), r["新排名"]))
        return rows

    def _update_live_rankings_after_day(self, matches: List[Match]) -> None:
        """根据本比赛日赛果更新国际积分与世界排名（含大洲强度/重要性/淘汰赛奖励）。"""
        if not matches:
            self.last_day_ranking_delta = []
            self.last_day_rating_details = []
            return
        # 保证积分表始终覆盖全部球队，避免榜单缺队
        for t in self.teams:
            if t.name not in self.fifa_points:
                self.fifa_points[t.name] = 1200.0
        before = self.ranking_snapshot()
        played: Set[str] = set()
        details: List[Dict[str, Any]] = []
        for m in matches:
            if not m.played:
                continue
            if m.home.name not in self.fifa_points or m.away.name not in self.fifa_points:
                continue
            imp = match_importance(m.comp, m.stage, m.kind)
            # 杯赛淘汰赛（含 24 强附加赛）：单场 knockout；预选两回合仍可扣败方分
            cup_knockout = elo_treat_as_cup_knockout(m.comp, m.stage, m.kind)
            ko = is_knockout_decisive(m.comp, m.stage, m.kind, m.round_num)
            winner_name = None
            if m.winner is not None:
                winner_name = m.winner.name
            elif ko and m.hg != m.ag:
                winner_name = m.home.name if m.hg > m.ag else m.away.name
            # 点球场面上可能仍平：按胜者记 1-0 再结算
            hg, ag = m.hg, m.ag
            if cup_knockout and winner_name and hg == ag:
                if winner_name == m.home.name:
                    hg, ag = 1, 0
                elif winner_name == m.away.name:
                    hg, ag = 0, 1
            detail = apply_elo(
                self.fifa_points,
                m.home.name,
                m.away.name,
                hg,
                ag,
                k=BASE_K,
                home_adv=0.0 if m.neutral else HOME_ADV_POINTS,
                home_confed=m.home.confed,
                away_confed=m.away.confed,
                importance=imp,
                knockout=cup_knockout,
                winner_name=winner_name,
            )
            detail.update(
                {
                    "比赛日": self.day,
                    "赛事": m.comp,
                    "阶段": m.stage,
                    "主队": m.home.name,
                    "客队": m.away.name,
                    "比分": f"{m.hg}-{m.ag}",
                }
            )
            details.append(detail)
            played.add(m.home.name)
            played.add(m.away.name)
        tb = {n: before[n][0] for n in before}
        # 只用全体球队重排，保证排名为连续 1…N
        pts = {t.name: self.fifa_points[t.name] for t in self.teams}
        self.fifa_points = pts
        self.live_ranks = ranks_from_ratings(pts, tiebreak_ranks=tb)
        for t in self.teams:
            t.world_rank = self.live_ranks[t.name]
        self.last_day_ranking_delta = self.ranking_delta_from(before, only_played=played)
        self.last_day_rating_details = details
        self._record_rank_snapshot()

    def _resolve_default_hosts(self) -> None:
        for spec in CONTINENTAL_CUPS:
            code = spec["code"]
            pool = pool_teams(self.teams, spec["confeds"])
            if not pool:
                raise RuntimeError(f"{code}: empty pool")
            if code not in self.hosts or self.hosts[code] not in {t.name for t in pool}:
                self.hosts[code] = sorted(pool, key=lambda t: t.world_rank)[0].name

    def _build_teams(self, wr_map: Optional[Dict[str, int]] = None) -> List[Team]:
        by_confed = {
            "UEFA": UEFA_TEAMS,
            "AFC": AFC_TEAMS,
            "CONCACAF": CONCACAF_TEAMS,
            "CAF": CAF_TEAMS,
            "OFC": OFC_TEAMS,
            "CONMEBOL": CONMEBOL_TEAMS,
        }
        all_names: List[Tuple[str, str]] = []
        for c in CONFEDS:
            all_names.extend([(n, c) for n in by_confed[c]])
        total = len(all_names)
        ovrd = load_ovr_overrides()
        if wr_map is None:
            wr_map = load_world_ranks()
        rank_ceiling = max([*wr_map.values(), total], default=total)
        base_fb = (max(wr_map.values(), default=0) + 1) if wr_map else 0
        teams: List[Team] = []
        for i, (name, confed) in enumerate(all_names, 1):
            if not wr_map:
                wr = i
            else:
                wr = wr_map.get(name, base_fb + i)
            ovr = ovr_for_team(name, wr, rank_ceiling, ovrd)
            teams.append(Team(name=name, confed=confed, world_rank=wr, ovr=ovr, power=power_from_ovr(ovr)))
        return teams

    def _apply_rank_map(self, wr_map: Dict[str, int]) -> None:
        """用新排名刷新球队 world_rank；有 OVR 覆盖的队保留 OVR，其余按曲线重算。"""
        ovrd = load_ovr_overrides()
        total = len(self.teams)
        rank_ceiling = max([*wr_map.values(), total], default=total)
        base_fb = (max(wr_map.values(), default=0) + 1) if wr_map else 0
        for t in self.teams:
            wr = wr_map.get(t.name, base_fb + total)
            ovr = ovr_for_team(t.name, wr, rank_ceiling, ovrd)
            t.world_rank = wr
            t.ovr = ovr
            t.power = power_from_ovr(ovr)
        self.team_map = {t.name: t for t in self.teams}

    # ---------- Part A: 洲际杯 ----------

    def _bootstrap_continental_qual(self) -> None:
        self.cycle_part = "A"
        self.phase_idx = 0
        self.phase_name = "洲际杯·预选小组赛（主客双循环）"
        self._cont_qual_groups = {}
        self._cont_qual_plan = {}
        self._cont_playoff_ties = {}
        self._cont_finalists = {}
        self._cont_po_winners = {}
        self._cont_finals_groups = {}
        self.continental_champions = {}

        all_plans: Dict[str, Dict[str, List[List[Tuple[Team, Team]]]]] = {}

        for spec in CONTINENTAL_CUPS:
            code = spec["code"]
            host_name = self.hosts[code]
            host = self.team_map[host_name]
            pool = [t for t in pool_teams(self.teams, spec["confeds"]) if t.name != host_name]
            sizes = list(spec["group_sizes"])
            if len(pool) != sum(sizes):
                raise RuntimeError(
                    f"{code}: pool {len(pool)} != sum(sizes) {sum(sizes)} (host={host_name})"
                )
            groups, pot_names = draw_qual_groups(pool, sizes, self.rng)
            self._cont_qual_groups[code] = groups
            self.draw_log.append(
                {
                    "type": "continental_qual_draw",
                    "赛事": CONTINENTAL_LABELS[code],
                    "code": code,
                    "东道主": host_name,
                    "小组规模": sizes,
                    "分档": pot_names,
                    "分组": {
                        chr(ord("A") + i): [t.name for t in g] for i, g in enumerate(groups)
                    },
                }
            )
            group_plans: Dict[str, List[List[Tuple[Team, Team]]]] = {}
            for i, g in enumerate(groups):
                lab = chr(ord("A") + i)
                plan = round_robin_double_any(g, self.rng)
                group_plans[lab] = plan
                comp = f"{code}-QUAL-{lab}"
                self._init_table(comp, g)
                self.league_schedule_by_confed[comp] = [
                    [(h.name, "vs", a.name, venue_caption(False, h.name)) for h, a in day]
                    for day in plan
                ]
                self.draw_log.append(
                    {
                        "type": "league_schedule_ready",
                        "赛事": comp,
                        "杯赛": CONTINENTAL_LABELS[code],
                        "总轮次": len(plan),
                        "赛制": "主客双循环",
                        "每队场次": 2 * (len(g) - 1),
                    }
                )
            all_plans[code] = group_plans
            # 东道主已锁定正赛席
            self._cont_finalists[code] = [host]

        self._cont_qual_plan = all_plans
        self._cal_segment = "QUAL_EARLY"
        days: List[List[Match]] = []
        for slot in range(1, QUAL_EARLY_SLOTS + 1):
            d = matchday_date(self.cycle_start_year, slot)
            days.append(self._qual_matches_on_date(d))
        self._set_matchdays(days)

    def _qual_matches_on_date(self, d: date) -> List[Match]:
        out: List[Match] = []
        for code, gplans in self._cont_qual_plan.items():
            for lab, plan in gplans.items():
                try:
                    qdates = qual_dates_for_rounds(len(plan), self.cycle_start_year)
                except KeyError:
                    continue
                if d not in qdates:
                    continue
                r = qdates.index(d)
                if r >= len(plan):
                    continue
                for home, away in plan[r]:
                    out.append(
                        Match(
                            comp=f"{code}-QUAL-{lab}",
                            stage=f"预选第{r + 1}轮",
                            day=0,
                            round_num=r + 1,
                            home=home,
                            away=away,
                            kind="league",
                            neutral=False,
                        )
                    )
        return out

    def _build_qual_last_day(self) -> None:
        self._cal_segment = "QUAL_LAST"
        self.phase_name = "洲际杯·预选小组赛（末轮）"
        d = matchday_date(self.cycle_start_year, QUAL_EARLY_SLOTS + 5)  # 比赛日 18
        self._set_matchdays([self._qual_matches_on_date(d)])

    def _build_friendly_days(self) -> None:
        self._cal_segment = "FRIENDLY"
        self.phase_name = "国际友谊赛"
        days: List[List[Match]] = []
        for i in range(3):
            pairs = draw_friendly_pairs(self.teams, self.rng, self._friendly_recent)
            day: List[Match] = []
            for home, away in pairs:
                self._friendly_recent.add(frozenset({home.name, away.name}))
                day.append(
                    Match(
                        comp="FRIENDLY",
                        stage="国际友谊赛",
                        day=0,
                        round_num=i + 1,
                        home=home,
                        away=away,
                        kind="league",
                        neutral=False,
                    )
                )
            days.append(day)
        self._set_matchdays(days)

    def _begin_calendar_wsc(self) -> None:
        self._cal_segment = "WSC"
        if self._wsc_pending:
            try:
                self._start_world_super_cup(from_prev=self._wsc_pending)
                return
            except (KeyError, RuntimeError):
                self._wsc_pending = None
        self.phase_name = "世界超级杯（本届不举办）"
        self._wsc_ko_sub = "skip"
        self._set_matchdays([[], [], [], []])

    def _collect_continental_qual_and_build_po(self) -> None:
        self.phase_name = "洲际杯·预选附加赛第一回合"
        self.phase_idx = 1
        self._cont_playoff_ties = {}
        leg1: List[Match] = []

        for spec in CONTINENTAL_CUPS:
            code = spec["code"]
            groups = self._cont_qual_groups[code]
            min_group_size = min(len(g) for g in groups)
            fourths: List[Tuple[str, Team, Dict[str, int]]] = []
            fourth_cmp_log: List[Dict[str, Any]] = []
            direct: List[Team] = list(self._cont_finalists.get(code, []))
            for i, g in enumerate(groups):
                lab = chr(ord("A") + i)
                comp = f"{code}-QUAL-{lab}"
                tab = self._sorted_table(comp)
                if len(tab) < 4:
                    raise RuntimeError(f"{comp}: need >=4 teams on table, got {len(tab)}")
                for name, _st in tab[:3]:
                    direct.append(self.team_map[name])
                name4, st4_full = tab[3]
                group_matches = [
                    m
                    for m in self.all_results
                    if m.comp == comp and m.played and m.kind == "league"
                ]
                st4 = fair_fourth_place_stats(name4, tab, group_matches, min_group_size)
                discard_ranks = list(range(min_group_size + 1, len(tab) + 1))
                fourths.append((lab, self.team_map[name4], st4))
                fourth_cmp_log.append(
                    {
                        "小组": lab,
                        "球队": name4,
                        "组规模": len(tab),
                        "完整积分": dict(st4_full),
                        "公平比较积分": dict(st4),
                        "剔除对名次": discard_ranks or "无（与最小组同规模）",
                    }
                )
            playoff, eliminated = select_playoff_fourths(fourths)
            ties = draw_playoff_ties(playoff, self.rng)
            self._cont_playoff_ties[code] = ties
            self._cont_finalists[code] = direct
            self.draw_log.append(
                {
                    "type": "continental_playoff_draw",
                    "赛事": CONTINENTAL_LABELS[code],
                    "code": code,
                    "说明_第四名比较": (
                        f"各组第四按公平战绩排序（最小组规模={min_group_size}）；"
                        f"多队组剔除对第 {min_group_size + 1}…n 名的比赛后再比 PTS/GD/GF"
                    ),
                    "第四名公平比较明细": fourth_cmp_log,
                    "直通正赛": [t.name for t in direct],
                    "淘汰的最差第四": {"小组": eliminated[0], "球队": eliminated[1].name},
                    "附加赛对阵": [
                        {
                            "一档": t["seed"].name,
                            "二档": t["other"].name,
                            "第一回合": f"{t['leg1_home'].name}(主) vs {t['leg1_away'].name}",
                            "第二回合": f"{t['leg2_home'].name}(主) vs {t['leg2_away'].name}",
                        }
                        for t in ties
                    ],
                }
            )
            for ti, t in enumerate(ties, start=1):
                tid = f"{code}-PO-{ti}"
                leg1.append(
                    Match(
                        comp=f"{code}-PO",
                        stage="附加赛第一回合",
                        day=0,
                        round_num=1,
                        home=t["leg1_home"],
                        away=t["leg1_away"],
                        kind="two_leg",
                        neutral=False,
                        tie_id=tid,
                    )
                )
        self.phase_matchdays = [leg1]

    def _build_continental_po_leg2(self) -> None:
        self.phase_name = "洲际杯·预选附加赛第二回合"
        self.phase_idx = 2
        leg2: List[Match] = []
        for code, ties in self._cont_playoff_ties.items():
            for ti, t in enumerate(ties, start=1):
                tid = f"{code}-PO-{ti}"
                leg2.append(
                    Match(
                        comp=f"{code}-PO",
                        stage="附加赛第二回合",
                        day=0,
                        round_num=2,
                        home=t["leg2_home"],
                        away=t["leg2_away"],
                        kind="two_leg",
                        neutral=False,
                        tie_id=tid,
                    )
                )
        self.phase_matchdays = [leg2]

    def _merge_continental_po_winners_and_build_finals_gs(self) -> None:
        self._cont_po_winners = {}
        for code, ties in self._cont_playoff_ties.items():
            winners: List[Team] = []
            for ti, t in enumerate(ties, start=1):
                tid = f"{code}-PO-{ti}"
                w = self._two_leg_winner(tid, t["seed"], t["other"])
                winners.append(w)
                self._cont_finalists[code].append(w)
            self._cont_po_winners[code] = winners
            # 去重保序
            seen: Set[str] = set()
            uniq: List[Team] = []
            for tm in self._cont_finalists[code]:
                if tm.name in seen:
                    continue
                seen.add(tm.name)
                uniq.append(tm)
            if len(uniq) != 32:
                raise RuntimeError(f"{code}: finalists={len(uniq)}, expect 32")
            self._cont_finalists[code] = uniq

        self.phase_name = "洲际杯·正赛小组赛（8 组 × 4，前二出线）"
        self.phase_idx = 3
        self._cont_finals_groups = {}
        max_rounds = 0
        cup_rounds: Dict[str, List[List[Match]]] = {}

        for spec in CONTINENTAL_CUPS:
            code = spec["code"]
            host = self.team_map[self.hosts[code]]
            finals = self._cont_finalists[code]
            po_winners = list(self._cont_po_winners.get(code, []))
            po_names = {t.name for t in po_winners}
            if len(po_winners) != 4:
                raise RuntimeError(f"{code}: playoff winners={len(po_winners)}, expect 4")
            direct_others = [t for t in finals if t.name != host.name and t.name not in po_names]
            if len(direct_others) != 27:
                raise RuntimeError(
                    f"{code}: direct non-host={len(direct_others)}, expect 27 "
                    f"(host={host.name}, po={sorted(po_names)})"
                )
            # 预选赛 9 个小组第一按公平战绩排序（沿用第四名跨组比较的剔除规则）：
            # 前 7 进正赛一档，其余 2 进二档
            min_group_size = min(len(g) for g in self._cont_qual_groups[code])
            gw_records: List[Tuple[str, Team, Dict[str, int]]] = []
            gw_cmp_log: List[Dict[str, Any]] = []
            for i, g in enumerate(self._cont_qual_groups[code]):
                lab = chr(ord("A") + i)
                comp = f"{code}-QUAL-{lab}"
                tab = self._sorted_table(comp)
                if not tab:
                    raise RuntimeError(f"{comp}: no table for group winner")
                w_name, st_full = tab[0]
                group_matches = [
                    m
                    for m in self.all_results
                    if m.comp == comp and m.played and m.kind == "league"
                ]
                st_fair = fair_fourth_place_stats(w_name, tab, group_matches, min_group_size)
                gw_records.append((lab, self.team_map[w_name], st_fair))
                gw_cmp_log.append(
                    {
                        "小组": lab,
                        "球队": w_name,
                        "组规模": len(tab),
                        "完整积分": dict(st_full),
                        "公平比较积分": dict(st_fair),
                        "剔除对名次": list(range(min_group_size + 1, len(tab) + 1)) or "无（与最小组同规模）",
                    }
                )
            gw_records.sort(key=lambda x: fourth_place_sort_key(x[1], x[2]), reverse=True)
            gw_top7 = [t for _, t, _ in gw_records[:7]]
            gw_rest2 = [t for _, t, _ in gw_records[7:]]
            gw_names = {t.name for t in gw_top7 + gw_rest2}
            direct_rest = sorted(
                (t for t in direct_others if t.name not in gw_names),
                key=lambda t: t.world_rank,
            )
            if len(direct_rest) != 18:
                raise RuntimeError(
                    f"{code}: direct non-winner={len(direct_rest)}, expect 18 "
                    f"(group winners={sorted(gw_names)})"
                )
            groups, pot_names = draw_finals_groups(
                host, gw_top7, gw_rest2, direct_rest, po_winners, self.rng
            )
            self._cont_finals_groups[code] = groups
            self.draw_log.append(
                {
                    "type": "continental_finals_draw",
                    "赛事": CONTINENTAL_LABELS[code],
                    "code": code,
                    "东道主A1": host.name,
                    "说明_分档": (
                        "一档=东道主+预选赛小组第一公平战绩前 7；剩余 2 个小组第一进二档；"
                        "二档其余 6 席、三档 8 席、四档 4 席按世界排名由剩余直通队填充；"
                        "附加赛晋级 4 队固定第四档"
                    ),
                    "说明_小组第一比较": (
                        f"各组第一按公平战绩排序（最小组规模={min_group_size}）；"
                        f"多队组剔除对第 {min_group_size + 1}…n 名的比赛后再比 PTS/GD/GF"
                    ),
                    "小组第一公平比较明细": gw_cmp_log,
                    "小组第一入一档": [t.name for t in gw_top7],
                    "小组第一入二档": [t.name for t in gw_rest2],
                    "附加赛晋级(第四档)": [t.name for t in po_winners],
                    "分档": pot_names,
                    "分组": {
                        FINAL_GROUP_LABELS[i]: [t.name for t in g] for i, g in enumerate(groups)
                    },
                }
            )
            # 组内单循环 3 轮（4 队）
            per_group_rounds: List[List[List[Tuple[Team, Team]]]] = []
            for g in groups:
                per_group_rounds.append(round_robin_single_even(g, self.rng))
            n_r = len(per_group_rounds[0])
            max_rounds = max(max_rounds, n_r)
            rounds_m: List[List[Match]] = [[] for _ in range(n_r)]
            for gi, lab in enumerate(FINAL_GROUP_LABELS):
                plan = per_group_rounds[gi]
                comp = f"{code}-GS-{lab}"
                self._init_table(comp, groups[gi])
                self.league_schedule_by_confed[comp] = [
                    [(h.name, "vs", a.name, venue_caption(True, h.name)) for h, a in day]
                    for day in plan
                ]
                for r, day in enumerate(plan):
                    for home, away in day:
                        rounds_m[r].append(
                            Match(
                                comp=comp,
                                stage=f"正赛小组第{r+1}轮",
                                day=0,
                                round_num=r + 1,
                                home=home,
                                away=away,
                                kind="league",
                                neutral=True,
                            )
                        )
            cup_rounds[code] = rounds_m

        days: List[List[Match]] = []
        for r in range(max_rounds):
            day: List[Match] = []
            for code in CONTINENTAL_CODES:
                if r < len(cup_rounds[code]):
                    day.extend(cup_rounds[code][r])
            days.append(day)
        self.phase_matchdays = days

    def _begin_continental_knockout(self) -> None:
        self.phase_name = "洲际杯·正赛淘汰赛（16强→决赛）"
        self.phase_idx = 4
        self._cont_ko_sub = "R16"
        self.draw_log.append(
            {
                "type": "continental_knockout_start",
                "签表": [f"{a} vs {b}" for a, b in R16_PAIRINGS],
            }
        )
        self._build_continental_r16()

    def _cont_placements(self, code: str) -> Dict[str, Team]:
        groups = self._cont_finals_groups[code]
        out: Dict[str, Team] = {}
        for gi, lab in enumerate(FINAL_GROUP_LABELS):
            comp = f"{code}-GS-{lab}"
            tab = self._sorted_table(comp)
            out[f"{lab}1"] = self.team_map[tab[0][0]]
            out[f"{lab}2"] = self.team_map[tab[1][0]]
        return out

    def _build_continental_r16(self) -> None:
        day: List[Match] = []
        for code in CONTINENTAL_CODES:
            pl = self._cont_placements(code)
            for i, (a, b) in enumerate(traditional_r16_slots(pl), start=1):
                day.append(
                    Match(
                        comp=f"{code}-KO",
                        stage="1/8决赛",
                        day=0,
                        round_num=i,
                        home=a,
                        away=b,
                        kind="knockout",
                        neutral=True,
                    )
                )
        self.phase_matchdays = [day]

    def _cont_ko_winners(self, code: str, stage: str, n: int) -> List[Team]:
        ms = [
            m
            for m in self.all_results
            if m.comp == f"{code}-KO" and m.played and m.stage == stage
        ]
        ms.sort(key=lambda m: m.round_num)
        if len(ms) < n:
            raise RuntimeError(f"{code} {stage}: need {n} results, got {len(ms)}")
        out: List[Team] = []
        for m in ms[:n]:
            w = m.winner
            if w is None:
                w = m.home if m.hg > m.ag else m.away
            out.append(w)
        return out

    def _build_continental_qf(self) -> None:
        day: List[Match] = []
        for code in CONTINENTAL_CODES:
            winners = self._cont_ko_winners(code, "1/8决赛", 8)
            for qi, (i, j) in enumerate(QF_FROM_R16, start=1):
                day.append(
                    Match(
                        comp=f"{code}-KO",
                        stage="1/4决赛",
                        day=0,
                        round_num=qi,
                        home=winners[i],
                        away=winners[j],
                        kind="knockout",
                        neutral=True,
                    )
                )
        self.phase_matchdays = [day]

    def _build_continental_sf(self) -> None:
        day: List[Match] = []
        for code in CONTINENTAL_CODES:
            winners = self._cont_ko_winners(code, "1/4决赛", 4)
            for si, (i, j) in enumerate(SF_FROM_QF, start=1):
                day.append(
                    Match(
                        comp=f"{code}-KO",
                        stage="半决赛",
                        day=0,
                        round_num=si,
                        home=winners[i],
                        away=winners[j],
                        kind="knockout",
                        neutral=True,
                    )
                )
        self.phase_matchdays = [day]

    def _build_continental_final(self) -> None:
        day: List[Match] = []
        for code in CONTINENTAL_CODES:
            winners = self._cont_ko_winners(code, "半决赛", 2)
            day.append(
                Match(
                    comp=f"{code}-KO",
                    stage="决赛",
                    day=0,
                    round_num=1,
                    home=winners[0],
                    away=winners[1],
                    kind="knockout",
                    neutral=True,
                )
            )
        self.phase_matchdays = [day]

    def _cup_tournament_matches(self, code: str) -> List[Match]:
        out: List[Match] = []
        for m in self.all_results:
            if not m.played:
                continue
            if code == "WSC":
                if m.comp in ("WSC-PO", "WSC-KO"):
                    out.append(m)
                continue
            if m.comp == f"{code}-KO" or m.comp.startswith(f"{code}-GS-"):
                out.append(m)
                continue
            if m.comp == f"{code}-PO" and "24强附加赛" in (m.stage or ""):
                out.append(m)
        return out

    def _ko_match_loser(self, m: Match) -> Team:
        w = m.winner
        if w is None:
            hg, ag = m.hg, m.ag
            w = m.home if hg > ag else m.away
        return m.away if w.name == m.home.name else m.home

    def _accumulate_rt_stats(self, matches: Sequence[Match]) -> Dict[str, Dict[str, int]]:
        stats: Dict[str, Dict[str, int]] = {}
        for m in matches:
            hg, ag = match_regular_score(m)
            for name, gf, ga in ((m.home.name, hg, ag), (m.away.name, ag, hg)):
                row = stats.setdefault(name, {"PTS": 0, "GF": 0, "GA": 0, "GD": 0})
                if gf > ga:
                    row["PTS"] += 3
                elif gf == ga:
                    row["PTS"] += 1
                row["GF"] += gf
                row["GA"] += ga
                row["GD"] = row["GF"] - row["GA"]
        return stats

    def _gs_place_map(self, code: str, labels: Sequence[str]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for lab in labels:
            tab = self._sorted_table(f"{code}-GS-{lab}")
            for i, (name, _) in enumerate(tab, start=1):
                out[name] = i
        return out

    def _finalize_cup_ranking(self, code: str) -> List[Dict[str, Any]]:
        if code == "WSC":
            rows = self._rank_wsc()
        elif code == "CC":
            rows = self._rank_group_knockout_cup(code, CC_GROUP_LABELS, has_r16=False, has_24po=False)
        elif code in CONTINENTAL_CODES:
            rows = self._rank_group_knockout_cup(code, FINAL_GROUP_LABELS, has_r16=True, has_24po=False)
        else:
            rows = self._rank_group_knockout_cup(code, WCC_GROUP_LABELS, has_r16=True, has_24po=True)
        self._cup_final_rankings[code] = rows
        self.draw_log.append(
            {
                "type": "cup_final_ranking",
                "赛事": CUP_FINAL_RANK_LABELS.get(code, code),
                "code": code,
                "排名": [
                    {
                        "rank": r["rank"],
                        "team": r["team"],
                        "confed": r["confed"],
                        "exit": r["exit"],
                        "PTS": r["PTS"],
                        "GD": r["GD"],
                        "GF": r["GF"],
                        **({"via_playoff": r["via_playoff"]} if "via_playoff" in r else {}),
                    }
                    for r in rows
                ],
            }
        )
        return rows

    def _rank_group_knockout_cup(
        self,
        code: str,
        gs_labels: Sequence[str],
        *,
        has_r16: bool,
        has_24po: bool,
    ) -> List[Dict[str, Any]]:
        matches = self._cup_tournament_matches(code)
        stats = self._accumulate_rt_stats(matches)
        gs_pos = self._gs_place_map(code, gs_labels)
        teams = [self.team_map[n] for n in gs_pos]
        playoff_names: Set[str] = set()
        po_losers: Set[str] = set()
        if has_24po:
            for m in matches:
                if m.comp != f"{code}-PO":
                    continue
                playoff_names.add(m.home.name)
                playoff_names.add(m.away.name)
                po_losers.add(self._ko_match_loser(m).name)

        def losers_of(pred) -> Set[str]:
            names: Set[str] = set()
            for m in matches:
                if m.comp != f"{code}-KO":
                    continue
                if not pred(m.stage or ""):
                    continue
                names.add(self._ko_match_loser(m).name)
            return names

        champ = ru = ""
        for m in matches:
            if m.comp == f"{code}-KO" and (m.stage or "") == "决赛":
                w = m.winner.name if m.winner is not None else (m.home.name if m.hg > m.ag else m.away.name)
                champ = w
                ru = m.away.name if w == m.home.name else m.home.name
                break
        sf_l = losers_of(lambda st: "半决赛" in st)
        qf_l = losers_of(lambda st: "1/4" in st)
        r16_l = losers_of(lambda st: "1/8" in st) if has_r16 else set()

        # exit: smaller is better
        # 0 champ, 1 RU, 2 SF, 3 QF, 4 R16, [5 PO], GS 3rd/4th or 5th/6th
        recs: List[Dict[str, Any]] = []
        for t in teams:
            name = t.name
            if name == champ:
                exit_i, exit_lab = 0, "冠军"
            elif name == ru:
                exit_i, exit_lab = 1, "亚军"
            elif name in sf_l:
                exit_i, exit_lab = 2, "四强"
            elif name in qf_l:
                exit_i, exit_lab = 3, "八强"
            elif name in r16_l:
                exit_i, exit_lab = 4, "十六强"
            elif name in po_losers:
                exit_i, exit_lab = 5, "24强附加赛"
            else:
                pos = gs_pos.get(name, 99)
                if has_24po:
                    if pos <= 4:
                        exit_i, exit_lab = 5, "24强附加赛"
                    elif pos == 5:
                        exit_i, exit_lab = 6, "小组第五"
                    else:
                        exit_i, exit_lab = 7, "小组第六"
                else:
                    if pos <= 2:
                        exit_i, exit_lab = 4 if has_r16 else 3, "十六强" if has_r16 else "八强"
                    elif pos == 3:
                        exit_i, exit_lab = (5 if has_r16 else 4), "小组第三"
                    else:
                        exit_i, exit_lab = (6 if has_r16 else 5), "小组第四"
            via = name in playoff_names
            via_key = 1 if (has_24po and via and exit_i in (2, 3, 4)) else 0
            st = stats.get(name, {"PTS": 0, "GF": 0, "GA": 0, "GD": 0})
            recs.append(
                {
                    "team": name,
                    "confed": t.confed,
                    "exit": exit_lab,
                    "exit_i": exit_i,
                    "via_playoff": via if has_24po else False,
                    "via_key": via_key,
                    "PTS": st["PTS"],
                    "GD": st["GD"],
                    "GF": st["GF"],
                    "wr": int(self.live_ranks.get(name, t.world_rank)),
                    "gs_pos": gs_pos.get(name, 99),
                }
            )
        recs.sort(key=lambda r: (r["exit_i"], r["via_key"], -r["PTS"], -r["GD"], -r["GF"], r["wr"], r["team"]))
        out: List[Dict[str, Any]] = []
        for i, r in enumerate(recs, start=1):
            row = {
                "rank": i,
                "team": r["team"],
                "confed": r["confed"],
                "exit": r["exit"],
                "PTS": r["PTS"],
                "GD": r["GD"],
                "GF": r["GF"],
                "GA": stats.get(r["team"], {}).get("GA", 0),
            }
            if has_24po:
                row["via_playoff"] = r["via_playoff"]
            out.append(row)
        return out

    def _rank_wsc(self) -> List[Dict[str, Any]]:
        matches = self._cup_tournament_matches("WSC")
        stats = self._accumulate_rt_stats(matches)
        s = self._wsc_slots or {}
        teams: List[Team] = []
        seen: Set[str] = set()
        for key in (
            "wc_champion",
            "wc_runner_up",
            "po1_upper_home",
            "po1_upper_away",
            "po1_lower_home",
            "po1_lower_away",
            "po2_upper_home",
            "po2_lower_home",
        ):
            t = s.get(key)
            if isinstance(t, Team) and t.name not in seen:
                seen.add(t.name)
                teams.append(t)
        if len(teams) < 8:
            for m in matches:
                for t in (m.home, m.away):
                    if t.name not in seen:
                        seen.add(t.name)
                        teams.append(t)

        def losers_stage(pred) -> Set[str]:
            names: Set[str] = set()
            for m in matches:
                if pred(m.stage or ""):
                    names.add(self._ko_match_loser(m).name)
            return names

        champ = ru = ""
        for m in matches:
            if (m.stage or "") == "决赛":
                w = m.winner.name if m.winner is not None else (m.home.name if m.hg > m.ag else m.away.name)
                champ = w
                ru = m.away.name if w == m.home.name else m.home.name
        sf_l = losers_stage(lambda st: st.startswith("半决赛"))
        po2_l = losers_stage(lambda st: "附加赛第二轮" in st)
        po1_l = losers_stage(lambda st: "附加赛第一轮" in st)
        recs: List[Dict[str, Any]] = []
        for t in teams:
            name = t.name
            if name == champ:
                exit_i, exit_lab = 0, "冠军"
            elif name == ru:
                exit_i, exit_lab = 1, "亚军"
            elif name in sf_l:
                exit_i, exit_lab = 2, "四强"
            elif name in po2_l:
                exit_i, exit_lab = 3, "附加赛第二轮"
            else:
                exit_i, exit_lab = 4, "附加赛第一轮"
            st = stats.get(name, {"PTS": 0, "GF": 0, "GA": 0, "GD": 0})
            recs.append(
                {
                    "team": name,
                    "confed": t.confed,
                    "exit": exit_lab,
                    "exit_i": exit_i,
                    "PTS": st["PTS"],
                    "GD": st["GD"],
                    "GF": st["GF"],
                    "wr": int(self.live_ranks.get(name, t.world_rank)),
                }
            )
        recs.sort(key=lambda r: (r["exit_i"], -r["PTS"], -r["GD"], -r["GF"], r["wr"], r["team"]))
        return [
            {
                "rank": i,
                "team": r["team"],
                "confed": r["confed"],
                "exit": r["exit"],
                "PTS": r["PTS"],
                "GD": r["GD"],
                "GF": r["GF"],
            }
            for i, r in enumerate(recs, start=1)
        ]

    def _record_continental_champions(self) -> None:
        for m in self._last_day_matches:
            if not m.comp.endswith("-KO") or m.stage != "决赛" or not m.played:
                continue
            code = m.comp.replace("-KO", "")
            w = m.winner
            if w is None:
                w = m.home if m.hg > m.ag else m.away
            self.continental_champions[code] = w.name
        self.draw_log.append(
            {
                "type": "continental_champions",
                "冠军": {
                    CONTINENTAL_LABELS.get(k, k): v for k, v in self.continental_champions.items()
                },
            }
        )
        for code in CONTINENTAL_CODES:
            self._finalize_cup_ranking(code)

    def _continental_knockout_advance(self) -> bool:
        if self._cont_ko_sub == "R16":
            self._cont_ko_sub = "QF"
            self._build_continental_qf()
            return True
        if self._cont_ko_sub == "QF":
            self._cont_ko_sub = "SF"
            self._build_continental_sf()
            return True
        if self._cont_ko_sub == "SF":
            self._cont_ko_sub = "F"
            self._build_continental_final()
            return True
        if self._cont_ko_sub == "F":
            self._record_continental_champions()
            self._cont_ko_sub = "done"
            return False
        return False

    # ---------------- 联合会杯（Confederations Cup） ----------------

    def _continental_semifinalists(self, code: str) -> List[Team]:
        """某洲际杯的四强：半决赛两场的参赛队（按场次顺序取 主队/客队）。"""
        ms = [
            m
            for m in self.all_results
            if m.comp == f"{code}-KO" and m.played and m.stage == "半决赛"
        ]
        ms.sort(key=lambda m: m.round_num)
        if len(ms) != 2:
            raise RuntimeError(f"{code} 半决赛场次={len(ms)}，应为 2")
        out: List[Team] = []
        for m in ms:
            out.extend([m.home, m.away])
        return out

    def _start_confederations_cup(self) -> None:
        self.phase_name = "联合会杯·小组赛（4 组 × 4，前二进八强）"
        pots = [self._continental_semifinalists(code) for code in CC_POT_CUP_ORDER]
        self._cc_pots = pots
        groups: List[List[Team]] = [[] for _ in CC_GROUP_LABELS]
        for pot in pots:
            perm = pot[:]
            self.rng.shuffle(perm)
            for gi, t in enumerate(perm):
                groups[gi].append(t)
        self._cc_groups = groups
        self._cup_draw_groups["CC"] = groups
        self.draw_log.append(
            {
                "type": "confederations_cup_draw",
                "赛事": "联合会杯",
                "说明_参赛": "四大洲杯四强（各杯半决赛参赛队）共 16 队",
                "说明_分档": "一档=欧洲杯四强；二档=美洲杯四强；三档=非洲杯四强；四档=亚太杯四强；每档抽一队落入 A–D 组",
                "说明_赛制": "小组单循环 3 轮（中立场）；各组前二进八强，随后 1/4决赛→半决赛→决赛（单场决胜，中立场）",
                "说明_积分": "小组赛正常结算国际积分；淘汰赛只加不扣（败方不扣分）",
                "分档": {
                    f"{i + 1}档（{CONTINENTAL_LABELS[code]}四强）": [t.name for t in pot]
                    for i, (code, pot) in enumerate(zip(CC_POT_CUP_ORDER, pots))
                },
                "分组": {lab: [t.name for t in g] for lab, g in zip(CC_GROUP_LABELS, groups)},
            }
        )
        per_group_rounds = [round_robin_single_even(g, self.rng) for g in groups]
        n_r = len(per_group_rounds[0])
        days: List[List[Match]] = [[] for _ in range(n_r)]
        for gi, lab in enumerate(CC_GROUP_LABELS):
            comp = f"CC-GS-{lab}"
            self._init_table(comp, groups[gi])
            self.league_schedule_by_confed[comp] = [
                [(h.name, "vs", a.name, venue_caption(True, h.name)) for h, a in day]
                for day in per_group_rounds[gi]
            ]
            for r, day in enumerate(per_group_rounds[gi]):
                for home, away in day:
                    days[r].append(
                        Match(
                            comp=comp,
                            stage=f"小组第{r + 1}轮",
                            day=0,
                            round_num=r + 1,
                            home=home,
                            away=away,
                            kind="league",
                            neutral=True,
                        )
                    )
        self.phase_matchdays = days

    def _cc_placements(self) -> Dict[str, Team]:
        out: Dict[str, Team] = {}
        for lab in CC_GROUP_LABELS:
            tab = self._sorted_table(f"CC-GS-{lab}")
            if len(tab) < 2:
                raise RuntimeError(f"CC-GS-{lab}: 积分榜不完整")
            out[f"{lab}1"] = self.team_map[tab[0][0]]
            out[f"{lab}2"] = self.team_map[tab[1][0]]
        return out

    def _begin_cc_knockout(self) -> None:
        self.phase_name = "联合会杯·淘汰赛（八强→决赛）"
        self._cc_ko_sub = "QF"
        self.draw_log.append(
            {
                "type": "confederations_cup_knockout_start",
                "签表": [f"{a} vs {b}" for a, b in CC_QF_PAIRINGS],
                "说明": "八强交叉：A1-B2、C1-D2、B1-A2、D1-C2；半决赛 QF1-QF2、QF3-QF4",
            }
        )
        self._build_cc_qf()

    def _build_cc_qf(self) -> None:
        pl = self._cc_placements()
        day: List[Match] = [
            Match(
                comp="CC-KO",
                stage="1/4决赛",
                day=0,
                round_num=i,
                home=pl[a],
                away=pl[b],
                kind="knockout",
                neutral=True,
            )
            for i, (a, b) in enumerate(CC_QF_PAIRINGS, start=1)
        ]
        self.phase_matchdays = [day]

    def _build_cc_sf(self) -> None:
        winners = self._cont_ko_winners("CC", "1/4决赛", 4)
        day: List[Match] = [
            Match(
                comp="CC-KO",
                stage="半决赛",
                day=0,
                round_num=si,
                home=winners[i],
                away=winners[j],
                kind="knockout",
                neutral=True,
            )
            for si, (i, j) in enumerate(SF_FROM_QF, start=1)
        ]
        self.phase_matchdays = [day]

    def _build_cc_final(self) -> None:
        winners = self._cont_ko_winners("CC", "半决赛", 2)
        self.phase_matchdays = [
            [
                Match(
                    comp="CC-KO",
                    stage="决赛",
                    day=0,
                    round_num=1,
                    home=winners[0],
                    away=winners[1],
                    kind="knockout",
                    neutral=True,
                )
            ]
        ]

    def _record_cc_champion(self) -> None:
        for m in self._last_day_matches:
            if m.comp != "CC-KO" or m.stage != "决赛" or not m.played:
                continue
            w = m.winner
            if w is None:
                w = m.home if m.hg > m.ag else m.away
            self.cc_champion = w.name
        self.draw_log.append({"type": "confederations_cup_champion", "冠军": self.cc_champion})
        self._finalize_cup_ranking("CC")

    def _cc_knockout_advance(self) -> bool:
        if self._cc_ko_sub == "QF":
            self._cc_ko_sub = "SF"
            self._build_cc_sf()
            return True
        if self._cc_ko_sub == "SF":
            self._cc_ko_sub = "F"
            self._build_cc_final()
            return True
        if self._cc_ko_sub == "F":
            self._record_cc_champion()
            self._cc_ko_sub = "done"
            return False
        return False

    def _update_cycle_ranks_after_continental(self) -> None:
        """Part A 洲际杯结束后：固化排名库并刷新 OVR；国际积分原样带入 Part B，不按名次重算。"""
        new_ranks = dict(self.live_ranks)
        save_world_ranks(
            new_ranks,
            comment="Part A（洲际杯）期间逐轮更新的国际积分所对应排名；开局从 team_world_ranks_original.json 回溯。",
        )
        self._apply_rank_map(new_ranks)
        # 保留洲际杯结束时的真实 fifa_points / live_ranks，供 Part B 继续累加
        self._rank_source = "cycle"
        self.draw_log.append(
            {
                "type": "world_ranks_updated",
                "说明": "已写入 team_world_ranks_cycle.json；Part B 沿用洲际杯结束时的国际积分（未重置）",
                "样本前10": [n for n, _ in sorted(new_ranks.items(), key=lambda kv: kv[1])[:10]],
            }
        )

    def _start_world_cup_cycle(self) -> None:
        self._update_cycle_ranks_after_continental()
        self.cycle_part = "B"
        self.draw_log.append({"type": "cycle_part_b_start", "说明": "进入世界杯周期（规则同原模拟器）"})
        self._bootstrap_prelim_and_queue()

    def _two_leg_winner(self, tie_id: str, a: Team, b: Team) -> Team:
        legs = [m for m in self.all_results if m.tie_id == tie_id and m.played]
        if len(legs) < 2:
            raise RuntimeError(f"两回合未赛完: {tie_id}")
        legs.sort(key=lambda m: m.round_num)
        # 以第二回合的 winner 为准（已含加时/点球）
        m2 = legs[1]
        if m2.winner is not None:
            return m2.winner
        g_a = g_b = 0
        for m in legs:
            if m.home.name == a.name:
                g_a += m.hg
                g_b += m.ag
            else:
                g_a += m.ag
                g_b += m.hg
        if g_a != g_b:
            return a if g_a > g_b else b
        return a if a.world_rank < b.world_rank else b

    def _bootstrap_prelim_and_queue(self) -> None:
        self.phase_idx = 0
        self._prelim_pairs_meta = {}
        self._prelim_state = {}
        self._prelim_live_round = 1
        for confed in CONFEDS:
            state = self._draw_preliminary(confed)
            self._prelim_state[confed] = state
            self._prelim_pairs_meta[confed] = {
                "直接晋级": list(state["direct"]),
                "ties": [],
            }
        self.phase_name = self._prelim_phase_name(1)
        r1 = self._prelim_matches_for_round(1)
        self.phase_matchdays = [r1] if r1 else []

    def _confed_teams(self, confed: str) -> List[Team]:
        return [t for t in self.teams if t.confed == confed]

    @staticmethod
    def _prelim_slot_team(team: Team) -> Dict[str, Any]:
        return {"type": "team", "name": team.name}

    @staticmethod
    def _prelim_slot_ref(from_round: int, tie: int, result: str) -> Dict[str, Any]:
        return {"type": "ref", "from_round": from_round, "tie": tie, "result": result}

    @staticmethod
    def prelim_slot_label(slot: Dict[str, Any]) -> str:
        if not slot:
            return "—"
        if slot.get("type") == "team":
            return str(slot.get("name") or "—")
        rnd = slot.get("from_round", "?")
        tie = slot.get("tie", "?")
        result = "胜者" if slot.get("result") == "winner" else "败者"
        return f"第{rnd}轮第{tie}场{result}"

    def prelim_filled_label(self, confed: str, slot: Dict[str, Any]) -> str:
        base = self.prelim_slot_label(slot)
        if not slot or slot.get("type") == "team":
            return base
        rec = (self._prelim_state.get(confed) or {}).get("resolved", {}).get(
            (int(slot.get("from_round") or 0), int(slot.get("tie") or 0))
        )
        if not rec:
            return base
        name = rec["winner"] if slot.get("result") == "winner" else rec["loser"]
        return f"{base}（{name}）"

    def _prelim_phase_name(self, rnd: int) -> str:
        return f"世界杯周期·第一阶段：洲内附加赛第{rnd}轮"

    def _prelim_stage_label(self, rnd: int, path: str) -> str:
        if path == "caf_1v2":
            return f"附加赛第{rnd}轮·1档vs2档"
        if path == "caf_3v4":
            return f"附加赛第{rnd}轮·3档vs4档"
        if path == "caf_r2":
            return f"附加赛第{rnd}轮"
        return f"附加赛第{rnd}轮"

    def _rank_pick(self, ordered: Sequence[Team], *ranks: int) -> List[Team]:
        return [ordered[r - 1] for r in ranks]

    def _leftover_pick(self, leftover: Sequence[Team], direct_n: int, *ranks: int) -> List[Team]:
        return [leftover[r - direct_n - 1] for r in ranks]

    def _select_prelim_direct(self, confed: str, teams: Sequence[Team]) -> Tuple[List[Team], List[str], List[str]]:
        """返回 (直通队, 杯赛直通名, 世界排名补位名)。"""
        quota = PRELIM_DIRECT_N[confed]
        if confed == "CONMEBOL":
            names = [t.name for t in teams]
            return list(teams), names, []
        if confed == "OFC":
            direct = list(teams[:quota])
            apac = {
                r["team"]
                for r in (self._cup_final_rankings.get("APAC") or [])
                if r.get("confed") == "OFC"
            }
            cup = [t.name for t in direct if t.name in apac]
            fill = [t.name for t in direct if t.name not in apac]
            return direct, cup, fill
        cup_code = CONFED_CUP_FOR_BYE[confed]
        ranking = self._cup_final_rankings.get(cup_code) or []
        cup_names = [r["team"] for r in ranking if r.get("confed") == confed]
        if len(cup_names) >= quota:
            chosen = cup_names[:quota]
            fill: List[str] = []
        else:
            chosen = list(cup_names)
            have = set(chosen)
            rest = [t for t in teams if t.name not in have]
            need = quota - len(chosen)
            fill = [t.name for t in rest[:need]]
            chosen = chosen + fill
        by_name = {t.name: t for t in teams}
        direct = [by_name[n] for n in chosen if n in by_name]
        cup_set = set(cup_names)
        direct_cup = [n for n in chosen if n in cup_set]
        direct_fill = [n for n in chosen if n not in cup_set]
        return direct, direct_cup, direct_fill

    def _pair_prelim_pots(
        self,
        pot1: Sequence[Dict[str, Any]],
        pot2: Sequence[Dict[str, Any]],
        *,
        path: str,
    ) -> List[Dict[str, Any]]:
        if len(pot1) != len(pot2):
            raise RuntimeError(f"附加赛档位人数不对齐: {len(pot1)} vs {len(pot2)}")
        away = list(pot2)
        self.rng.shuffle(away)
        ties: List[Dict[str, Any]] = []
        for i, (home, aw) in enumerate(zip(pot1, away), 1):
            ties.append({"序号": i, "home": home, "away": aw, "path": path})
        return ties

    def _draw_preliminary(self, confed: str) -> Dict[str, Any]:
        teams = sorted(self._confed_teams(confed), key=lambda t: t.world_rank)
        n = len(teams)
        expect_n = {
            "UEFA": 55,
            "AFC": 47,
            "CONCACAF": 41,
            "CAF": 54,
            "OFC": 13,
            "CONMEBOL": 10,
        }.get(confed)
        if expect_n is not None and n != expect_n:
            raise RuntimeError(f"{confed} 应为 {expect_n} 队，实际 {n}")

        if confed == "CONMEBOL":
            direct, cup, fill = self._select_prelim_direct(confed, teams)
            state = {
                "confed": confed,
                "direct": [t.name for t in direct],
                "direct_cup": cup,
                "direct_fill": fill,
                "league": [t.name for t in direct],
                "wcc": [],
                "rounds": {},
                "max_round": 0,
                "resolved": {},
                "rank_snapshot": [t.name for t in teams],
            }
            self.draw_log.append({"type": "prelim_draw", "payload": self._prelim_draw_payload(state, teams)})
            return state

        direct, direct_cup, direct_fill = self._select_prelim_direct(confed, teams)
        leftover = [t for t in teams if t.name not in {x.name for x in direct}]
        leftover_n = len(leftover)
        expect_left = {
            "AFC": 16,
            "CONCACAF": 16,
            "UEFA": 11,
            "CAF": 12,
            "OFC": 2,
        }.get(confed)
        if expect_left is not None and leftover_n != expect_left:
            raise RuntimeError(f"{confed} 附加赛池应为 {expect_left} 队，实际 {leftover_n}")
        dn = PRELIM_DIRECT_N[confed]
        pick = lambda *ranks: self._leftover_pick(leftover, dn, *ranks)

        slot = self._prelim_slot_team
        ref = self._prelim_slot_ref
        rounds: Dict[int, List[Dict[str, Any]]] = {}
        if confed == "AFC":
            r1 = self._pair_prelim_pots(
                [slot(t) for t in pick(45, 44)],
                [slot(t) for t in pick(47, 46)],
                path="ladder",
            )
            r2 = self._pair_prelim_pots(
                [slot(t) for t in pick(41, 40, 39, 38)],
                [slot(t) for t in pick(43, 42)]
                + [ref(1, 1, "winner"), ref(1, 2, "winner")],
                path="ladder",
            )
            r3 = self._pair_prelim_pots(
                [slot(t) for t in pick(36, 35, 34, 33, 32)],
                [slot(t) for t in pick(37)]
                + [ref(2, i, "winner") for i in range(1, 5)],
                path="ladder",
            )
            rounds = {1: r1, 2: r2, 3: r3}
        elif confed == "CONCACAF":
            r1 = self._pair_prelim_pots(
                [slot(t) for t in pick(39, 38)],
                [slot(t) for t in pick(41, 40)],
                path="ladder",
            )
            r2 = self._pair_prelim_pots(
                [slot(t) for t in pick(35, 34, 33, 32)],
                [slot(t) for t in pick(37, 36)]
                + [ref(1, 1, "winner"), ref(1, 2, "winner")],
                path="ladder",
            )
            r3 = self._pair_prelim_pots(
                [slot(t) for t in pick(30, 29, 28, 27, 26)],
                [slot(t) for t in pick(31)]
                + [ref(2, i, "winner") for i in range(1, 5)],
                path="ladder",
            )
            rounds = {1: r1, 2: r2, 3: r3}
        elif confed == "UEFA":
            r1 = self._pair_prelim_pots(
                [slot(t) for t in pick(52, 51, 50)],
                [slot(t) for t in pick(55, 54, 53)],
                path="ladder",
            )
            r2 = self._pair_prelim_pots(
                [slot(t) for t in pick(48, 47, 46, 45)],
                [slot(t) for t in pick(49)]
                + [ref(1, i, "winner") for i in range(1, 4)],
                path="ladder",
            )
            rounds = {1: r1, 2: r2}
        elif confed == "CAF":
            r1_12 = self._pair_prelim_pots(
                [slot(t) for t in pick(45, 44, 43)],
                [slot(t) for t in pick(48, 47, 46)],
                path="caf_1v2",
            )
            r1_34 = self._pair_prelim_pots(
                [slot(t) for t in pick(51, 50, 49)],
                [slot(t) for t in pick(54, 53, 52)],
                path="caf_3v4",
            )
            for t in r1_34:
                t["序号"] += 3
            r1 = r1_12 + r1_34
            r2_homes = [ref(1, i, "loser") for i in range(1, 4)]
            r2_aways = [ref(1, i, "winner") for i in range(4, 7)]
            r2 = self._pair_prelim_pots(r2_homes, r2_aways, path="caf_r2")
            rounds = {1: r1, 2: r2}
        elif confed == "OFC":
            r1 = self._pair_prelim_pots(
                [slot(leftover[0])],
                [slot(leftover[1])],
                path="ladder",
            )
            rounds = {1: r1}
        else:
            raise RuntimeError(f"未知大洲附加赛: {confed}")

        state = {
            "confed": confed,
            "direct": [t.name for t in direct],
            "direct_cup": list(direct_cup),
            "direct_fill": list(direct_fill),
            "league": [t.name for t in direct],
            "wcc": [],
            "rounds": rounds,
            "max_round": max(rounds) if rounds else 0,
            "resolved": {},
            "rank_snapshot": [t.name for t in teams],
        }
        self.draw_log.append({"type": "prelim_draw", "payload": self._prelim_draw_payload(state, teams)})
        return state

    def _prelim_draw_payload(self, state: Dict[str, Any], teams: Sequence[Team]) -> Dict[str, Any]:
        bracket: List[Dict[str, Any]] = []
        for rnd in sorted(state.get("rounds") or {}):
            for tie in state["rounds"][rnd]:
                bracket.append(
                    {
                        "轮次": rnd,
                        "场次": tie["序号"],
                        "路径": tie.get("path", "ladder"),
                        "主队": self.prelim_slot_label(tie["home"]),
                        "客队": self.prelim_slot_label(tie["away"]),
                        "说明": "一档主场" if tie.get("path") != "caf_r2" else "1档vs2档败者主场",
                    }
                )
        notes = {
            "AFC": "直通优先亚太杯亚洲队（超额按正赛排名取 31）；剩余 16 队打三轮阶梯。",
            "CONCACAF": "直通优先美洲杯 CONCACAF 队（超额按正赛排名取 25）；剩余 16 队打三轮阶梯。",
            "UEFA": "直通优先欧洲杯球队，不足按世界排名补至 44；剩余 11 队打两轮阶梯。",
            "CAF": "直通优先非洲杯球队，不足按世界排名补至 42；剩余 12 队走双路径附加赛。",
            "OFC": "直通仍为世界排名前 11；第 12 名主场对第 13 名。",
            "CONMEBOL": "10 队全部直通联赛，无洲内附加赛。",
        }
        return {
            "confed": state["confed"],
            "说明": "开赛一次分档抽签，写满全部轮次对阵；后轮只填占位。直通按洲际杯正赛席，不足世界排名补。",
            "规则": notes.get(state["confed"], ""),
            "开赛洲内排名": [t.name for t in teams],
            "直接晋级": list(state["direct"]),
            "直通·洲际杯": list(state.get("direct_cup") or []),
            "直通·排名补位": list(state.get("direct_fill") or []),
            "对阵表": bracket,
        }

    def _resolve_prelim_slot(self, confed: str, slot: Dict[str, Any]) -> Team:
        if slot.get("type") == "team":
            return self.team_map[slot["name"]]
        key = (int(slot["from_round"]), int(slot["tie"]))
        rec = self._prelim_state[confed]["resolved"].get(key)
        if not rec:
            raise RuntimeError(f"{confed} 附加赛占位未填: 第{slot.get('from_round')}轮第{slot.get('tie')}场")
        name = rec["winner"] if slot.get("result") == "winner" else rec["loser"]
        return self.team_map[name]

    def _prelim_tie_id(self, confed: str, rnd: int, seq: int) -> str:
        return f"{confed}-PRE-R{rnd}-{seq}"

    def _prelim_matches_for_round(self, rnd: int) -> List[Match]:
        matches: List[Match] = []
        for confed in CONFEDS:
            state = self._prelim_state.get(confed) or {}
            for tie in (state.get("rounds") or {}).get(rnd, []):
                home = self._resolve_prelim_slot(confed, tie["home"])
                away = self._resolve_prelim_slot(confed, tie["away"])
                matches.append(
                    Match(
                        comp=f"{confed}-PRE",
                        stage=self._prelim_stage_label(rnd, tie.get("path", "ladder")),
                        day=0,
                        round_num=rnd,
                        home=home,
                        away=away,
                        kind="knockout",
                        tie_id=self._prelim_tie_id(confed, rnd, int(tie["序号"])),
                    )
                )
        return matches

    def _find_prelim_match(self, confed: str, rnd: int, seq: int) -> Match:
        tid = self._prelim_tie_id(confed, rnd, seq)
        for m in self.all_results:
            if m.tie_id == tid and m.played:
                return m
        raise RuntimeError(f"缺少附加赛结果: {tid}")

    def _apply_prelim_round_results(self, rnd: int) -> None:
        for confed in CONFEDS:
            state = self._prelim_state.get(confed) or {}
            ties = (state.get("rounds") or {}).get(rnd, [])
            if not ties:
                continue
            last = rnd >= int(state.get("max_round") or 0)
            for tie in ties:
                seq = int(tie["序号"])
                home = self._resolve_prelim_slot(confed, tie["home"])
                away = self._resolve_prelim_slot(confed, tie["away"])
                found = self._find_prelim_match(confed, rnd, seq)
                winner = self._prelim_match_winner_team(found, home, away)
                loser = away if winner.name == home.name else home
                state["resolved"][(rnd, seq)] = {"winner": winner.name, "loser": loser.name}
                path = tie.get("path", "ladder")
                if path == "caf_1v2":
                    if winner.name not in state["league"]:
                        state["league"].append(winner.name)
                elif path == "caf_3v4":
                    if loser.name not in state["wcc"]:
                        state["wcc"].append(loser.name)
                elif path == "caf_r2" or last:
                    if winner.name not in state["league"]:
                        state["league"].append(winner.name)
                    if loser.name not in state["wcc"]:
                        state["wcc"].append(loser.name)
                else:
                    if loser.name not in state["wcc"]:
                        state["wcc"].append(loser.name)

    def _prelim_advance(self) -> bool:
        played = int(self._prelim_live_round or 1)
        self._apply_prelim_round_results(played)
        nxt = played + 1
        has_next = any(
            nxt in (self._prelim_state.get(c) or {}).get("rounds", {})
            for c in CONFEDS
        )
        if not has_next:
            return False
        self._prelim_live_round = nxt
        nxt_matches = self._prelim_matches_for_round(nxt)
        if not nxt_matches:
            return False
        self.phase_matchdays.append(nxt_matches)
        self.phase_name = self._prelim_phase_name(nxt)
        return True

    def _prelim_match_winner_team(self, m: Match, a: Team, b: Team) -> Team:
        if m.winner is not None:
            return m.winner
        if m.hg > m.ag:
            return m.home
        if m.ag > m.hg:
            return m.away
        return a if self.rng.random() < _p_win(a, b, 0.0) else b

    def _collect_prelim_winners(self) -> None:
        winners_by_confed: Dict[str, List[Team]] = {}
        wcc_losers: List[Team] = []
        for confed in CONFEDS:
            state = self._prelim_state.get(confed)
            if not state:
                raise RuntimeError(f"缺少附加赛状态: {confed}")
            names = list(dict.fromkeys(state["league"]))
            winners = sorted(
                (self.team_map[nm] for nm in names if nm in self.team_map),
                key=lambda t: t.world_rank,
            )
            exp = PRELIM_LEAGUE_N.get(confed)
            if exp is not None and len(winners) != exp:
                raise RuntimeError(
                    f"{confed} 预选晋级应为 {exp} 队，实际 {len(winners)} "
                    f"（直通 {len(state['direct'])} + 附加赛晋级）"
                )
            winners_by_confed[confed] = winners
            for nm in state["wcc"]:
                if nm in self.team_map:
                    wcc_losers.append(self.team_map[nm])
        self._wcc_prelim_losers = sorted(wcc_losers, key=lambda t: t.world_rank)
        if len(self._wcc_prelim_losers) != 36:
            raise RuntimeError(f"挑战者杯入队应为 36，实际 {len(self._wcc_prelim_losers)}")
        self._build_league_after_prelim(winners_by_confed)

    def _build_league_after_prelim(self, winners_by_confed: Dict[str, List[Team]]) -> None:
        self.league_schedule_by_confed = {}
        self.league_play_plan = {}
        self.league_opponents_by_comp = {}

        specs = [
            ("UEFA", 6, "UEFA-QUAL", True),
            ("AFC", 6, "AFC-QUAL", True),
            ("CONCACAF", 6, "CONCACAF-QUAL", True),
            ("CAF", 6, "CAF-QUAL", True),
            ("OFC", 4, "OFC-QUAL", False),
        ]

        for confed, n_pots, comp_label, use_standard in specs:
            league_teams = winners_by_confed[confed]
            stt = self._prelim_state.get(confed) or {}
            cup_names = set(stt.get("direct_cup") or [])
            fill_names = set(stt.get("direct_fill") or [])
            direct_names = set(stt.get("direct") or [])
            by_name = {t.name: t for t in league_teams}
            band_cup = [by_name[n] for n in cup_names if n in by_name]
            band_fill = [by_name[n] for n in fill_names if n in by_name]
            band_po = [t for t in league_teams if t.name not in direct_names]
            pots = split_into_pots_banded([band_cup, band_fill, band_po], n_pots)
            pot_names = [[t.name for t in pot] for pot in pots]
            self.draw_log.append(
                {
                    "type": "league_pots",
                    "赛事": comp_label,
                    "大洲": confed,
                    "分档说明": "先洲际杯正赛直通队、再世界排名补位直通队、最后附加赛晋级队；各带内按世界排名入档",
                    "直通·洲际杯": [t.name for t in band_cup],
                    "直通·排名补位": [t.name for t in band_fill],
                    "附加赛晋级": [t.name for t in band_po],
                    "pots": pot_names,
                }
            )

            if use_standard:
                edges, draw_steps, opp_by_team = simulate_uefa_style_league_draw(pots, self.rng)
                self.draw_log.append(
                    {
                        "type": "league_pairing_draw",
                        "赛事": comp_label,
                        "规则说明": "自六档至一档、档内按世界排名从低到高依次抽签；每档抽满 2 个不同对手；"
                        "跨档时若某队与「当前抽签档」已配对 2 次则不可再抽中。",
                        "抽签步数": len(draw_steps),
                        "抽签过程": draw_steps,
                        "各队对手按档": opp_by_team,
                    }
                )
                self.league_opponents_by_comp[comp_label] = opp_by_team
            else:
                edges, draw_steps, opp_by_team = simulate_uefa_style_league_draw(pots, self.rng)
                self.draw_log.append(
                    {
                        "type": "league_pairing_draw",
                        "赛事": comp_label,
                        "规则说明": "大洋洲 4 档联赛：与洲内其它联赛相同的模拟抽签（每档 2 对手，共 8 场），自四档至一档、档内弱队先抽。",
                        "抽签步数": len(draw_steps),
                        "抽签过程": draw_steps,
                        "各队对手按档": opp_by_team,
                    }
                )
                self.league_opponents_by_comp[comp_label] = opp_by_team
            # 每档各 2 个对手：6 档 -> 12 场；大洋洲 4 档 -> 8 场
            deg = n_pots * 2 if use_standard else 8
            _verify_regular(edges, league_teams, deg)

            sched = assign_rounds_auto(edges, deg, self.rng)
            if sched is None:
                raise RuntimeError(f"{comp_label} 无法分配轮次（请 pip install ortools 或更换种子）")

            oriented = assign_balanced_home_away(pots, edges, self.rng)
            rounds_fixtures: List[List[Tuple[Team, Team]]] = []
            display_rows: List[List[Tuple[str, str, str, str]]] = []
            for r in range(deg):
                rnd_pairs: List[Tuple[Team, Team]] = []
                row_disp: List[Tuple[str, str, str, str]] = []
                for mi in sched[r]:
                    home, away = oriented[mi]
                    rnd_pairs.append((home, away))
                    row_disp.append((home.name, "vs", away.name, venue_caption(False, home.name)))
                rounds_fixtures.append(rnd_pairs)
                display_rows.append(row_disp)

            self.league_play_plan[comp_label] = rounds_fixtures
            self.league_schedule_by_confed[comp_label] = display_rows
            self._init_table(comp_label, league_teams)

            self.draw_log.append(
                {
                    "type": "league_schedule_ready",
                    "赛事": comp_label,
                    "总轮次": deg,
                    "总场次": len(edges),
                    "每队场次": deg,
                    "主客场规则": "每队对每一档的 2 个对手一主一客（现行欧冠/欧联/欧协联规则）",
                }
            )

        cmb = round_robin_double(sorted(winners_by_confed["CONMEBOL"], key=lambda t: t.world_rank), self.rng)
        self.league_play_plan["CONMEBOL-QUAL"] = cmb
        cmb_teams = sorted(winners_by_confed["CONMEBOL"], key=lambda t: t.world_rank)
        self._init_table("CONMEBOL-QUAL", cmb_teams)
        self.league_schedule_by_confed["CONMEBOL-QUAL"] = [
            [(h.name, "vs", a.name, f"主场 {h.name}") for h, a in day] for day in cmb
        ]
        self.draw_log.append({"type": "league_schedule_ready", "赛事": "CONMEBOL-QUAL", "总轮次": len(cmb), "说明": "主客场双循环"})

        long_dates = league_dates_for_rounds(LEAGUE_LONG_ROUNDS, self.cycle_start_year)
        all_days: List[List[Match]] = []
        league_specs = list(specs) + [("CONMEBOL", 0, "CONMEBOL-QUAL", False)]
        for d in long_dates:
            day_list: List[Match] = []
            for _confed, _n_pots, comp_label, _use_standard in league_specs:
                plan = self.league_play_plan.get(comp_label)
                if not plan:
                    continue
                try:
                    dates = league_dates_for_rounds(len(plan), self.cycle_start_year)
                except KeyError:
                    continue
                if d not in dates:
                    continue
                r = dates.index(d)
                for home, away in plan[r]:
                    day_list.append(
                        Match(
                            comp=comp_label,
                            stage=f"联赛第{r + 1}轮",
                            day=0,
                            round_num=r + 1,
                            home=home,
                            away=away,
                            kind="league",
                            neutral=False,
                        )
                    )
            all_days.append(day_list)

        # 世界挑战者杯：前 5 个长日历比赛日与小组赛同步；之后附加赛/淘汰赛按轮次注入
        self._p1_days_completed = 0
        self._wcc_inject_flags = {k: False for k in ("po", "r16", "qf", "sf", "fin")}
        self._wcc_draw_groups = []
        self.wcc_champion = ""
        wcc_gs = self._wcc_build_group_stage_matches(self._wcc_prelim_losers)
        for r in range(min(5, len(all_days))):
            all_days[r].extend(wcc_gs[r])

        self._league_tail = all_days[LEAGUE_SPLIT_AFTER:]
        self._set_matchdays(all_days[:LEAGUE_SPLIT_AFTER])
        self.phase_name = "第二阶段：洲内联赛（上半）+ 世界挑战者杯"

    def _resume_league_tail(self) -> None:
        self.phase_idx = 12
        self.phase_name = "第二阶段：洲内联赛（下半）+ 世界挑战者杯"
        tail = list(self._league_tail or [])
        self._league_tail = []
        self._set_matchdays(tail)

    def _challenger_build_group_stage(
        self,
        comp_prefix: str,
        teams36: Optional[List[Team]] = None,
        *,
        pots: Optional[List[List[Team]]] = None,
        log_type: str,
        log_note: str,
        log_extra: Optional[Dict[str, Any]] = None,
    ) -> List[List[Match]]:
        if pots is not None:
            groups, pot_names = draw_groups_from_pots(pots, self.rng)
        elif teams36 is not None:
            groups, pot_names = draw_six_pots_into_groups(teams36, self.rng)
        else:
            raise ValueError(f"{comp_prefix}: need teams36 or pots")
        if comp_prefix == "WCC":
            self._wcc_draw_groups = groups
        else:
            self._cup_draw_groups[comp_prefix] = groups
        draw_strength = compute_draw_strength(groups, self.rng)
        self._challenger_draw_strength[comp_prefix] = draw_strength
        self.draw_log.append(
            {
                "type": log_type,
                "赛事": comp_prefix,
                "说明": log_note,
                **(log_extra or {}),
                "分档": {f"第{i + 1}档": names for i, names in enumerate(pot_names)},
                "分组": {WCC_GROUP_LABELS[i]: [t.name for t in g] for i, g in enumerate(groups)},
                "组硬度(抽签后锁定)": draw_strength["second_strength_log"],
                "小组第四对阵组(前四均值最弱四组)": draw_strength["fourth_t_groups"],
                "小组第四交叉组(余下两组)": draw_strength["fourth_3rd_groups"],
                "24强附加赛签位(赛前锁定)": [
                    f"{s}: {a} vs {b}" for s, a, b in draw_strength["playoff_slot_defs"]
                ],
                "16强签表(算法生成)": [
                    f"{s}: {l} vs {r}胜者" for s, l, r in draw_strength["r16_slots"]
                ],
                "16强落位详情": draw_strength.get("bracket_layout_log", []),
                "S7直通小组": draw_strength["s7_group"],
                "S8直通小组": draw_strength["s8_group"],
                "前四均值排序": draw_strength["fourth_strength_log"],
                "说明_组硬度": "S7/S8：各组一档+二档世界排名均值最优的两组；T对阵第四：各组一至四档均值最弱的四组（分组抽签结束时锁定）",
                "说明_淘汰赛签表": "S7/S8分上下半区且各对阵P1-4；小组第一对阵P5-8；同半区回避+尽量16强不同组",
            }
        )
        by_round: List[List[Match]] = [[] for _ in range(5)]
        for gi, grp in enumerate(groups):
            lab = WCC_GROUP_LABELS[gi]
            comp = gs_comp_label(comp_prefix, lab)
            self._init_table(comp, grp)
            rnds = round_robin_single_even(grp, self.rng)
            for ri, pairs in enumerate(rnds):
                for hi, ai in pairs:
                    by_round[ri].append(
                        Match(
                            comp=comp,
                            stage=f"小组赛·组{lab}·第{ri + 1}轮",
                            day=0,
                            round_num=ri + 1,
                            home=hi,
                            away=ai,
                            kind="league",
                            neutral=True,
                        )
                    )
        return by_round

    def _wcc_build_group_stage_matches(self, teams36: List[Team]) -> List[List[Match]]:
        return self._challenger_build_group_stage(
            "WCC",
            teams36,
            log_type="wcc_group_draw",
            log_note="洲内附加赛败者 36 队；6 组单循环 5 轮中立场地；24 强积分种子附加赛制",
        )

    def _challenger_refresh_bracket_state(self, comp_prefix: str) -> Dict[str, Any]:
        groups = self._wcc_draw_groups if comp_prefix == "WCC" else self._cup_draw_groups[comp_prefix]
        draw_strength = self._challenger_draw_strength.get(comp_prefix)
        state = compute_bracket_state(
            groups, self.team_map, self.tables, comp_prefix, self.rng, draw_strength=draw_strength
        )
        self._challenger_bracket_state[comp_prefix] = state
        return state

    def _challenger_po_winner(self, comp_prefix: str, slot: str) -> Team:
        for m in reversed(self.all_results):
            if m.comp != f"{comp_prefix}-PO" or not m.played:
                continue
            if slot not in m.stage:
                continue
            if m.winner is not None:
                return m.winner
            if m.hg > m.ag:
                return m.home
            if m.ag > m.hg:
                return m.away
            return m.home if m.home.world_rank < m.away.world_rank else m.away
        raise RuntimeError(f"缺少 {comp_prefix} 24强附加赛结果 {slot}")

    def _challenger_ko_winners_by_round(self, comp_prefix: str, stage_prefix: str, n: int) -> List[Team]:
        ms = [
            m
            for m in self._last_day_matches
            if m.comp == f"{comp_prefix}-KO" and m.stage.startswith(stage_prefix) and m.played
        ]
        if len(ms) < n:
            all_ms = [
                m
                for m in self.all_results
                if m.comp == f"{comp_prefix}-KO" and m.stage.startswith(stage_prefix) and m.played
            ]
            if all_ms:
                last_day = max(m.day for m in all_ms)
                ms = [m for m in all_ms if m.day == last_day]
        ms.sort(key=lambda x: x.round_num)
        if len(ms) != n:
            raise RuntimeError(f"{comp_prefix} {stage_prefix} 胜者数量异常：期望 {n}，实际 {len(ms)}")
        out: List[Team] = []
        for m in ms:
            w = m.winner
            if w is None:
                w = m.home if m.hg > m.ag else m.away
            out.append(w)
        return out

    def _challenger_build_po_matches(self, comp_prefix: str) -> List[Match]:
        if not gs_tables_ready(comp_prefix, self.tables):
            raise RuntimeError(f"{comp_prefix} 小组积分榜未就绪，无法开始 24 强附加赛")
        state = self._challenger_refresh_bracket_state(comp_prefix)
        self.draw_log.append(
            {
                "type": f"{comp_prefix.lower()}_bracket_draw",
                "赛事": comp_prefix,
                "组硬度排序(S7/S8)": state["second_strength_log"],
                "小组第四角色": state["fourth_strength_log"],
                "最弱四组(第四打T)": state["fourth_t_groups"],
                "S7": state["S7"].name,
                "S7来自小组": state["S7_group"],
                "S8": state["S8"].name,
                "S8来自小组": state["S8_group"],
                "T1-T4": [state["placements"][f"T{i}"].name for i in range(1, 5)],
                "24强附加赛对阵": [f"{slot}: {a.name} vs {b.name}" for slot, a, b, _ in state["playoff_pairs"]],
            }
        )
        ms: List[Match] = []
        for slot, a, b, _ in state["playoff_pairs"]:
            h, aw = (a, b) if self.rng.random() < 0.5 else (b, a)
            ms.append(
                Match(
                    comp=f"{comp_prefix}-PO",
                    stage=f"24强附加赛·{slot}",
                    day=0,
                    round_num=int(slot[1:]),
                    home=h,
                    away=aw,
                    kind="knockout",
                    neutral=True,
                )
            )
        return ms

    def _challenger_build_r16_matches(self, comp_prefix: str) -> List[Match]:
        state = self._challenger_bracket_state.get(comp_prefix) or self._challenger_refresh_bracket_state(comp_prefix)
        draw_strength = self._challenger_draw_strength[comp_prefix]
        r16_slots = get_r16_slots(draw_strength, self.rng)
        p_winners = {f"P{i}": self._challenger_po_winner(comp_prefix, f"P{i}") for i in range(1, 9)}
        slot_teams: Dict[str, Team] = dict(state["placements"])
        pair_log: List[str] = []
        ms: List[Match] = []
        for ri, (slot, left_key, right_key) in enumerate(r16_slots, start=1):
            a = slot_teams[left_key]
            b = slot_teams[right_key] if not right_key.startswith("P") else p_winners[right_key]
            pair_log.append(f"{slot}: {a.name} vs {b.name} ({left_key} vs {right_key})")
            h, aw = (a, b) if self.rng.random() < 0.5 else (b, a)
            ms.append(
                Match(
                    comp=f"{comp_prefix}-KO",
                    stage=f"1/8决赛·{slot}",
                    day=0,
                    round_num=ri,
                    home=h,
                    away=aw,
                    kind="knockout",
                    neutral=True,
                )
            )
        self.draw_log.append(
            {
                "type": f"{comp_prefix.lower()}_r16_fixed",
                "赛事": comp_prefix,
                "说明": "16强签表（抽签后算法生成，八强前同组回避）",
                "对阵": pair_log,
            }
        )
        return ms

    def _challenger_build_qf_matches(self, comp_prefix: str) -> List[Match]:
        w = self._challenger_ko_winners_by_round(comp_prefix, "1/8决赛", 8)
        if len(w) != 8:
            raise RuntimeError(f"{comp_prefix} 1/8 胜者数量异常")
        ms: List[Match] = []
        pair_log: List[str] = []
        for qi, (ia, ib) in enumerate(QF_PAIR_IDX, start=1):
            a, b = w[ia], w[ib]
            pair_log.append(f"QF{qi}: {a.name} vs {b.name}")
            h, aw = (a, b) if self.rng.random() < 0.5 else (b, a)
            ms.append(
                Match(
                    comp=f"{comp_prefix}-KO",
                    stage=f"1/4决赛·QF{qi}",
                    day=0,
                    round_num=qi,
                    home=h,
                    away=aw,
                    kind="knockout",
                    neutral=True,
                )
            )
        self.draw_log.append(
            {"type": f"{comp_prefix.lower()}_qf_fixed", "赛事": comp_prefix, "说明": "8强固定签表", "对阵": pair_log}
        )
        return ms

    def _challenger_build_sf_matches(self, comp_prefix: str) -> List[Match]:
        w = self._challenger_ko_winners_by_round(comp_prefix, "1/4决赛", 4)
        if len(w) != 4:
            raise RuntimeError(f"{comp_prefix} 1/4 胜者数量异常")
        ms: List[Match] = []
        pair_log: List[str] = []
        for si, (ia, ib) in enumerate(SF_PAIR_IDX, start=1):
            a, b = w[ia], w[ib]
            pair_log.append(f"SF{si}: {a.name} vs {b.name}")
            h, aw = (a, b) if self.rng.random() < 0.5 else (b, a)
            ms.append(
                Match(
                    comp=f"{comp_prefix}-KO",
                    stage=f"半决赛·SF{si}",
                    day=0,
                    round_num=si,
                    home=h,
                    away=aw,
                    kind="knockout",
                    neutral=True,
                )
            )
        self.draw_log.append(
            {"type": f"{comp_prefix.lower()}_sf_fixed", "赛事": comp_prefix, "说明": "半决赛固定签表", "对阵": pair_log}
        )
        return ms

    def _challenger_build_final_match(self, comp_prefix: str) -> Match:
        w = self._challenger_ko_winners_by_round(comp_prefix, "半决赛", 2)
        if len(w) != 2:
            raise RuntimeError(f"{comp_prefix} 半决赛胜者数量异常")
        a, b = w[0], w[1]
        h, aw = (a, b) if self.rng.random() < 0.5 else (b, a)
        self.draw_log.append(
            {
                "type": f"{comp_prefix.lower()}_final",
                "赛事": comp_prefix,
                "说明": "决赛（名义主客随机）",
                "对阵": f"{h.name} vs {aw.name}",
            }
        )
        return Match(
            comp=f"{comp_prefix}-KO",
            stage="决赛",
            day=0,
            round_num=1,
            home=h,
            away=aw,
            kind="knockout",
            neutral=True,
        )

    def _wcc_extend_next_day(self, extra: List[Match]) -> None:
        if not self.phase_matchdays:
            return
        self.phase_matchdays[0].extend(extra)

    def _wcc_schedule_po(self) -> None:
        self._wcc_extend_next_day(self._challenger_build_po_matches("WCC"))

    def _wcc_schedule_r16(self) -> None:
        self._wcc_extend_next_day(self._challenger_build_r16_matches("WCC"))

    def _wcc_schedule_qf(self) -> None:
        self._wcc_extend_next_day(self._challenger_build_qf_matches("WCC"))

    def _wcc_schedule_sf(self) -> None:
        self._wcc_extend_next_day(self._challenger_build_sf_matches("WCC"))

    def _wcc_schedule_final(self) -> None:
        self._wcc_extend_next_day([self._challenger_build_final_match("WCC")])

    def _wcc_maybe_inject_after_p1_day(self) -> None:
        if not self.phase_matchdays:
            return
        d = self._p1_days_completed
        if d == 5 and not self._wcc_inject_flags["po"]:
            self._wcc_schedule_po()
            self._wcc_inject_flags["po"] = True
        elif d == 6 and not self._wcc_inject_flags["r16"]:
            self._wcc_schedule_r16()
            self._wcc_inject_flags["r16"] = True
        elif d == 7 and not self._wcc_inject_flags["qf"]:
            self._wcc_schedule_qf()
            self._wcc_inject_flags["qf"] = True
        elif d == 8 and not self._wcc_inject_flags["sf"]:
            self._wcc_schedule_sf()
            self._wcc_inject_flags["sf"] = True
        elif d == 9 and not self._wcc_inject_flags["fin"]:
            self._wcc_schedule_final()
            self._wcc_inject_flags["fin"] = True

    def _wcc_note_champion_from_last_day(self) -> None:
        for m in self._last_day_matches:
            if m.comp != "WCC-KO" or m.stage != "决赛" or not m.played:
                continue
            w = m.winner
            if w is None:
                w = m.home if m.hg > m.ag else m.away
            self.wcc_champion = w.name
            self.draw_log.append({"type": "wcc_champion", "冠军": self.wcc_champion})
            self._finalize_cup_ranking("WCC")
            return

    def _po_single_winner(self, comp: str, a: Team, b: Team) -> Team:
        for m in self.all_results:
            if m.comp != comp:
                continue
            if {m.home.name, m.away.name} != {a.name, b.name}:
                continue
            if m.winner is not None:
                return m.winner
            if m.hg > m.ag:
                return m.home
            if m.ag > m.hg:
                return m.away
            return a if a.world_rank < b.world_rank else b
        raise RuntimeError(f"未找到单场附加赛结果: {comp} {a.name} vs {b.name}")

    def _match_ovr_with_home(self, t: Team, home_adv: float) -> float:
        return self._clamp_match_ovr(t.ovr + home_adv * OVR_HOME_OVR_PER_POWER)

    def _goals_league_90(self, hp: Team, ap: Team, adv: float) -> Tuple[int, int]:
        ho = self._match_ovr_with_home(hp, adv)
        return _goals_from_ovr(self.rng, ho, ap.ovr)

    def _goals_knockout_90(self, hp: Team, ap: Team, adv: float) -> Tuple[int, int]:
        ho = self._match_ovr_with_home(hp, adv)
        return _goals_from_ovr(self.rng, ho, ap.ovr)

    def _league_home_adv(self, m: Match) -> float:
        return 0.0 if m.neutral else 20.0

    def _ko_home_adv(self, m: Match) -> float:
        return 0.0 if m.neutral else 16.0

    def _et_home_adv(self, m: Match) -> float:
        return 0.0 if m.neutral else 14.0

    def _goals_extra_time(self, hp: Team, ap: Team, et_adv: float) -> Tuple[int, int]:
        if self.rng.random() < 0.52:
            return 0, 0
        if self.rng.random() < _p_win(hp, ap, et_adv):
            return 1, 0
        return 0, 1

    def _penalty_winner(self, hp: Team, ap: Team) -> Tuple[Team, str]:
        sh = sa = 0
        for _ in range(5):
            sh += 1 if self.rng.random() < _pen_score_prob(hp) else 0
            sa += 1 if self.rng.random() < _pen_score_prob(ap) else 0
        while sh == sa:
            sh += 1 if self.rng.random() < _pen_score_prob(hp) else 0
            sa += 1 if self.rng.random() < _pen_score_prob(ap) else 0
        w = hp if sh > sa else ap
        return w, f"点球 {sh}-{sa}"

    def _clamp_match_ovr(self, o: float) -> float:
        return max(12.0, min(99.0, o))

    def _sample_match_team(self, t: Team) -> Team:
        j = self.rng.uniform(-MATCH_OVR_JITTER, MATCH_OVR_JITTER)
        o = self._clamp_match_ovr(t.ovr + j)
        return replace(t, ovr=o, power=power_from_ovr(o))

    def _play_league_match(self, m: Match) -> None:
        hp, ap = m.home, m.away
        hp_m = self._sample_match_team(hp)
        ap_m = self._sample_match_team(ap)
        m.home_match_ovr = hp_m.ovr
        m.away_match_ovr = ap_m.ovr
        adv = self._league_home_adv(m)
        m.hg, m.ag = self._goals_league_90(hp_m, ap_m, adv)
        m.reg_hg, m.reg_ag = m.hg, m.ag

    def _play_knockout_match(self, m: Match) -> None:
        hp, ap = m.home, m.away
        hp_m = self._sample_match_team(hp)
        ap_m = self._sample_match_team(ap)
        m.home_match_ovr = hp_m.ovr
        m.away_match_ovr = ap_m.ovr
        adv = self._ko_home_adv(m)
        hg, ag = self._goals_knockout_90(hp_m, ap_m, adv)
        m.reg_hg, m.reg_ag = hg, ag
        parts: List[str] = []
        if hg != ag:
            m.hg, m.ag = hg, ag
            m.winner = hp if hg > ag else ap
            return
        parts.append(f"90分钟{hg}-{ag}")
        eh, ea = self._goals_extra_time(hp_m, ap_m, self._et_home_adv(m))
        th, ta = hg + eh, ag + ea
        if eh != ea:
            m.hg, m.ag = th, ta
            m.winner = hp if th > ta else ap
            parts.append(f"加时{eh}-{ea}，全场{th}-{ta}")
            m.score_note = "；".join(parts)
            return
        parts.append(f"加时{eh}-{ea}")
        w, pnote = self._penalty_winner(hp_m, ap_m)
        m.winner = w
        # 保留 90+加时 真实比分；勿用虚构 2-1，否则总进球会小于实际常规时间/加时进球
        m.hg, m.ag = th, ta
        parts.append(pnote + f"，晋级 {w.name}")
        m.score_note = "；".join(parts)

    def _play_two_leg_match(self, m: Match) -> None:
        """两回合：首回合仅 90 分钟；次回合若总比分平则加时+点球（无客场进球）。"""
        hp, ap = m.home, m.away
        hp_m = self._sample_match_team(hp)
        ap_m = self._sample_match_team(ap)
        m.home_match_ovr = hp_m.ovr
        m.away_match_ovr = ap_m.ovr
        adv = self._ko_home_adv(m)
        hg, ag = self._goals_knockout_90(hp_m, ap_m, adv)
        m.hg, m.ag = hg, ag
        m.reg_hg, m.reg_ag = hg, ag

        if m.round_num < 2:
            return

        # 第二回合：汇总两回合
        leg1 = None
        for x in self.all_results:
            if x.tie_id == m.tie_id and x.played and x.round_num == 1:
                leg1 = x
                break
        if leg1 is None:
            raise RuntimeError(f"缺少第一回合: {m.tie_id}")

        # 双方累计进球（按队名）
        names = {hp.name, ap.name}
        g: Dict[str, int] = {hp.name: 0, ap.name: 0}
        for leg in (leg1,):
            g[leg.home.name] += leg.hg
            g[leg.away.name] += leg.ag
        g[hp.name] += hg
        g[ap.name] += ag

        if g[hp.name] != g[ap.name]:
            m.winner = hp if g[hp.name] > g[ap.name] else ap
            m.score_note = f"两回合总比分 {g[hp.name]}-{g[ap.name]}"
            return

        parts = [f"两回合 {g[hp.name]}-{g[ap.name]}"]
        eh, ea = self._goals_extra_time(hp_m, ap_m, self._et_home_adv(m))
        th, ta = hg + eh, ag + ea
        g[hp.name] += eh
        g[ap.name] += ea
        if eh != ea:
            m.hg, m.ag = th, ta
            m.winner = hp if eh > ea else ap
            parts.append(f"加时{eh}-{ea}")
            m.score_note = "；".join(parts) + f"，晋级 {m.winner.name}"
            return
        parts.append(f"加时{eh}-{ea}")
        w, pnote = self._penalty_winner(hp_m, ap_m)
        m.winner = w
        m.hg, m.ag = th, ta
        parts.append(pnote + f"，晋级 {w.name}")
        m.score_note = "；".join(parts)

    def _play(self, m: Match) -> None:
        if m.kind == "knockout":
            self._play_knockout_match(m)
        elif m.kind == "two_leg":
            self._play_two_leg_match(m)
        else:
            self._play_league_match(m)
        m.played = True
        m.day = self.day
        self.phase_results.append(m)
        self.all_results.append(m)
        if self._should_update_table(m.comp):
            self._table_update(m.comp, m.home.name, m.away.name, m.hg, m.ag)

    def _should_update_table(self, comp: str) -> bool:
        if comp == "FRIENDLY":
            return False
        if "-KO" in comp:
            return False
        if comp.endswith("-PO") or "-PO" in comp:
            return False
        if comp.endswith("-PRE"):
            return False
        return True

    def _blank_row(self) -> Dict[str, int]:
        return {"P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "GD": 0, "PTS": 0}

    def _init_table(self, comp: str, teams: List[Team]) -> None:
        """抽签结束后写入全 0 初始积分榜，便于赛前展示。"""
        tab = self.tables.setdefault(comp, {})
        for t in teams:
            if t.name not in tab:
                tab[t.name] = self._blank_row()

    def _table_update(self, comp: str, home: str, away: str, hg: int, ag: int) -> None:
        if comp not in self.tables:
            self.tables[comp] = {}
        for n in [home, away]:
            if n not in self.tables[comp]:
                self.tables[comp][n] = self._blank_row()
        hs = self.tables[comp][home]
        a_s = self.tables[comp][away]
        hs["P"] += 1
        a_s["P"] += 1
        hs["GF"] += hg
        hs["GA"] += ag
        a_s["GF"] += ag
        a_s["GA"] += hg
        if hg > ag:
            hs["W"] += 1
            a_s["L"] += 1
            hs["PTS"] += 3
        elif hg < ag:
            a_s["W"] += 1
            hs["L"] += 1
            a_s["PTS"] += 3
        else:
            hs["D"] += 1
            a_s["D"] += 1
            hs["PTS"] += 1
            a_s["PTS"] += 1
        hs["GD"] = hs["GF"] - hs["GA"]
        a_s["GD"] = a_s["GF"] - a_s["GA"]

    def _sorted_table(self, comp: str) -> List[Tuple[str, Dict[str, int]]]:
        if comp not in self.tables:
            return []

        def k(item: Tuple[str, Dict[str, int]]) -> Tuple[int, int, int, int, int]:
            n, s = item
            wr = self.team_map[n].world_rank
            return (s["PTS"], s["GD"], s["GF"], s["W"], -wr)

        return sorted(self.tables[comp].items(), key=k, reverse=True)

    def _compute_qual_slots_from_tables(self) -> None:
        self.qual_slots = {"WC": [], "WC_PO": [], "WL": [], "WL_PO": [], "WA": [], "WA_PO": []}
        quota = {
            "UEFA-QUAL": (14, 4, 8, 4, 3, 4),
            "AFC-QUAL": (4, 2, 6, 2, 7, 5),
            "CONCACAF-QUAL": (3, 2, 2, 2, 1, 3),
            "CAF-QUAL": (4, 2, 6, 2, 8, 6),
            "OFC-QUAL": (0, 1, 0, 1, 1, 1),
            "CONMEBOL-QUAL": (5, 1, 2, 1, 0, 1),
        }
        for comp, q in quota.items():
            tab = self._sorted_table(comp)
            teams = [self.team_map[n] for n, _ in tab]
            p = 0
            keys = ["WC", "WC_PO", "WL", "WL_PO", "WA", "WA_PO"]
            for key, cnt in zip(keys, q):
                self.qual_slots[key].extend(teams[p : p + cnt])
                p += cnt

    def _build_intercontinental(self) -> None:
        self._compute_qual_slots_from_tables()
        self.phase_name = "第三阶段：洲际附加赛（单场决胜，档位回避同洲）"
        self._po_pairs = {}
        md: List[List[Match]] = [[]]

        def pair_draw(teams: List[Team]) -> List[Tuple[Team, Team]]:
            ordered = sorted(teams, key=lambda t: t.world_rank)
            half = len(ordered) // 2
            a, b = ordered[:half], ordered[half:]
            self.rng.shuffle(b)
            pairs = []
            used: Set[str] = set()
            for t in a:
                pick = None
                for x in b:
                    if x.name in used:
                        continue
                    if x.confed != t.confed:
                        pick = x
                        break
                if pick is None:
                    for x in b:
                        if x.name not in used:
                            pick = x
                            break
                used.add(pick.name)
                pairs.append((t, pick))
            return pairs

        draws = []
        for comp_key, bucket in [("WC-PO", self.qual_slots["WC_PO"]), ("WL-PO", self.qual_slots["WL_PO"]), ("WA-PO", self.qual_slots["WA_PO"])]:
            if len(bucket) < 2:
                continue
            prs = pair_draw(bucket)
            self._po_pairs[comp_key] = prs
            draws.append(
                {
                    "赛事": comp_key,
                    "主场规则": "单场附加赛由世界排名更靠前（world_rank 数值更小）的一方主场；同分按队名序。",
                    "第一档(排名靠前)": [t.name for t in sorted(bucket, key=lambda x: x.world_rank)[: len(bucket) // 2]],
                    "第二档": [t.name for t in sorted(bucket, key=lambda x: x.world_rank)[len(bucket) // 2 :]],
                    "抽签对阵": [(a.name, b.name) for a, b in prs],
                }
            )
            for a, b in prs:
                # 世界排名更靠前（数值更小）的一方主场；平局按队名稳定决胜
                if a.world_rank != b.world_rank:
                    home, away = (a, b) if a.world_rank < b.world_rank else (b, a)
                else:
                    home, away = (a, b) if a.name <= b.name else (b, a)
                md[0].append(
                    Match(
                        comp=comp_key,
                        stage="单场附加赛",
                        day=0,
                        round_num=1,
                        home=home,
                        away=away,
                        kind="knockout",
                        neutral=False,
                    )
                )
        self.draw_log.append({"type": "intercontinental_draw", "payload": draws})
        self.phase_matchdays = md

    def _merge_po_into_tournament_slots(self) -> None:
        self._compute_qual_slots_from_tables()
        fixed: Dict[str, Dict[int, List[Team]]] = {
            "WORLD-CHAMPIONS": {6: []},
            "WORLD-LEAGUE": {1: [], 6: []},
            "WORLD-ASSOCIATION": {1: [], 5: [], 6: []},
        }
        for comp_po, win_bucket, lose_bucket, win_cup, win_pot, lose_cup, lose_pot in [
            ("WC-PO", "WC", "WL", "WORLD-CHAMPIONS", 6, "WORLD-LEAGUE", 1),
            ("WL-PO", "WL", "WA", "WORLD-LEAGUE", 6, "WORLD-ASSOCIATION", 1),
        ]:
            for a, b in self._po_pairs.get(comp_po, []):
                w = self._po_single_winner(comp_po, a, b)
                l = b if w.name == a.name else a
                self.qual_slots[win_bucket].append(w)
                self.qual_slots[lose_bucket].append(l)
                fixed[win_cup][win_pot].append(w)
                fixed[lose_cup][lose_pot].append(l)
        wa_winners: List[Team] = []
        for a, b in self._po_pairs.get("WA-PO", []):
            w = self._po_single_winner("WA-PO", a, b)
            self.qual_slots["WA"].append(w)
            wa_winners.append(w)
        wa_winners.sort(key=lambda t: t.world_rank)
        fixed["WORLD-ASSOCIATION"][5].extend(wa_winners[:4])
        fixed["WORLD-ASSOCIATION"][6].extend(wa_winners[4:])
        self._cup_fixed_pots = fixed

    def _build_cup_pots(self, cup_name: str, bucket: List[Team]) -> List[List[Team]]:
        """三大杯 6 档：附加赛结果固定档位（_cup_fixed_pots），其余队按世界排名依次填档。"""
        fixed = self._cup_fixed_pots.get(cup_name, {})
        fixed_names = {t.name for teams in fixed.values() for t in teams}
        direct = sorted(
            {t.name: t for t in bucket if t.name not in fixed_names}.values(),
            key=lambda t: t.world_rank,
        )
        take_by_cup = {
            "WORLD-CHAMPIONS": [(1, 6), (2, 6), (3, 6), (4, 6), (5, 6)],
            "WORLD-LEAGUE": [(2, 6), (3, 6), (4, 6), (5, 6)],
            "WORLD-ASSOCIATION": [(2, 6), (3, 6), (4, 6), (5, 2)],
        }
        take = take_by_cup[cup_name]
        pots: List[List[Team]] = [[] for _ in range(6)]
        idx = 0
        for pot_no, cnt in take:
            pots[pot_no - 1].extend(direct[idx : idx + cnt])
            idx += cnt
        if idx != len(direct):
            raise RuntimeError(f"{cup_name}: 非固定档队 {len(direct)} 与档位需求 {idx} 不符")
        for pot_no, teams in fixed.items():
            pots[pot_no - 1].extend(teams)
        for pi, pot in enumerate(pots, start=1):
            if len(pot) != 6:
                raise RuntimeError(f"{cup_name} 第{pi}档应为 6 队，实际 {len(pot)}")
        return pots

    def _build_cup_group_stages(self) -> None:
        self._merge_po_into_tournament_slots()
        self.phase_name = "第四阶段：三大杯正赛（6 组单循环 5 轮，24 强积分种子附加赛制）"
        self.draw_log.append({"type": "final_cup_qualifiers_merged", "note": "洲际附加赛胜者已并入各杯名额"})

        fixed_notes = {
            "WORLD-CHAMPIONS": "WC-PO 胜者 6 队固定第六档；其余 30 队按世界排名入一至五档",
            "WORLD-LEAGUE": "WC-PO 败者 6 队固定第一档；WL-PO 胜者 6 队固定第六档；其余 24 队按世界排名入二至五档",
            "WORLD-ASSOCIATION": "WL-PO 败者 6 队固定第一档；WA-PO 胜者世界排名前 4 进第五档、其余 6 队进第六档；其余 20 队按世界排名入二至四档及第五档余席",
        }
        cups = [
            (cup_name, self._build_cup_pots(cup_name, self.qual_slots[key]))
            for cup_name, key in [
                ("WORLD-CHAMPIONS", "WC"),
                ("WORLD-LEAGUE", "WL"),
                ("WORLD-ASSOCIATION", "WA"),
            ]
        ]

        cup_rounds: Dict[str, List[List[Match]]] = {}
        for cup_name, pots in cups:
            cup_rounds[cup_name] = self._challenger_build_group_stage(
                cup_name,
                pots=pots,
                log_type="final_cup_group_draw",
                log_note="36 队分 6 组单循环 5 轮；前二均值定 S7/S8，前四均值最弱四组第四打 T",
                log_extra={
                    "说明_固定档": fixed_notes[cup_name],
                    "固定档": {
                        f"第{pot_no}档": [t.name for t in teams]
                        for pot_no, teams in sorted(self._cup_fixed_pots.get(cup_name, {}).items())
                    },
                },
            )
            disp: List[List[Tuple[str, str, str, str]]] = []
            for ri, rnd in enumerate(cup_rounds[cup_name], start=1):
                row: List[Tuple[str, str, str, str]] = []
                for m in rnd:
                    row.append((m.home.name, "vs", m.away.name, venue_caption(True, m.home.name)))
                disp.append(row)
            self.league_schedule_by_confed[cup_name] = disp
            self.draw_log.append(
                {"type": "league_schedule_ready", "赛事": cup_name, "总轮次": 5, "每队场次": 5, "赛制": "6组单循环"}
            )

        days: List[List[Match]] = []
        for r in range(5):
            day: List[Match] = []
            for cup_name, _ in cups:
                day.extend(cup_rounds[cup_name][r])
            days.append(day)
        self.phase_matchdays = days

    def _begin_cup_knockout_bracket(self) -> None:
        self.phase_name = "第五阶段：三大杯淘汰赛（24 强附加赛 → 固定签表淘汰）"
        self._ko_sub = "PO"
        self.draw_log.append(
            {
                "type": "cup_knockout_start",
                "说明": "6 个小组第一 + 前二均值最优两组第二名（S7/S8）直通 16 强；前四均值最弱四组第四 vs 其余四组第二",
                "签表": {
                    "24强附加赛": "P1–P4: T vs 最弱四组第四；P5/P6: 余下两组第三第四交叉；P7/P8: 最弱四组第三两两对阵",
                    "1/8决赛": "抽签后按同组回避算法生成（见分组抽签记录）",
                    "1/4决赛": "R16-1 vs R16-2, R16-3 vs R16-4, R16-5 vs R16-6, R16-7 vs R16-8",
                    "半决赛": "QF1 vs QF2, QF3 vs QF4",
                    "决赛": "两场半决赛胜者",
                },
            }
        )
        self._build_cup_po_all()

    def _build_cup_po_all(self) -> None:
        day: List[Match] = []
        for cup in FINAL_CUPS:
            day.extend(self._challenger_build_po_matches(cup))
        self.phase_matchdays = [day]

    def _build_cup_r16_all(self) -> None:
        day: List[Match] = []
        for cup in FINAL_CUPS:
            day.extend(self._challenger_build_r16_matches(cup))
        self.phase_matchdays = [day]

    def _build_cup_qf_all(self) -> None:
        day: List[Match] = []
        for cup in FINAL_CUPS:
            day.extend(self._challenger_build_qf_matches(cup))
        self.phase_matchdays = [day]

    def _build_cup_sf_all(self) -> None:
        day: List[Match] = []
        for cup in FINAL_CUPS:
            day.extend(self._challenger_build_sf_matches(cup))
        self.phase_matchdays = [day]

    def _build_cup_final_all(self) -> None:
        day: List[Match] = []
        for cup in FINAL_CUPS:
            day.append(self._challenger_build_final_match(cup))
        self.phase_matchdays = [day]

    def _record_cup_champions(self) -> None:
        for m in self._last_day_matches:
            if "-KO" not in m.comp or "决赛" not in m.stage:
                continue
            w = m.winner
            if w is None:
                w = m.home if m.hg > m.ag else m.away
            key = m.comp.replace("-KO", "")
            self.cup_champions[key] = w.name
        self.draw_log.append({"type": "cup_champions", "冠军": dict(self.cup_champions)})
        for cup in FINAL_CUPS:
            self._finalize_cup_ranking(cup)

    def _cup_knockout_advance(self) -> bool:
        if self._ko_sub == "PO":
            self._ko_sub = "R16"
            self._build_cup_r16_all()
            return True
        if self._ko_sub == "R16":
            self._ko_sub = "QF"
            self._build_cup_qf_all()
            return True
        if self._ko_sub == "QF":
            self._ko_sub = "SF"
            self._build_cup_sf_all()
            return True
        if self._ko_sub == "SF":
            self._ko_sub = "F"
            self._build_cup_final_all()
            return True
        if self._ko_sub == "F":
            self._record_cup_champions()
            self._ko_sub = "done"
            return False
        return False

    # ---------------- 世界超级杯 ----------------

    def _ko_side_winner(self, m: Match) -> Team:
        if m.winner is not None:
            return m.winner
        if m.hg != m.ag:
            return m.home if m.hg > m.ag else m.away
        raise RuntimeError(f"淘汰赛无胜者: {m.comp} {m.stage} {m.home.name}-{m.away.name}")

    def _ko_side_loser(self, m: Match) -> Team:
        w = self._ko_side_winner(m)
        return m.away if w.name == m.home.name else m.home

    def _played_stage_matches(self, comp: str, stage_pred) -> List[Match]:
        ms = [m for m in self.all_results if m.comp == comp and m.played and stage_pred(m.stage)]
        ms.sort(key=lambda x: (x.day, x.round_num))
        return ms

    def _cup_champion_and_runner(self, cup: str) -> Tuple[Team, Team]:
        ms = self._played_stage_matches(f"{cup}-KO", lambda st: st == "决赛")
        if len(ms) != 1:
            raise RuntimeError(f"{cup} 决赛场次={len(ms)}，应为 1")
        m = ms[0]
        return self._ko_side_winner(m), self._ko_side_loser(m)

    def _cup_sf_losers(self, cup: str) -> List[Team]:
        ms = self._played_stage_matches(f"{cup}-KO", lambda st: st.startswith("半决赛"))
        if len(ms) != 2:
            raise RuntimeError(f"{cup} 半决赛场次={len(ms)}，应为 2")
        return [self._ko_side_loser(m) for m in ms]

    def _wsc_match_by_stage(self, stage: str) -> Optional[Match]:
        for m in reversed(self.all_results):
            if m.played and m.comp in ("WSC-PO", "WSC-KO") and m.stage == stage:
                return m
        return None

    def _wsc_winner_of(self, stage: str) -> Team:
        m = self._wsc_match_by_stage(stage)
        if m is None:
            raise RuntimeError(f"世界超级杯尚未产生胜者: {stage}")
        return self._ko_side_winner(m)

    def _wsc_ranking_qualifier(self, excluded: Set[str]) -> Team:
        rest = [t for t in self.teams if t.name not in excluded]
        if not rest:
            raise RuntimeError("世界超级杯：无可用排名递补队")
        return min(rest, key=lambda t: int(self.live_ranks.get(t.name, t.world_rank)))

    def _wsc_ko_match(
        self,
        comp: str,
        stage: str,
        round_num: int,
        home: Team,
        away: Team,
        *,
        neutral: bool,
    ) -> Match:
        if neutral:
            h, aw = (home, away) if self.rng.random() < 0.5 else (away, home)
        else:
            h, aw = home, away
        return Match(
            comp=comp,
            stage=stage,
            day=0,
            round_num=round_num,
            home=h,
            away=aw,
            kind="knockout",
            neutral=neutral,
        )

    def _start_world_super_cup(self, from_prev: Optional[Dict[str, Any]] = None) -> None:
        src = from_prev or {}
        if src:
            wc_ch = self.team_map[str(src["wc_champion"])]
            wc_ru = self.team_map[str(src["wc_runner_up"])]
            wl_ch = self.team_map[str(src["wl_champion"])]
            wl_ru = self.team_map[str(src["wl_runner_up"])]
            wa_ch = self.team_map[str(src["wa_champion"])]
            wc_sf_losers = [self.team_map[str(n)] for n in (src.get("wc_sf_losers") or [])]
            if len(wc_sf_losers) != 2:
                raise RuntimeError("世界超级杯：上届冠军杯半决赛负方不足 2 队")
        else:
            wc_ch, wc_ru = self._cup_champion_and_runner("WORLD-CHAMPIONS")
            wl_ch, wl_ru = self._cup_champion_and_runner("WORLD-LEAGUE")
            wa_ch, _wa_ru = self._cup_champion_and_runner("WORLD-ASSOCIATION")
            wc_sf_losers = self._cup_sf_losers("WORLD-CHAMPIONS")
        core = [wc_ch, wc_ru, wl_ch, wl_ru, wa_ch, *wc_sf_losers]
        core_names = [t.name for t in core]
        if len(set(core_names)) != 7:
            raise RuntimeError(f"世界超级杯核心 7 队不唯一: {core_names}")
        rank_q = self._wsc_ranking_qualifier(set(core_names))

        pot1 = [wl_ch, wl_ru]
        pot2 = [wa_ch, rank_q]
        self.rng.shuffle(pot1)
        self.rng.shuffle(pot2)
        po1_pairs = list(zip(pot1, pot2))  # (home 一档, away 二档)
        self.rng.shuffle(po1_pairs)

        po2_homes = list(wc_sf_losers)
        self.rng.shuffle(po2_homes)
        feed = [0, 1]
        self.rng.shuffle(feed)
        # 按半区重标：PO1 上/下 的胜者分别进入同半区 PO2（客场）
        po1_upper = po1_pairs[feed[0]]
        po1_lower = po1_pairs[feed[1]]

        self._wsc_slots = {
            "wc_champion": wc_ch,
            "wc_runner_up": wc_ru,
            "wl_champion": wl_ch,
            "wl_runner_up": wl_ru,
            "wa_champion": wa_ch,
            "rank_qualifier": rank_q,
            "wc_sf_losers": list(wc_sf_losers),
            "po1_upper_home": po1_upper[0],
            "po1_upper_away": po1_upper[1],
            "po1_lower_home": po1_lower[0],
            "po1_lower_away": po1_lower[1],
            "po2_upper_home": po2_homes[0],
            "po2_lower_home": po2_homes[1],
        }
        self._wsc_ko_sub = "PO1"
        self.phase_name = "世界超级杯·附加赛第一轮"
        s = self._wsc_slots
        self.draw_log.append(
            {
                "type": "world_super_cup_draw",
                "赛事": "世界超级杯",
                "说明_录取": (
                    "世界冠军杯四强 + 世界联赛杯冠亚军 + 世界协会杯冠军"
                    " + 其余球队中世界排名最高者，共 8 队"
                ),
                "说明_分档": (
                    "附加赛第一轮：一档=联赛冠亚军（主场），二档=协会冠军+排名递补（客场）；"
                    "附加赛第二轮：一档=冠军杯 3–4 名（主场），二档=第一轮胜者（客场）；"
                    "半决赛/决赛中立场"
                ),
                "说明_签位": "全部签位在超级杯开赛时一次性抽定并锁定",
                "参赛": {
                    "世界冠军杯冠军": wc_ch.name,
                    "世界冠军杯亚军": wc_ru.name,
                    "世界冠军杯3-4名": [t.name for t in wc_sf_losers],
                    "世界联赛杯冠军": wl_ch.name,
                    "世界联赛杯亚军": wl_ru.name,
                    "世界协会杯冠军": wa_ch.name,
                    "排名递补": rank_q.name,
                    "排名递补世界排名": int(self.live_ranks.get(rank_q.name, rank_q.world_rank)),
                },
                "分档": {
                    "附加赛第一轮一档": [wl_ch.name, wl_ru.name],
                    "附加赛第一轮二档": [wa_ch.name, rank_q.name],
                    "附加赛第二轮一档": [t.name for t in wc_sf_losers],
                    "附加赛第二轮二档": ["第一轮上半区胜者", "第一轮下半区胜者"],
                },
                "签表": {
                    "附加赛第一轮·上半区": f"{s['po1_upper_home'].name}（主） vs {s['po1_upper_away'].name}（客）",
                    "附加赛第一轮·下半区": f"{s['po1_lower_home'].name}（主） vs {s['po1_lower_away'].name}（客）",
                    "附加赛第二轮·上半区": f"{s['po2_upper_home'].name}（主） vs 第一轮上半区胜者（客）",
                    "附加赛第二轮·下半区": f"{s['po2_lower_home'].name}（主） vs 第一轮下半区胜者（客）",
                    "半决赛·上半区": f"{wc_ch.name} vs 附加赛第二轮上半区胜者（中立）",
                    "半决赛·下半区": f"{wc_ru.name} vs 附加赛第二轮下半区胜者（中立）",
                    "决赛": "上半区胜者 vs 下半区胜者（中立）",
                },
            }
        )
        self._build_wsc_po1()

    def _build_wsc_po1(self) -> None:
        s = self._wsc_slots
        self.phase_matchdays = [
            [
                self._wsc_ko_match(
                    "WSC-PO",
                    "附加赛第一轮·上半区",
                    1,
                    s["po1_upper_home"],
                    s["po1_upper_away"],
                    neutral=False,
                ),
                self._wsc_ko_match(
                    "WSC-PO",
                    "附加赛第一轮·下半区",
                    2,
                    s["po1_lower_home"],
                    s["po1_lower_away"],
                    neutral=False,
                ),
            ]
        ]

    def _build_wsc_po2(self) -> None:
        self.phase_name = "世界超级杯·附加赛第二轮"
        s = self._wsc_slots
        self.phase_matchdays = [
            [
                self._wsc_ko_match(
                    "WSC-PO",
                    "附加赛第二轮·上半区",
                    1,
                    s["po2_upper_home"],
                    self._wsc_winner_of("附加赛第一轮·上半区"),
                    neutral=False,
                ),
                self._wsc_ko_match(
                    "WSC-PO",
                    "附加赛第二轮·下半区",
                    2,
                    s["po2_lower_home"],
                    self._wsc_winner_of("附加赛第一轮·下半区"),
                    neutral=False,
                ),
            ]
        ]

    def _build_wsc_sf(self) -> None:
        self.phase_name = "世界超级杯·四强"
        s = self._wsc_slots
        self.phase_matchdays = [
            [
                self._wsc_ko_match(
                    "WSC-KO",
                    "半决赛·上半区",
                    1,
                    s["wc_champion"],
                    self._wsc_winner_of("附加赛第二轮·上半区"),
                    neutral=True,
                ),
                self._wsc_ko_match(
                    "WSC-KO",
                    "半决赛·下半区",
                    2,
                    s["wc_runner_up"],
                    self._wsc_winner_of("附加赛第二轮·下半区"),
                    neutral=True,
                ),
            ]
        ]

    def _build_wsc_final(self) -> None:
        self.phase_name = "世界超级杯·决赛"
        self.phase_matchdays = [
            [
                self._wsc_ko_match(
                    "WSC-KO",
                    "决赛",
                    1,
                    self._wsc_winner_of("半决赛·上半区"),
                    self._wsc_winner_of("半决赛·下半区"),
                    neutral=True,
                )
            ]
        ]

    def _record_wsc_champion(self) -> None:
        w = self._wsc_winner_of("决赛")
        self.wsc_champion = w.name
        self.draw_log.append({"type": "world_super_cup_champion", "冠军": self.wsc_champion})
        self._finalize_cup_ranking("WSC")

    def _wsc_advance(self) -> bool:
        if self._wsc_ko_sub == "skip":
            return False
        if self._wsc_ko_sub == "PO1":
            self._wsc_ko_sub = "PO2"
            self._build_wsc_po2()
            return True
        if self._wsc_ko_sub == "PO2":
            self._wsc_ko_sub = "SF"
            self._build_wsc_sf()
            return True
        if self._wsc_ko_sub == "SF":
            self._wsc_ko_sub = "F"
            self._build_wsc_final()
            return True
        if self._wsc_ko_sub == "F":
            self._record_wsc_champion()
            self._wsc_ko_sub = "done"
            return False
        return False

    def next_day(self) -> bool:
        if not self.phase_matchdays:
            return False

        self._restamp_queued_kickoffs()
        today = self.phase_matchdays.pop(0)
        self.day += 1
        info = matchday_info(self.cycle_start_year, self.day)
        if not today and info.kind == "wsc":
            self.phase_name = "世界超级杯（本届不举办）"
        elif info.kind == "friendly":
            self.phase_name = "国际友谊赛"
        self._last_day_matches = list(today)
        for m in today:
            m.day = self.day
            self._play(m)
        self._update_live_rankings_after_day(today)

        if self.cycle_part == "B" and self.phase_idx in (1, 12) and len(self._wcc_prelim_losers) == 36:
            self._p1_days_completed += 1
            if self.phase_matchdays:
                self._wcc_maybe_inject_after_p1_day()
        if self.cycle_part == "B":
            self._wcc_note_champion_from_last_day()

        if not self.phase_matchdays:
            if self.cycle_part == "A":
                if self.phase_idx == 0:
                    if self._cal_segment == "QUAL_EARLY":
                        self._begin_calendar_wsc()
                    elif self._cal_segment == "WSC":
                        if self._wsc_advance():
                            self._restamp_queued_kickoffs()
                            return True
                        self._build_qual_last_day()
                    elif self._cal_segment == "QUAL_LAST":
                        self._build_friendly_days()
                    elif self._cal_segment == "FRIENDLY":
                        self._cal_segment = "PO"
                        self._collect_continental_qual_and_build_po()
                    else:
                        self._collect_continental_qual_and_build_po()
                elif self.phase_idx == 1:
                    self._build_continental_po_leg2()
                elif self.phase_idx == 2:
                    self._merge_continental_po_winners_and_build_finals_gs()
                elif self.phase_idx == 3:
                    self._begin_continental_knockout()
                elif self.phase_idx == 4:
                    if not self._continental_knockout_advance():
                        self._start_world_cup_cycle()
            else:
                if self.phase_idx == 0:
                    if not self._prelim_advance():
                        self._collect_prelim_winners()
                        self.phase_idx = 1
                elif self.phase_idx == 1:
                    self.phase_idx = 10
                    self._start_confederations_cup()
                elif self.phase_idx == 10:
                    self.phase_idx = 11
                    self._begin_cc_knockout()
                elif self.phase_idx == 11:
                    if not self._cc_knockout_advance():
                        self._resume_league_tail()
                elif self.phase_idx == 12:
                    self._build_intercontinental()
                    self.phase_idx = 2
                elif self.phase_idx == 2:
                    self._build_cup_group_stages()
                    self.phase_idx = 3
                elif self.phase_idx == 3:
                    self._begin_cup_knockout_bracket()
                    self.phase_idx = 4
                elif self.phase_idx == 4:
                    if not self._cup_knockout_advance():
                        self.phase_name = "已结束"
                        self._restamp_queued_kickoffs()
                        return False
        self._restamp_queued_kickoffs()
        return True

    def upcoming_matches_for_team(self, team_name: str) -> List[Dict[str, Any]]:
        """
        查询该队在「当前尚未进行的比赛日队列」中的赛程（自下一轮起按顺序）。
        赛季全部结束后 `phase_matchdays` 为空，则返回空列表。
        """
        if team_name not in self.team_map:
            return []
        out: List[Dict[str, Any]] = []
        for d_off, day in enumerate(self.phase_matchdays, start=1):
            for m in day:
                if m.home.name != team_name and m.away.name != team_name:
                    continue
                opp = m.away.name if m.home.name == team_name else m.home.name
                if m.neutral:
                    venue = "中立"
                else:
                    venue = "主场" if m.home.name == team_name else "客场"
                out.append(
                    {
                        "再过比赛日": d_off,
                        "开球": format_kickoff(getattr(m, "kickoff", "") or ""),
                        "赛事": m.comp,
                        "阶段": m.stage,
                        "轮次": m.round_num,
                        "对手": opp,
                        "主客": venue,
                        "对阵": f"{m.home.name} vs {m.away.name}",
                    }
                )
        return out

    def list_competitions(self) -> List[str]:
        comps = set(self.tables.keys())
        for m in self.all_results:
            comps.add(m.comp)
        return sorted(comps)


def run_cli(seed: int) -> None:
    sim = Simulator(seed)
    print(sim.phase_name)
    while True:
        try:
            raw = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if raw in ("quit", "exit"):
            return
        if raw == "next":
            sim.next_day()
            print(sim.phase_name, "day", sim.day)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    run_cli(args.seed)


if __name__ == "__main__":
    main()
