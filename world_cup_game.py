"""
世界杯预选赛模拟：联赛阶段每队场次严格一致；附加赛抽签、联赛分档与赛程可完整展示。
淘汰赛：加时 + 点球；战力采用 FIFA 风格 OVR（见 world_cup_ratings.py 与 data/team_ovr_overrides.json）。
"""
from __future__ import annotations

import argparse
import hashlib
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Set, Tuple

from world_cup_ratings import load_ovr_overrides, load_world_ranks, ovr_for_team, power_from_ovr

CONFEDS = ["UEFA", "AFC", "CONCACAF", "CAF", "OFC", "CONMEBOL"]
FINAL_CUPS = ["WORLD-CHAMPIONS", "WORLD-LEAGUE", "WORLD-ASSOCIATION"]

# 单场战力：在球队基准 OVR（JSON/曲线）附近小幅波动；整届大赛内基准不变
MATCH_OVR_JITTER = 1.35

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
    "WORLD-CHAMPIONS": [
        (1, 8, "16强直接晋级"),
        (9, 24, "9～24名附加赛"),
        (25, 36, "未晋级淘汰赛"),
    ],
    "WORLD-LEAGUE": [
        (1, 8, "16强直接晋级"),
        (9, 24, "9～24名附加赛"),
        (25, 36, "未晋级淘汰赛"),
    ],
    "WORLD-ASSOCIATION": [
        (1, 8, "16强直接晋级"),
        (9, 24, "9～24名附加赛"),
        (25, 36, "未晋级淘汰赛"),
    ],
}


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


def build_ofc_league_edges(pots: List[List[Team]]) -> List[Tuple[Team, Team]]:
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


def _p_win(home: Team, away: Team, home_adv: float = 52.0) -> float:
    d = (home.power + home_adv) - away.power
    return 1.0 / (1.0 + 10 ** (-d / 315.0))


def _p_win_neutral(a: Team, b: Team) -> float:
    d = a.power - b.power
    return 1.0 / (1.0 + 10 ** (-d / 300.0))


def _pen_score_prob(t: Team) -> float:
    return max(0.64, min(0.93, 0.72 + (t.ovr - 58.0) * 0.0038))


