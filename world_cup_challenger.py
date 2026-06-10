"""
世界挑战者杯赛制：36 队、6 组单循环，24 强积分种子附加赛，固定淘汰赛签表。
"""
from __future__ import annotations

import random
from itertools import permutations
from typing import Any, Dict, List, Optional, Tuple

WCC_GROUP_LABELS = ["A", "B", "C", "D", "E", "F"]

QF_PAIR_IDX = [(0, 1), (2, 3), (4, 5), (6, 7)]
SF_PAIR_IDX = [(0, 1), (2, 3)]

UPPER_R16_IDX = frozenset(range(4))
LOWER_R16_IDX = frozenset(range(4, 8))
EARLY_PO_SLOTS = ("P1", "P2", "P3", "P4")
LATE_PO_SLOTS = ("P5", "P6", "P7", "P8")


def draw_six_pots_into_groups(
    teams36: List[Any], rng: random.Random
) -> Tuple[List[List[Any]], List[List[str]]]:
    """
    按世界排名（数值越小越强）将 36 队分为 6 档，每档 6 队；
    每档内随机打乱后依次落入 6 个小组，使每组各含 1～6 档各一队（世界杯式抽签）。
    """
    t = sorted(teams36, key=lambda x: x.world_rank)
    pots = [t[6 * i : 6 * (i + 1)] for i in range(6)]
    groups: List[List[Any]] = [[] for _ in range(6)]
    for pot in pots:
        perm = pot[:]
        rng.shuffle(perm)
        for gi in range(6):
            groups[gi].append(perm[gi])
    pot_names = [[tm.name for tm in pot] for pot in pots]
    return groups, pot_names


