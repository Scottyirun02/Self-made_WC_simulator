"""
四大洲际杯：欧洲杯 / 非洲杯 / 亚太杯 / 美洲杯
预选：主客双循环小组赛 + 第四名附加赛（两回合）；正赛：32 队传统赛制。
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

CONTINENTAL_CUPS: List[Dict[str, Any]] = [
    {
        "code": "EURO",
        "label": "欧洲杯",
        "confeds": ("UEFA",),
        "group_sizes": [6] * 9,
    },
    {
        "code": "AFCON",
        "label": "非洲杯",
        "confeds": ("CAF",),
        "group_sizes": [6] * 8 + [5],
    },
    {
        "code": "APAC",
        "label": "亚太杯",
        "confeds": ("AFC", "OFC"),
        "group_sizes": [7] * 5 + [6] * 4,
    },
    {
        "code": "AMERICA",
        "label": "美洲杯",
        "confeds": ("CONCACAF", "CONMEBOL"),
        "group_sizes": [6] * 5 + [5] * 4,
    },
]

CONTINENTAL_CODES = [c["code"] for c in CONTINENTAL_CUPS]
CONTINENTAL_LABELS = {c["code"]: c["label"] for c in CONTINENTAL_CUPS}
FINAL_GROUP_LABELS = list("ABCDEFGH")

# 正赛 16 强签表（传统交叉）
R16_PAIRINGS = [
    ("A1", "B2"),
    ("C1", "D2"),
    ("E1", "F2"),
    ("G1", "H2"),
    ("B1", "A2"),
    ("D1", "C2"),
    ("F1", "E2"),
    ("H1", "G2"),
]
QF_FROM_R16 = [(0, 1), (2, 3), (4, 5), (6, 7)]
SF_FROM_QF = [(0, 1), (2, 3)]


def cup_by_code(code: str) -> Dict[str, Any]:
    for c in CONTINENTAL_CUPS:
        if c["code"] == code:
            return c
    raise KeyError(code)


def pool_teams(all_teams: Sequence[Any], confeds: Tuple[str, ...]) -> List[Any]:
    conf = set(confeds)
    return [t for t in all_teams if t.confed in conf]


def draw_qual_groups(
    teams: List[Any], group_sizes: List[int], rng: random.Random
) -> Tuple[List[List[Any]], List[List[str]]]:
    """
    按世界排名分档落入各组：第 k 档对应各组第 k 名席位；
    规模较小的组不拿靠后档位（如 5 队组无第 6 档）。
    """
    if sum(group_sizes) != len(teams):
        raise ValueError(f"group_sizes sum {sum(group_sizes)} != teams {len(teams)}")
    ordered = sorted(teams, key=lambda t: t.world_rank)
    n_groups = len(group_sizes)
    max_size = max(group_sizes)
    groups: List[List[Any]] = [[] for _ in range(n_groups)]
    pot_names: List[List[str]] = []
    idx = 0
    for slot in range(max_size):
        needing = [gi for gi, sz in enumerate(group_sizes) if sz > slot]
        batch = ordered[idx : idx + len(needing)]
        idx += len(needing)
        if len(batch) != len(needing):
            raise RuntimeError("draw_qual_groups: pot size mismatch")
        pot_names.append([t.name for t in batch])
        shuffled = batch[:]
        rng.shuffle(shuffled)
        for j, gi in enumerate(needing):
            groups[gi].append(shuffled[j])
    return groups, pot_names


def round_robin_double_any(
    teams: List[Any], rng: random.Random
) -> List[List[Tuple[Any, Any]]]:
    """主客双循环；奇数队时用轮空，每轮一队轮空。"""
    n = len(teams)
    if n < 2:
        return []
    s: List[Optional[Any]] = teams[:]
    rng.shuffle(s)
    if n % 2 == 1:
        s.append(None)
    m = len(s)
    rounds_first: List[List[Tuple[Any, Any]]] = []
    for _ in range(m - 1):
        day: List[Tuple[Any, Any]] = []
        for i in range(m // 2):
            a, b = s[i], s[m - 1 - i]
            if a is None or b is None:
                continue
            if rng.random() < 0.5:
                day.append((a, b))
            else:
                day.append((b, a))
        rounds_first.append(day)
        s = [s[0]] + [s[-1]] + s[1 : m - 1]
    rounds_second = [[(b, a) for a, b in day] for day in rounds_first]
    return rounds_first + rounds_second


def fourth_place_sort_key(team: Any, stats: Dict[str, int]) -> Tuple[int, int, int, int, int]:
    return (stats["PTS"], stats["GD"], stats["GF"], stats["W"], -int(team.world_rank))


def _blank_row() -> Dict[str, int]:
    return {"P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "GD": 0, "PTS": 0}


def fair_fourth_place_stats(
    fourth_name: str,
    table_sorted: Sequence[Tuple[str, Dict[str, int]]],
    matches: Sequence[Any],
    min_group_size: int,
) -> Dict[str, int]:
    """
    跨组比较第四名时的公平战绩（对齐 UEFA「剔除对多出来垫底队」做法）：

    - min_group_size = 本洲际杯预选各组队数的最小值
    - 只计对「小组最终名次 ≤ min_group_size」球队的比赛
    - 多队组里对第 (min_group_size+1)…n 名的比赛全部剔除后再比 PTS/GD/GF

    例：组规模 5 与 6 并存时，6 人组第四剔对第 6 名的两回合；7 人组剔对第 6、7 名。
    各组同规模时不剔除，与完整积分榜一致。
    """
    n = len(table_sorted)
    # table_sorted[0] = 第 1 名 … table_sorted[k] = 第 k+1 名
    discard = {table_sorted[i][0] for i in range(min_group_size, n)}
    stats = _blank_row()
    for m in matches:
        if not getattr(m, "played", False):
            continue
        hn, an = m.home.name, m.away.name
        if fourth_name not in (hn, an):
            continue
        opp = an if hn == fourth_name else hn
        if opp in discard:
            continue
        if hn == fourth_name:
            gf, ga = int(m.hg), int(m.ag)
        else:
            gf, ga = int(m.ag), int(m.hg)
        stats["P"] += 1
        stats["GF"] += gf
        stats["GA"] += ga
        if gf > ga:
            stats["W"] += 1
            stats["PTS"] += 3
        elif gf < ga:
            stats["L"] += 1
        else:
            stats["D"] += 1
            stats["PTS"] += 1
    stats["GD"] = stats["GF"] - stats["GA"]
    return stats


def select_playoff_fourths(
    fourths: List[Tuple[str, Any, Dict[str, int]]],
) -> Tuple[List[Tuple[str, Any]], Optional[Tuple[str, Any]]]:
    """
    9 个小组第四：成绩最差者直接淘汰，其余 8 个进附加赛。
    fourths: (group_label, team, stats) — stats 应为公平化后的战绩。
    """
    if len(fourths) != 9:
        raise ValueError(f"need 9 fourths, got {len(fourths)}")
    ordered = sorted(
        fourths,
        key=lambda x: fourth_place_sort_key(x[1], x[2]),
        reverse=True,
    )
    eliminated = (ordered[-1][0], ordered[-1][1])
    playoff = [(g, t) for g, t, _ in ordered[:8]]
    return playoff, eliminated


def draw_playoff_ties(
    playoff_fourths: List[Tuple[str, Any]], rng: random.Random
) -> List[Dict[str, Any]]:
    """
    8 队按世界排名分两档各 4；一档抽二档，回避小组赛同组；
    一档先客后主。
    """
    if len(playoff_fourths) != 8:
        raise ValueError("need 8 playoff teams")
    ranked = sorted(playoff_fourths, key=lambda x: x[1].world_rank)
    pot1 = ranked[:4]  # stronger
    pot2 = ranked[4:]
    group_of = {t.name: g for g, t in playoff_fourths}
    used: set = set()
    ties: List[Dict[str, Any]] = []
    for seed_g, seed in pot1:
        candidates = [x for x in pot2 if x[1].name not in used and group_of[x[1].name] != seed_g]
        if not candidates:
            candidates = [x for x in pot2 if x[1].name not in used]
        pick_g, pick = rng.choice(candidates)
        used.add(pick.name)
        ties.append(
            {
                "seed": seed,
                "other": pick,
                "seed_group": seed_g,
                "other_group": pick_g,
                # 先客后主：第一回合 seed 客场
                "leg1_home": pick,
                "leg1_away": seed,
                "leg2_home": seed,
                "leg2_away": pick,
            }
        )
    return ties


def draw_finals_groups(
    host: Any,
    direct_others: List[Any],
    pot4_fixed: List[Any],
    rng: random.Random,
) -> Tuple[List[List[Any]], List[List[str]]]:
    """
    东道主固定 A1（一档）；直通队按世界排名入一二三四档；
    附加赛晋级的 4 队固定进入第四档。
    """
    if len(pot4_fixed) != 4:
        raise ValueError(f"need 4 playoff winners in pot 4, got {len(pot4_fixed)}")
    if len(direct_others) != 27:
        raise ValueError(f"need 27 direct non-host teams, got {len(direct_others)}")
    fixed_names = {t.name for t in pot4_fixed}
    if host.name in fixed_names:
        raise ValueError("host cannot be a playoff winner pot")
    rest = sorted(
        [t for t in direct_others if t.name not in fixed_names],
        key=lambda t: t.world_rank,
    )
    if len(rest) != 27:
        raise ValueError(f"direct others overlap playoff winners: got {len(rest)}")

    # 一档其余 7 + 二档 8 + 三档 8 + 四档直通 4；附加赛 4 队固定四档
    pot1_rest = rest[:7]
    pot2 = rest[7:15]
    pot3 = rest[15:23]
    pot4_rest = rest[23:27]
    pot4 = pot4_rest + list(pot4_fixed)

    groups: List[List[Any]] = [[] for _ in range(8)]
    groups[0].append(host)  # A1
    pot_names: List[List[str]] = [[host.name] + [t.name for t in pot1_rest]]

    p1 = pot1_rest[:]
    rng.shuffle(p1)
    for i, t in enumerate(p1):
        groups[i + 1].append(t)

    for pot in (pot2, pot3, pot4):
        pot_names.append([t.name for t in pot])
        batch = pot[:]
        rng.shuffle(batch)
        for gi, t in enumerate(batch):
            groups[gi].append(t)

    return groups, pot_names


def traditional_r16_slots(placements: Dict[str, Any]) -> List[Tuple[Any, Any]]:
    return [(placements[a], placements[b]) for a, b in R16_PAIRINGS]
