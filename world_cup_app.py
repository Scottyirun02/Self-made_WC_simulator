"""
世界杯模拟器 — Streamlit 网页界面
运行: streamlit run world_cup_app.py
或:  python -m streamlit run d:/intern/world_cup_app.py
"""
from __future__ import annotations

import html
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 保证可导入同目录下的 world_cup_game
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from continental_cups import (
    CONTINENTAL_CODES,
    CONTINENTAL_LABELS,
    FINAL_GROUP_LABELS,
    QF_FROM_R16,
    R16_PAIRINGS,
    SF_FROM_QF,
)
from world_cup_challenger import WCC_GROUP_LABELS, build_t_slot_map, get_r16_slots, gs_tables_ready
from world_cup_game import (
    AFC_TEAMS,
    CAF_TEAMS,
    CONCACAF_TEAMS,
    CONFEDS,
    CONMEBOL_TEAMS,
    OFC_TEAMS,
    Simulator,
    TABLE_ZONES,
    UEFA_TEAMS,
    zone_label_for_rank,
)

BRACKET_CUP_LABELS = {
    "WORLD-CHAMPIONS": "世界冠军杯",
    "WORLD-LEAGUE": "世界联赛杯",
    "WORLD-ASSOCIATION": "世界协会杯",
    "WCC": "世界挑战者杯",
    **{c: CONTINENTAL_LABELS[c] for c in CONTINENTAL_CODES},
}

GROUP_STAGE_CUPS = [
    ("EURO", "欧洲杯"),
    ("AFCON", "非洲杯"),
    ("APAC", "亚太杯"),
    ("AMERICA", "美洲杯"),
    ("WORLD-CHAMPIONS", "世界冠军杯"),
    ("WORLD-LEAGUE", "世界联赛杯"),
    ("WORLD-ASSOCIATION", "世界协会杯"),
    ("WCC", "世界挑战者杯"),
]

HOST_POOLS = {
    "EURO": UEFA_TEAMS,
    "AFCON": CAF_TEAMS,
    "APAC": AFC_TEAMS + OFC_TEAMS,
    "AMERICA": CONCACAF_TEAMS + CONMEBOL_TEAMS,
}

CONFED_LABELS = {
    "UEFA": "欧洲 (UEFA)",
    "AFC": "亚洲 (AFC)",
    "CONCACAF": "中北美 (CONCACAF)",
    "CAF": "非洲 (CAF)",
    "OFC": "大洋洲 (OFC)",
    "CONMEBOL": "南美 (CONMEBOL)",
}