def round_robin_single_even(group: List[Any], rng: random.Random) -> List[List[Tuple[Any, Any]]]:
    """偶数队单循环，每轮每队一场；返回 n-1 轮。"""
    n = len(group)
    if n < 2 or n % 2 == 1:
        raise ValueError("round_robin_single_even: need even n>=2")
    s = group[:]
    rng.shuffle(s)
    rounds: List[List[Tuple[Any, Any]]] = []
    for _ in range(n - 1):
        day: List[Tuple[Any, Any]] = []
        for i in range(n // 2):
            a, b = s[i], s[n - 1 - i]
            day.append((a, b) if rng.random() < 0.5 else (b, a))
        rounds.append(day)
        s = [s[0]] + [s[-1]] + s[1 : n - 1]
    return rounds


def table_sort_key(team: Any, stats: Dict[str, int]) -> Tuple[int, int, int, int, int]:
    """积分、净胜球、进球、胜场、世界排名（抽签）。"""
    wr = int(team.world_rank)
    return (stats["PTS"], stats["GD"], stats["GF"], stats["W"], -wr)


def sorted_group_table(team_map: Dict[str, Any], table: Dict[str, Dict[str, int]]) -> List[Tuple[str, Dict[str, int]]]:
    items = list(table.items())

    def k(it: Tuple[str, Dict[str, int]]) -> Tuple[int, int, int, int, int]:
        n, s = it
        return table_sort_key(team_map[n], s)

    return sorted(items, key=k, reverse=True)


def cross_rank_subtable(
    team_map: Dict[str, Any], comps_and_names: List[Tuple[str, str]], tables: Dict[str, Dict[str, Dict[str, int]]]
) -> List[Tuple[str, str, Dict[str, int]]]:
    """(小组 comp, 队名, 该组积分数据) 按成绩排序。"""
    rows: List[Tuple[str, str, Dict[str, int]]] = []
    for comp, name in comps_and_names:
        st = tables.get(comp, {}).get(name)
        if st is None:
            continue
        rows.append((comp, name, dict(st)))

    def rk(x: Tuple[str, str, Dict[str, int]]) -> Tuple[int, int, int, int, int]:
        _, n, s = x
        return table_sort_key(team_map[n], s)

    return sorted(rows, key=rk, reverse=True)


def _group_pot_teams(group: List[Any], pot_lo: int, pot_hi: int) -> List[Any]:
    """抽签落位顺序即档位：index 0=一档 … index 5=六档（分组结束时已固定）。"""
    if len(group) < pot_hi:
        raise ValueError(f"group needs at least {pot_hi} teams for pots {pot_lo + 1}–{pot_hi}")
    return group[pot_lo:pot_hi]


def _top2_strength_key(groups: List[List[Any]], gi: int) -> Tuple[float, float, float]:
    """S7/S8：一档+二档世界排名均值（越小越强）及同分规则。"""
    pots12 = _group_pot_teams(groups[gi], 0, 2)
    ranks = [float(t.world_rank) for t in pots12]
    return (sum(ranks) / len(ranks), min(ranks), max(ranks))


def _top4_strength_key(groups: List[List[Any]], gi: int) -> Tuple[float, float, float]:
    """小组第四对阵：一至四档世界排名均值（越大越弱）及同分规则。"""
    pots14 = _group_pot_teams(groups[gi], 0, 4)
    ranks = [float(t.world_rank) for t in pots14]
    return (sum(ranks) / len(ranks), min(ranks), max(ranks))


def _rank_groups_by_key(
    groups: List[List[Any]],
    rng: random.Random,
    key_fn,
    *,
    ascending: bool,
) -> List[int]:
    """按给定 key 排序 group_index；ascending=True 表示 key 越小越靠前。"""
    meta: List[Tuple[int, Tuple[float, float, float]]] = []
    for gi in range(len(groups)):
        meta.append((gi, key_fn(groups, gi)))

    meta.sort(key=lambda x: x[1], reverse=not ascending)
    out: List[int] = []
    i = 0
    while i < len(meta):
        j = i + 1
        while j < len(meta) and meta[j][1] == meta[i][1]:
            j += 1
        chunk = [meta[k][0] for k in range(i, j)]
        if len(chunk) > 1:
            rng.shuffle(chunk)
        out.extend(chunk)
        i = j
    return out


def _r16_half(idx: int) -> int:
    return 0 if idx < 4 else 1


def placement_group(key: str, draw_strength: Dict[str, Any]) -> str:
    if key == "S7":
        return draw_strength["s7_group"]
    if key == "S8":
        return draw_strength["s8_group"]
    if key.startswith("T") and len(key) == 2:
        return draw_strength["t_slot_map"][key]
    if len(key) == 2 and key[0] in WCC_GROUP_LABELS:
        return key[0]
    raise ValueError(f"unknown placement key: {key}")


def build_t_slot_map(draw_strength: Dict[str, Any]) -> Dict[str, str]:
    """抽签后锁定 T1–T4 对应小组（含同组回避交换）。"""
    if "t_slot_map" in draw_strength:
        return draw_strength["t_slot_map"]
    s7, s8 = draw_strength["s7_group"], draw_strength["s8_group"]
    t_groups = sorted(g for g in WCC_GROUP_LABELS if g not in (s7, s8))
    resolved = resolve_t_swaps(t_groups, t_groups, draw_strength["t_opp_fourth_groups"])
    return {f"T{i + 1}": resolved[i] for i in range(4)}


def _po_def_map(draw_strength: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    return {slot: (left, right) for slot, left, right in draw_strength["playoff_slot_defs"]}


def _po_participant_groups(side: str, draw_strength: Dict[str, Any]) -> List[str]:
    t_map = draw_strength["t_slot_map"]
    if side.startswith("T"):
        return [t_map[side]]
    if len(side) == 2 and side[0] in WCC_GROUP_LABELS and side[1] in "1234":
        return [side[0]]
    raise ValueError(f"unknown playoff side: {side}")


def _seed_positions_valid(order: List[str], s7g: str, s8g: str) -> bool:
    """同组第一/第二分处上下半区；S7 与 S8 也必分处上下半区。"""
    if _r16_half(order.index("S7")) == _r16_half(order.index("S8")):
        return False
    for g, sk in ((s7g, "S7"), (s8g, "S8")):
        if _r16_half(order.index(f"{g}1")) == _r16_half(order.index(sk)):
            return False
    return True


def _assign_seed_positions(draw_strength: Dict[str, Any], rng: random.Random) -> List[str]:
    """8 个直通种子落位：6 个小组第一 + S7 + S8。"""
    s7g, s8g = draw_strength["s7_group"], draw_strength["s8_group"]
    seeds = [f"{g}1" for g in WCC_GROUP_LABELS] + ["S7", "S8"]

    for _ in range(3000):
        order = seeds[:]
        rng.shuffle(order)
        if _seed_positions_valid(order, s7g, s8g):
            return order

    slots: List[Optional[str]] = [None] * 8
    upper = [0, 1, 2, 3]
    lower = [4, 5, 6, 7]
    rng.shuffle(upper)
    rng.shuffle(lower)

    s7_pos = upper.pop()
    s8_pos = lower.pop()
    slots[s7_pos] = "S7"
    slots[s8_pos] = "S8"

    s7g_pool = lower if s7_pos < 4 else upper
    s8g_pool = lower if s8_pos < 4 else upper
    slots[s7g_pool.pop()] = f"{s7g}1"
    slots[s8g_pool.pop()] = f"{s8g}1"

    rest_g1 = [f"{g}1" for g in WCC_GROUP_LABELS if g not in (s7g, s8g)]
    free = [i for i in range(8) if slots[i] is None]
    rng.shuffle(rest_g1)
    rng.shuffle(free)
    for idx, key in zip(free, rest_g1):
        slots[idx] = key
    return [s for s in slots if s is not None]


def _is_group_first_key(left_key: str) -> bool:
    return len(left_key) == 2 and left_key[0] in WCC_GROUP_LABELS and left_key[1] == "1"


def _r16_pairing_role(left_key: str, p_slot: str) -> str:
    if left_key in ("S7", "S8") and p_slot in EARLY_PO_SLOTS:
        return "直通第二 vs 未直通第二(T附加赛)"
    if _is_group_first_key(left_key) and p_slot in LATE_PO_SLOTS:
        return "小组第一 vs 第三/第四附加赛"
    if _is_group_first_key(left_key) and p_slot in EARLY_PO_SLOTS:
        return "小组第一 vs 未直通第二(T附加赛)"
    return "—"


def _r16_same_group_conflict(left_key: str, p_slot: str, draw_strength: Dict[str, Any]) -> int:
    """同组球队在 16 强相遇的惩罚分（越大越差）。"""
    lg = placement_group(left_key, draw_strength)
    po_left, po_right = _po_def_map(draw_strength)[p_slot]
    pg = set(_po_participant_groups(po_left, draw_strength) + _po_participant_groups(po_right, draw_strength))
    if lg in pg:
        return 10
    return 0


def _fairness_po_placement_valid(
    left_at: List[str], p_at: Dict[str, int], draw_strength: Dict[str, Any]
) -> bool:
    """
    公平性硬约束：
    - S7/S8（直通小组第二）各对阵一场 P1–P4 胜者（未直通小组第二的 T 附加赛）
    - P5–P8 胜者只对阵小组第一，不与 S7/S8 相遇
    - P1–P4 中另 2 场胜者对阵小组第一
    - T 所在组的第一与 T 附加赛晋级半区分离
    """
    t_map = draw_strength["t_slot_map"]
    early_on_direct = 0
    early_on_first = 0
    late_on_first = 0

    for p_slot, r16_i in p_at.items():
        left = left_at[r16_i]
        if p_slot in EARLY_PO_SLOTS:
            if left in ("S7", "S8"):
                early_on_direct += 1
            elif _is_group_first_key(left):
                early_on_first += 1
            else:
                return False
            po_left, _ = _po_def_map(draw_strength)[p_slot]
            tg = t_map[po_left]
            g1_i = left_at.index(f"{tg}1")
            if _r16_half(r16_i) == _r16_half(g1_i):
                return False
        else:
            if left in ("S7", "S8") or not _is_group_first_key(left):
                return False
            late_on_first += 1

    return early_on_direct == 2 and early_on_first == 2 and late_on_first == 4


def build_knockout_bracket_layout(draw_strength: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    """
    抽签结束后生成 16 强签表（八强前同组回避）：
    1) 8 个直通种子落位，同组第一/第二必分上下半区，S7/S8 也必分上下半区
    2) 8 个附加赛席整体落位，满足：
       - S7/S8 各对阵一场 P1–P4（未直通小组第二 T 附加赛胜者）
       - 6 个小组第一对阵 2 场 P1–P4 + 4 场 P5–P8
       - T 与对应小组第一分处上下半区
    3) 在满足上述硬约束的方案中，尽量降低 16 强同组相遇
    """
    if "t_slot_map" not in draw_strength:
        draw_strength = {**draw_strength, "t_slot_map": build_t_slot_map(draw_strength)}

    left_at = _assign_seed_positions(draw_strength, rng)
    all_po = list(EARLY_PO_SLOTS) + list(LATE_PO_SLOTS)

    best_p_at: Optional[Dict[str, int]] = None
    best_total = 10**9

    for r16_idxs in permutations(range(8)):
        p_at = {all_po[i]: r16_idxs[i] for i in range(8)}
        if not _fairness_po_placement_valid(left_at, p_at, draw_strength):
            continue
        score = sum(_r16_same_group_conflict(left_at[i], p, draw_strength) for p, i in p_at.items())
        if score < best_total:
            best_total = score
            best_p_at = dict(p_at)

    if best_p_at is None:
        raise RuntimeError("knockout bracket: cannot satisfy fairness + half-separation constraints")

    r16_slots: List[Tuple[str, str, str]] = []
    layout_rows: List[Dict[str, Any]] = []
    for i in range(8):
        slot_id = f"R16-{i + 1}"
        left_key = left_at[i]
        p_slot = next(p for p, idx in best_p_at.items() if idx == i)
        r16_slots.append((slot_id, left_key, p_slot))
        layout_rows.append(
            {
                "场次": slot_id,
                "半区": "上半区" if i < 4 else "下半区",
                "直通种子": left_key,
                "附加赛席": p_slot,
                "对阵性质": _r16_pairing_role(left_key, p_slot),
                "同组16强冲突分": _r16_same_group_conflict(left_key, p_slot, draw_strength),
            }
        )

    return {
        "r16_slots": r16_slots,
        "seed_positions": left_at,
        "po_positions": best_p_at,
        "layout_log": layout_rows,
        "conflict_score": best_total,
    }


def get_r16_slots(draw_strength: Dict[str, Any], rng: Optional[random.Random] = None) -> List[Tuple[str, str, str]]:
    if draw_strength.get("r16_slots"):
        return draw_strength["r16_slots"]
    if rng is None:
        raise ValueError("r16_slots missing and no rng to build layout")
    layout = build_knockout_bracket_layout(draw_strength, rng)
    return layout["r16_slots"]


def build_playoff_slots(fourth_t_groups: List[str], fourth_3rd_groups: List[str]) -> List[Tuple[str, str, str]]:
    """
    24 强附加赛 P1–P8：
    - P1–P4：T1–T4 对阵「前四平均排名最弱四组」的小组第四
    - P5–P6：余下两组第三 vs 第四交叉
    - P7–P8：最弱四组内部第三两两对阵
    """
    if len(fourth_t_groups) != 4 or len(fourth_3rd_groups) != 2:
        raise ValueError("playoff slot build: need 4 fourth_t groups and 2 fourth_3rd groups")
    ft = fourth_t_groups
    a, b = fourth_3rd_groups
    return [
        ("P1", "T1", f"{ft[0]}4"),
        ("P2", "T2", f"{ft[1]}4"),
        ("P3", "T3", f"{ft[2]}4"),
        ("P4", "T4", f"{ft[3]}4"),
        ("P5", f"{a}3", f"{b}4"),
        ("P6", f"{b}3", f"{a}4"),
        ("P7", f"{ft[0]}3", f"{ft[3]}3"),
        ("P8", f"{ft[1]}3", f"{ft[2]}3"),
    ]


def compute_draw_strength(groups: List[List[Any]], rng: random.Random) -> Dict[str, Any]:
    """
    分组抽签结束后立即锁定（与小组赛赛果无关）：
    1) 各组一档+二档世界排名均值 → 最优两组第二名直通 16 强（S7/S8）
    2) 各组一至四档世界排名均值 → 最弱四组的小组第四对阵其他组第二名（T1–T4）
    """
    second_order = _rank_groups_by_key(groups, rng, _top2_strength_key, ascending=True)
    s7_gi, s8_gi = second_order[0], second_order[1]

    fourth_order_worst = _rank_groups_by_key(groups, rng, _top4_strength_key, ascending=False)
    fourth_t_gis = fourth_order_worst[:4]
    fourth_3rd_gis = fourth_order_worst[4:]

    fourth_t_groups = [WCC_GROUP_LABELS[gi] for gi in fourth_t_gis]
    fourth_3rd_groups = [WCC_GROUP_LABELS[gi] for gi in fourth_3rd_gis]
    t_opp_fourth_groups = fourth_t_groups[:]

    second_strength_log: List[Dict[str, Any]] = []
    for gi in second_order:
        pots12 = _group_pot_teams(groups[gi], 0, 2)
        ranks = [int(t.world_rank) for t in pots12]
        slot = "S7直通" if gi == s7_gi else ("S8直通" if gi == s8_gi else "—")
        second_strength_log.append(
            {
                "小组": WCC_GROUP_LABELS[gi],
                "一档": pots12[0].name,
                "二档": pots12[1].name,
                "一档排名": ranks[0],
                "二档排名": ranks[1],
                "前二平均世界排名": round(sum(ranks) / len(ranks), 2),
                "第二名直通槽位": slot,
            }
        )

    fourth_strength_log: List[Dict[str, Any]] = []
    for gi in fourth_order_worst:
        pots14 = _group_pot_teams(groups[gi], 0, 4)
        ranks = [int(t.world_rank) for t in pots14]
        lab = WCC_GROUP_LABELS[gi]
        if gi in fourth_t_gis:
            slot = f"小组第四→T{fourth_t_groups.index(lab) + 1}对手"
        else:
            slot = "小组第四→与另一强组第三交叉(P5/P6)"
        fourth_strength_log.append(
            {
                "小组": lab,
                "一至四档": [t.name for t in pots14],
                "一至四档排名": ranks,
                "前四平均世界排名": round(sum(ranks) / len(ranks), 2),
                "小组第四附加赛角色": slot,
            }
        )

    playoff_slot_defs = build_playoff_slots(fourth_t_groups, fourth_3rd_groups)
    base = {
        "second_order": second_order,
        "s7_gi": s7_gi,
        "s8_gi": s8_gi,
        "s7_group": WCC_GROUP_LABELS[s7_gi],
        "s8_group": WCC_GROUP_LABELS[s8_gi],
        "second_strength_log": second_strength_log,
        "fourth_order_worst": fourth_order_worst,
        "fourth_t_groups": fourth_t_groups,
        "fourth_3rd_groups": fourth_3rd_groups,
        "t_opp_fourth_groups": t_opp_fourth_groups,
        "fourth_strength_log": fourth_strength_log,
        "playoff_slot_defs": playoff_slot_defs,
        # 兼容旧字段名
        "strength_order": second_order,
        "strength_log": second_strength_log,
    }
    base["t_slot_map"] = build_t_slot_map(base)
    layout = build_knockout_bracket_layout(base, rng)
    base["r16_slots"] = layout["r16_slots"]
    base["seed_positions"] = layout["seed_positions"]
    base["po_positions"] = layout["po_positions"]
    base["bracket_layout_log"] = layout["layout_log"]
    base["bracket_conflict_score"] = layout["conflict_score"]
    return base


def team_group_index(team_name: str, groups: List[List[Any]]) -> int:
    for gi, grp in enumerate(groups):
        if any(t.name == team_name for t in grp):
            return gi
    raise ValueError(f"team {team_name} not in draw groups")


def resolve_t_swaps(t_teams: List[Any], t_group_labels: List[str], opp_fourth_group_labels: List[str]) -> List[Any]:
    """
    T1–T4 按小组字母序分配后，按 P1→P4 顺序处理同组回避：
    若 Ti 将迎战同组第四，与后续首个不冲突的 Tj 交换位置。
    """
    if len(t_teams) != 4 or len(opp_fourth_group_labels) != 4:
        raise ValueError("resolve_t_swaps: need exactly 4 T teams and 4 opponent group labels")
    slots = list(range(4))
    for pi in range(4):
        opp_g = opp_fourth_group_labels[pi]
        ti = slots[pi]
        if t_group_labels[ti] != opp_g:
            continue
        for pj in range(pi + 1, 4):
            tj = slots[pj]
            if t_group_labels[tj] != opp_g:
                slots[pi], slots[pj] = slots[pj], slots[pi]
                break
    return [t_teams[slots[i]] for i in range(4)]


def compute_bracket_state(
    groups: List[List[Any]],
    team_map: Dict[str, Any],
    tables: Dict[str, Dict[str, Dict[str, int]]],
    comp_prefix: str,
    rng: random.Random,
    *,
    draw_strength: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """小组赛结束后，按抽签时已锁定的规则确定 S7/S8 与 24 强附加赛签表。"""
    placements: Dict[str, Any] = {}
    seconds: List[Any] = []
    thirds: List[Any] = []
    fourths: List[Any] = []

    for gi, lab in enumerate(WCC_GROUP_LABELS):
        comp = f"{comp_prefix}-GS-{lab}"
        tab = sorted_group_table(team_map, tables[comp])
        for rank, (name, _) in enumerate(tab[:4], start=1):
            placements[f"{lab}{rank}"] = team_map[name]
        seconds.append(team_map[tab[1][0]])
        thirds.append(team_map[tab[2][0]])
        fourths.append(team_map[tab[3][0]])

    if draw_strength is None:
        raise ValueError(
            f"{comp_prefix}: draw_strength missing — S7/S8 与附加赛签位须在分组抽签结束时锁定"
        )
    s7_gi, s8_gi = draw_strength["s7_gi"], draw_strength["s8_gi"]
    s7, s8 = seconds[s7_gi], seconds[s8_gi]
    placements["S7"] = s7
    placements["S8"] = s8

    non_direct: List[Tuple[str, Any]] = []
    for gi, sec in enumerate(seconds):
        if gi in (s7_gi, s8_gi):
            continue
        non_direct.append((WCC_GROUP_LABELS[gi], sec))
    non_direct.sort(key=lambda x: x[0])
    t_labels = [x[0] for x in non_direct]
    t_teams_raw = [x[1] for x in non_direct]
    t_resolved = resolve_t_swaps(t_teams_raw, t_labels, draw_strength["t_opp_fourth_groups"])
    for i, tm in enumerate(t_resolved, start=1):
        placements[f"T{i}"] = tm

    second_log = []
    for row in draw_strength["second_strength_log"]:
        lab = row["小组"]
        gi = WCC_GROUP_LABELS.index(lab)
        second_log.append({**row, "小组第二(赛后)": seconds[gi].name})

    fourth_log = []
    for row in draw_strength["fourth_strength_log"]:
        lab = row["小组"]
        gi = WCC_GROUP_LABELS.index(lab)
        fourth_log.append({**row, "小组第四(赛后)": placements[f"{lab}4"].name})

    playoff_pairs: List[Tuple[str, Any, Any, str]] = []
    slot_teams: Dict[str, Any] = dict(placements)
    for slot, left, right in draw_strength["playoff_slot_defs"]:
        playoff_pairs.append((slot, slot_teams[left], slot_teams[right], f"{left} vs {right}"))

    r16_pairs: List[Tuple[Any, str, str]] = []
    r16_slots = get_r16_slots(draw_strength, rng)
    for slot, left, right in r16_slots:
        r16_pairs.append((slot, slot_teams[left], left, right))

    return {
        "placements": placements,
        "S7": s7,
        "S8": s8,
        "S7_group": draw_strength["s7_group"],
        "S8_group": draw_strength["s8_group"],
        "second_strength_log": second_log,
        "fourth_strength_log": fourth_log,
        "fourth_t_groups": draw_strength["fourth_t_groups"],
        "fourth_3rd_groups": draw_strength["fourth_3rd_groups"],
        "playoff_pairs": playoff_pairs,
        "r16_slots": r16_slots,
        "r16_template": r16_pairs,
        "thirds": thirds,
        "fourths": fourths,
        "strength_log": second_log,
        "strength_order": draw_strength["second_order"],
    }


def gs_comp_label(comp_prefix: str, group_lab: str) -> str:
    return f"{comp_prefix}-GS-{group_lab}"


def gs_tables_ready(
    comp_prefix: str,
    tables: Dict[str, Dict[str, Dict[str, int]]],
    *,
    matches_per_team: int = 5,
) -> bool:
    for lab in WCC_GROUP_LABELS:
        comp = gs_comp_label(comp_prefix, lab)
        tab = tables.get(comp)
        if not tab or len(tab) != 6:
            return False
        for s in tab.values():
            if s.get("P", 0) != matches_per_team:
                return False
    return True