class Simulator:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.teams = self._build_teams()
        self.team_map = {t.name: t for t in self.teams}

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
        self.qual_slots: Dict[str, List[Team]] = {
            "WC": [], "WC_PO": [], "WL": [], "WL_PO": [], "WA": [], "WA_PO": [],
        }
        self._prelim_pairs_meta: Dict[str, Dict[str, Any]] = {}
        self._po_pairs: Dict[str, List[Tuple[Team, Team]]] = {}
        self._ko_sub: str = ""
        self._last_day_matches: List[Match] = []
        self.cup_champions: Dict[str, str] = {}

        self._bootstrap_prelim_and_queue()

    def _build_teams(self) -> List[Team]:
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

    def _bootstrap_prelim_and_queue(self) -> None:
        self.phase_name = "第一阶段：洲内附加赛（抽签→单回合）"
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

    def _collect_prelim_winners(self) -> None:
        winners_by_confed: Dict[str, List[Team]] = {}
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
                if found.winner is not None:
                    wset.add(found.winner.name)
                elif found.hg > found.ag:
                    wset.add(found.home.name)
                elif found.ag > found.hg:
                    wset.add(found.away.name)
                else:
                    wset.add(a.name if self.rng.random() < _p_win(a, b, 0.0) else b.name)
            winners_by_confed[confed] = [self.team_map[nm] for nm in wset]

        self._build_league_after_prelim(winners_by_confed)

    def _build_league_after_prelim(self, winners_by_confed: Dict[str, List[Team]]) -> None:
        self.league_schedule_by_confed = {}
        self.league_play_plan = {}

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

            edges = build_pot_league_edges(pots) if use_standard else build_ofc_league_edges(pots)
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

        self.phase_matchdays = all_days
        self.phase_name = "第二阶段：洲内联赛（每轮一个比赛日，每队总场次相同）"

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

    def _weak_goals_trailing(self, pow_diff: float, *, upset: bool) -> int:
        """预计输球一方的进球；pow_diff 为双方战力差绝对值，upset 时更偏保守。"""
        x = max(0.0, pow_diff)
        if upset:
            return int(self.rng.choices([0, 1, 2, 3], weights=[28, 38, 24, 10])[0])
        if x < 200:
            return int(self.rng.choices([0, 1, 2, 3, 4], weights=[18, 32, 32, 14, 4])[0])
        if x < 450:
            return int(self.rng.choices([0, 1, 2, 3], weights=[32, 38, 22, 8])[0])
        if x < 700:
            return int(self.rng.choices([0, 1, 2, 3], weights=[45, 35, 15, 5])[0])
        return int(self.rng.choices([0, 1, 2], weights=[58, 32, 10])[0])

    def _win_margin(self, pow_diff: float, *, upset: bool) -> int:
        """胜方相对负方的净胜球（至少 1）；大差距时仍可出大比分但概率低。"""
        x = max(0.0, pow_diff)
        if upset:
            return int(self.rng.choices([1, 2, 3, 4], weights=[42, 38, 15, 5])[0])
        if x < 150:
            return int(self.rng.choices([1, 2, 3, 4], weights=[38, 40, 18, 4])[0])
        if x < 350:
            r = int(self.rng.choices([1, 2, 3, 4, 5], weights=[22, 35, 28, 12, 3])[0])
            return max(1, min(5, r + int(x // 280)))
        if x < 600:
            r = int(self.rng.choices([2, 3, 4, 5, 6], weights=[18, 32, 28, 15, 7])[0])
            return max(1, min(7, r))
        r = int(self.rng.choices([2, 3, 4, 5, 6, 7], weights=[12, 28, 28, 18, 10, 4])[0])
        if x > 750 and self.rng.random() < 0.06:
            r += self.rng.randint(1, 3)
        if x > 900 and self.rng.random() < 0.035:
            r += self.rng.randint(2, 4)
        return max(1, min(r, 9))

    def _goals_non_draw_90(self, hp: Team, ap: Team, adv: float) -> Tuple[int, int]:
        """先按主场胜率定胜负，再采样比分；爆冷时压低总进球与净胜。"""
        ph = _p_win(hp, ap, adv)
        favor_home = (hp.power + adv) >= ap.power
        home_wins = self.rng.random() < ph
        upset = home_wins != favor_home
        d = abs((hp.power + adv) - ap.power)
        d_eff = min(d, 200.0) if upset else d
        lg = self._weak_goals_trailing(d_eff, upset=upset)
        m = self._win_margin(max(d_eff, 35.0), upset=upset)
        wg = lg + m
        max_w = 7 if upset else 12
        max_l = 4 if upset else 7
        wg = min(wg, max_w)
        lg = min(lg, max_l)
        if wg <= lg:
            wg = lg + 1
        if home_wins:
            return wg, lg
        return lg, wg

    def _league_home_adv(self, m: Match) -> float:
        return 0.0 if m.neutral else 20.0

    def _ko_home_adv(self, m: Match) -> float:
        return 0.0 if m.neutral else 16.0

    def _et_home_adv(self, m: Match) -> float:
        return 0.0 if m.neutral else 14.0

    def _goals_league_90(self, hp: Team, ap: Team, adv: float) -> Tuple[int, int]:
        draw_p = max(0.08, min(0.30, 0.26 - abs(hp.power - ap.power) / 1050.0))
        if self.rng.random() < draw_p:
            g = self.rng.choice([0, 1, 1, 2])
            return g, g
        return self._goals_non_draw_90(hp, ap, adv)

    def _goals_knockout_90(self, hp: Team, ap: Team, adv: float) -> Tuple[int, int]:
        draw_p = max(0.05, min(0.22, 0.17 - abs(hp.power - ap.power) / 720.0))
        if self.rng.random() < draw_p:
            g = self.rng.choice([0, 1, 1, 2])
            return g, g
        return self._goals_non_draw_90(hp, ap, adv)

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

    def _play(self, m: Match) -> None:
        if m.kind == "knockout":
            self._play_knockout_match(m)
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
        if comp.endswith("-PO"):
            return False
        return True

    def _table_update(self, comp: str, home: str, away: str, hg: int, ag: int) -> None:
        if comp not in self.tables:
            self.tables[comp] = {}
        for n in [home, away]:
            if n not in self.tables[comp]:
                self.tables[comp][n] = {"P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "GD": 0, "PTS": 0}
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

        def k(item: Tuple[str, Dict[str, int]]) -> Tuple[int, int, int, int]:
            n, s = item
            wr = self.team_map[n].world_rank
            return (s["PTS"], s["GD"], s["GF"], -wr)

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
                    "第一档(排名靠前)": [t.name for t in sorted(bucket, key=lambda x: x.world_rank)[: len(bucket) // 2]],
                    "第二档": [t.name for t in sorted(bucket, key=lambda x: x.world_rank)[len(bucket) // 2 :]],
                    "抽签对阵": [(a.name, b.name) for a, b in prs],
                }
            )
            for a, b in prs:
                home, away = (a, b) if self.rng.random() < 0.5 else (b, a)
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

    def _build_cup_leagues(self) -> None:
        self._merge_po_into_tournament_slots()
        self.phase_name = "第四阶段：三大杯正赛联赛（每队8场，4档×每档2对手）"
        self.draw_log.append({"type": "final_cup_qualifiers_merged", "note": "洲际附加赛胜者已并入各杯名额"})

        cups = [
            ("WORLD-CHAMPIONS", self._fill_36(self.qual_slots["WC"])),
            ("WORLD-LEAGUE", self._fill_36(self.qual_slots["WL"])),
            ("WORLD-ASSOCIATION", self._fill_36(self.qual_slots["WA"])),
        ]

        cup_plans: Dict[str, List[List[Tuple[Team, Team]]]] = {}
        days: List[List[Match]] = []

        for cup_name, t36 in cups:
            pots = split_into_pots(sorted(t36, key=lambda t: t.world_rank), 4)
            pot_names = [[t.name for t in p] for p in pots]
            self.draw_log.append({"type": "final_cup_pots", "杯赛": cup_name, "pots": pot_names})
            edges = build_pot_league_edges(pots)
            _verify_regular(edges, sorted(t36, key=lambda t: t.world_rank), 8)
            sched = assign_rounds_auto(edges, 8, self.rng)
            if sched is None:
                raise RuntimeError(f"{cup_name} 决赛圈赛程分配失败")
            oriented = assign_balanced_home_away(pots, edges)
            rounds_fixtures: List[List[Tuple[Team, Team]]] = []
            disp: List[List[Tuple[str, str, str, str]]] = []
            for r in range(8):
                rnd: List[Tuple[Team, Team]] = []
                row: List[Tuple[str, str, str, str]] = []
                for mi in sched[r]:
                    home, away = oriented[mi]
                    rnd.append((home, away))
                    row.append((home.name, "vs", away.name, venue_caption(True, home.name)))
                rounds_fixtures.append(rnd)
                disp.append(row)
            cup_plans[cup_name] = rounds_fixtures
            self.league_schedule_by_confed[cup_name] = disp
            self.draw_log.append({"type": "league_schedule_ready", "赛事": cup_name, "总轮次": 8, "每队场次": 8})

        for r in range(8):
            day: List[Match] = []
            for cup_name, _ in cups:
                for home, away in cup_plans[cup_name][r]:
                    day.append(
                        Match(
                            comp=cup_name,
                            stage=f"正赛联赛第{r+1}轮",
                            day=0,
                            round_num=r + 1,
                            home=home,
                            away=away,
                            kind="league",
                            neutral=True,
                        )
                    )
            days.append(day)

        self.phase_matchdays = days

    def _team_at_cup_rank(self, cup: str, rank_1based: int) -> Team:
        tab = self._sorted_table(cup)
        return self.team_map[tab[rank_1based - 1][0]]

    def _cup_playoff_rank_pairs(self) -> List[Tuple[int, int]]:
        return [(9, 24), (10, 23), (11, 22), (12, 21), (13, 20), (14, 19), (15, 18), (16, 17)]

    def _cup_po_winner(self, cup: str, hi: int, lo: int) -> Team:
        ta = self._team_at_cup_rank(cup, hi)
        tb = self._team_at_cup_rank(cup, lo)
        for m in self.all_results:
            if m.comp != f"{cup}-PO":
                continue
            if {m.home.name, m.away.name} != {ta.name, tb.name}:
                continue
            if m.winner is not None:
                return m.winner
            if m.hg > m.ag:
                return m.home
            if m.ag > m.hg:
                return m.away
            return ta if ta.world_rank < tb.world_rank else tb
        raise RuntimeError(f"未找到 {cup} 附加赛单场结果: {ta.name} vs {tb.name}")

    def _winners_from_last_ko(self, cup: str, stage_sub: str) -> List[Team]:
        ms = [m for m in self._last_day_matches if m.comp == f"{cup}-KO" and stage_sub in m.stage]
        out: List[Team] = []
        for m in ms:
            w = m.winner
            if w is None:
                w = m.home if m.hg > m.ag else m.away
            out.append(w)
        return out

    def _begin_cup_knockout_bracket(self) -> None:
        self.phase_name = "第五阶段：三大杯淘汰赛（9～24 名单回合附加赛 → 单场淘汰）"
        self._ko_sub = "PO1"
        self.draw_log.append({"type": "cup_knockout_start", "说明": "联赛 1～8 名直接晋级 16 强；9～24 名单场决胜另 8 席"})
        self._build_cup_po_single_all()

    def _build_cup_po_single_all(self) -> None:
        day: List[Match] = []
        for cup in FINAL_CUPS:
            for hi, lo in self._cup_playoff_rank_pairs():
                ta = self._team_at_cup_rank(cup, hi)
                tb = self._team_at_cup_rank(cup, lo)
                home, away = (ta, tb) if self.rng.random() < 0.5 else (tb, ta)
                day.append(
                    Match(
                        comp=f"{cup}-PO",
                        stage="9-24附加赛",
                        day=0,
                        round_num=1,
                        home=home,
                        away=away,
                        kind="knockout",
                        neutral=True,
                    )
                )
        self.phase_matchdays = [day]

    def _build_cup_r16_all(self) -> None:
        day: List[Match] = []
        pairs = self._cup_playoff_rank_pairs()
        for cup in FINAL_CUPS:
            tops = [self._team_at_cup_rank(cup, r) for r in range(1, 9)]
            wins = [self._cup_po_winner(cup, hi, lo) for hi, lo in pairs]
            for i in range(8):
                t1, t2 = tops[i], wins[i]
                home, away = (t1, t2) if self.rng.random() < 0.5 else (t2, t1)
                day.append(
                    Match(
                        comp=f"{cup}-KO",
                        stage="1/8决赛",
                        day=0,
                        round_num=1,
                        home=home,
                        away=away,
                        kind="knockout",
                        neutral=True,
                    )
                )
        self.phase_matchdays = [day]

    def _build_cup_qf_all(self) -> None:
        day: List[Match] = []
        for cup in FINAL_CUPS:
            w = self._winners_from_last_ko(cup, "1/8")
            for j in range(0, 8, 2):
                a, b = w[j], w[j + 1]
                home, away = (a, b) if self.rng.random() < 0.5 else (b, a)
                day.append(
                    Match(
                        comp=f"{cup}-KO",
                        stage="1/4决赛",
                        day=0,
                        round_num=1,
                        home=home,
                        away=away,
                        kind="knockout",
                        neutral=True,
                    )
                )
        self.phase_matchdays = [day]

    def _build_cup_sf_all(self) -> None:
        day: List[Match] = []
        for cup in FINAL_CUPS:
            w = self._winners_from_last_ko(cup, "1/4")
            for j in range(0, 4, 2):
                a, b = w[j], w[j + 1]
                home, away = (a, b) if self.rng.random() < 0.5 else (b, a)
                day.append(
                    Match(
                        comp=f"{cup}-KO",
                        stage="半决赛",
                        day=0,
                        round_num=1,
                        home=home,
                        away=away,
                        kind="knockout",
                        neutral=True,
                    )
                )
        self.phase_matchdays = [day]

    def _build_cup_final_all(self) -> None:
        day: List[Match] = []
        for cup in FINAL_CUPS:
            w = self._winners_from_last_ko(cup, "半决赛")
            a, b = w[0], w[1]
            home, away = (a, b) if self.rng.random() < 0.5 else (b, a)
            day.append(
                Match(
                    comp=f"{cup}-KO",
                    stage="决赛",
                    day=0,
                    round_num=1,
                    home=home,
                    away=away,
                    kind="knockout",
                    neutral=True,
                )
            )
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
        if self._ko_sub == "PO1":
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

        if not self.phase_matchdays:
            if self.phase_idx == 0:
                self._collect_prelim_winners()
                self.phase_idx = 1
            elif self.phase_idx == 1:
                self._build_intercontinental()
                self.phase_idx = 2
            elif self.phase_idx == 2:
                self._build_cup_leagues()
                self.phase_idx = 3
            elif self.phase_idx == 3:
                self._begin_cup_knockout_bracket()
                self.phase_idx = 4
            elif self.phase_idx == 4:
                if not self._cup_knockout_advance():
                    self.phase_name = "已结束"
                    return False
        return True

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
