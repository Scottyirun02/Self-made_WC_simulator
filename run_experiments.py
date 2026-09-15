"""
批量实验：固定东道主，用不同随机种子并行跑完整四年周期。

存储（--out 目录，默认 experiments/）：
  runs/seed_<N>.json         每次实验完整结果（全部赛事完整积分榜 + 杯赛淘汰赛逐场比分 + 附加赛逐场比分 + 冠军）
  index.json                 seed -> 10 项冠军 + 文件名（轻量索引）
  champion_distribution.csv  10 项赛事冠军分布大表

用法：
  python run_experiments.py --runs 1000                 # 跑实验（中断可续跑，已存在的 seed 跳过）
  python run_experiments.py --query 42                  # 完整打印 seed=42 的实验结果
  python run_experiments.py --query 42 --comp EURO-KO   # 只看单个赛事
  python run_experiments.py --reaggregate               # 从已有结果重建冠军分布大表
"""
from __future__ import annotations

import argparse
import csv
import functools
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import world_cup_ratings as _wr
import world_cup_game as _game
from continental_cups import CONTINENTAL_LABELS
from world_cup_game import CONFEDS, CONTINENTAL_CODES, FINAL_CUPS, Simulator

EVENT_ORDER = [
    "EURO", "AFCON", "APAC", "AMERICA", "CC",
    "WCC", "WORLD-CHAMPIONS", "WORLD-LEAGUE", "WORLD-ASSOCIATION", "WSC",
]
EVENT_LABELS = {
    **CONTINENTAL_LABELS,
    "CC": "联合会杯",
    "WCC": "世界挑战者杯",
    "WORLD-CHAMPIONS": "世界冠军杯",
    "WORLD-LEAGUE": "世界联赛杯",
    "WORLD-ASSOCIATION": "世界协会杯",
    "WSC": "世界超级杯",
}
CUP_IDS = CONTINENTAL_CODES + ["CC", "WCC"] + FINAL_CUPS + ["WSC"]
PLAYOFF_COMPS = [f"{c}-PRE" for c in CONFEDS] + ["WC-PO", "WL-PO", "WA-PO"]
PLAYOFF_LABELS = {
    **{f"{c}-PRE": f"{c} 洲内附加赛" for c in CONFEDS},
    "WC-PO": "世界冠军杯·洲际附加赛",
    "WL-PO": "世界联赛杯·洲际附加赛",
    "WA-PO": "世界协会杯·洲际附加赛",
}


_orig_league_draw = _game.simulate_uefa_style_league_draw


def _patch_io() -> None:
    """禁用排名库文件写（并行安全）；reset 语义等价于直接读 original 库。
    联赛阶段抽签提高重试上限：对 8000 次内成功的种子结果不变，仅挽救 greedy_dead_end 的种子。"""
    _game.reset_cycle_ranks_from_original = _wr.load_original_ranks
    _game.save_world_ranks = lambda *a, **k: None
    _game.simulate_uefa_style_league_draw = functools.partial(_orig_league_draw, max_attempts=200000)


def _match_winner_name(m: Any) -> Optional[str]:
    if m.winner is not None:
        return m.winner.name
    if m.hg > m.ag:
        return m.home.name
    if m.ag > m.hg:
        return m.away.name
    return None  # 两回合首回合等允许平局的情形


def _match_record(m: Any) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "stage": m.stage,
        "round": m.round_num,
        "day": m.day,
        "home": m.home.name,
        "away": m.away.name,
        "score": f"{m.hg}-{m.ag}",
        "winner": _match_winner_name(m),
    }
    if m.score_note:
        rec["note"] = m.score_note
    tie_id = getattr(m, "tie_id", None)
    if tie_id:
        rec["tie_id"] = tie_id
    return rec


def extract_record(sim: Simulator, seed: int) -> Dict[str, Any]:
    standings: Dict[str, List[Dict[str, Any]]] = {}
    for comp in sorted(sim.tables.keys()):
        rows = []
        for rank, (name, st) in enumerate(sim._sorted_table(comp), start=1):
            rows.append(
                {
                    "rank": rank, "team": name,
                    "P": st["P"], "W": st["W"], "D": st["D"], "L": st["L"],
                    "GF": st["GF"], "GA": st["GA"], "GD": st["GD"], "PTS": st["PTS"],
                }
            )
        standings[comp] = rows

    ko_comps = {f"{c}-PO" for c in CUP_IDS} | {f"{c}-KO" for c in CUP_IDS}
    knockouts: Dict[str, List[Dict[str, Any]]] = {c: [] for c in CUP_IDS}
    playoffs: Dict[str, List[Dict[str, Any]]] = {c: [] for c in PLAYOFF_COMPS}
    for m in sim.all_results:
        if not m.played:
            continue
        if m.comp in ko_comps:
            knockouts[m.comp.rsplit("-", 1)[0]].append(_match_record(m))
        elif m.comp in playoffs:
            playoffs[m.comp].append(_match_record(m))
    for lst in list(knockouts.values()) + list(playoffs.values()):
        lst.sort(key=lambda r: (r["day"], r["round"]))

    champions = {e: "" for e in EVENT_ORDER}
    champions.update(sim.continental_champions)
    champions["CC"] = sim.cc_champion
    champions["WCC"] = sim.wcc_champion
    champions["WSC"] = sim.wsc_champion
    champions.update(sim.cup_champions)

    return {
        "seed": seed,
        "hosts": dict(sim.hosts),
        "days": sim.day,
        "champions": champions,
        "standings": standings,
        "knockouts": knockouts,
        "playoffs": playoffs,
    }


