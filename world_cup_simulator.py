import argparse
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple


CONFEDS = ["UEFA", "AFC", "CONCACAF", "CAF", "OFC", "CONMEBOL"]


@dataclass(order=True)
class Team:
    world_rank: int
    name: str = field(compare=False)
    confed: str = field(compare=False)
    rating: float = field(compare=False)


def rating_from_rank(rank: int, total: int) -> float:
    pct = 1.0 - ((rank - 1) / max(1, total - 1))
    return 1200 + 800 * pct


def generate_teams() -> List[Team]:
    confed_counts = {
        "UEFA": 55,
        "AFC": 47,
        "CONCACAF": 41,
        "CAF": 54,
        "OFC": 13,
        "CONMEBOL": 10,
    }
    teams: List[Team] = []
    rank = 1
    total = sum(confed_counts.values())
    for confed in CONFEDS:
        for i in range(1, confed_counts[confed] + 1):
            teams.append(
                Team(
                    world_rank=rank,
                    name=f"{confed}_Team_{i:02d}",
                    confed=confed,
                    rating=rating_from_rank(rank, total),
                )
            )
            rank += 1
    return teams


def win_prob(home: Team, away: Team, home_adv: float = 60.0) -> float:
    d = (home.rating + home_adv) - away.rating
    return 1.0 / (1.0 + 10 ** (-d / 400.0))


def play_match(home: Team, away: Team, rng: random.Random) -> Tuple[int, int]:
    p_home = win_prob(home, away)
    draw_prob = max(0.10, 0.22 - abs(home.rating - away.rating) / 3000.0)
    r = rng.random()
    if r < draw_prob:
        g = rng.choice([0, 1, 1, 2])
        return g, g
    if rng.random() < p_home:
        return rng.choice([1, 2, 2, 3]), rng.choice([0, 1, 1, 2])
    return rng.choice([0, 1, 1, 2]), rng.choice([1, 2, 2, 3])


def single_leg_winner(home: Team, away: Team, rng: random.Random) -> Team:
    hg, ag = play_match(home, away, rng)
    if hg > ag:
        return home
    if ag > hg:
        return away
    return home if rng.random() < win_prob(home, away) else away


def two_leg_winner(a: Team, b: Team, rng: random.Random) -> Team:
    a1, b1 = play_match(a, b, rng)
    b2, a2 = play_match(b, a, rng)
    agg_a = a1 + a2
    agg_b = b1 + b2
    if agg_a > agg_b:
        return a
    if agg_b > agg_a:
        return b
    pa = 1.0 / (1.0 + math.exp(-(a.rating - b.rating) / 120.0))
    return a if rng.random() < pa else b


def split_pots(teams: List[Team], pot_count: int) -> List[List[Team]]:
    ordered = sorted(teams, key=lambda x: x.world_rank)
    n = len(ordered)
    base = n // pot_count
    rem = n % pot_count
    pots: List[List[Team]] = []
    idx = 0
    for i in range(pot_count):
        size = base + (1 if i < rem else 0)
        pots.append(ordered[idx : idx + size])
        idx += size
    return pots


def swiss_opponents(teams: List[Team], pot_count: int, per_pot: int, rng: random.Random) -> Dict[str, List[Team]]:
    pots = split_pots(teams, pot_count)
    team_map = {t.name: t for t in teams}
    team_to_pot: Dict[str, int] = {}
    for pi, p in enumerate(pots):
        for t in p:
            team_to_pot[t.name] = pi
    opps: Dict[str, Set[str]] = {t.name: set() for t in teams}
    names = [t.name for t in sorted(teams, key=lambda x: x.world_rank)]

    for name in names:
        for p in range(pot_count):
            need = per_pot - sum(1 for o in opps[name] if team_to_pot[o] == p)
            if need <= 0:
                continue
            cands = [x for x in pots[p] if x.name != name and x.name not in opps[name]]
            rng.shuffle(cands)
            for c in cands:
                if need <= 0:
                    break
                c_need = per_pot - sum(1 for o in opps[c.name] if team_to_pot[o] == team_to_pot[name])
                if c_need <= 0:
                    continue
                opps[name].add(c.name)
                opps[c.name].add(name)
                need -= 1

    for name in names:
        for p in range(pot_count):
            while sum(1 for o in opps[name] if team_to_pot[o] == p) < per_pot:
                cands = [x for x in pots[p] if x.name != name and x.name not in opps[name]]
                if not cands:
                    break
                c = rng.choice(cands)
                opps[name].add(c.name)
                opps[c.name].add(name)

    return {k: [team_map[n] for n in v] for k, v in opps.items()}


