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

from world_cup_challenger import WCC_GROUP_LABELS, build_t_slot_map, get_r16_slots
from world_cup_game import CONFEDS, Simulator, TABLE_ZONES, zone_label_for_rank

BRACKET_CUP_LABELS = {
    "WORLD-CHAMPIONS": "世界冠军杯",
    "WORLD-LEAGUE": "世界联赛杯",
    "WORLD-ASSOCIATION": "世界协会杯",
    "WCC": "世界挑战者杯",
}

GROUP_STAGE_CUPS = [
    ("WORLD-CHAMPIONS", "世界冠军杯"),
    ("WORLD-LEAGUE", "世界联赛杯"),
    ("WORLD-ASSOCIATION", "世界协会杯"),
    ("WCC", "世界挑战者杯"),
]

CONFED_LABELS = {
    "UEFA": "欧洲 (UEFA)",
    "AFC": "亚洲 (AFC)",
    "CONCACAF": "中北美 (CONCACAF)",
    "CAF": "非洲 (CAF)",
    "OFC": "大洋洲 (OFC)",
    "CONMEBOL": "南美 (CONMEBOL)",
}

st.set_page_config(
    page_title="世界杯模拟器",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _ensure_sim(seed: int) -> Simulator:
    if "sim" not in st.session_state or st.session_state.get("sim_seed") != seed:
        st.session_state.sim = Simulator(seed)
        st.session_state.sim_seed = seed
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
    """分组抽签落位档位：1=一档 … 6=六档（抽签结束时固定）。"""
    groups = (
        sim._wcc_draw_groups
        if prefix == "WCC"
        else getattr(sim, "_cup_draw_groups", {}).get(prefix, [])
    )
    if not groups:
        return None
    gi = WCC_GROUP_LABELS.index(group_lab)
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


def _cup_has_group_data(sim: Simulator, prefix: str) -> bool:
    return any(sim.tables.get(f"{prefix}-GS-{lab}") for lab in WCC_GROUP_LABELS)


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
        out.append(
            {
                "排名": rank,
                "球队": name,
                "抽签档位": f"{pot}档" if pot else "—",
                "大洲": t.confed,
                "晋级区间": _group_zone_label(sim, prefix, group_lab, rank),
                "积分": s["PTS"],
                "场次": s["P"],
                "胜": s["W"],
                "平": s["D"],
                "负": s["L"],
                "进球": s["GF"],
                "失球": s["GA"],
                "净胜": s["GD"],
                "OVR": round(t.ovr, 1),
            }
        )
    return pd.DataFrame(out)


def _render_cup_group_standings(sim: Simulator, prefix: str, cup_label: str) -> None:
    """按 A–F 组分块展示，表格样式与联赛阶段一致。"""
    ds = getattr(sim, "_challenger_draw_strength", {}).get(prefix)
    has_data = _cup_has_group_data(sim, prefix)

    if not ds and not has_data:
        st.info(f"「{cup_label}」小组赛尚未开始或暂无积分榜。请推进比赛日。")
        return

    with st.expander("晋级线说明", expanded=False):
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
            st.dataframe(_pre_knockout_po_df(ds, t_map), use_container_width=True, hide_index=True)
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
        st.info(f"「{cup_label}」小组赛尚未开始，积分榜将在正赛开打后更新；上方已展示抽签锁定的淘汰赛签表。")
        return

    for lab in WCC_GROUP_LABELS:
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
    """某杯赛 6 个小组积分榜合并为一张表。"""
    rows = []
    ds = getattr(sim, "_challenger_draw_strength", {}).get(prefix)
    for lab in WCC_GROUP_LABELS:
        comp = f"{prefix}-GS-{lab}"
        tab = sim._sorted_table(comp)
        if not tab:
            continue
        for rank, (name, s) in enumerate(tab, 1):
            t = sim.team_map[name]
            rows.append(
                {
                    "杯赛": cup_label,
                    "小组": lab,
                    "排名": rank,
                    "球队": name,
                    "大洲": t.confed,
                    "晋级区间": _group_zone_label(sim, prefix, lab, rank),
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
                    "OVR": round(t.ovr, 1),
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
                "OVR": round(t.ovr, 1),
            }
        )
    return pd.DataFrame(out)


def _match_winner_name(m) -> str:
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


def _cup_knockout_rounds(sim: Simulator, cup_base: str) -> Dict[str, Any]:
    """收集淘汰赛各轮次赛果（附加赛按 P1–P8 排序）。"""
    po_comp = f"{cup_base}-PO"
    ko_comp = f"{cup_base}-KO"
    order = {id(m): i for i, m in enumerate(sim.all_results)}

    po_ms = [
        m for m in sim.all_results if m.comp == po_comp and m.played and "24强附加赛" in m.stage
    ]
    po_ms.sort(key=lambda x: (x.day, x.round_num, order[id(x)]))
    po_by_slot: Dict[str, Any] = {}
    for m in po_ms:
        slot = _playoff_slot_from_stage(m.stage)
        if slot:
            po_by_slot[slot] = m
    po_ordered = [po_by_slot.get(f"P{i}") for i in range(1, 9)]

    r16 = [m for m in sim.all_results if m.comp == ko_comp and m.played and m.stage.startswith("1/8决赛")]
    r16.sort(key=lambda x: (x.day, x.round_num, order[id(x)]))

    qf = [m for m in sim.all_results if m.comp == ko_comp and m.played and m.stage.startswith("1/4决赛")]
    qf.sort(key=lambda x: (x.day, x.round_num, order[id(x)]))

    sf = [m for m in sim.all_results if m.comp == ko_comp and m.played and m.stage.startswith("半决赛")]
    sf.sort(key=lambda x: (x.day, x.round_num, order[id(x)]))

    fin_ms = [m for m in sim.all_results if m.comp == ko_comp and m.played and m.stage == "决赛"]

    return {
        "po": po_ordered,
        "r16": r16,
        "qf": qf,
        "sf": sf,
        "fin": fin_ms[0] if fin_ms else None,
    }


def _r16_matches_by_index(rounds: Dict[str, Any]) -> Dict[int, Any]:
    return {m.round_num - 1: m for m in rounds["r16"]}


def _placement_team_name(sim: Simulator, cup_base: str, key: str) -> str:
    state = getattr(sim, "_challenger_bracket_state", {}).get(cup_base, {})
    team = state.get("placements", {}).get(key)
    return team.name if team else key


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

    seed = _placement_team_name(sim, cup_base, left_key)
    if right_key.startswith("P"):
        po_m = rounds["po"][int(right_key[1:]) - 1]
        if po_m and po_m.played:
            p_winner = _match_winner_name(po_m)
            return _fixed_pair_card_html(meta, seed, p_winner, bottom_win=True)
        return _fixed_pair_card_html(meta, seed, f"{right_key} 待定")
    other = _placement_team_name(sim, cup_base, right_key)
    return _fixed_pair_card_html(meta, seed, other)


def _po_match_for_slot(rounds: Dict[str, Any], p_key: str) -> Any:
    return rounds["po"][int(p_key[1:]) - 1]


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
    if not any(rounds["po"]) and not rounds["r16"]:
        return lines_txt

    cup_title = BRACKET_CUP_LABELS.get(cup_base, cup_base)
    lines_txt.append(f"【{cup_title} 淘汰赛】")
    r16_map = _r16_matches_by_index(rounds)

    po_any = [m for m in rounds["po"] if m is not None]
    if po_any:
        lines_txt.append("24强附加赛（8 场，胜者进入固定 16 强签位）")
        for slot_id, _left, right_key in r16_slots:
            if not right_key.startswith("P"):
                continue
            po_m = _po_match_for_slot(rounds, right_key)
            if po_m is None:
                continue
            lines_txt.append(
                f"  [{right_key}→{slot_id}] {po_m.home.name} {po_m.hg}-{po_m.ag} {po_m.away.name}"
                f"  →  {_match_winner_name(po_m)}"
            )

    if any(rounds["po"]) or rounds["r16"]:
        lines_txt.append("16强（直通种子 vs 附加赛胜者）")
        for i, (slot_id, left_key, right_key) in enumerate(r16_slots):
            m = r16_map.get(i)
            if m:
                lines_txt.append(
                    f"  [{slot_id}] {left_key} vs {right_key}: "
                    f"{m.home.name} {m.hg}-{m.ag} {m.away.name}  →  {_match_winner_name(m)}"
                )
            else:
                seed = _placement_team_name(sim, cup_base, left_key)
                if right_key.startswith("P"):
                    po_m = _po_match_for_slot(rounds, right_key)
                    if po_m and po_m.played:
                        pw = _match_winner_name(po_m)
                        lines_txt.append(
                            f"  [{slot_id}] {left_key} {seed} vs {right_key}胜者 {pw}  （待赛）"
                        )
                    else:
                        lines_txt.append(f"  [{slot_id}] {left_key} {seed} vs {right_key}待定")
                else:
                    other = _placement_team_name(sim, cup_base, right_key)
                    lines_txt.append(f"  [{slot_id}] {left_key} {seed} vs {right_key} {other}  （待赛）")

    for label, ms in [("8强", rounds["qf"]), ("半决赛", rounds["sf"])]:
        if not ms:
            continue
        lines_txt.append(label)
        for m in ms:
            tag = m.stage.split("·")[-1] if "·" in m.stage else m.stage
            lines_txt.append(f"  [{tag}] {m.home.name} {m.hg}-{m.ag} {m.away.name}  →  {_match_winner_name(m)}")

    if rounds["fin"]:
        m = rounds["fin"]
        lines_txt.append("决赛")
        lines_txt.append(
            f"  {m.home.name} {m.hg}-{m.ag} {m.away.name}  →  冠军 {_match_winner_name(m)}"
        )
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
    if not any(rounds["po"]) and not rounds["r16"]:
        return ""

    h = 780
    po_cards = _po_cards_aligned_to_r16(rounds, r16_slots)
    r16_cards = [
        _r16_slot_card_html(sim, cup_base, rounds, i, slot_id, left_key, right_key)
        for i, (slot_id, left_key, right_key) in enumerate(r16_slots)
    ]
    qf_cards = [
        _match_card_html(rounds["qf"][i] if i < len(rounds["qf"]) else None, f"QF{i + 1}")
        for i in range(4)
    ]
    sf_cards = [
        _match_card_html(rounds["sf"][i] if i < len(rounds["sf"]) else None, f"SF{i + 1}")
        for i in range(2)
    ]
    fin_card = _match_card_html(rounds["fin"], "决赛", final=True)

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


def _render_bracket_html(page: str, height: int = 820) -> None:
    components.html(page, height=height, scrolling=True)


def main() -> None:
    st.title("⚽ 世界杯预选赛 & 三大杯模拟器")
    st.caption(
        "推进比赛日 · 洲际/分档按 data/team_world_ranks.json · 战力 OVR 见 data/team_ovr_overrides.json · "
        "三大杯与世界挑战者杯均采用 36 队 6 组单循环 + 24 强积分种子附加赛制"
    )

    with st.sidebar:
        st.header("控制")
        seed = st.number_input("随机种子", min_value=0, max_value=2**31 - 1, value=42, step=1)
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("新开局", use_container_width=True):
                st.session_state.pop("sim", None)
                st.session_state.pop("sim_seed", None)
                _ensure_sim(int(seed))
                st.rerun()
        with col_b:
            if st.button("重置种子并开局", use_container_width=True):
                st.session_state.pop("sim", None)
                st.session_state.pop("sim_seed", None)
                _ensure_sim(int(seed))
                st.rerun()

        sim = _ensure_sim(int(seed))

        st.divider()
        n_skip = st.slider("一次推进天数", 1, 30, 1)
        if st.button(f"推进 {n_skip} 个比赛日", type="primary", use_container_width=True):
            for _ in range(n_skip):
                if not sim.next_day():
                    break
            st.rerun()

        if st.button("推进到赛季结束", use_container_width=True):
            while sim.next_day():
                pass
            st.rerun()

        st.divider()
        st.subheader("状态")
        st.write(f"**当前比赛日:** {sim.day}")
        st.write(f"**阶段:** {sim.phase_name or '—'}")
        if sim.phase_name == "已结束":
            st.success("本赛季已全部结束。")
            if getattr(sim, "cup_champions", None):
                st.markdown(
                    "**三大杯冠军：** "
                    + " | ".join(f"{k}: **{v}**" for k, v in sim.cup_champions.items())
                )
        else:
            left = sum(len(d) for d in sim.phase_matchdays) if sim.phase_matchdays else 0
            st.caption(f"本阶段剩余比赛日: {left}")

    sim = _ensure_sim(int(seed))

    tab_draws, tab_overview, tab_matches, tab_tables, tab_slots, tab_bracket = st.tabs(
        ["抽签与赛程", "总览", "全部赛果", "积分榜", "三大杯资格", "淘汰赛对阵"]
    )

    with tab_draws:
        st.subheader("抽签记录")
        if not sim.draw_log:
            st.info("开局后可见：附加赛抽签 → 联赛分档 → 完整轮次赛程表。")
        else:
            for i, entry in enumerate(sim.draw_log):
                et = entry.get("type", "?")
                with st.expander(f"{i+1}. [{et}]", expanded=(i < 3)):
                    st.json(entry)

        st.subheader("联赛 / 正赛赛程表（赛前即定，与模拟赛果一致）")
        comps_sched = sorted(sim.league_schedule_by_confed.keys())
        if not comps_sched:
            st.caption("进行附加赛并生成联赛后显示。")
        else:
            pick_s = st.selectbox("选择赛事", comps_sched, key="sched_pick")
            rounds_data = sim.league_schedule_by_confed.get(pick_s, [])
            for ridx, rnd in enumerate(rounds_data, start=1):
                lines = [f"{a} {vs} {b}  （{lbl}）" for a, vs, b, lbl in rnd]
                st.markdown(f"**第 {ridx} 轮**（{len(lines)} 场）")
                st.text("\n".join(lines))

        st.subheader("联赛阶段：各队对手（按档）")
        if not sim.league_opponents_by_comp:
            st.caption("生成洲内联赛或三大杯正赛联赛后，此处显示模拟抽签得到的各队对手。")
        else:
            c_opp, t_opp = st.columns([1, 1])
            with c_opp:
                opp_comp = st.selectbox(
                    "选择赛事",
                    sorted(sim.league_opponents_by_comp.keys()),
                    key="opp_by_pot_comp",
                )
            teams_for_comp = sorted(sim.league_opponents_by_comp.get(opp_comp, {}).keys())
            with t_opp:
                opp_team = st.selectbox("选择球队", teams_for_comp, key="opp_by_pot_team")
            if opp_team and opp_comp:
                rows = []
                for pot_label, names in sorted(
                    sim.league_opponents_by_comp[opp_comp][opp_team].items(),
                    key=lambda x: x[0],
                ):
                    for n in names:
                        rows.append({"对手所在档": pot_label, "对手": n})
                if rows:
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                else:
                    st.caption("无数据。")

        st.subheader("查询球队未来赛程")
        all_names = sorted(t.name for t in sim.teams)
        q_team = st.selectbox("选择球队", all_names, key="future_sched_team")
        fut = sim.upcoming_matches_for_team(q_team)
        if not fut:
            st.info("暂无未赛场次（可能本赛季已结束，或该队已无剩余比赛）。")
        else:
            st.dataframe(pd.DataFrame(fut), use_container_width=True, hide_index=True)

    with tab_overview:
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("已赛总场次", len(sim.all_results))
        with c2:
            st.metric("涉及赛事数", len(sim.list_competitions()))
        with c3:
            st.metric("阶段", sim.phase_idx)
        with c4:
            st.metric("种子", sim.seed)

        if getattr(sim, "wcc_champion", ""):
            st.success(f"世界挑战者杯冠军：**{sim.wcc_champion}**")

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

    with tab_matches:
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

    with tab_tables:
        tab_cup_tables, tab_qual_tables = st.tabs(["杯赛积分榜", "预选赛积分榜"])

        with tab_cup_tables:
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

        with tab_qual_tables:
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
                st.info("联赛阶段尚未开始或暂无积分榜。请推进比赛日。")
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

    with tab_slots:
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

    with tab_bracket:
        st.subheader("杯赛淘汰赛树状图")
        st.caption(
            "24强附加赛 (P1–P8) → 16强 → 8强 → 半决赛 → 决赛；"
            "16强签表在分组抽签后由同组回避算法生成（种子分上下半区，P1–P4 同半区回避）。"
        )
        pick_cup = st.selectbox(
            "选择杯赛",
            list(BRACKET_CUP_LABELS.keys()),
            format_func=lambda k: BRACKET_CUP_LABELS[k],
            key="bracket_cup",
        )
        r16_slots = _cup_r16_slots(sim, pick_cup)
        rounds = _cup_knockout_rounds(sim, pick_cup)
        lines_txt = _cup_knockout_bracket_text(rounds, pick_cup, sim, r16_slots) if r16_slots else []
        bracket_page = _cup_knockout_bracket_html(rounds, pick_cup, sim, r16_slots) if r16_slots else ""
        if not lines_txt:
            st.info("本赛季尚无该杯淘汰赛赛果（需至少完成 24 强附加赛或 16 强）。")
        else:
            with st.expander("文字对阵", expanded=False):
                st.code("\n".join(lines_txt), language=None)
            if bracket_page:
                _render_bracket_html(bracket_page, height=840)


if __name__ == "__main__":
    main()
