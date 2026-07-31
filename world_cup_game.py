"""
四年周期模拟：Part A 四大洲际杯 → Part B 世界杯预选与三大杯。
淘汰赛：加时 + 点球；战力采用 FIFA 风格 OVR（见 world_cup_ratings.py 与 data/team_ovr_overrides.json）。
"""
from __future__ import annotations

import argparse
import hashlib
import math
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Set, Tuple

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
    load_ovr_overrides,
    load_world_ranks,
    match_importance,
    ovr_for_team,
    power_from_ovr,
    ranks_from_ratings,
    reset_cycle_ranks_from_original,
    save_world_ranks,
)

CONFEDS = ["UEFA", "AFC", "CONCACAF", "CAF", "OFC", "CONMEBOL"]
FINAL_CUPS = ["WORLD-CHAMPIONS", "WORLD-LEAGUE", "WORLD-ASSOCIATION"]

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


def venue_caption(neutral: bool, home_name: str) -> str:
    if neutral:
        return f"中立球场（记名主队 {home_name}）"
    return f"主场 {home_name}"


def assign_balanced_home_away(
    pots: List[List[Team]], edges: List[Tuple[Team, Team]]
) -> List[Tuple[Team, Team]]:
    """
    对 pot 联赛每条边定向为主客场，使每队面对「同一对手档」的两支队时恰好 1 主 1 客
    （含同档两对手）。与轮次分配顺序无关，按边字典序处理。
    """
    pot_of: Dict[str, int] = {}
    for pi, pot in enumerate(pots):
        for t in pot:
            pot_of[t.name] = pi
    st: Dict[Tuple[str, int], Dict[str, int]] = defaultdict(lambda: {"h": 0, "a": 0})
    tmp: Dict[int, Tuple[Team, Team]] = {}
    order = sorted(range(len(edges)), key=lambda mi: (edges[mi][0].name, edges[mi][1].name))
    for mi in order:
        a, b = edges[mi]
        pa, pb = pot_of[a.name], pot_of[b.name]
        if pa == pb:
            ka, kb = (a.name, pa), (b.name, pa)
        else:
            ka, kb = (a.name, pb), (b.name, pa)
        na_h = st[ka]["a"] >= 1 and st[ka]["h"] < 1
        na_a = st[ka]["h"] >= 1 and st[ka]["a"] < 1
        nb_h = st[kb]["a"] >= 1 and st[kb]["h"] < 1
        nb_a = st[kb]["h"] >= 1 and st[kb]["a"] < 1
        if na_h and nb_h:
            a_home = a.name < b.name
        elif na_h:
            a_home = True
        elif na_a:
            a_home = False
        elif nb_h:
            a_home = False
        elif nb_a:
            a_home = True
        else:
            h = hashlib.md5(f"{a.name}|{b.name}".encode()).hexdigest()
            a_home = (int(h[:8], 16) % 2 == 0)
        if a_home:
            tmp[mi] = (a, b)
            st[ka]["h"] += 1
            st[kb]["a"] += 1
        else:
            tmp[mi] = (b, a)
            st[ka]["a"] += 1
            st[kb]["h"] += 1
    return [tmp[i] for i in range(len(edges))]


def split_into_pots(teams: List[Team], n_pots: int) -> List[List[Team]]:
    ordered = sorted(teams, key=lambda t: t.world_rank)
    n = len(ordered)
    if n % n_pots != 0:
        raise ValueError(f"球队数 {n} 无法均分为 {n_pots} 档")
    m = n // n_pots
    return [ordered[i * m : (i + 1) * m] for i in range(n_pots)]


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