def league_phase(teams: List[Team], pot_count: int, per_pot: int, rng: random.Random) -> List[Tuple[Team, Dict[str, int]]]:
    opps = swiss_opponents(teams, pot_count, per_pot, rng)
    stats = {t.name: {"P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "GD": 0, "PTS": 0} for t in teams}
    team_map = {t.name: t for t in teams}
    seen: Set[Tuple[str, str]] = set()
    for t in teams:
        for o in opps[t.name]:
            a, b = sorted([t.name, o.name])
            if (a, b) in seen:
                continue
            seen.add((a, b))
            home, away = (t, o) if rng.random() < 0.5 else (o, t)
            hg, ag = play_match(home, away, rng)
            for x in (home.name, away.name):
                stats[x]["P"] += 1
            stats[home.name]["GF"] += hg
            stats[home.name]["GA"] += ag
            stats[away.name]["GF"] += ag
            stats[away.name]["GA"] += hg
            if hg > ag:
                stats[home.name]["W"] += 1
                stats[away.name]["L"] += 1
                stats[home.name]["PTS"] += 3
            elif ag > hg:
                stats[away.name]["W"] += 1
                stats[home.name]["L"] += 1
                stats[away.name]["PTS"] += 3
            else:
                stats[home.name]["D"] += 1
                stats[away.name]["D"] += 1
                stats[home.name]["PTS"] += 1
                stats[away.name]["PTS"] += 1
    for t in teams:
        stats[t.name]["GD"] = stats[t.name]["GF"] - stats[t.name]["GA"]
    return sorted(
        [(team_map[n], s) for n, s in stats.items()],
        key=lambda x: (x[1]["PTS"], x[1]["GD"], x[1]["GF"], -x[0].world_rank),
        reverse=True,
    )


def preliminary_round(confed_teams: List[Team], direct_count: int, seed_count: int, rng: random.Random) -> List[Team]:
    ordered = sorted(confed_teams, key=lambda x: x.world_rank)
    direct = ordered[:direct_count]
    rest = ordered[direct_count:]
    seeds = rest[:seed_count]
    others = rest[seed_count:]
    rng.shuffle(others)
    winners = []
    for i, s in enumerate(seeds):
        if i >= len(others):
            break
        winners.append(single_leg_winner(s, others[i], rng))
    return direct + winners


def assign_positions(table: List[Tuple[Team, Dict[str, int]]], mapping: Dict[str, List[Tuple[int, int]]]) -> Dict[str, List[Team]]:
    out = {"WC": [], "WC_PO": [], "WL": [], "WL_PO": [], "WA": [], "WA_PO": []}
    for key, ranges in mapping.items():
        for lo, hi in ranges:
            out[key].extend([table[i - 1][0] for i in range(lo, hi + 1) if 1 <= i <= len(table)])
    return out


def simulate_confed_qualifiers(all_teams: List[Team], rng: random.Random) -> Dict[str, List[Team]]:
    by_confed: Dict[str, List[Team]] = {c: [] for c in CONFEDS}
    for t in all_teams:
        by_confed[t.confed].append(t)
    slots = {"WC": [], "WC_PO": [], "WL": [], "WL_PO": [], "WA": [], "WA_PO": []}

    u2 = preliminary_round(by_confed["UEFA"], 41, 7, rng)
    ut = league_phase(u2, 6, 2, rng)
    us = assign_positions(ut, {"WC": [(1, 14)], "WC_PO": [(15, 18)], "WL": [(19, 26)], "WL_PO": [(27, 30)], "WA": [(31, 33)], "WA_PO": [(34, 37)]})

    a2 = preliminary_round(by_confed["AFC"], 25, 11, rng)
    at = league_phase(a2, 6, 2, rng)
    # 按合理修正：WA 15-21，WA_PO 22-26，避免21重复
    aa = assign_positions(at, {"WC": [(1, 4)], "WC_PO": [(5, 6)], "WL": [(7, 12)], "WL_PO": [(13, 14)], "WA": [(15, 21)], "WA_PO": [(22, 26)]})

    c2 = preliminary_round(by_confed["CONCACAF"], 19, 11, rng)
    ct = league_phase(c2, 6, 2, rng)
    cc = assign_positions(ct, {"WC": [(1, 3)], "WC_PO": [(4, 5)], "WL": [(6, 7)], "WL_PO": [(8, 9)], "WA": [(10, 10)], "WA_PO": [(11, 13)]})

    f2 = preliminary_round(by_confed["CAF"], 42, 6, rng)
    ft = league_phase(f2, 6, 2, rng)
    ff = assign_positions(ft, {"WC": [(1, 4)], "WC_PO": [(5, 6)], "WL": [(7, 12)], "WL_PO": [(13, 14)], "WA": [(15, 22)], "WA_PO": [(23, 28)]})

    o2 = preliminary_round(by_confed["OFC"], 11, 1, rng)
    ot = league_phase(o2, 4, 2, rng)
    oo = assign_positions(ot, {"WC_PO": [(1, 1)], "WL_PO": [(2, 2)], "WA": [(3, 3)], "WA_PO": [(4, 4)]})

    south = sorted(by_confed["CONMEBOL"], key=lambda x: x.world_rank)
    s = {t.name: {"PTS": 0, "GF": 0, "GA": 0, "GD": 0} for t in south}
    for i in range(len(south)):
        for j in range(i + 1, len(south)):
            a, b = south[i], south[j]
            a1, b1 = play_match(a, b, rng)
            b2, a2 = play_match(b, a, rng)
            for hg, ag, h, aw in [(a1, b1, a, b), (b2, a2, b, a)]:
                s[h.name]["GF"] += hg
                s[h.name]["GA"] += ag
                s[aw.name]["GF"] += ag
                s[aw.name]["GA"] += hg
                if hg > ag:
                    s[h.name]["PTS"] += 3
                elif ag > hg:
                    s[aw.name]["PTS"] += 3
                else:
                    s[h.name]["PTS"] += 1
                    s[aw.name]["PTS"] += 1
    for t in south:
        s[t.name]["GD"] = s[t.name]["GF"] - s[t.name]["GA"]
    sr = sorted(south, key=lambda x: (s[x.name]["PTS"], s[x.name]["GD"], s[x.name]["GF"], -x.world_rank), reverse=True)
    sm = {"WC": sr[0:5], "WC_PO": sr[5:6], "WL": sr[6:8], "WL_PO": sr[8:9], "WA": [], "WA_PO": sr[9:10]}

    for k in slots:
        slots[k].extend(us.get(k, []))
        slots[k].extend(aa.get(k, []))
        slots[k].extend(cc.get(k, []))
        slots[k].extend(ff.get(k, []))
        slots[k].extend(oo.get(k, []))
        slots[k].extend(sm.get(k, []))
    return slots


def confed_safe_pairing(teams: List[Team], rng: random.Random) -> List[Tuple[Team, Team]]:
    ordered = sorted(teams, key=lambda x: x.world_rank)
    half = len(ordered) // 2
    a = ordered[:half]
    b = ordered[half:]
    rng.shuffle(b)
    pairs = []
    used = set()
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


def intercontinental_playoff(teams: List[Team], rng: random.Random) -> Tuple[List[Team], List[Team]]:
    winners, losers = [], []
    for a, b in confed_safe_pairing(teams, rng):
        w = two_leg_winner(a, b, rng)
        winners.append(w)
        losers.append(b if w.name == a.name else a)
    return winners, losers


def cup_knockout_playoff(rank_teams: List[Team], rng: random.Random) -> List[Team]:
    wins = []
    for i in range(8):
        wins.append(two_leg_winner(rank_teams[8 + i], rank_teams[23 - i], rng))
    return wins


def cup_final_champion(teams36: List[Team], rng: random.Random) -> Team:
    table = league_phase(teams36, 4, 2, rng)
    ranked = [t for t, _ in table]
    top8 = ranked[:8]
    pows = cup_knockout_playoff(ranked, rng)
    r16_pairs = [
        (top8[0], pows[7]), (top8[1], pows[6]), (top8[2], pows[5]), (top8[3], pows[4]),
        (top8[4], pows[3]), (top8[5], pows[2]), (top8[6], pows[1]), (top8[7], pows[0]),
    ]
    r16 = [two_leg_winner(x, y, rng) for x, y in r16_pairs]
    qf = [two_leg_winner(r16[i], r16[i + 1], rng) for i in range(0, 8, 2)]
    sf = [two_leg_winner(qf[0], qf[1], rng), two_leg_winner(qf[2], qf[3], rng)]
    return single_leg_winner(sf[0], sf[1], rng)


def unique_fill_36(teams: List[Team], all_teams: List[Team]) -> List[Team]:
    d = {t.name: t for t in teams}
    if len(d) < 36:
        for t in sorted(all_teams, key=lambda x: x.world_rank):
            if t.name not in d:
                d[t.name] = t
            if len(d) == 36:
                break
    return sorted(d.values(), key=lambda x: x.world_rank)[:36]


def run(seed: int) -> Dict[str, object]:
    rng = random.Random(seed)
    all_teams = generate_teams()
    slots = simulate_confed_qualifiers(all_teams, rng)

    wc_w, wc_l = intercontinental_playoff(slots["WC_PO"], rng)
    wl_playoff_teams = slots["WL_PO"] + wc_l
    wl_w, wl_l = intercontinental_playoff(wl_playoff_teams, rng)
    wa_playoff_teams = slots["WA_PO"] + wl_l
    wa_w, wa_l = intercontinental_playoff(wa_playoff_teams, rng)

    wc_teams = unique_fill_36(slots["WC"] + wc_w, all_teams)
    wl_teams = unique_fill_36(slots["WL"] + wl_w, all_teams)
    wa_teams = unique_fill_36(slots["WA"] + wa_w, all_teams)

    return {
        "seed": seed,
        "champions": {
            "World Champions Cup": cup_final_champion(wc_teams, rng),
            "World League Cup": cup_final_champion(wl_teams, rng),
            "World Association Cup": cup_final_champion(wa_teams, rng),
        },
        "counts": {"WC": len(wc_teams), "WL": len(wl_teams), "WA": len(wa_teams)},
        "assoc_eliminated": [t.name for t in wa_l],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Custom World Cup simulator")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    for i in range(args.runs):
        seed = args.seed + i
        out = run(seed)
        print(f"=== Simulation #{i + 1} seed={seed} ===")
        for cup, champ in out["champions"].items():
            print(f"{cup} champion: {champ.name} ({champ.confed}, rank#{champ.world_rank})")
        print("Cup sizes:", out["counts"])
        print("Association playoff eliminated:", len(out["assoc_eliminated"]))
        print()


if __name__ == "__main__":
    main()