def run_one(seed: int, hosts: Dict[str, str]) -> Dict[str, Any]:
    sim = Simulator(seed, hosts=hosts)
    while sim.next_day():
        pass
    return extract_record(sim, seed)


# ---------------- 存储 ----------------

def _write_json_atomic(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _load_index(out_dir: Path) -> Dict[str, Any]:
    p = out_dir / "index.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


# ---------------- 聚合：冠军分布大表 ----------------

def aggregate(out_dir: Path) -> tuple[Dict[str, Counter], int]:
    counters: Dict[str, Counter] = {e: Counter() for e in EVENT_ORDER}
    files = sorted((out_dir / "runs").glob("seed_*.json"))
    n = 0
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"[warn] 跳过无法解析的文件: {f}")
            continue
        n += 1
        for e in EVENT_ORDER:
            ch = rec.get("champions", {}).get(e)
            if ch:
                counters[e][ch] += 1
    return counters, n


def write_distribution(out_dir: Path, counters: Dict[str, Counter], n_runs: int) -> None:
    teams = sorted({t for c in counters.values() for t in c})
    rows = []
    for t in teams:
        counts = [counters[e][t] for e in EVENT_ORDER]
        total = sum(counts)
        rows.append((t, counts, total))
    rows.sort(key=lambda x: (-x[2], x[0]))

    csv_path = out_dir / "champion_distribution.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["球队"] + [EVENT_LABELS[e] for e in EVENT_ORDER] + ["总计", "夺冠率%"])
        for t, counts, total in rows:
            w.writerow([t] + counts + [total, round(100.0 * total / max(1, n_runs), 2)])

    print(f"\n冠军分布大表（{n_runs} 次实验，{len(rows)} 支夺冠球队）-> {csv_path}")
    header = f"{'球队':<24}" + "".join(f"{EVENT_LABELS[e][:4]:>6}" for e in EVENT_ORDER) + f"{'总计':>6}{'夺冠率%':>9}"
    print(header)
    print("-" * len(header))
    for t, counts, total in rows:
        line = f"{t:<24}" + "".join(f"{c:>6}" for c in counts)
        line += f"{total:>6}{100.0 * total / max(1, n_runs):>9.2f}"
        print(line)


# ---------------- 单次调取 ----------------

def comp_label(comp: str) -> str:
    for code, lab in CONTINENTAL_LABELS.items():
        if comp.startswith(f"{code}-QUAL-"):
            return f"{lab}·预选{comp.rsplit('-', 1)[1]}组"
        if comp.startswith(f"{code}-GS-"):
            return f"{lab}·正赛{comp.rsplit('-', 1)[1]}组"
        if comp == f"{code}-PO":
            return f"{lab}·预选附加赛"
        if comp == f"{code}-KO":
            return f"{lab}·淘汰赛"
    if comp.startswith("CC-GS-"):
        return f"联合会杯·小组{comp.rsplit('-', 1)[1]}"
    if comp == "CC-KO":
        return "联合会杯·淘汰赛"
    if comp == "WSC-PO":
        return "世界超级杯·附加赛"
    if comp == "WSC-KO":
        return "世界超级杯·淘汰赛"
    for cup in ["WCC"] + FINAL_CUPS:
        lab = EVENT_LABELS[cup]
        if comp.startswith(f"{cup}-GS-"):
            return f"{lab}·小组{comp.rsplit('-', 1)[1]}"
        if comp == f"{cup}-PO":
            return f"{lab}·24强附加赛"
        if comp == f"{cup}-KO":
            return f"{lab}·淘汰赛"
    if comp in PLAYOFF_LABELS:
        return PLAYOFF_LABELS[comp]
    if comp.endswith("-QUAL"):
        return f"{comp[:-5]} 洲内联赛"
    return comp


def _print_match_lines(matches: List[Dict[str, Any]]) -> None:
    for r in matches:
        note = f"（{r['note']}）" if r.get("note") else ""
        win = r["winner"] or "—"
        print(f"  [第{r['day']}日 {r['stage']}] {r['home']} {r['score']} {r['away']}{note}  胜者: {win}")


