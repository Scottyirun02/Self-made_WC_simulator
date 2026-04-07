"""
世界杯模拟器 — Streamlit 网页界面
运行: streamlit run world_cup_app.py
或:  python -m streamlit run d:/intern/world_cup_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

# 保证可导入同目录下的 world_cup_game
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from world_cup_game import CONFEDS, Simulator, TABLE_ZONES, zone_label_for_rank

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


def _mq(s: str) -> str:
    """Mermaid 节点文案简单消毒。"""
    return s.replace('"', "'").replace("\n", " ").replace("[", "(").replace("]", ")")


def _cup_knockout_bracket_mermaid(sim: Simulator, cup_base: str) -> Tuple[str, List[str]]:
    """横向分层、无 subgraph，避免决赛节点视觉上「套住」前面所有轮次。"""
    comp = f"{cup_base}-KO"
    order = {id(m): i for i, m in enumerate(sim.all_results)}
    stages = [("1/8决赛", "r16"), ("1/4决赛", "qf"), ("半决赛", "sf"), ("决赛", "fin")]
    by: dict[str, list] = {abbr: [] for _, abbr in stages}
    for st_cn, abbr in stages:
        ms = [m for m in sim.all_results if m.comp == comp and st_cn in m.stage]
        ms.sort(key=lambda x: (x.day, order[id(x)]))
        by[abbr] = ms

    r16, qf, sf, fin = by["r16"], by["qf"], by["sf"], by["fin"]
    lines_txt: List[str] = []
    if not r16:
        return "", []

    cup_title = {
        "WORLD-CHAMPIONS": "世界冠军杯",
        "WORLD-LEAGUE": "世界联赛杯",
        "WORLD-ASSOCIATION": "世界协会杯",
    }.get(cup_base, cup_base)
    lines_txt.append(f"【{cup_title} 淘汰赛】")
    for label, ms in [("1/8 决赛", r16), ("1/4 决赛", qf), ("半决赛", sf), ("决赛", fin)]:
        if not ms:
            continue
        lines_txt.append(label)
        for m in ms:
            lines_txt.append(f"  {m.home.name} {m.hg}-{m.ag} {m.away.name}  →  {_match_winner_name(m)}")

    lines: List[str] = ["flowchart LR"]
    for i, m in enumerate(r16):
        lines.append(f'  r{i}["{_mq(f"{m.hg}-{m.ag} {m.home.name} v {m.away.name}")}"]')
    for i, m in enumerate(qf):
        w = _match_winner_name(m)
        lines.append(f'  q{i}["{_mq(f"{m.hg}-{m.ag} → {w}")}"]')
    for i, m in enumerate(sf):
        w = _match_winner_name(m)
        lines.append(f'  s{i}["{_mq(f"{m.hg}-{m.ag} → {w}")}"]')
    for i, m in enumerate(fin):
        w = _match_winner_name(m)
        lines.append(f'  f{i}["{_mq(f"决赛 {m.hg}-{m.ag} 冠军 {w}")}"]')

    for j in range(0, len(r16), 2):
        t = j // 2
        if t < len(qf):
            lines.append(f"  r{j} --> q{t}")
            lines.append(f"  r{j + 1} --> q{t}")
    for j in range(0, len(qf), 2):
        t = j // 2
        if t < len(sf):
            lines.append(f"  q{j} --> s{t}")
            lines.append(f"  q{j + 1} --> s{t}")
    if len(sf) >= 2 and fin:
        lines.append("  s0 --> f0")
        lines.append("  s1 --> f0")

    return "\n".join(lines), lines_txt


def _render_mermaid(diagram: str, height: int = 600) -> None:
    # 赛果来自本地模拟；直接嵌入 Mermaid 文本
    html_page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.6.1/dist/mermaid.min.js"></script>
</head><body style="margin:0;padding:12px;background:#0e1117;">
<div class="mermaid">
{diagram}
</div>
<script>
mermaid.initialize({{ startOnLoad: true, theme: "dark", securityLevel: "loose" }});
</script>
</body></html>"""
    components.html(html_page, height=height, scrolling=True)


def main() -> None:
    st.title("⚽ 世界杯预选赛 & 三大杯模拟器")
    st.caption(
        "推进比赛日 · 洲际/分档按 data/team_world_ranks.json · 战力 OVR 见 data/team_ovr_overrides.json"
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
        comps = [c for c in sim.list_competitions() if c in sim.tables and sim.tables[c]]
        if not comps:
            st.info("暂无积分榜。请先推进比赛日。")
        else:
            choice = st.selectbox("选择赛事", comps, index=0)
            if choice in TABLE_ZONES:
                st.markdown("**本赛事晋级线（名次区间）**")
                for lo, hi, lab in TABLE_ZONES[choice]:
                    st.markdown(f"- 第 **{lo}–{hi}** 名：{lab}")
            top_n = st.slider("显示名次", 5, 220, 40)
            tdf = _table_to_df(sim, choice)
            st.dataframe(tdf.head(top_n), use_container_width=True, hide_index=True)
            st.download_button(
                "下载该赛事积分榜 CSV",
                tdf.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"table_{choice}_seed{sim.seed}.csv",
                mime="text/csv",
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
        st.subheader("三大杯淘汰赛树状图")
        st.caption("左→右为晋级方向；仅决赛节点写「冠军」。图需联网加载 Mermaid。")
        cup_labels = {
            "WORLD-CHAMPIONS": "世界冠军杯",
            "WORLD-LEAGUE": "世界联赛杯",
            "WORLD-ASSOCIATION": "世界协会杯",
        }
        pick_cup = st.selectbox("选择杯赛", list(cup_labels.keys()), format_func=lambda k: cup_labels[k], key="bracket_cup")
        diagram, lines_txt = _cup_knockout_bracket_mermaid(sim, pick_cup)
        if not lines_txt:
            st.info("本赛季尚无该杯淘汰赛赛果（或尚未进行到 1/8 决赛及之后）。")
        else:
            with st.expander("文字对阵（不依赖外网）", expanded=True):
                st.code("\n".join(lines_txt), language=None)
            if diagram:
                _render_mermaid(diagram, height=720)


if __name__ == "__main__":
    main()