class Simulator:
    def __init__(self, seed: int, hosts: Optional[Dict[str, str]] = None) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        # 东道主：cup_code -> team name；缺省时各大区取排名最前的队
        self.hosts: Dict[str, str] = dict(hosts or {})
        self.cycle_part = "A"  # A=洲际杯, B=世界杯周期
        self.day = 0
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

        # 开局：从原始排名库覆盖周期库
        self._rank_source = "original"
        wr_map = reset_cycle_ranks_from_original()
        self.teams = self._build_teams(wr_map)
        self.team_map = {t.name: t for t in self.teams}
        self.fifa_points: Dict[str, float] = {}
        self.live_ranks: Dict[str, int] = {}
        self.last_day_ranking_delta: List[Dict[str, Any]] = []
        self.last_day_rating_details: List[Dict[str, Any]] = []
        self._init_live_rankings()
        self._resolve_default_hosts()
        self._bootstrap_continental_qual()

    def _init_live_rankings(self) -> None:
        base = {t.name: t.world_rank for t in self.teams}
        self.fifa_points = init_ratings_from_ranks(base)
        self.live_ranks = ranks_from_ratings(self.fifa_points, tiebreak_ranks=base)
        for t in self.teams:
            t.world_rank = self.live_ranks[t.name]
        self.last_day_ranking_delta = []
        self.last_day_rating_details = []

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
            cup_knockout = m.kind == "knockout"
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

        max_rounds = 0
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
                max_rounds = max(max_rounds, len(plan))
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
        days: List[List[Match]] = []
        for r in range(max_rounds):
            day: List[Match] = []
            for code, gplans in all_plans.items():
                for lab, plan in gplans.items():
                    if r >= len(plan):
                        continue
                    for home, away in plan[r]:
                        day.append(
                            Match(
                                comp=f"{code}-QUAL-{lab}",
                                stage=f"预选第{r+1}轮",
                                day=0,
                                round_num=r + 1,
                                home=home,
                                away=away,
                                kind="league",
                                neutral=False,
                            )
                        )
            days.append(day)
        self.phase_matchdays = days

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
            groups, pot_names = draw_finals_groups(host, direct_others, po_winners, self.rng)
            self._cont_finals_groups[code] = groups
            self.draw_log.append(
                {
                    "type": "continental_finals_draw",
                    "赛事": CONTINENTAL_LABELS[code],
                    "code": code,
                    "东道主A1": host.name,
                    "说明_分档": "附加赛晋级 4 队固定第四档；其余直通队按世界排名分档",
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

    def _update_cycle_ranks_after_continental(self) -> None:
        """Part A 结束后：将实时国际积分固化为周期排名库，并刷新 OVR 供 Part B。"""
        new_ranks = dict(self.live_ranks)
        save_world_ranks(
            new_ranks,
            comment="Part A 洲际杯期间逐轮更新的国际积分所对应排名；开局从 team_world_ranks_original.json 回溯。",
        )
        self._apply_rank_map(new_ranks)
        # 与固化排名对齐积分，避免 Part B 初值漂移
        self.fifa_points = init_ratings_from_ranks(new_ranks)
        self.live_ranks = ranks_from_ratings(self.fifa_points, tiebreak_ranks=new_ranks)
        self._rank_source = "cycle"
        self.draw_log.append(
            {
                "type": "world_ranks_updated",
                "说明": "已根据洲际杯期间国际积分写入 team_world_ranks_cycle.json，Part B 使用新排名",
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
        self.phase_name = "世界杯周期·第一阶段：洲内附加赛（抽签→单回合）"
        self.phase_idx = 0
        self._prelim_pairs_meta = {}
        all_pre: List[Match] = []

        for confed in CONFEDS:
            meta = self._draw_preliminary(confed)
            self._prelim_pairs_meta[confed] = meta
            for tie in meta.get("ties", []):
                seed_t = tie["seed_team"]
                other_t = tie["other_team"]
                all_pre.append(
                    Match(
                        comp=f"{confed}-PRE",
                        stage="Preliminary",
                        day=0,
                        round_num=1,
                        home=seed_t,
                        away=other_t,
                        kind="knockout",
                    )
                )

        self.phase_matchdays = [all_pre] if all_pre else []

    def _confed_teams(self, confed: str) -> List[Team]:
        return [t for t in self.teams if t.confed == confed]

    def _draw_preliminary(self, confed: str) -> Dict[str, Any]:
        teams = sorted(self._confed_teams(confed), key=lambda t: t.world_rank)
        cfg = {
            "UEFA": (41, 7),
            "AFC": (25, 11),
            "CONCACAF": (19, 11),
            "CAF": (42, 6),
            "OFC": (11, 1),
            "CONMEBOL": (10, 0),
        }
        direct_n, playoff_slots = cfg[confed]
        if confed == "CONMEBOL":
            payload = {
                "confed": confed,
                "直接晋级": [t.name for t in teams],
                "附加赛候选池": [],
                "种子队(按排名)": [],
                "非种子抽签顺序": [],
                "ties": [],
            }
            self.draw_log.append({"type": "prelim_draw", "payload": payload})
            return {"ties": [], **payload}

        direct = teams[:direct_n]
        pool = teams[direct_n:]
        need_winners = len(pool) // 2
        seeds = pool[:playoff_slots]
        others = pool[playoff_slots:]
        self.rng.shuffle(others)
        ties: List[Dict[str, Any]] = []
        for i in range(need_winners):
            ties.append({"seed_team": seeds[i], "other_team": others[i], "序号": i + 1})

        payload = {
            "confed": confed,
            "说明": "种子队主场；非种子队抽签落位",
            "直接晋级": [t.name for t in direct],
            "附加赛候选池": [t.name for t in pool],
            "种子队(按排名)": [t.name for t in seeds],
            "非种子抽签顺序": [t.name for t in others],
            "对阵(种子主场)": [{"种子": x["seed_team"].name, "对手": x["other_team"].name} for x in ties],
        }
        self.draw_log.append({"type": "prelim_draw", "payload": payload})
        return {"ties": ties, **{k: v for k, v in payload.items() if k != "对阵(种子主场)"}}

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
            if confed == "CONMEBOL":
                winners_by_confed[confed] = sorted(self._confed_teams(confed), key=lambda t: t.world_rank)
                continue
            meta = self._prelim_pairs_meta[confed]
            direct_n = len(meta["直接晋级"])
            teams = sorted(self._confed_teams(confed), key=lambda t: t.world_rank)
            direct = teams[:direct_n]
            wset: Set[str] = {t.name for t in direct}
            for tie in meta["ties"]:
                a, b = tie["seed_team"], tie["other_team"]
                found = None
                for m in self.all_results:
                    if m.comp != f"{confed}-PRE":
                        continue
                    if {m.home.name, m.away.name} != {a.name, b.name}:
                        continue
                    found = m
                    break
                if found is None:
                    raise RuntimeError(f"缺少附加赛结果: {confed} {a.name} vs {b.name}")
                w_t = self._prelim_match_winner_team(found, a, b)
                wset.add(w_t.name)
                loser = b if w_t.name == a.name else a
                wcc_losers.append(loser)
            winners_by_confed[confed] = [self.team_map[nm] for nm in wset]
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

        max_rounds = 0

        for confed, n_pots, comp_label, use_standard in specs:
            teams = sorted(winners_by_confed[confed], key=lambda t: t.world_rank)
            pots = split_into_pots(teams, n_pots)
            pot_names = [[t.name for t in pot] for pot in pots]
            self.draw_log.append(
                {"type": "league_pots", "赛事": comp_label, "大洲": confed, "分档说明": "按世界排名蛇形/顺位入档（1档最强）", "pots": pot_names}
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
            _verify_regular(edges, teams, deg)

            sched = assign_rounds_auto(edges, deg, self.rng)
            if sched is None:
                raise RuntimeError(f"{comp_label} 无法分配轮次（请 pip install ortools 或更换种子）")

            oriented = assign_balanced_home_away(pots, edges)
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
            self._init_table(comp_label, teams)
            max_rounds = max(max_rounds, deg)

            self.draw_log.append(
                {
                    "type": "league_schedule_ready",
                    "赛事": comp_label,
                    "总轮次": deg,
                    "总场次": len(edges),
                    "每队场次": deg,
                }
            )

        cmb = round_robin_double(sorted(winners_by_confed["CONMEBOL"], key=lambda t: t.world_rank), self.rng)
        self.league_play_plan["CONMEBOL-QUAL"] = cmb
        cmb_teams = sorted(winners_by_confed["CONMEBOL"], key=lambda t: t.world_rank)
        self._init_table("CONMEBOL-QUAL", cmb_teams)
        max_rounds = max(max_rounds, len(cmb))
        self.league_schedule_by_confed["CONMEBOL-QUAL"] = [
            [(h.name, "vs", a.name, f"主场 {h.name}") for h, a in day] for day in cmb
        ]
        self.draw_log.append({"type": "league_schedule_ready", "赛事": "CONMEBOL-QUAL", "总轮次": len(cmb), "说明": "主客场双循环"})

        all_days: List[List[Match]] = []
        for r in range(max_rounds):
            day_list: List[Match] = []
            for confed, n_pots, comp_label, use_standard in specs:
                plan = self.league_play_plan.get(comp_label)
                if plan is None or r >= len(plan):
                    continue
                for home, away in plan[r]:
                    day_list.append(
                        Match(
                            comp=comp_label,
                            stage=f"联赛第{r+1}轮",
                            day=0,
                            round_num=r + 1,
                            home=home,
                            away=away,
                            kind="league",
                            neutral=False,
                        )
                    )
            cplan = self.league_play_plan.get("CONMEBOL-QUAL")
            if cplan and r < len(cplan):
                for home, away in cplan[r]:
                    day_list.append(
                        Match(
                            comp="CONMEBOL-QUAL",
                            stage=f"联赛第{r+1}轮",
                            day=0,
                            round_num=r + 1,
                            home=home,
                            away=away,
                            kind="league",
                            neutral=False,
                        )
                    )
            all_days.append(day_list)

        # 世界挑战者杯：前 5 个预选赛比赛日与小组赛同步；之后附加赛/淘汰赛按轮次注入
        self._p1_days_completed = 0
        self._wcc_inject_flags = {k: False for k in ("po", "r16", "qf", "sf", "fin")}
        self._wcc_draw_groups = []
        self.wcc_champion = ""
        wcc_gs = self._wcc_build_group_stage_matches(self._wcc_prelim_losers)
        for r in range(min(5, len(all_days))):
            all_days[r].extend(wcc_gs[r])

        self.phase_matchdays = all_days
        self.phase_name = "第二阶段：洲内联赛（每轮一个比赛日，每队总场次相同）+ 世界挑战者杯"

    def _challenger_build_group_stage(
        self, comp_prefix: str, teams36: List[Team], *, log_type: str, log_note: str
    ) -> List[List[Match]]:
        groups, pot_names = draw_six_pots_into_groups(teams36, self.rng)
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

    def _play_knockout_match(self, m: Match) -> None:
        hp, ap = m.home, m.away
        hp_m = self._sample_match_team(hp)
        ap_m = self._sample_match_team(ap)
        m.home_match_ovr = hp_m.ovr
        m.away_match_ovr = ap_m.ovr
        adv = self._ko_home_adv(m)
        hg, ag = self._goals_knockout_90(hp_m, ap_m, adv)
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
        for comp_po, win_bucket, lose_bucket in [
            ("WC-PO", "WC", "WL"),
            ("WL-PO", "WL", "WA"),
        ]:
            for a, b in self._po_pairs.get(comp_po, []):
                w = self._po_single_winner(comp_po, a, b)
                l = b if w.name == a.name else a
                self.qual_slots[win_bucket].append(w)
                self.qual_slots[lose_bucket].append(l)
        for a, b in self._po_pairs.get("WA-PO", []):
            w = self._po_single_winner("WA-PO", a, b)
            self.qual_slots["WA"].append(w)

    def _fill_36(self, lst: List[Team]) -> List[Team]:
        lst = sorted({t.name: t for t in lst}.values(), key=lambda t: t.world_rank)
        if len(lst) >= 36:
            return lst[:36]
        for t in sorted(self.teams, key=lambda x: x.world_rank):
            if t.name not in {x.name for x in lst}:
                lst.append(t)
            if len(lst) == 36:
                break
        return lst[:36]

    def _build_cup_group_stages(self) -> None:
        self._merge_po_into_tournament_slots()
        self.phase_name = "第四阶段：三大杯正赛（6 组单循环 5 轮，24 强积分种子附加赛制）"
        self.draw_log.append({"type": "final_cup_qualifiers_merged", "note": "洲际附加赛胜者已并入各杯名额"})

        cups = [
            ("WORLD-CHAMPIONS", self._fill_36(self.qual_slots["WC"])),
            ("WORLD-LEAGUE", self._fill_36(self.qual_slots["WL"])),
            ("WORLD-ASSOCIATION", self._fill_36(self.qual_slots["WA"])),
        ]

        cup_rounds: Dict[str, List[List[Match]]] = {}
        for cup_name, t36 in cups:
            cup_rounds[cup_name] = self._challenger_build_group_stage(
                cup_name,
                t36,
                log_type="final_cup_group_draw",
                log_note="36 队分 6 组单循环 5 轮；前二均值定 S7/S8，前四均值最弱四组第四打 T",
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

    def next_day(self) -> bool:
        if not self.phase_matchdays:
            return False

        today = self.phase_matchdays.pop(0)
        self.day += 1
        self._last_day_matches = list(today)
        for m in today:
            m.day = self.day
            self._play(m)
        self._update_live_rankings_after_day(today)

        if self.cycle_part == "B" and self.phase_idx == 1 and len(self._wcc_prelim_losers) == 36:
            self._p1_days_completed += 1
            if self.phase_matchdays:
                self._wcc_maybe_inject_after_p1_day()
        if self.cycle_part == "B":
            self._wcc_note_champion_from_last_day()

        if not self.phase_matchdays:
            if self.cycle_part == "A":
                if self.phase_idx == 0:
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
                    self._collect_prelim_winners()
                    self.phase_idx = 1
                elif self.phase_idx == 1:
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
                        return False
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