st.set_page_config(
    page_title="四年周期模拟器",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _default_hosts() -> Dict[str, str]:
    return {
        "EURO": "Germany",
        "AFCON": "Morocco",
        "APAC": "Japan",
        "AMERICA": "Brazil",
    }


def _ensure_sim(seed: int, hosts: Optional[Dict[str, str]] = None) -> Simulator:
    """种子变化或尚未开局时创建；东道主仅在「新开局」时生效（由调用方清空 sim）。"""
    hosts = hosts or st.session_state.get("hosts") or _default_hosts()
    key_hosts = tuple(sorted(hosts.items()))
    if "sim" not in st.session_state or st.session_state.get("sim_seed") != seed:
        st.session_state.sim = Simulator(seed, hosts=hosts)
        st.session_state.sim_seed = seed
        st.session_state.sim_hosts = key_hosts
        st.session_state.hosts = dict(hosts)
    return st.session_state.sim


def _two_leg_aggregate_str(sim: Simulator, m) -> str:
    """按队名字母序固定双方，汇总同一对阵两回合总进球（仅两回合赛且两场都已赛）。"""
    if getattr(m, "kind", "") != "two_leg" or not m.played:
        return ""
    n1, n2 = sorted([m.home.name, m.away.name])
    legs = [
        x
        for x in sim.all_results
        if x.kind == "two_leg"
        and x.comp == m.comp
        and x.played
        and {x.home.name, x.away.name} == {n1, n2}
    ]
    if len(legs) < 2:
        return ""
    g1, g2 = 0, 0
    for x in sorted(legs, key=lambda z: (z.round_num, z.day)):
        if x.home.name == n1:
            g1 += x.hg
            g2 += x.ag
        else:
            g1 += x.ag
            g2 += x.hg
    return f"{n1} {g1}-{g2} {n2}"


def _matches_to_df(sim: Simulator) -> pd.DataFrame:
    rows = []
    for m in sim.all_results:
        if m.winner is not None:
            w = m.winner.name
        elif m.hg > m.ag:
            w = m.home.name
        elif m.ag > m.hg:
            w = m.away.name
        else:
            w = "平局"
        note = (m.score_note or "").strip()
        sc = f"{m.hg}-{m.ag}"
        if note:
            sc = f"{sc} ({note})"
        agg = _two_leg_aggregate_str(sim, m)
        rows.append(
            {
                "比赛日": m.day,
                "轮次": m.round_num,
                "赛事": m.comp,
                "阶段": m.stage,
                "赛制": m.kind,
                "场地": "中立球场" if m.neutral else "主场制",
                "主队": m.home.name,
                "OVR主": round(m.home_match_ovr if m.home_match_ovr is not None else m.home.ovr, 1),
                "比分": sc,
                "客队": m.away.name,
                "OVR客": round(m.away_match_ovr if m.away_match_ovr is not None else m.away.ovr, 1),
                "两回合累计": agg if agg else "—",
                "结果": w,
            }
        )
    return pd.DataFrame(rows)


def _team_draw_pot(sim: Simulator, prefix: str, group_lab: str, team_name: str) -> Optional[int]:
    """分组抽签落位档位：1=一档 …（抽签结束时固定）。"""
    if prefix in CONTINENTAL_CODES:
        groups = getattr(sim, "_cont_finals_groups", {}).get(prefix, [])
        labels = FINAL_GROUP_LABELS
    elif prefix == "WCC":
        groups = sim._wcc_draw_groups
        labels = WCC_GROUP_LABELS
    else:
        groups = getattr(sim, "_cup_draw_groups", {}).get(prefix, [])
        labels = WCC_GROUP_LABELS
    if not groups or group_lab not in labels:
        return None
    gi = labels.index(group_lab)
    if gi >= len(groups):
        return None
    for pi, t in enumerate(groups[gi]):
        if t.name == team_name:
            return pi + 1
    return None


def _rank_cn(n: int) -> str:
    return {1: "一", 2: "二", 3: "三", 4: "四"}.get(n, str(n))


def _group_rank_label(key: str) -> str:
    if len(key) == 2 and key[0] in WCC_GROUP_LABELS and key[1] in "1234":
        return f"{key[0]}组第{_rank_cn(int(key[1]))}名"
    return key


def _t_slot_group_map(ds: Dict[str, Any]) -> Dict[str, str]:
    return ds.get("t_slot_map") or build_t_slot_map(ds)


def _cup_r16_slots(sim: Simulator, prefix: str) -> List[Tuple[str, str, str]]:
    ds = getattr(sim, "_challenger_draw_strength", {}).get(prefix)
    if not ds:
        return []
    return get_r16_slots(ds, sim.rng)


def _bracket_side_label(key: str, ds: Dict[str, Any], t_map: Dict[str, str]) -> str:
    if key == "S7":
        return f"S7（{ds['s7_group']}组第二名）"
    if key == "S8":
        return f"S8（{ds['s8_group']}组第二名）"
    if key in t_map:
        return f"{key}（{t_map[key]}组第二名）"
    if key.startswith("P") and key[1:].isdigit():
        return f"{key}胜者"
    return _group_rank_label(key)


def _po_to_r16_slot(ds: Dict[str, Any]) -> Dict[str, str]:
    return {p_key: slot_id for slot_id, _left, p_key in ds.get("r16_slots", [])}


def _pre_knockout_po_df(ds: Dict[str, Any], t_map: Dict[str, str]) -> pd.DataFrame:
    po_r16 = _po_to_r16_slot(ds)
    rows = []
    for slot, left, right in ds.get("playoff_slot_defs", []):
        rows.append(
            {
                "场次": slot,
                "主队": _bracket_side_label(left, ds, t_map),
                "客队": _bracket_side_label(right, ds, t_map),
                "胜者晋级": po_r16.get(slot, "—"),
            }
        )
    return pd.DataFrame(rows)


def _ensure_challenger_bracket_state(sim: Simulator, prefix: str) -> None:
    """小组赛积分齐备后立刻生成 24→16 落位（不必等附加赛开踢）。"""
    if prefix in CONTINENTAL_CODES:
        return
    if prefix in getattr(sim, "_challenger_bracket_state", {}):
        return
    if not gs_tables_ready(prefix, sim.tables):
        return
    try:
        sim._challenger_refresh_bracket_state(prefix)
    except Exception:
        return


def _live_po_matchups_df(sim: Simulator, prefix: str, ds: Dict[str, Any], t_map: Dict[str, str]) -> pd.DataFrame:
    """小组赛结束后展示真实 24 强附加赛对阵；否则仍用签位占位。"""
    _ensure_challenger_bracket_state(sim, prefix)
    state = getattr(sim, "_challenger_bracket_state", {}).get(prefix)
    po_r16 = _po_to_r16_slot(ds)
    if state and state.get("playoff_pairs"):
        rows = []
        for slot, a, b, desc in state["playoff_pairs"]:
            rows.append(
                {
                    "场次": slot,
                    "主队": a.name,
                    "客队": b.name,
                    "签位来源": desc,
                    "胜者晋级": po_r16.get(slot, "—"),
                }
            )
        return pd.DataFrame(rows)
    return _pre_knockout_po_df(ds, t_map)


def _pre_knockout_r16_df(ds: Dict[str, Any], t_map: Dict[str, str]) -> pd.DataFrame:
    rows = []
    for slot_id, left_key, right_key in ds.get("r16_slots", []):
        rows.append(
            {
                "场次": slot_id,
                "半区": "上半区" if int(slot_id.split("-")[1]) <= 4 else "下半区",
                "对阵性质": (
                    "直通第二 vs T附加赛"
                    if left_key in ("S7", "S8") and right_key in ("P1", "P2", "P3", "P4")
                    else (
                        "小组第一 vs 第三/第四附加赛"
                        if left_key.endswith("1") and right_key in ("P5", "P6", "P7", "P8")
                        else "小组第一 vs T附加赛"
                    )
                ),
                "主队": _bracket_side_label(left_key, ds, t_map),
                "客队": _bracket_side_label(right_key, ds, t_map),
            }
        )
    return pd.DataFrame(rows)


def _pre_knockout_layout_df(ds: Dict[str, Any]) -> pd.DataFrame:
    rows = ds.get("bracket_layout_log", [])
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _pre_knockout_later_df() -> pd.DataFrame:
    rows = [
        {"阶段": "8强", "场次": "QF1", "对阵": "R16-1胜者 vs R16-2胜者"},
        {"阶段": "8强", "场次": "QF2", "对阵": "R16-3胜者 vs R16-4胜者"},
        {"阶段": "8强", "场次": "QF3", "对阵": "R16-5胜者 vs R16-6胜者"},
        {"阶段": "8强", "场次": "QF4", "对阵": "R16-7胜者 vs R16-8胜者"},
        {"阶段": "半决赛", "场次": "SF1", "对阵": "QF1胜者 vs QF2胜者"},
        {"阶段": "半决赛", "场次": "SF2", "对阵": "QF3胜者 vs QF4胜者"},
        {"阶段": "决赛", "场次": "决赛", "对阵": "SF1胜者 vs SF2胜者"},
    ]
    return pd.DataFrame(rows)


def _pre_knockout_direct_df(ds: Dict[str, Any]) -> pd.DataFrame:
    rows = [
        {"签位": f"{lab}1", "说明": f"{lab}组第一名 → 16强直通"}
        for lab in WCC_GROUP_LABELS
    ]
    rows.append({"签位": "S7", "说明": f"{ds['s7_group']}组第二名 → 16强直通"})
    rows.append({"签位": "S8", "说明": f"{ds['s8_group']}组第二名 → 16强直通"})
    return pd.DataFrame(rows)


def _draw_strength_summary_df(sim: Simulator, prefix: str) -> pd.DataFrame:
    ds = getattr(sim, "_challenger_draw_strength", {}).get(prefix)
    if not ds:
        return pd.DataFrame()
    rows = []
    for row in ds.get("second_strength_log", []):
        rows.append(
            {
                "小组": row["小组"],
                "一档": row.get("一档", "—"),
                "二档": row.get("二档", "—"),
                "一二档均值": row.get("前二平均世界排名"),
                "S7/S8槽位": row.get("第二名直通槽位", "—"),
            }
        )
    return pd.DataFrame(rows)


def _group_zone_label(sim: Simulator, prefix: str, group_lab: str, rank: int) -> str:
    """按抽签时已锁定的组硬度，标注各队晋级区间。"""
    if rank == 1:
        return "16强直通（小组第一）"
    if rank == 3:
        return "24强附加赛"
    if rank == 4:
        ds = getattr(sim, "_challenger_draw_strength", {}).get(prefix)
        if ds:
            if group_lab in ds.get("fourth_t_groups", []):
                return "24强附加赛（小组第四→对阵T）"
            if group_lab in ds.get("fourth_3rd_groups", []):
                return "24强附加赛（小组第四→与第三交叉）"
        return "24强附加赛"
    if rank == 5 or rank >= 6:
        return "未晋级淘汰赛"
    if rank == 2:
        ds = getattr(sim, "_challenger_draw_strength", {}).get(prefix)
        if not ds:
            return zone_label_for_rank(f"{prefix}-GS-{group_lab}", 2)
        if group_lab == ds.get("s7_group"):
            return "16强直通（S7·组硬度第1）"
        if group_lab == ds.get("s8_group"):
            return "16强直通（S8·组硬度第2）"
        return "24强附加赛（T位候选）"
    return "—"


def _cup_group_labels(prefix: str) -> List[str]:
    if prefix in CONTINENTAL_CODES:
        return list(FINAL_GROUP_LABELS)
    return list(WCC_GROUP_LABELS)


def _cup_draw_groups(sim: Simulator, prefix: str) -> List[List[Any]]:
    """已抽签的正赛分组（有分组即视为应展示积分榜）。"""
    if prefix in CONTINENTAL_CODES:
        return list(getattr(sim, "_cont_finals_groups", {}).get(prefix) or [])
    if prefix == "WCC":
        return list(getattr(sim, "_wcc_draw_groups", None) or [])
    return list(getattr(sim, "_cup_draw_groups", {}).get(prefix) or [])


def _ensure_cup_group_tables(sim: Simulator, prefix: str) -> None:
    """
    抽签完成后保证各小组有全 0 积分榜。
    覆盖：旧会话在补丁前已抽签、或漏写 tables 的情况。
    """
    groups = _cup_draw_groups(sim, prefix)
    if not groups:
        return
    labels = _cup_group_labels(prefix)
    for gi, lab in enumerate(labels):
        if gi >= len(groups):
            break
        sim._init_table(f"{prefix}-GS-{lab}", groups[gi])


def _cup_has_group_data(sim: Simulator, prefix: str) -> bool:
    if _cup_draw_groups(sim, prefix):
        return True
    return any(sim.tables.get(f"{prefix}-GS-{lab}") for lab in _cup_group_labels(prefix))


def _next_opponent_name(sim: Simulator, team_name: str, prefer_comp: str = "") -> str:
    """下一场对手队名；优先同赛事中最近一场，否则任意最近一场。"""
    any_opp: Optional[str] = None
    for day in sim.phase_matchdays:
        for m in day:
            if m.home.name != team_name and m.away.name != team_name:
                continue
            opp = m.away.name if m.home.name == team_name else m.home.name
            if prefer_comp and m.comp == prefer_comp:
                return opp
            if any_opp is None:
                any_opp = opp
    return any_opp or "—"


def _group_table_df(sim: Simulator, prefix: str, group_lab: str) -> pd.DataFrame:
    """单组积分榜，列与联赛阶段一致。"""
    comp = f"{prefix}-GS-{group_lab}"
    tab = sim._sorted_table(comp)
    if not tab:
        return pd.DataFrame()
    out = []
    for rank, (name, s) in enumerate(tab, 1):
        t = sim.team_map[name]
        pot = _team_draw_pot(sim, prefix, group_lab, name)
        if prefix in CONTINENTAL_CODES:
            zone = zone_label_for_rank(comp, rank)
        else:
            zone = _group_zone_label(sim, prefix, group_lab, rank)
        out.append(
            {
                "排名": rank,
                "球队": name,
                "抽签档位": f"{pot}档" if pot else "—",
                "大洲": t.confed,
                "晋级区间": zone,
                "积分": s["PTS"],
                "场次": s["P"],
                "胜": s["W"],
                "平": s["D"],
                "负": s["L"],
                "进球": s["GF"],
                "失球": s["GA"],
                "净胜": s["GD"],
                "世界排名": sim.live_ranks.get(name, t.world_rank),
                "下一场对手": _next_opponent_name(sim, name, prefer_comp=comp),
            }
        )
    return pd.DataFrame(out)


def _render_cup_group_standings(sim: Simulator, prefix: str, cup_label: str) -> None:
    """按小组分块展示积分榜。"""
    _ensure_cup_group_tables(sim, prefix)
    ds = getattr(sim, "_challenger_draw_strength", {}).get(prefix)
    has_data = _cup_has_group_data(sim, prefix)
    labels = _cup_group_labels(prefix)
    is_cont = prefix in CONTINENTAL_CODES

    if not ds and not has_data:
        st.info(f"「{cup_label}」小组赛尚未开始或暂无积分榜。请推进比赛日。")
        return

    with st.expander("晋级线说明", expanded=False):
        if is_cont:
            st.markdown("- 第 **1–2** 名：晋级 16 强")
            st.markdown("- 第 **3–4** 名：小组出局")
            st.markdown("- 东道主固定 **A1**")
        else:
            st.markdown("- 第 **1** 名：16强直通（小组第一）")
            st.markdown("- 第 **2** 名：抽签落位**一档+二档**世界排名均值最优两组直通（S7/S8），其余进 24 强附加赛")
            st.markdown("- 第 **3** 名：24 强附加赛")
            st.markdown("- 第 **4** 名：抽签落位**一至四档**均值最弱四组 → 对阵 T；余下两组 → 与第三交叉")
            st.markdown("- 第 **5–6** 名：未晋级淘汰赛")
            if ds:
                st.markdown(
                    f"- S7 直通小组 **{ds['s7_group']}** · S8 直通小组 **{ds['s8_group']}**（抽签后锁定）"
                )
                ft = ds.get("fourth_t_groups", [])
                f3 = ds.get("fourth_3rd_groups", [])
                if ft:
                    st.markdown(f"- 小组第四对阵 T：**{', '.join(ft)}** 组")
                if f3:
                    st.markdown(f"- 小组第四与第三交叉：**{', '.join(f3)}** 组")
            sdf = _draw_strength_summary_df(sim, prefix)
            if not sdf.empty:
                st.markdown("**组硬度（抽签后锁定，与积分榜无关）**")
                st.dataframe(sdf, use_container_width=True, hide_index=True)

            if ds:
                t_map = _t_slot_group_map(ds)
                st.markdown("---")
                st.markdown("**淘汰赛对阵表（小组抽签结束即锁定，正赛开赛前已定）**")
                st.caption(
                    "队名以「X组第N名」表示小组赛结束后的落位；S7/S8 各对阵一场 P1–P4（未直通小组第二），"
                    "小组第一对阵 P5–P8（第三/第四附加赛路径）。"
                )
                st.markdown("**16强直通签位（8 席）**")
                st.dataframe(_pre_knockout_direct_df(ds), use_container_width=True, hide_index=True)
                st.markdown("**24强附加赛（8 场 → 8 个 16 强席）**")
                st.dataframe(
                    _live_po_matchups_df(sim, prefix, ds, t_map),
                    use_container_width=True,
                    hide_index=True,
                )
                st.markdown("**16强（算法生成签表 · 八强前同组回避）**")
                st.dataframe(_pre_knockout_r16_df(ds, t_map), use_container_width=True, hide_index=True)
                layout_df = _pre_knockout_layout_df(ds)
                if not layout_df.empty:
                    st.caption(
                        f"签表同组16强冲突分：{ds.get('bracket_conflict_score', '—')}（越低越好，0 为无同组16强相遇）"
                    )
                st.markdown("**8强 → 半决赛 → 决赛**")
                st.dataframe(_pre_knockout_later_df(), use_container_width=True, hide_index=True)

    if not has_data:
        st.info(f"「{cup_label}」小组赛尚未抽签，暂无积分榜。")
        return

    for lab in labels:
        gdf = _group_table_df(sim, prefix, lab)
        if gdf.empty:
            continue
        st.markdown(f"**{lab} 组**")
        st.dataframe(gdf, use_container_width=True, hide_index=True)


def _prelim_results_df(sim: Simulator, confed: str) -> pd.DataFrame:
    rows = []
    for m in sim.all_results:
        if m.comp != f"{confed}-PRE":
            continue
        w = _match_winner_name(m)
        note = (m.score_note or "").strip()
        sc = f"{m.hg}-{m.ag}"
        if note:
            sc = f"{sc} ({note})"
        rows.append(
            {
                "比赛日": m.day,
                "主队": m.home.name,
                "比分": sc,
                "客队": m.away.name,
                "晋级": w,
            }
        )
    return pd.DataFrame(rows)


def _combined_group_tables_df(sim: Simulator, prefix: str, cup_label: str) -> pd.DataFrame:
    """某杯赛各小组积分榜合并为一张表。"""
    _ensure_cup_group_tables(sim, prefix)
    rows = []
    ds = getattr(sim, "_challenger_draw_strength", {}).get(prefix)
    for lab in _cup_group_labels(prefix):
        comp = f"{prefix}-GS-{lab}"
        tab = sim._sorted_table(comp)
        if not tab:
            continue
        for rank, (name, s) in enumerate(tab, 1):
            t = sim.team_map[name]
            if prefix in CONTINENTAL_CODES:
                zone = zone_label_for_rank(comp, rank)
            else:
                zone = _group_zone_label(sim, prefix, lab, rank)
            rows.append(
                {
                    "杯赛": cup_label,
                    "小组": lab,
                    "排名": rank,
                    "球队": name,
                    "大洲": t.confed,
                    "晋级区间": zone,
                    "S7/S8槽位": (
                        "S7"
                        if ds and lab == ds.get("s7_group")
                        else ("S8" if ds and lab == ds.get("s8_group") else "—")
                    ),
                    "积分": s["PTS"],
                    "场次": s["P"],
                    "胜": s["W"],
                    "平": s["D"],
                    "负": s["L"],
                    "进球": s["GF"],
                    "失球": s["GA"],
                    "净胜": s["GD"],
                    "世界排名": sim.live_ranks.get(name, t.world_rank),
                    "下一场对手": _next_opponent_name(sim, name, prefer_comp=comp),
                }
            )
    return pd.DataFrame(rows)


def _table_to_df(sim: Simulator, comp: str) -> pd.DataFrame:
    tab = sim._sorted_table(comp)
    if not tab:
        return pd.DataFrame()
    out = []
    for i, (name, s) in enumerate(tab, 1):
        t = sim.team_map[name]
        out.append(
            {
                "排名": i,
                "球队": name,
                "大洲": t.confed,
                "晋级区间": zone_label_for_rank(comp, i),
                "积分": s["PTS"],
                "场次": s["P"],
                "胜": s["W"],
                "平": s["D"],
                "负": s["L"],
                "进球": s["GF"],
                "失球": s["GA"],
                "净胜": s["GD"],
                "世界排名": sim.live_ranks.get(name, t.world_rank),
                "下一场对手": _next_opponent_name(sim, name, prefer_comp=comp),
            }
        )
    return pd.DataFrame(out)


def _match_winner_name(m) -> str:
    if not getattr(m, "played", False):
        return "—"
    if m.winner is not None:
        return m.winner.name
    if m.hg > m.ag:
        return m.home.name
    if m.ag > m.hg:
        return m.away.name
    return "待定"


def _playoff_slot_from_stage(stage: str) -> str:
    for i in range(1, 9):
        tag = f"P{i}"
        if tag in stage:
            return tag
    return ""


def _esc(s: str) -> str:
    return html.escape(str(s))


def _iter_scheduled_matches(sim: Simulator) -> List[Any]:
    """尚未踢完的场次（当前阶段赛程）。"""
    out: List[Any] = []
    for day in getattr(sim, "phase_matchdays", []) or []:
        out.extend(day)
    return out


def _cup_knockout_rounds(sim: Simulator, cup_base: str) -> Dict[str, Any]:
    """收集淘汰赛各轮次：已赛果 + 已排期未赛（小组结束后即可看到 24→16 对阵）。"""
    po_comp = f"{cup_base}-PO"
    ko_comp = f"{cup_base}-KO"
    order = {id(m): i for i, m in enumerate(sim.all_results)}

    def _merge_by_slot(played: List[Any], scheduled: List[Any]) -> List[Any]:
        by_slot: Dict[str, Any] = {}
        for m in scheduled + played:  # played 覆盖同槽未赛
            slot = _playoff_slot_from_stage(m.stage)
            if not slot:
                continue
            prev = by_slot.get(slot)
            if prev is None or (m.played and not prev.played):
                by_slot[slot] = m
        return [by_slot.get(f"P{i}") for i in range(1, 9)]

    po_played = [
        m for m in sim.all_results if m.comp == po_comp and m.played and "24强附加赛" in m.stage
    ]
    po_sched = [
        m
        for m in _iter_scheduled_matches(sim)
        if m.comp == po_comp and "24强附加赛" in m.stage
    ]
    po_ordered = _merge_by_slot(po_played, po_sched)
    if not any(po_ordered):
        _ensure_challenger_bracket_state(sim, cup_base)
        state = getattr(sim, "_challenger_bracket_state", {}).get(cup_base) or {}
        pairs = state.get("playoff_pairs") or []
        if pairs:
            from types import SimpleNamespace

            by_slot: Dict[str, Any] = {}
            for slot, a, b, _desc in pairs:
                by_slot[slot] = SimpleNamespace(
                    home=a,
                    away=b,
                    played=False,
                    hg=0,
                    ag=0,
                    winner=None,
                    stage=f"24强附加赛·{slot}",
                    comp=po_comp,
                    round_num=int(slot[1:]) if slot[1:].isdigit() else 0,
                    day=0,
                    score_note="",
                )
            po_ordered = [by_slot.get(f"P{i}") for i in range(1, 9)]

    def _merge_ko(stage_prefix: str, played_filter) -> List[Any]:
        played = [
            m
            for m in sim.all_results
            if m.comp == ko_comp and m.played and played_filter(m.stage)
        ]
        played.sort(key=lambda x: (x.day, x.round_num, order.get(id(x), 0)))
        if played:
            return played
        sched = [
            m
            for m in _iter_scheduled_matches(sim)
            if m.comp == ko_comp and played_filter(m.stage)
        ]
        sched.sort(key=lambda x: x.round_num)
        return sched

    r16 = _merge_ko("1/8", lambda st: st.startswith("1/8决赛"))
    qf = _merge_ko("1/4", lambda st: st.startswith("1/4决赛"))
    sf = _merge_ko("半决赛", lambda st: st.startswith("半决赛"))
    fin_list = _merge_ko("决赛", lambda st: st == "决赛")

    return {
        "po": po_ordered,
        "r16": r16,
        "qf": qf,
        "sf": sf,
        "fin": fin_list[0] if fin_list else None,
    }


def _r16_matches_by_index(rounds: Dict[str, Any]) -> Dict[int, Any]:
    return {m.round_num - 1: m for m in rounds["r16"]}


def _placement_team_name(sim: Simulator, cup_base: str, key: str) -> str:
    state = getattr(sim, "_challenger_bracket_state", {}).get(cup_base, {})
    team = state.get("placements", {}).get(key)
    return team.name if team else key


def _challenger_side_display(sim: Simulator, cup_base: str, key: str) -> str:
    """
    三大杯/挑战者杯签位展示：
    有 placements 用队名；小组签位在小组赛结束后填入；否则保留占位。
    """
    state = getattr(sim, "_challenger_bracket_state", {}).get(cup_base, {})
    team = state.get("placements", {}).get(key)
    if team is not None:
        return team.name

    ds = getattr(sim, "_challenger_draw_strength", {}).get(cup_base) or {}
    if len(key) == 2 and key[0].isalpha() and key[1].isdigit():
        return _resolve_gs_slot_name(sim, cup_base, key)

    if key in ("S7", "S8"):
        g = ds.get("s7_group" if key == "S7" else "s8_group")
        if g:
            resolved = _resolve_gs_slot_name(sim, cup_base, f"{g}2")
            if "组第" in resolved:
                return f"{key}（{g}组第二名）"
            return resolved
        return key

    if key.startswith("T") and key[1:].isdigit():
        g = _t_slot_group_map(ds).get(key)
        if g:
            # T 位可能经同组回避换位，须等 placements 生成后再显示队名
            return f"{key}（{g}组第二名）"
        return key

    if key.startswith("P") and key[1:].isdigit():
        return f"{key}胜者"
    return key


def _fixed_pair_card_html(
    meta: str,
    top: str,
    bottom: str,
    *,
    top_score: str = "—",
    bottom_score: str = "—",
    top_win: bool = False,
    bottom_win: bool = False,
) -> str:
    tw = " win" if top_win else ""
    bw = " win" if bottom_win else ""
    return (
        f'<div class="mc"><div class="mc-hd">{_esc(meta)}</div>'
        f'<div class="mc-row{tw}"><span class="mc-team">{_esc(top)}</span><span class="mc-score">{_esc(top_score)}</span></div>'
        f'<div class="mc-row{bw}"><span class="mc-team">{_esc(bottom)}</span><span class="mc-score">{_esc(bottom_score)}</span></div>'
        f"</div>"
    )


def _r16_slot_card_html(
    sim: Simulator, cup_base: str, rounds: Dict[str, Any], idx: int, slot_id: str, left_key: str, right_key: str
) -> str:
    """按固定签表展示 24→16 对阵：直通种子 vs 附加赛胜者（或已赛的 16 强场次）。"""
    meta = f"{slot_id} · {left_key} vs {right_key}"
    r16_map = _r16_matches_by_index(rounds)
    if idx in r16_map:
        return _match_card_html(r16_map[idx], meta)

    seed = _challenger_side_display(sim, cup_base, left_key)
    if right_key.startswith("P"):
        po_m = _po_match_for_slot(rounds, right_key)
        if po_m and po_m.played:
            p_winner = _match_winner_name(po_m)
            return _fixed_pair_card_html(meta, seed, p_winner, bottom_win=True)
        return _fixed_pair_card_html(meta, seed, f"{right_key} 待定")
    other = _challenger_side_display(sim, cup_base, right_key)
    return _fixed_pair_card_html(meta, seed, other)


def _po_match_for_slot(rounds: Dict[str, Any], p_key: str) -> Any:
    if not p_key.startswith("P") or not p_key[1:].isdigit():
        return None
    idx = int(p_key[1:]) - 1
    po = rounds.get("po") or []
    if idx < 0 or idx >= len(po):
        return None
    return po[idx]


def _po_cards_aligned_to_r16(rounds: Dict[str, Any], r16_slots: List[Tuple[str, str, str]]) -> List[str]:
    """附加赛按 16 强签位顺序排列，与右侧 R16 场次一一对齐。"""
    cards: List[str] = []
    for slot_id, _left, right_key in r16_slots:
        po_m = _po_match_for_slot(rounds, right_key) if right_key.startswith("P") else None
        cards.append(_match_card_html(po_m, f"{right_key} → {slot_id}"))
    return cards


def _svg_connector_aligned(h: int, n: int) -> str:
    """同行对齐的直线连接（附加赛胜者 → 对应 16 强场次）。"""
    ys = [y * h for y in _slot_centers(n)]
    return "".join(f'<path d="M 0,{y:.1f} H 28" />' for y in ys)


def _cup_knockout_bracket_text(
    rounds: Dict[str, Any], cup_base: str, sim: Simulator, r16_slots: List[Tuple[str, str, str]]
) -> List[str]:
    lines_txt: List[str] = []
    if not r16_slots and not any(rounds["po"]) and not rounds["r16"]:
        return lines_txt

    cup_title = BRACKET_CUP_LABELS.get(cup_base, cup_base)
    lines_txt.append(f"【{cup_title} 淘汰赛】")
    r16_map = _r16_matches_by_index(rounds)

    if r16_slots:
        lines_txt.append("24强附加赛（8 场，胜者进入固定 16 强签位）")
        for slot_id, _left, right_key in r16_slots:
            if not right_key.startswith("P"):
                continue
            po_m = _po_match_for_slot(rounds, right_key)
            if po_m is None:
                lines_txt.append(f"  [{right_key}→{slot_id}] （待定）")
            elif not getattr(po_m, "played", False):
                lines_txt.append(
                    f"  [{right_key}→{slot_id}] {po_m.home.name} vs {po_m.away.name}  （待赛）"
                )
            else:
                lines_txt.append(
                    f"  [{right_key}→{slot_id}] {po_m.home.name} {po_m.hg}-{po_m.ag} {po_m.away.name}"
                    f"  →  {_match_winner_name(po_m)}"
                )

    if r16_slots or rounds["r16"]:
        lines_txt.append("16强（直通种子 vs 附加赛胜者）")
        for i, (slot_id, left_key, right_key) in enumerate(r16_slots):
            m = r16_map.get(i)
            if m and getattr(m, "played", False):
                lines_txt.append(
                    f"  [{slot_id}] {left_key} vs {right_key}: "
                    f"{m.home.name} {m.hg}-{m.ag} {m.away.name}  →  {_match_winner_name(m)}"
                )
            elif m and not getattr(m, "played", False):
                lines_txt.append(
                    f"  [{slot_id}] {left_key} vs {right_key}: "
                    f"{m.home.name} vs {m.away.name}  （待赛）"
                )
            else:
                seed = _challenger_side_display(sim, cup_base, left_key)
                if right_key.startswith("P"):
                    po_m = _po_match_for_slot(rounds, right_key)
                    if po_m and po_m.played:
                        pw = _match_winner_name(po_m)
                        lines_txt.append(
                            f"  [{slot_id}] {left_key} {seed} vs {right_key}胜者 {pw}  （待赛）"
                        )
                    elif po_m:
                        lines_txt.append(
                            f"  [{slot_id}] {left_key} {seed} vs {right_key}胜者"
                            f"（{po_m.home.name}/{po_m.away.name}）"
                        )
                    else:
                        lines_txt.append(f"  [{slot_id}] {left_key} {seed} vs {right_key}待定")
                else:
                    other = _challenger_side_display(sim, cup_base, right_key)
                    lines_txt.append(f"  [{slot_id}] {left_key} {seed} vs {right_key} {other}  （待赛）")

    if r16_slots or rounds["qf"] or rounds["sf"] or rounds["fin"]:
        lines_txt.append("8强")
        for i in range(4):
            qf_map = _match_list_by_round(rounds["qf"])
            m = qf_map.get(i)
            if m and getattr(m, "played", False):
                lines_txt.append(
                    f"  [QF{i + 1}] {m.home.name} {m.hg}-{m.ag} {m.away.name}  →  {_match_winner_name(m)}"
                )
            elif m:
                lines_txt.append(f"  [QF{i + 1}] {m.home.name} vs {m.away.name}  （待赛）")
            else:
                r16_map = _r16_matches_by_index(rounds)
                a = _winner_or_placeholder(r16_map.get(2 * i), f"R16-{2 * i + 1}胜者")
                b = _winner_or_placeholder(r16_map.get(2 * i + 1), f"R16-{2 * i + 2}胜者")
                lines_txt.append(f"  [QF{i + 1}] {a} vs {b}")
        lines_txt.append("半决赛")
        for i in range(2):
            sf_map = _match_list_by_round(rounds["sf"])
            m = sf_map.get(i)
            if m and getattr(m, "played", False):
                lines_txt.append(
                    f"  [SF{i + 1}] {m.home.name} {m.hg}-{m.ag} {m.away.name}  →  {_match_winner_name(m)}"
                )
            elif m:
                lines_txt.append(f"  [SF{i + 1}] {m.home.name} vs {m.away.name}  （待赛）")
            else:
                qf_map = _match_list_by_round(rounds["qf"])
                a = _winner_or_placeholder(qf_map.get(2 * i), f"QF{2 * i + 1}胜者")
                b = _winner_or_placeholder(qf_map.get(2 * i + 1), f"QF{2 * i + 2}胜者")
                lines_txt.append(f"  [SF{i + 1}] {a} vs {b}")
        lines_txt.append("决赛")
        if rounds["fin"] and getattr(rounds["fin"], "played", False):
            m = rounds["fin"]
            lines_txt.append(
                f"  {m.home.name} {m.hg}-{m.ag} {m.away.name}  →  冠军 {_match_winner_name(m)}"
            )
        elif rounds["fin"]:
            m = rounds["fin"]
            lines_txt.append(f"  {m.home.name} vs {m.away.name}  （待赛）")
        else:
            sf_map = _match_list_by_round(rounds["sf"])
            a = _winner_or_placeholder(sf_map.get(0), "SF1胜者")
            b = _winner_or_placeholder(sf_map.get(1), "SF2胜者")
            lines_txt.append(f"  {a} vs {b}")
    return lines_txt


def _team_row_html(name: str, goals: int, *, winner: bool, pen_note: str = "") -> str:
    cls = "mc-row win" if winner else "mc-row"
    score = str(goals)
    if pen_note:
        score += f' <span class="pen">{_esc(pen_note)}</span>'
    return f'<div class="{cls}"><span class="mc-team">{_esc(name)}</span><span class="mc-score">{score}</span></div>'


def _match_card_html(m: Any, label: str, *, final: bool = False) -> str:
    mc_cls = "mc mc-final" if final else "mc"
    if m is None:
        return (
            f'<div class="{mc_cls} empty"><div class="mc-hd">{_esc(label)}</div>'
            f'<div class="mc-row"><span class="mc-team">待定</span><span class="mc-score">—</span></div>'
            f'<div class="mc-row"><span class="mc-team">待定</span><span class="mc-score">—</span></div></div>'
        )
    if not getattr(m, "played", False):
        return _fixed_pair_card_html(label, m.home.name, m.away.name)
    wname = _match_winner_name(m)
    note = (m.score_note or "").strip()
    pen = ""
    if "点球" in note:
        pen = "点"
    day = f"第{m.day}比赛日" if m.day else ""
    meta = " · ".join(x for x in [label, day] if x)
    home_w = wname == m.home.name
    away_w = wname == m.away.name
    return (
        f'<div class="{mc_cls}"><div class="mc-hd">{_esc(meta)}</div>'
        f'{_team_row_html(m.home.name, m.hg, winner=home_w, pen_note=pen if home_w and pen else "")}'
        f'{_team_row_html(m.away.name, m.ag, winner=away_w, pen_note=pen if away_w and pen else "")}'
        f"</div>"
    )


def _slot_centers(n: int) -> List[float]:
    return [(i + 0.5) / max(1, n) for i in range(n)]


def _svg_connector_merge(h: int, n_from: int, pairs: List[Tuple[int, int]]) -> str:
    ys = [y * h for y in _slot_centers(n_from)]
    out_y = [y * h for y in _slot_centers(len(pairs))]
    parts: List[str] = []
    for qi, (a, b) in enumerate(pairs):
        ym = out_y[qi]
        parts.append(f'<path d="M 0,{ys[a]:.1f} H 10 V {ym:.1f} H 28" />')
        parts.append(f'<path d="M 0,{ys[b]:.1f} H 10 V {ym:.1f} H 28" />')
    return "".join(parts)


def _link_col_html(h: int, inner_svg: str) -> str:
    return (
        f'<div class="link-col" style="height:{h}px">'
        f'<svg viewBox="0 0 28 {h}" preserveAspectRatio="none">{inner_svg}</svg></div>'
    )


def _round_col_html(title: str, cards: List[str], h: int) -> str:
    slots = "".join(f'<div class="slot">{c}</div>' for c in cards)
    return (
        f'<div class="round-col" style="height:{h}px">'
        f'<div class="rtitle">{_esc(title)}</div><div class="slots">{slots}</div></div>'
    )


BRACKET_CSS = """
* { box-sizing: border-box; }
body { margin: 0; padding: 12px 8px; background: #0e1117; color: #e6edf3; }
.bracket-board { display: flex; flex-direction: row; align-items: flex-start; overflow-x: auto; }
.round-col { width: 178px; flex-shrink: 0; display: flex; flex-direction: column; }
.rtitle {
  text-align: center; color: #8b949e; font-size: 13px; font-weight: 600;
  margin-bottom: 8px; letter-spacing: 0.02em;
}
.slots { flex: 1; display: flex; flex-direction: column; height: 100%; }
.slot { flex: 1; display: flex; align-items: center; justify-content: center; min-height: 0; }
.mc {
  width: 168px; border: 1px solid #3d4450; border-radius: 3px;
  background: #161b22; overflow: hidden; font-size: 12px; box-shadow: 0 1px 2px rgba(0,0,0,.35);
}
.mc.empty { opacity: 0.55; }
.mc-hd {
  padding: 5px 8px; font-size: 10px; color: #58a6ff;
  border-bottom: 1px solid #30363d; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mc-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 7px 8px; border-bottom: 1px solid #21262d; gap: 6px;
}
.mc-row:last-child { border-bottom: none; }
.mc-row.win { background: #1a3a5c; font-weight: 600; }
.mc-team { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mc-score { font-weight: 700; font-size: 13px; min-width: 18px; text-align: right; }
.pen { font-size: 10px; color: #8b949e; font-weight: 500; }
.link-col { width: 28px; flex-shrink: 0; position: relative; }
.link-col svg { display: block; width: 100%; height: 100%; }
.link-col path { stroke: #484f58; stroke-width: 1.2; fill: none; }
"""


def _cup_knockout_bracket_html(
    rounds: Dict[str, Any], cup_base: str, sim: Simulator, r16_slots: List[Tuple[str, str, str]]
) -> str:
    if not r16_slots and not any(rounds["po"]) and not rounds["r16"]:
        return ""

    h = 780
    po_cards = _po_cards_aligned_to_r16(rounds, r16_slots) if r16_slots else [_match_card_html(None, f"P{i}") for i in range(1, 9)]
    r16_cards = [
        _r16_slot_card_html(sim, cup_base, rounds, i, slot_id, left_key, right_key)
        for i, (slot_id, left_key, right_key) in enumerate(r16_slots)
    ] if r16_slots else [_match_card_html(None, f"R16-{i}") for i in range(1, 9)]
    qf_cards = []
    for i in range(4):
        if i < len(rounds["qf"]):
            qf_cards.append(_match_card_html(rounds["qf"][i], f"QF{i + 1}"))
        else:
            r16_map = _r16_matches_by_index(rounds)
            a = _winner_or_placeholder(r16_map.get(2 * i), f"R16-{2 * i + 1}胜者")
            b = _winner_or_placeholder(r16_map.get(2 * i + 1), f"R16-{2 * i + 2}胜者")
            qf_cards.append(_fixed_pair_card_html(f"QF{i + 1}", a, b))
    sf_cards = []
    for i in range(2):
        if i < len(rounds["sf"]):
            sf_cards.append(_match_card_html(rounds["sf"][i], f"SF{i + 1}"))
        else:
            qf_map = _match_list_by_round(rounds["qf"])
            a = _winner_or_placeholder(qf_map.get(2 * i), f"QF{2 * i + 1}胜者")
            b = _winner_or_placeholder(qf_map.get(2 * i + 1), f"QF{2 * i + 2}胜者")
            sf_cards.append(_fixed_pair_card_html(f"SF{i + 1}", a, b))
    if rounds["fin"]:
        fin_card = _match_card_html(rounds["fin"], "决赛", final=True)
    else:
        sf_map = _match_list_by_round(rounds["sf"])
        fin_card = _fixed_pair_card_html(
            "决赛",
            _winner_or_placeholder(sf_map.get(0), "SF1胜者"),
            _winner_or_placeholder(sf_map.get(1), "SF2胜者"),
        )

    body = (
        '<div class="bracket-board">'
        + _round_col_html("24强附加赛", po_cards, h)
        + _link_col_html(h, _svg_connector_aligned(h, 8))
        + _round_col_html("16强", r16_cards, h)
        + _link_col_html(h, _svg_connector_merge(h, 8, [(0, 1), (2, 3), (4, 5), (6, 7)]))
        + _round_col_html("8强", qf_cards, h)
        + _link_col_html(h, _svg_connector_merge(h, 4, [(0, 1), (2, 3)]))
        + _round_col_html("半决赛", sf_cards, h)
        + _link_col_html(h, _svg_connector_merge(h, 2, [(0, 1)]))
        + _round_col_html("决赛", [fin_card], h)
        + "</div>"
    )
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'/><style>{BRACKET_CSS}</style></head><body>{body}</body></html>"


def _cup_knockout_slots_df(
    sim: Simulator, cup_base: str, rounds: Dict[str, Any], r16_slots: List[Tuple[str, str, str]]
) -> pd.DataFrame:
    """三大杯/挑战者杯淘汰赛对阵表：分组抽签后即有行，落位后填队名。"""
    rows = []
    r16_map = _r16_matches_by_index(rounds)
    for i, (slot_id, left_key, right_key) in enumerate(r16_slots):
        if right_key.startswith("P"):
            po_m = _po_match_for_slot(rounds, right_key)
            rows.append(
                {
                    "轮次": "24强附加赛",
                    "场次": right_key,
                    "签位": f"{right_key} → {slot_id}",
                    "主队": po_m.home.name if po_m else "待定",
                    "比分": f"{po_m.hg}-{po_m.ag}" if po_m and po_m.played else "—",
                    "客队": po_m.away.name if po_m else "待定",
                    "胜者": _match_winner_name(po_m) if po_m and po_m.played else "—",
                }
            )
        m = r16_map.get(i)
        if m and getattr(m, "played", False):
            home, away, score, winner = m.home.name, m.away.name, f"{m.hg}-{m.ag}", _match_winner_name(m)
        elif m:
            home, away, score, winner = m.home.name, m.away.name, "—", "—"
        else:
            home = _challenger_side_display(sim, cup_base, left_key)
            if right_key.startswith("P"):
                po_m = _po_match_for_slot(rounds, right_key)
                away = _match_winner_name(po_m) if po_m and po_m.played else f"{right_key}待定"
            else:
                away = _challenger_side_display(sim, cup_base, right_key)
            score, winner = "—", "—"
        rows.append(
            {
                "轮次": "16强",
                "场次": slot_id,
                "签位": f"{left_key} vs {right_key}",
                "主队": home,
                "比分": score,
                "客队": away,
                "胜者": winner,
            }
        )
    for i in range(4):
        qf_map = _match_list_by_round(rounds["qf"])
        m = qf_map.get(i)
        r16_map = _r16_matches_by_index(rounds)
        played = m is not None and getattr(m, "played", False)
        rows.append(
            {
                "轮次": "8强",
                "场次": f"QF{i + 1}",
                "签位": f"R16-{2 * i + 1}胜 vs R16-{2 * i + 2}胜",
                "主队": m.home.name if m else _winner_or_placeholder(r16_map.get(2 * i), f"R16-{2 * i + 1}胜者"),
                "比分": f"{m.hg}-{m.ag}" if played else "—",
                "客队": m.away.name if m else _winner_or_placeholder(r16_map.get(2 * i + 1), f"R16-{2 * i + 2}胜者"),
                "胜者": _match_winner_name(m) if played else "—",
            }
        )
    for i in range(2):
        sf_map = _match_list_by_round(rounds["sf"])
        m = sf_map.get(i)
        qf_map = _match_list_by_round(rounds["qf"])
        played = m is not None and getattr(m, "played", False)
        rows.append(
            {
                "轮次": "半决赛",
                "场次": f"SF{i + 1}",
                "签位": f"QF{2 * i + 1}胜 vs QF{2 * i + 2}胜",
                "主队": m.home.name if m else _winner_or_placeholder(qf_map.get(2 * i), f"QF{2 * i + 1}胜者"),
                "比分": f"{m.hg}-{m.ag}" if played else "—",
                "客队": m.away.name if m else _winner_or_placeholder(qf_map.get(2 * i + 1), f"QF{2 * i + 2}胜者"),
                "胜者": _match_winner_name(m) if played else "—",
            }
        )
    m = rounds["fin"]
    sf_map = _match_list_by_round(rounds["sf"])
    played = m is not None and getattr(m, "played", False)
    rows.append(
        {
            "轮次": "决赛",
            "场次": "F",
            "签位": "SF1胜 vs SF2胜",
            "主队": m.home.name if m else _winner_or_placeholder(sf_map.get(0), "SF1胜者"),
            "比分": f"{m.hg}-{m.ag}" if played else "—",
            "客队": m.away.name if m else _winner_or_placeholder(sf_map.get(1), "SF2胜者"),
            "胜者": _match_winner_name(m) if played else "—",
        }
    )
    return pd.DataFrame(rows)


def _gs_finished(sim: Simulator, comp: str) -> bool:
    """小组赛是否打完（每队场次达到应赛场次）。"""
    tab = sim.tables.get(comp) or {}
    if len(tab) < 2:
        return False
    n = len(tab)
    # 洲际杯预选主客双循环；正赛/挑战者杯小组为单循环
    double = "-QUAL-" in comp
    need = (2 * (n - 1)) if double else (n - 1)
    return all(int(s.get("P", 0)) >= need for s in tab.values())


def _resolve_gs_slot_name(sim: Simulator, prefix: str, slot: str) -> str:
    """
    小组签位 → 队名。
    小组赛未结束：返回「A组第1名」等占位；结束后填入队名。
    """
    if len(slot) == 2 and slot[0].isalpha() and slot[1].isdigit():
        lab, rk = slot[0], int(slot[1])
        comp = f"{prefix}-GS-{lab}"
        label = f"{lab}组第{_rank_cn(rk)}名"
        if not _gs_finished(sim, comp):
            return label
        tab = sim._sorted_table(comp)
        if len(tab) >= rk:
            return tab[rk - 1][0]
        return label
    return slot


def _match_list_by_round(ms: List[Any]) -> Dict[int, Any]:
    return {m.round_num - 1: m for m in ms}


def _winner_or_placeholder(m: Any, placeholder: str) -> str:
    if m is not None and getattr(m, "played", False):
        return _match_winner_name(m)
    return placeholder


def _cont_placement_name(sim: Simulator, cup_base: str, slot: str) -> str:
    """洲际杯签位展示名（未落位保留占位）。"""
    if cup_base not in getattr(sim, "_cont_finals_groups", {}):
        return slot
    return _resolve_gs_slot_name(sim, cup_base, slot)


def _cont_r16_card_html(sim: Simulator, cup_base: str, rounds: Dict[str, Any], idx: int, left: str, right: str) -> str:
    meta = f"R16-{idx + 1} · {left} vs {right}"
    r16_map = _r16_matches_by_index(rounds)
    if idx in r16_map:
        return _match_card_html(r16_map[idx], meta)
    a = _cont_placement_name(sim, cup_base, left)
    b = _cont_placement_name(sim, cup_base, right)
    return _fixed_pair_card_html(meta, a, b)


def _cont_qf_card_html(sim: Simulator, rounds: Dict[str, Any], qi: int) -> str:
    """QF：有赛果用赛果；否则用已赛完的 16 强胜者或占位。"""
    qf_map = _match_list_by_round(rounds["qf"])
    if qi in qf_map:
        return _match_card_html(qf_map[qi], f"QF{qi + 1}")
    i, j = QF_FROM_R16[qi]
    r16_map = _r16_matches_by_index(rounds)
    left = _winner_or_placeholder(r16_map.get(i), f"R16-{i + 1}胜者")
    right = _winner_or_placeholder(r16_map.get(j), f"R16-{j + 1}胜者")
    return _fixed_pair_card_html(f"QF{qi + 1}", left, right)


def _cont_sf_card_html(rounds: Dict[str, Any], si: int) -> str:
    sf_map = _match_list_by_round(rounds["sf"])
    if si in sf_map:
        return _match_card_html(sf_map[si], f"SF{si + 1}")
    i, j = SF_FROM_QF[si]
    qf_map = _match_list_by_round(rounds["qf"])
    left = _winner_or_placeholder(qf_map.get(i), f"QF{i + 1}胜者")
    right = _winner_or_placeholder(qf_map.get(j), f"QF{j + 1}胜者")
    return _fixed_pair_card_html(f"SF{si + 1}", left, right)


def _cont_final_card_html(rounds: Dict[str, Any]) -> str:
    if rounds["fin"]:
        return _match_card_html(rounds["fin"], "决赛", final=True)
    sf_map = _match_list_by_round(rounds["sf"])
    left = _winner_or_placeholder(sf_map.get(0), "SF1胜者")
    right = _winner_or_placeholder(sf_map.get(1), "SF2胜者")
    return _fixed_pair_card_html("决赛", left, right)


def _cont_knockout_bracket_text(rounds: Dict[str, Any], cup_base: str, sim: Simulator) -> List[str]:
    if cup_base not in getattr(sim, "_cont_finals_groups", {}):
        return []
    lines: List[str] = [f"【{CONTINENTAL_LABELS.get(cup_base, cup_base)} 淘汰赛】"]
    r16_map = _r16_matches_by_index(rounds)
    lines.append("16强（传统签表 · 小组结束前为占位）")
    for i, (left, right) in enumerate(R16_PAIRINGS):
        m = r16_map.get(i)
        if m:
            lines.append(
                f"  [R16-{i + 1}] {left} vs {right}: "
                f"{m.home.name} {m.hg}-{m.ag} {m.away.name}  →  {_match_winner_name(m)}"
            )
        else:
            a = _cont_placement_name(sim, cup_base, left)
            b = _cont_placement_name(sim, cup_base, right)
            lines.append(f"  [R16-{i + 1}] {left}→{a}  vs  {right}→{b}")
    lines.append("8强")
    for qi in range(4):
        i, j = QF_FROM_R16[qi]
        qf_map = _match_list_by_round(rounds["qf"])
        m = qf_map.get(qi)
        if m:
            lines.append(
                f"  [QF{qi + 1}] {m.home.name} {m.hg}-{m.ag} {m.away.name}  →  {_match_winner_name(m)}"
            )
        else:
            r16_map = _r16_matches_by_index(rounds)
            a = _winner_or_placeholder(r16_map.get(i), f"R16-{i + 1}胜者")
            b = _winner_or_placeholder(r16_map.get(j), f"R16-{j + 1}胜者")
            lines.append(f"  [QF{qi + 1}] {a} vs {b}")
    lines.append("半决赛")
    for si in range(2):
        i, j = SF_FROM_QF[si]
        sf_map = _match_list_by_round(rounds["sf"])
        m = sf_map.get(si)
        if m:
            lines.append(
                f"  [SF{si + 1}] {m.home.name} {m.hg}-{m.ag} {m.away.name}  →  {_match_winner_name(m)}"
            )
        else:
            qf_map = _match_list_by_round(rounds["qf"])
            a = _winner_or_placeholder(qf_map.get(i), f"QF{i + 1}胜者")
            b = _winner_or_placeholder(qf_map.get(j), f"QF{j + 1}胜者")
            lines.append(f"  [SF{si + 1}] {a} vs {b}")
    lines.append("决赛")
    if rounds["fin"]:
        m = rounds["fin"]
        lines.append(
            f"  {m.home.name} {m.hg}-{m.ag} {m.away.name}  →  冠军 {_match_winner_name(m)}"
        )
    else:
        sf_map = _match_list_by_round(rounds["sf"])
        a = _winner_or_placeholder(sf_map.get(0), "SF1胜者")
        b = _winner_or_placeholder(sf_map.get(1), "SF2胜者")
        lines.append(f"  {a} vs {b}")
    return lines


def _cont_knockout_slots_df(sim: Simulator, cup_base: str, rounds: Dict[str, Any]) -> pd.DataFrame:
    """淘汰赛对阵表：抽签后即有行，落位后填队名。"""
    rows = []
    r16_map = _r16_matches_by_index(rounds)
    for i, (left, right) in enumerate(R16_PAIRINGS):
        m = r16_map.get(i)
        rows.append(
            {
                "轮次": "16强",
                "场次": f"R16-{i + 1}",
                "签位": f"{left} vs {right}",
                "主队": m.home.name if m else _cont_placement_name(sim, cup_base, left),
                "比分": f"{m.hg}-{m.ag}" if m else "—",
                "客队": m.away.name if m else _cont_placement_name(sim, cup_base, right),
                "胜者": _match_winner_name(m) if m else "—",
            }
        )
    for qi in range(4):
        i, j = QF_FROM_R16[qi]
        qf_map = _match_list_by_round(rounds["qf"])
        m = qf_map.get(qi)
        r16_map = _r16_matches_by_index(rounds)
        rows.append(
            {
                "轮次": "8强",
                "场次": f"QF{qi + 1}",
                "签位": f"R16-{i + 1}胜 vs R16-{j + 1}胜",
                "主队": m.home.name if m else _winner_or_placeholder(r16_map.get(i), f"R16-{i + 1}胜者"),
                "比分": f"{m.hg}-{m.ag}" if m else "—",
                "客队": m.away.name if m else _winner_or_placeholder(r16_map.get(j), f"R16-{j + 1}胜者"),
                "胜者": _match_winner_name(m) if m else "—",
            }
        )
    for si in range(2):
        i, j = SF_FROM_QF[si]
        sf_map = _match_list_by_round(rounds["sf"])
        m = sf_map.get(si)
        qf_map = _match_list_by_round(rounds["qf"])
        rows.append(
            {
                "轮次": "半决赛",
                "场次": f"SF{si + 1}",
                "签位": f"QF{i + 1}胜 vs QF{j + 1}胜",
                "主队": m.home.name if m else _winner_or_placeholder(qf_map.get(i), f"QF{i + 1}胜者"),
                "比分": f"{m.hg}-{m.ag}" if m else "—",
                "客队": m.away.name if m else _winner_or_placeholder(qf_map.get(j), f"QF{j + 1}胜者"),
                "胜者": _match_winner_name(m) if m else "—",
            }
        )
    m = rounds["fin"]
    sf_map = _match_list_by_round(rounds["sf"])
    rows.append(
        {
            "轮次": "决赛",
            "场次": "F",
            "签位": "SF1胜 vs SF2胜",
            "主队": m.home.name if m else _winner_or_placeholder(sf_map.get(0), "SF1胜者"),
            "比分": f"{m.hg}-{m.ag}" if m else "—",
            "客队": m.away.name if m else _winner_or_placeholder(sf_map.get(1), "SF2胜者"),
            "胜者": _match_winner_name(m) if m else "—",
        }
    )
    return pd.DataFrame(rows)


def _cont_knockout_bracket_html(rounds: Dict[str, Any], cup_base: str, sim: Simulator) -> str:
    """洲际杯传统淘汰赛树：正赛分组抽签后即可显示占位签表。"""
    if cup_base not in getattr(sim, "_cont_finals_groups", {}):
        return ""

    h = 780
    r16_cards = [
        _cont_r16_card_html(sim, cup_base, rounds, i, left, right)
        for i, (left, right) in enumerate(R16_PAIRINGS)
    ]
    qf_cards = [_cont_qf_card_html(sim, rounds, i) for i in range(4)]
    sf_cards = [_cont_sf_card_html(rounds, i) for i in range(2)]
    fin_card = _cont_final_card_html(rounds)

    body = (
        '<div class="bracket-board">'
        + _round_col_html("16强", r16_cards, h)
        + _link_col_html(h, _svg_connector_merge(h, 8, [(0, 1), (2, 3), (4, 5), (6, 7)]))
        + _round_col_html("8强", qf_cards, h)
        + _link_col_html(h, _svg_connector_merge(h, 4, [(0, 1), (2, 3)]))
        + _round_col_html("半决赛", sf_cards, h)
        + _link_col_html(h, _svg_connector_merge(h, 2, [(0, 1)]))
        + _round_col_html("决赛", [fin_card], h)
        + "</div>"
    )
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'/><style>{BRACKET_CSS}</style></head><body>{body}</body></html>"


def _render_bracket_html(page: str, height: int = 820) -> None:
    components.html(page, height=height, scrolling=True)


DRAW_CARDS_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: transparent;
  color: #e6e9ef;
  padding: 4px 2px 8px;
}
.grid, .swiss-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}
.gcard {
  background: #1c2333;
  border: 1px solid #2a3346;
  border-radius: 10px;
  overflow: hidden;
}
.ghead {
  background: #243049;
  color: #d7deea;
  text-align: center;
  font-weight: 700;
  font-size: 14px;
  padding: 8px 6px;
  letter-spacing: .06em;
  border-bottom: 1px solid #2f3a52;
}
.gbody { padding: 4px 0; }
.gbody .team {
  padding: 7px 14px;
  font-size: 13px;
  font-weight: 500;
  color: #e8ecf4;
  border-bottom: 1px solid #262f42;
}
.gbody .team:last-child { border-bottom: none; }
.gbody .team:nth-child(even) { background: rgba(255,255,255,.03); }
.scard {
  background: #1a2740;
  color: #e8ecf4;
  border: 1px solid #2c3d5c;
  border-radius: 10px;
  padding: 12px 12px 10px;
  min-height: 200px;
}
.scard .top {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid #334866;
}
.scard .team-name {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.2;
  color: #f2f5fa;
}
.scard .vs {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .08em;
  color: #8fa4c4;
}
.scard .opp {
  font-size: 12px;
  padding: 3px 0;
  font-weight: 500;
  color: #d5dce8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.scard .split {
  height: 1px;
  background: #334866;
  margin: 6px 0;
}
.scard .side-lab {
  font-size: 10px;
  color: #8fa4c4;
  margin-bottom: 2px;
  letter-spacing: .06em;
}
.stand-wrap { max-width: 720px; margin: 0 auto; }
.stand-card {
  background: #1c2333;
  border: 1px solid #2a3346;
  border-radius: 10px;
  overflow: hidden;
}
.stand-card .ghead { font-size: 15px; padding: 10px; }
.stand-card table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  color: #e6e9ef;
}
.stand-card th {
  background: #243049;
  text-align: left;
  padding: 8px 10px;
  font-weight: 700;
  color: #c5cede;
  border-bottom: 1px solid #2f3a52;
}
.stand-card td {
  padding: 7px 10px;
  border-bottom: 1px solid #262f42;
}
.stand-card tr:nth-child(even) td { background: rgba(255,255,255,.025); }
.stand-card tr:last-child td { border-bottom: none; }
.stand-card .rk { width: 36px; color: #8b95a8; font-weight: 700; }
.stand-card .num { text-align: right; font-variant-numeric: tabular-nums; color: #cfd6e4; }
.empty { color: #8b95a8; padding: 24px; text-align: center; }
@media (max-width: 900px) {
  .grid, .swiss-grid { grid-template-columns: repeat(2, 1fr); }
}
"""


def _group_names_from_teams(groups: List[List[Any]], labels: List[str]) -> List[Tuple[str, List[str]]]:
    out: List[Tuple[str, List[str]]] = []
    for i, g in enumerate(groups):
        lab = labels[i] if i < len(labels) else chr(ord("A") + i)
        names = [t.name if hasattr(t, "name") else str(t) for t in g]
        out.append((lab, names))
    return out


def _group_draw_html(groups: List[Tuple[str, List[str]]]) -> str:
    cards: List[str] = []
    for lab, teams in groups:
        rows = "".join(f'<div class="team">{html.escape(n)}</div>' for n in teams)
        title = lab if lab.endswith("组") else f"{lab}组"
        cards.append(
            f'<div class="gcard"><div class="ghead">{html.escape(title)}</div>'
            f'<div class="gbody">{rows}</div></div>'
        )
    body = f'<div class="grid">{"".join(cards)}</div>' if cards else '<div class="empty">暂无分组</div>'
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        f"<style>{DRAW_CARDS_CSS}</style></head><body>{body}</body></html>"
    )


def _swiss_home_away_lists(
    sim: Simulator, comp: str, team: str
) -> Tuple[List[str], List[str]]:
    """从赛程表拆主/客对手；若无赛程则按档位对手上下对半分。"""
    home: List[str] = []
    away: List[str] = []
    for rnd in sim.league_schedule_by_confed.get(comp, []):
        for a, _vs, b, _lbl in rnd:
            if a == team:
                home.append(b)
            elif b == team:
                away.append(a)
    if home or away:
        return home, away
    by_pot = (sim.league_opponents_by_comp.get(comp) or {}).get(team) or {}
    flat: List[str] = []
    for _pot, names in sorted(by_pot.items(), key=lambda x: x[0]):
        flat.extend(names)
    mid = (len(flat) + 1) // 2
    return flat[:mid], flat[mid:]


def _swiss_draw_html(
    sim: Simulator, comp: str, team_order: List[str]
) -> str:
    cards: List[str] = []
    for team in team_order:
        home, away = _swiss_home_away_lists(sim, comp, team)
        home_rows = "".join(f'<div class="opp">{html.escape(n)}</div>' for n in home)
        away_rows = "".join(f'<div class="opp">{html.escape(n)}</div>' for n in away)
        cards.append(
            f'<div class="scard">'
            f'<div class="top"><span class="team-name">{html.escape(team)}</span>'
            f'<span class="vs">VS</span></div>'
            f'<div class="side-lab">主场</div>{home_rows}'
            f'<div class="split"></div>'
            f'<div class="side-lab">客场</div>{away_rows}'
            f"</div>"
        )
    body = (
        f'<div class="swiss-grid">{"".join(cards)}</div>'
        if cards
        else '<div class="empty">暂无瑞士轮对手</div>'
    )
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        f"<style>{DRAW_CARDS_CSS}</style></head><body>{body}</body></html>"
    )


def _standings_draw_html(sim: Simulator, comp: str, title: str) -> str:
    tab = sim._sorted_table(comp)
    if not tab:
        # 抽签后可能尚无积分行：用赛程/队名兜底
        names: List[str] = []
        sched = sim.league_schedule_by_confed.get(comp, [])
        seen = set()
        for rnd in sched:
            for a, _vs, b, _lbl in rnd:
                for n in (a, b):
                    if n not in seen:
                        seen.add(n)
                        names.append(n)
        tab = [(n, {"P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "GD": 0, "PTS": 0}) for n in names]
    thead = (
        "<tr><th class='rk'>#</th><th>球队</th>"
        "<th class='num'>赛</th><th class='num'>胜</th><th class='num'>平</th>"
        "<th class='num'>负</th><th class='num'>进</th><th class='num'>失</th>"
        "<th class='num'>净</th><th class='num'>分</th></tr>"
    )
    rows_html: List[str] = []
    for i, (name, s) in enumerate(tab, 1):
        rows_html.append(
            "<tr>"
            f"<td class='rk'>{i}</td>"
            f"<td>{html.escape(name)}</td>"
            f"<td class='num'>{int(s.get('P', 0))}</td>"
            f"<td class='num'>{int(s.get('W', 0))}</td>"
            f"<td class='num'>{int(s.get('D', 0))}</td>"
            f"<td class='num'>{int(s.get('L', 0))}</td>"
            f"<td class='num'>{int(s.get('GF', 0))}</td>"
            f"<td class='num'>{int(s.get('GA', 0))}</td>"
            f"<td class='num'>{int(s.get('GD', 0))}</td>"
            f"<td class='num'><b>{int(s.get('PTS', 0))}</b></td>"
            "</tr>"
        )
    body = (
        f"<div class='stand-wrap'><div class='stand-card'>"
        f"<div class='ghead'>{html.escape(title)}</div>"
        f"<table><thead>{thead}</thead><tbody>{''.join(rows_html)}</tbody></table>"
        f"</div></div>"
    )
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        f"<style>{DRAW_CARDS_CSS}</style></head><body>{body}</body></html>"
    )


def _available_draw_views(sim: Simulator) -> List[Dict[str, Any]]:
    """可展示的抽签视图：小组卡 / 瑞士轮卡 / 南美积分榜卡。"""
    views: List[Dict[str, Any]] = []

    for code in CONTINENTAL_CODES:
        groups = getattr(sim, "_cont_qual_groups", {}).get(code) or []
        if groups:
            labels = [chr(ord("A") + i) for i in range(len(groups))]
            views.append(
                {
                    "id": f"cont_qual_{code}",
                    "label": f"{CONTINENTAL_LABELS[code]} · 预选分组",
                    "kind": "groups",
                    "groups": _group_names_from_teams(groups, labels),
                }
            )

    for code in CONTINENTAL_CODES:
        groups = getattr(sim, "_cont_finals_groups", {}).get(code) or []
        if groups:
            views.append(
                {
                    "id": f"cont_final_{code}",
                    "label": f"{CONTINENTAL_LABELS[code]} · 正赛分组",
                    "kind": "groups",
                    "groups": _group_names_from_teams(groups, list(FINAL_GROUP_LABELS)),
                }
            )

    swiss_label = {
        "UEFA-QUAL": "欧洲预选 · 瑞士轮",
        "AFC-QUAL": "亚洲预选 · 瑞士轮",
        "CAF-QUAL": "非洲预选 · 瑞士轮",
        "CONCACAF-QUAL": "中北美预选 · 瑞士轮",
        "OFC-QUAL": "大洋洲预选 · 瑞士轮",
    }
    for comp in sorted(sim.league_opponents_by_comp.keys()):
        def _rank_key(n: str, _comp: str = comp) -> int:
            if n in sim.live_ranks:
                return int(sim.live_ranks[n])
            t = sim.team_map.get(n)
            return int(t.world_rank) if t else 999

        teams = sorted(sim.league_opponents_by_comp[comp].keys(), key=_rank_key)
        views.append(
            {
                "id": f"swiss_{comp}",
                "label": swiss_label.get(comp, f"{comp} · 瑞士轮"),
                "kind": "swiss",
                "comp": comp,
                "teams": teams,
            }
        )

    if sim.tables.get("CONMEBOL-QUAL") or "CONMEBOL-QUAL" in sim.league_schedule_by_confed:
        views.append(
            {
                "id": "standings_CONMEBOL-QUAL",
                "label": "南美预选 · 双循环积分榜",
                "kind": "standings",
                "comp": "CONMEBOL-QUAL",
                "title": "南美预选（主客双循环）",
            }
        )

    for prefix, lab in GROUP_STAGE_CUPS:
        if prefix in CONTINENTAL_CODES:
            continue
        groups = _cup_draw_groups(sim, prefix)
        if not groups:
            continue
        views.append(
            {
                "id": f"cup_gs_{prefix}",
                "label": f"{lab} · 正赛分组",
                "kind": "groups",
                "groups": _group_names_from_teams(groups, _cup_group_labels(prefix)),
            }
        )
    return views


def _render_draw_view(sim: Simulator, view: Dict[str, Any]) -> None:
    """分页渲染，避免手机端一次推送过大 HTML 导致 Websocket RangeError。"""
    kind = view["kind"]
    if kind == "groups":
        groups = view["groups"]
        page_size = 8
        if len(groups) > page_size:
            n_pages = (len(groups) + page_size - 1) // page_size
            page_i = st.number_input(
                "分组页",
                min_value=1,
                max_value=n_pages,
                value=1,
                step=1,
                key=f"draw_groups_page_{view['id']}",
            )
            start = (int(page_i) - 1) * page_size
            chunk = groups[start : start + page_size]
            st.caption(f"第 {page_i}/{n_pages} 页 · 共 {len(groups)} 组")
        else:
            chunk = groups
        nrows = (len(chunk) + 3) // 4
        h = min(720, max(200, 40 + nrows * 190))
        _render_bracket_html(_group_draw_html(chunk), height=h)
    elif kind == "swiss":
        teams = view["teams"]
        page_size = 8
        n_pages = max(1, (len(teams) + page_size - 1) // page_size)
        page_i = 1
        if n_pages > 1:
            page_i = st.number_input(
                "球队页",
                min_value=1,
                max_value=n_pages,
                value=1,
                step=1,
                key=f"draw_swiss_page_{view['id']}",
            )
            st.caption(f"第 {int(page_i)}/{n_pages} 页 · 共 {len(teams)} 队（分页减轻手机端卡顿）")
        start = (int(page_i) - 1) * page_size
        chunk = teams[start : start + page_size]
        nrows = (len(chunk) + 3) // 4
        h = min(780, max(240, 36 + nrows * 235))
        _render_bracket_html(_swiss_draw_html(sim, view["comp"], chunk), height=h)
    elif kind == "standings":
        n = len(sim.tables.get(view["comp"], {})) or 12
        h = min(640, max(260, 80 + n * 32))
        _render_bracket_html(
            _standings_draw_html(sim, view["comp"], view.get("title", view["label"])),
            height=h,
        )


def _world_rank_board_df(sim: Simulator) -> pd.DataFrame:
    """完整 1…N 世界排名表（全体球队，无断号）。"""
    board = sorted(sim.teams, key=lambda t: sim.live_ranks.get(t.name, t.world_rank))
    return pd.DataFrame(
        [
            {
                "世界排名": sim.live_ranks.get(t.name, t.world_rank),
                "球队": t.name,
                "大洲": t.confed,
                "国际积分": round(sim.fifa_points.get(t.name, 0.0), 1),
                "OVR": round(t.ovr, 1),
            }
            for t in board
        ]
    )


MAIN_PAGES = [
    "抽签与赛程",
    "总览",
    "全部赛果",
    "积分榜",
    "世界排名",
    "三大杯资格",
    "淘汰赛对阵",
]


def _hosts_from_session() -> Dict[str, str]:
    """on_click 时从东道主控件读最新值（早于脚本里写回 hosts）。"""
    base = st.session_state.get("hosts") or _default_hosts()
    hosts: Dict[str, str] = {}
    for code in CONTINENTAL_CODES:
        key = f"host_{code}"
        hosts[code] = st.session_state[key] if key in st.session_state else base[code]
    return hosts


def _reset_sim_session() -> None:
    st.session_state.pop("sim", None)
    st.session_state.pop("sim_seed", None)
    st.session_state.pop("sim_hosts", None)
    st.session_state.pop("rank_change_report", None)
    seed = int(st.session_state.get("seed", 42))
    hosts = _hosts_from_session()
    st.session_state.hosts = hosts
    _ensure_sim(seed, hosts)


def _advance_n_days() -> None:
    """on_click：避免 st.rerun() 把主页面 radio 重置回第一项。"""
    if "sim" not in st.session_state:
        return
    sim = st.session_state.sim
    n_skip = int(st.session_state.get("n_skip", 1))
    snap = sim.ranking_snapshot()
    advanced = 0
    for _ in range(n_skip):
        if not sim.next_day():
            break
        advanced += 1
    st.session_state.rank_change_report = {
        "days": advanced,
        "day": sim.day,
        "rows": sim.ranking_delta_from(
            snap,
            only_played={
                m.home.name
                for m in sim.all_results
                if m.day > sim.day - advanced
            }
            | {
                m.away.name
                for m in sim.all_results
                if m.day > sim.day - advanced
            },
        ),
    }


def _advance_to_season_end() -> None:
    """on_click：推进到结束且保持当前导航页。"""
    if "sim" not in st.session_state:
        return
    sim = st.session_state.sim
    snap = sim.ranking_snapshot()
    start_day = sim.day
    while sim.next_day():
        pass
    st.session_state.rank_change_report = {
        "days": max(0, sim.day - start_day),
        "day": sim.day,
        "rows": sim.ranking_delta_from(snap),
    }


def main() -> None:
    st.title("⚽ 四年周期模拟器：洲际杯 → 世界杯三大杯")
    st.caption(
        "Part A 欧洲杯/非洲杯/亚太杯/美洲杯（主客双循环预选 + 32 队正赛）→ "
        "Part B 世界杯预选与三大杯/挑战者杯 · 排名见 data/team_world_ranks_original.json（回溯）"
        "与 team_world_ranks_cycle.json（Part A 后更新）"
    )

    if "hosts" not in st.session_state:
        st.session_state.hosts = _default_hosts()
    if "main_page" not in st.session_state:
        st.session_state.main_page = MAIN_PAGES[0]
    elif st.session_state.main_page not in MAIN_PAGES:
        st.session_state.main_page = MAIN_PAGES[0]

    with st.sidebar:
        st.header("控制")
        seed = st.number_input(
            "随机种子",
            min_value=0,
            max_value=2**31 - 1,
            value=42,
            step=1,
            key="seed",
        )

        st.subheader("洲际杯东道主")
        host_vals: Dict[str, str] = {}
        for code in CONTINENTAL_CODES:
            opts = sorted(HOST_POOLS[code])
            cur = st.session_state.hosts.get(code, opts[0])
            if cur not in opts:
                cur = opts[0]
            host_vals[code] = st.selectbox(
                CONTINENTAL_LABELS[code],
                opts,
                index=opts.index(cur),
                key=f"host_{code}",
            )
        st.session_state.hosts = host_vals

        col_a, col_b = st.columns(2)
        with col_a:
            st.button("新开局", use_container_width=True, on_click=_reset_sim_session)
        with col_b:
            st.button("重置种子并开局", use_container_width=True, on_click=_reset_sim_session)

        sim = _ensure_sim(int(seed), host_vals)

        st.divider()
        n_skip = st.slider("一次推进天数", 1, 30, 1, key="n_skip")
        # 勿在按钮分支里 st.rerun()：会重置下方主页面 radio 到第一项
        st.button(
            f"推进 {n_skip} 个比赛日",
            type="primary",
            use_container_width=True,
            on_click=_advance_n_days,
        )
        st.button(
            "推进到赛季结束",
            use_container_width=True,
            on_click=_advance_to_season_end,
        )

        st.divider()
        st.subheader("状态")
        part_lab = "洲际杯 (Part A)" if getattr(sim, "cycle_part", "A") == "A" else "世界杯周期 (Part B)"
        st.write(f"**周期部分:** {part_lab}")
        st.write(f"**当前比赛日:** {sim.day}")
        st.write(f"**阶段:** {sim.phase_name or '—'}")
        st.caption(
            "东道主："
            + " · ".join(f"{CONTINENTAL_LABELS[c]}={sim.hosts.get(c,'?')}" for c in CONTINENTAL_CODES)
        )
        if sim.phase_name == "已结束":
            st.success("本四年周期已全部结束。")
            if getattr(sim, "continental_champions", None):
                st.markdown(
                    "**洲际杯冠军：** "
                    + " | ".join(
                        f"{CONTINENTAL_LABELS.get(k,k)}: **{v}**"
                        for k, v in sim.continental_champions.items()
                    )
                )
            if getattr(sim, "cup_champions", None):
                st.markdown(
                    "**三大杯冠军：** "
                    + " | ".join(f"{k}: **{v}**" for k, v in sim.cup_champions.items())
                )
            if getattr(sim, "wcc_champion", ""):
                st.markdown(f"**挑战者杯冠军：** **{sim.wcc_champion}**")
        else:
            left = sum(len(d) for d in sim.phase_matchdays) if sim.phase_matchdays else 0
            st.caption(f"本阶段剩余比赛日: {left}")

    sim = _ensure_sim(int(seed), st.session_state.hosts)

    report = st.session_state.get("rank_change_report")
    if report and report.get("rows") is not None:
        with st.expander(
            f"本轮参赛队积分变化（非完整榜 · 推进 {report.get('days', 0)} 日 · 第 {report.get('day', sim.day)} 比赛日）",
            expanded=False,
        ):
            rows = report["rows"]
            if not rows:
                st.caption("参赛队积分变化很小或本轮无赛。")
            else:
                rdf = pd.DataFrame(rows)
                rdf["排名箭头"] = rdf["排名变化"].map(
                    lambda x: f"↑{x}" if x > 0 else (f"↓{abs(x)}" if x < 0 else "—")
                )
                rdf["积分箭头"] = rdf["积分变化"].map(
                    lambda x: f"+{x:.1f}" if x > 0 else (f"{x:.1f}" if x < 0 else "—")
                )
                rdf = rdf.sort_values("新排名", ascending=True)
                show = rdf[
                    ["球队", "大洲", "原排名", "新排名", "排名箭头", "原积分", "新积分", "积分箭头"]
                ].rename(
                    columns={
                        "排名箭头": "排名变化",
                        "积分箭头": "积分变化",
                    }
                )
                st.caption("仅含本轮踢过球的球队；完整 1–220 请看下方「总览」或「世界排名」。")
                st.dataframe(show, use_container_width=True, hide_index=True, height=min(420, 48 + 35 * len(show)))
                follow = st.selectbox(
                    "关注单队",
                    ["（全部）"] + sorted({r["球队"] for r in rows}),
                    key="rank_follow_in_report",
                )
                if follow != "（全部）":
                    one = next(r for r in rows if r["球队"] == follow)
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("世界排名", one["新排名"], delta=one["排名变化"], delta_color="normal")
                    c2.metric("国际积分", one["新积分"], delta=one["积分变化"])
                    c3.metric("原排名", one["原排名"])
                    c4.metric("原积分", one["原积分"])

    # key=main_page 持久化；推进按钮用 on_click、勿 st.rerun()，否则会跳回第一项
    page = st.radio("页面", MAIN_PAGES, horizontal=True, key="main_page", label_visibility="collapsed")

    if page == "抽签与赛程":
        st.subheader("抽签记录")
        draw_views = _available_draw_views(sim)
        if not draw_views:
            st.info("开局并完成抽签后，此处显示分组 / 瑞士轮 / 南美积分榜卡片。")
        else:
            by_id = {v["id"]: v for v in draw_views}
            ids = list(by_id.keys())
            if st.session_state.get("draw_view_pick") not in by_id:
                st.session_state.draw_view_pick = ids[0]
            pick_id = st.selectbox(
                "选择抽签",
                ids,
                format_func=lambda i: by_id[i]["label"],
                key="draw_view_pick",
            )
            view = by_id[pick_id]
            kind_hint = {
                "groups": "小组抽签（队名卡片）",
                "swiss": "瑞士轮（各队对手卡片）",
                "standings": "双循环积分榜卡片",
            }.get(view["kind"], "")
            if kind_hint:
                st.caption(kind_hint)
            _render_draw_view(sim, view)

        st.subheader("联赛 / 正赛赛程表（赛前即定，与模拟赛果一致）")
        comps_sched = sorted(sim.league_schedule_by_confed.keys())
        if not comps_sched:
            st.caption("进行附加赛并生成联赛后显示。")
        else:
            pick_s = st.selectbox("选择赛事", comps_sched, key="sched_pick")
            rounds_data = sim.league_schedule_by_confed.get(pick_s, [])
            if not rounds_data:
                st.caption("该赛事暂无赛程。")
            else:
                # 只渲染一轮，避免手机端一次推送全部轮次文本过大
                ridx = st.number_input(
                    "查看轮次",
                    min_value=1,
                    max_value=len(rounds_data),
                    value=1,
                    step=1,
                    key="sched_round_pick",
                )
                rnd = rounds_data[int(ridx) - 1]
                lines = [f"{a} {vs} {b}  （{lbl}）" for a, vs, b, lbl in rnd]
                st.markdown(f"**第 {int(ridx)} / {len(rounds_data)} 轮**（{len(lines)} 场）")
                st.text("\n".join(lines))

        st.subheader("查询球队未来赛程")
        all_names = sorted(t.name for t in sim.teams)
        q_team = st.selectbox("选择球队", all_names, key="future_sched_team")
        fut = sim.upcoming_matches_for_team(q_team)
        if not fut:
            st.info("暂无未赛场次（可能本赛季已结束，或该队已无剩余比赛）。")
        else:
            # 手机端限制行数，完整数据可下载
            show_n = min(40, len(fut))
            st.caption(f"显示最近 {show_n} / 共 {len(fut)} 场未赛")
            st.dataframe(pd.DataFrame(fut[:show_n]), use_container_width=True, hide_index=True)
            if len(fut) > show_n:
                st.download_button(
                    "下载该队全部未来赛程 CSV",
                    data=pd.DataFrame(fut).to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"upcoming_{q_team}.csv",
                    mime="text/csv",
                    key="dl_future_sched",
                )

    elif page == "总览":
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("已赛总场次", len(sim.all_results))
        with c2:
            st.metric("涉及赛事数", len(sim.list_competitions()))
        with c3:
            st.metric("阶段", sim.phase_idx)
        with c4:
            st.metric("种子", sim.seed)

        if getattr(sim, "continental_champions", None):
            st.success(
                "洲际杯冠军："
                + " · ".join(
                    f"{CONTINENTAL_LABELS.get(k, k)} **{v}**"
                    for k, v in sim.continental_champions.items()
                )
            )
        if getattr(sim, "wcc_champion", ""):
            st.success(f"世界挑战者杯冠军：**{sim.wcc_champion}**")

        st.subheader("周期与东道主")
        st.write(
            f"当前：**{'Part A 洲际杯' if getattr(sim,'cycle_part','A')=='A' else 'Part B 世界杯周期'}** · "
            f"排名源：`{getattr(sim, '_rank_source', 'original')}`"
        )
        st.caption(
            " · ".join(f"{CONTINENTAL_LABELS[c]}东道主 {sim.hosts.get(c,'—')}" for c in CONTINENTAL_CODES)
        )

        st.subheader("国际足联排名")
        full_board = _world_rank_board_df(sim)
        st.caption(f"完整榜共 **{len(full_board)}** 队，按世界排名 1→{len(full_board)}（全体球队）。")
        st.dataframe(full_board, use_container_width=True, hide_index=True, height=520)
        st.download_button(
            "下载完整世界排名 CSV",
            full_board.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"fifa_ranks_day{sim.day}_seed{sim.seed}.csv",
            mime="text/csv",
            key="dl_overview_ranks",
        )

        st.subheader("大洲预选赛进度（积分榜已有球队数）")
        cols = st.columns(len(CONFEDS))
        for i, c in enumerate(CONFEDS):
            comp = f"{c}-QUAL"
            n = len(sim.tables.get(comp, {}))
            with cols[i]:
                st.metric(c, n)

        if sim.all_results:
            st.subheader("最近 15 场")
            df = _matches_to_df(sim)
            st.dataframe(df.tail(15), use_container_width=True, hide_index=True)

    elif page == "全部赛果":
        df = _matches_to_df(sim)
        if df.empty:
            st.info("暂无赛果。请在左侧推进比赛日。")
        else:
            comps = sorted(df["赛事"].unique().tolist())
            f1, f2 = st.columns([1, 2])
            with f1:
                pick = st.multiselect("筛选赛事（不选表示全部）", comps, default=[])
            with f2:
                q = st.text_input("搜索球队名", placeholder="例如 France、Japan")
            view = df.copy()
            if pick:
                view = view[view["赛事"].isin(pick)]
            if q.strip():
                s = q.strip().lower()
                view = view[
                    view["主队"].str.lower().str.contains(s, na=False)
                    | view["客队"].str.lower().str.contains(s, na=False)
                ]
            st.caption(f"当前显示 **{len(view)}** / 共 {len(df)} 场")
            st.dataframe(
                view.sort_values(["比赛日", "赛事"], ascending=[True, True]),
                use_container_width=True,
                height=520,
                hide_index=True,
            )
            csv = view.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="下载当前表格为 CSV",
                data=csv,
                file_name=f"world_cup_matches_seed{sim.seed}.csv",
                mime="text/csv",
            )

    elif page == "积分榜":
        table_sub = st.radio(
            "积分榜分类",
            ["杯赛积分榜", "洲际杯预选积分榜", "世界杯预选积分榜"],
            horizontal=True,
            key="tables_sub_page",
            label_visibility="collapsed",
        )

        if table_sub == "杯赛积分榜":
            cup_options = {label: prefix for prefix, label in GROUP_STAGE_CUPS}
            cup_pick_label = st.selectbox(
                "选择杯赛",
                list(cup_options.keys()),
                key="cup_table_pick",
            )
            cup_prefix = cup_options[cup_pick_label]
            _render_cup_group_standings(sim, cup_prefix, cup_pick_label)

            export_df = _combined_group_tables_df(sim, cup_prefix, cup_pick_label)
            if not export_df.empty:
                st.download_button(
                    f"下载「{cup_pick_label}」小组积分榜 CSV",
                    export_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"group_{cup_prefix}_seed{sim.seed}.csv",
                    mime="text/csv",
                    key="dl_cup_gs",
                )

        elif table_sub == "洲际杯预选积分榜":
            cont_pick = st.selectbox(
                "选择洲际杯",
                CONTINENTAL_CODES,
                format_func=lambda c: CONTINENTAL_LABELS.get(c, c),
                key="cont_qual_cup_pick",
            )
            st.caption(
                f"东道主 **{sim.hosts.get(cont_pick, '—')}** 不参加预选，已直接晋级正赛。"
                " 小组前三直通；第四名中成绩最差者淘汰，其余进附加赛。"
                " 比较各组第四时：以本杯最小组规模为准，多队组剔除对垫底多出来名次的比赛后再比。"
            )
            with st.expander("晋级线说明", expanded=False):
                st.markdown("- 第 **1–3** 名：正赛直通")
                st.markdown(
                    "- 第 **4** 名：附加赛候选（九组第四中最差一组直接淘汰；"
                    "组规模不一时，多队组剔对最低名次队的战绩后再横向比较）"
                )
                st.markdown("- 第 **5** 名及以后：未晋级")

            any_tab = False
            export_rows = []
            for lab in "ABCDEFGHI":
                comp = f"{cont_pick}-QUAL-{lab}"
                tdf = _table_to_df(sim, comp)
                if tdf.empty:
                    continue
                any_tab = True
                st.markdown(f"**{lab} 组**（`{comp}`）")
                st.dataframe(tdf, use_container_width=True, hide_index=True)
                for _, row in tdf.iterrows():
                    export_rows.append({"小组": lab, **row.to_dict()})
            if not any_tab:
                st.info("暂无该杯预选积分榜（抽签后应自动出现初始榜）。")
            elif export_rows:
                edf = pd.DataFrame(export_rows)
                st.download_button(
                    f"下载「{CONTINENTAL_LABELS[cont_pick]}」预选积分榜 CSV",
                    edf.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"cont_qual_{cont_pick}_seed{sim.seed}.csv",
                    mime="text/csv",
                    key="dl_cont_qual",
                )

            st.divider()
            st.subheader("预选附加赛（两回合）")
            po_rows = []
            for m in sim.all_results:
                if m.comp != f"{cont_pick}-PO" or not m.played:
                    continue
                note = (m.score_note or "").strip()
                sc = f"{m.hg}-{m.ag}"
                if note:
                    sc = f"{sc} ({note})"
                po_rows.append(
                    {
                        "回合": m.stage,
                        "tie": m.tie_id,
                        "主队": m.home.name,
                        "比分": sc,
                        "客队": m.away.name,
                        "胜者": _match_winner_name(m) if m.round_num >= 2 else "—",
                    }
                )
            if not po_rows:
                st.caption("附加赛尚未开始。")
            else:
                st.dataframe(pd.DataFrame(po_rows), use_container_width=True, hide_index=True)

        elif table_sub == "世界杯预选积分榜":
            st.caption("世界杯周期（Part B）各大洲联赛积分榜。")
            confed_pick = st.selectbox(
                "选择大洲",
                CONFEDS,
                format_func=lambda c: CONFED_LABELS.get(c, c),
                key="qual_confed_pick",
            )
            qual_comp = f"{confed_pick}-QUAL"
            pre_comp = f"{confed_pick}-PRE"

            st.subheader(f"{CONFED_LABELS.get(confed_pick, confed_pick)} · 联赛阶段积分榜")
            if qual_comp in TABLE_ZONES:
                with st.expander("晋级线说明", expanded=False):
                    for lo, hi, lab in TABLE_ZONES[qual_comp]:
                        st.markdown(f"- 第 **{lo}–{hi}** 名：{lab}")

            tdf = _table_to_df(sim, qual_comp)
            if tdf.empty:
                st.info("联赛阶段尚未开始或暂无积分榜。请推进比赛日（需进入 Part B）。")
            else:
                st.dataframe(tdf, use_container_width=True, hide_index=True, height=480)
                st.download_button(
                    f"下载 {qual_comp} 积分榜 CSV",
                    tdf.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"table_{qual_comp}_seed{sim.seed}.csv",
                    mime="text/csv",
                    key="dl_qual_league",
                )

            st.divider()
            st.subheader(f"{CONFED_LABELS.get(confed_pick, confed_pick)} · 第一阶段单场附加赛")
            if confed_pick == "CONMEBOL":
                st.caption("南美预选赛无洲内附加赛，10 队直接进入联赛双循环。")
            pre_df = _prelim_results_df(sim, confed_pick)
            if pre_df.empty:
                st.info("暂无附加赛赛果（可能尚未进行或该洲无附加赛）。")
            else:
                st.dataframe(pre_df, use_container_width=True, hide_index=True)
                st.download_button(
                    f"下载 {pre_comp} 赛果 CSV",
                    pre_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"prelim_{confed_pick}_seed{sim.seed}.csv",
                    mime="text/csv",
                    key="dl_qual_pre",
                )

    elif page == "世界排名":
        st.subheader("实时世界排名与国际积分")
        st.caption(
            "贴近 FIFA SUM：ΔP = I×(W−We)，We 用 600 分档；"
            "I≈10/15/25/35/50；主场+100；大洲权重仅轻度修正；无净胜球放大、无淘汰赛定额奖励。"
            "杯赛淘汰赛（含 24 强附加赛）败方不扣分；预选/小组赛仍可扣分。"
            "Part A 结束写入 team_world_ranks_cycle.json。"
        )
        q_team = st.selectbox(
            "查询国家/地区",
            sorted(t.name for t in sim.teams),
            key="world_rank_lookup",
        )
        if q_team:
            rk = sim.live_ranks.get(q_team, sim.team_map[q_team].world_rank)
            pts = sim.fifa_points.get(q_team, 0.0)
            last = next((r for r in sim.last_day_ranking_delta if r["球队"] == q_team), None)
            c1, c2, c3 = st.columns(3)
            c1.metric("世界排名", rk, delta=(last["排名变化"] if last else None))
            c2.metric("国际积分", round(pts, 1), delta=(last["积分变化"] if last else None))
            c3.metric("大洲", sim.team_map[q_team].confed)
            if last:
                st.caption(
                    f"上个比赛日：排名 {last['原排名']}→{last['新排名']}，"
                    f"积分 {last['原积分']}→{last['新积分']}"
                )

        st.divider()
        st.subheader("完整世界排名（全体球队）")
        full_board = _world_rank_board_df(sim)
        st.caption(f"共 {len(full_board)} 队 · 排名 1–{len(full_board)}，无缺号。")
        st.dataframe(full_board, use_container_width=True, hide_index=True, height=520)
        st.download_button(
            "下载完整世界排名 CSV",
            full_board.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"fifa_ranks_day{sim.day}_seed{sim.seed}.csv",
            mime="text/csv",
            key="dl_tab_ranks",
        )

        if sim.last_day_ranking_delta:
            st.subheader(f"最近一个比赛日（第 {sim.day} 日）参赛队变化")
            st.caption("仅本轮参赛队；名次可能不连续。")
            delta_df = pd.DataFrame(sim.last_day_ranking_delta).sort_values("新排名", ascending=True)
            st.dataframe(delta_df, use_container_width=True, hide_index=True)

        details = getattr(sim, "last_day_rating_details", None) or []
        if details:
            with st.expander("本比赛日积分结算明细（重要性 I / 大洲权重 / 期望）", expanded=False):
                st.dataframe(
                    pd.DataFrame(details)[
                        [
                            c
                            for c in [
                                "赛事",
                                "阶段",
                                "主队",
                                "客队",
                                "比分",
                                "importance",
                                "away_confed_w",
                                "home_confed_w",
                                "delta_home",
                                "delta_away",
                                "expected_home",
                                "knockout",
                            ]
                            if details and c in details[0]
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

    elif page == "三大杯资格":
        if not any(sim.qual_slots.values()):
            st.info("资格名单将在洲际阶段后更新。请继续推进比赛日。")
        else:
            for key, title, n_show in [
                ("WC", "世界冠军杯", 40),
                ("WC_PO", "世界冠军杯附加赛", 30),
                ("WL", "世界联赛杯", 40),
                ("WL_PO", "世界联赛杯附加赛", 30),
                ("WA", "世界协会杯", 40),
                ("WA_PO", "世界协会杯附加赛", 30),
            ]:
                teams = sim.qual_slots.get(key, [])
                if not teams:
                    st.write(f"**{title}**（暂无）")
                    continue
                names = [t.name for t in sorted(teams, key=lambda x: x.world_rank)[:n_show]]
                st.write(f"**{title}**（共 {len(teams)} 队，显示前 {min(n_show, len(names))}）")
                st.write(", ".join(names))

    elif page == "淘汰赛对阵":
        st.subheader("杯赛淘汰赛")
        pick_cup = st.selectbox(
            "选择杯赛",
            list(BRACKET_CUP_LABELS.keys()),
            format_func=lambda k: BRACKET_CUP_LABELS[k],
            key="bracket_cup",
        )
        if pick_cup in CONTINENTAL_CODES:
            st.caption(
                "洲际杯正赛：分组抽签后即生成完整淘汰赛签表占位；"
                "小组赛结束后填入队名，每轮赛果确定后继续填入下一轮。"
                "（传统交叉：A1-B2、C1-D2、E1-F2、G1-H2、B1-A2、D1-C2、F1-E2、H1-G2）"
            )
            rounds = _cup_knockout_rounds(sim, pick_cup)
            if pick_cup not in getattr(sim, "_cont_finals_groups", {}):
                st.info("尚无该杯淘汰赛签表（需先完成正赛分组抽签）。")
            else:
                st.dataframe(
                    _cont_knockout_slots_df(sim, pick_cup, rounds),
                    use_container_width=True,
                    hide_index=True,
                )
                lines_txt = _cont_knockout_bracket_text(rounds, pick_cup, sim)
                bracket_page = _cont_knockout_bracket_html(rounds, pick_cup, sim)
                with st.expander("文字对阵", expanded=False):
                    st.code("\n".join(lines_txt) if lines_txt else "（签表生成中）", language=None)
                if bracket_page:
                    _render_bracket_html(bracket_page, height=840)
        else:
            st.caption(
                "分组抽签后即锁定 16 强签表占位；小组赛/附加赛落位后填队名，"
                "再逐轮填入 8 强→半决赛→决赛。"
                "（24强附加赛 P1–P8 → 16强 → 8强 → 半决赛 → 决赛）"
            )
            r16_slots = _cup_r16_slots(sim, pick_cup)
            _ensure_challenger_bracket_state(sim, pick_cup)
            rounds = _cup_knockout_rounds(sim, pick_cup)
            if not r16_slots:
                st.info("尚无该杯淘汰赛签表（需先完成分组抽签）。")
            else:
                st.dataframe(
                    _cup_knockout_slots_df(sim, pick_cup, rounds, r16_slots),
                    use_container_width=True,
                    hide_index=True,
                )
                lines_txt = _cup_knockout_bracket_text(rounds, pick_cup, sim, r16_slots)
                bracket_page = _cup_knockout_bracket_html(rounds, pick_cup, sim, r16_slots)
                with st.expander("文字对阵", expanded=False):
                    st.code("\n".join(lines_txt) if lines_txt else "（签表生成中）", language=None)
                if bracket_page:
                    _render_bracket_html(bracket_page, height=840)


if __name__ == "__main__":
    main()