def query_run(out_dir: Path, seed: int, comp: Optional[str] = None) -> None:
    p = out_dir / "runs" / f"seed_{seed}.json"
    if not p.is_file():
        print(f"未找到实验文件: {p}")
        return
    rec = json.loads(p.read_text(encoding="utf-8"))
    print(f"seed={rec['seed']}  比赛日数={rec['days']}  东道主={rec['hosts']}")
    print("\n== 冠军 ==")
    for e in EVENT_ORDER:
        print(f"  {EVENT_LABELS[e]}: {rec['champions'].get(e) or '—'}")

    def want(c: str) -> bool:
        return comp is None or c == comp

    print("\n== 附加赛（洲内 PRE + 洲际 WC/WL/WA-PO）==")
    for c in PLAYOFF_COMPS:
        ms = rec["playoffs"].get(c) or []
        if not ms or not want(c):
            continue
        print(f"\n-- {c} | {comp_label(c)} --")
        _print_match_lines(ms)

    print("\n== 杯赛淘汰赛路径（含预选/24强附加赛）==")
    for c in CUP_IDS:
        ms = rec["knockouts"].get(c) or []
        if not ms or not (want(f"{c}-PO") or want(f"{c}-KO") or comp == c):
            continue
        print(f"\n-- {EVENT_LABELS[c]} --")
        _print_match_lines(ms)

    print("\n== 积分榜（完整）==")
    for c in sorted(rec["standings"].keys()):
        if not want(c):
            continue
        rows = rec["standings"][c]
        print(f"\n-- {c} | {comp_label(c)} --")
        print(f"  {'#':>2} {'球队':<24}{'P':>3}{'W':>4}{'D':>4}{'L':>4}{'GF':>5}{'GA':>5}{'GD':>5}{'PTS':>6}")
        for r in rows:
            print(
                f"  {r['rank']:>2} {r['team']:<24}{r['P']:>3}{r['W']:>4}{r['D']:>4}{r['L']:>4}"
                f"{r['GF']:>5}{r['GA']:>5}{r['GD']:>5}{r['PTS']:>6}"
            )


# ---------------- 主流程 ----------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=1000, help="实验次数（默认 1000）")
    ap.add_argument("--seed-start", type=int, default=1, help="起始种子（默认 1，种子=seed_start+i）")
    ap.add_argument("--workers", type=int, default=min(14, os.cpu_count() or 4), help="并行进程数")
    ap.add_argument("--out", default="experiments", help="输出目录（默认 experiments/）")
    ap.add_argument("--query", type=int, default=None, metavar="SEED", help="调取并完整打印某次实验")
    ap.add_argument("--comp", default=None, help="配合 --query：只看指定赛事代码")
    ap.add_argument("--reaggregate", action="store_true", help="不重跑，从已有结果重建冠军分布")
    args = ap.parse_args()
    out_dir = Path(args.out)

    if args.query is not None:
        query_run(out_dir, args.query, args.comp)
        return
    if args.reaggregate:
        counters, n = aggregate(out_dir)
        if n == 0:
            print(f"{out_dir}/runs 下没有可用的实验结果")
            return
        write_distribution(out_dir, counters, n)
        return

    _patch_io()
    probe = Simulator(0)
    hosts = dict(probe.hosts)
    print("固定东道主:", hosts, flush=True)

    runs_dir = out_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    seeds = [args.seed_start + i for i in range(args.runs)]
    todo = [s for s in seeds if not (runs_dir / f"seed_{s}.json").exists()]
    print(f"总 {len(seeds)} 次；已完成 {len(seeds) - len(todo)}；待跑 {len(todo)}；workers={args.workers}", flush=True)

    index = _load_index(out_dir)
    t0 = time.time()
    done = 0
    errors = 0
    if todo:
        with ProcessPoolExecutor(max_workers=args.workers, initializer=_patch_io) as ex:
            futs = {ex.submit(run_one, s, hosts): s for s in todo}
            for fut in as_completed(futs):
                seed = futs[fut]
                try:
                    rec = fut.result()
                except Exception as e:
                    errors += 1
                    print(f"[ERROR] seed={seed}: {e!r}", flush=True)
                    continue
                _write_json_atomic(runs_dir / f"seed_{seed}.json", rec)
                index[str(seed)] = {"file": f"runs/seed_{seed}.json", "champions": rec["champions"]}
                done += 1
                if done % 50 == 0 or done == len(todo):
                    el = time.time() - t0
                    eta = el / done * (len(todo) - done)
                    print(f"[{done}/{len(todo)}] 已用 {el / 60:.1f} 分钟，预计剩余 {eta / 60:.1f} 分钟", flush=True)
                    if done % 200 == 0:
                        _write_json_atomic(out_dir / "index.json", index)
    _write_json_atomic(out_dir / "index.json", index)

    counters, n = aggregate(out_dir)
    write_distribution(out_dir, counters, n)
    print(f"ALL DONE: 成功 {n} 次，失败 {errors} 次，耗时 {(time.time() - t0) / 60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
