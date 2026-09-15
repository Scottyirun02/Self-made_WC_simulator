"""四年周期比赛日表（2026–2030 为模板，后续周期整表年份 +4）。

连续比赛日间隔 ≥ 3 天；1 月冬窗不排赛。友谊赛仅 09-29、11-10、11-16。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence, Set, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[misc, assignment]


CYCLE_BASE_YEAR = 2026
N_MATCHDAYS = 68
QUAL_EARLY_SLOTS = 13
LEAGUE_LONG_ROUNDS = 18
LEAGUE_SPLIT_AFTER = 11  # 第 11 轮之后插入联合会杯

# (year_offset, month, day, kind, label) — year_offset 相对周期开局年
CYCLE_MATCHDAYS: List[Tuple[int, int, int, str, str]] = [
    (0, 9, 24, "qual", "洲际杯预选 第1轮"),
    (0, 9, 27, "qual", "洲际杯预选 第2轮"),
    (0, 9, 30, "qual", "洲际杯预选 第3轮"),
    (0, 10, 3, "qual", "洲际杯预选 第4轮"),
    (0, 11, 11, "qual", "洲际杯预选 第5轮"),
    (0, 11, 14, "qual", "洲际杯预选 第6轮"),
    (0, 11, 17, "qual", "洲际杯预选 第7轮"),
    (1, 3, 24, "qual", "洲际杯预选 第8轮"),
    (1, 3, 27, "qual", "洲际杯预选 第9轮"),
    (1, 3, 30, "qual", "洲际杯预选 第10轮"),
    (1, 6, 9, "qual", "洲际杯预选 第11轮"),
    (1, 6, 12, "qual", "洲际杯预选 第12轮"),
    (1, 6, 15, "qual", "洲际杯预选 第13轮"),
    (1, 6, 21, "wsc", "世界超级杯 附加赛第一轮"),
    (1, 6, 24, "wsc", "世界超级杯 附加赛第二轮"),
    (1, 6, 27, "wsc", "世界超级杯 四强"),
    (1, 7, 3, "wsc", "世界超级杯 决赛"),
    (1, 9, 23, "qual_last", "洲际杯预选 第14轮"),
    (1, 9, 29, "friendly", "国际友谊赛"),
    (1, 11, 10, "friendly", "国际友谊赛"),
    (1, 11, 16, "friendly", "国际友谊赛"),
    (2, 3, 23, "cont_po", "洲际杯预选附加 第一回合"),
    (2, 3, 26, "cont_po", "洲际杯预选附加 第二回合"),
    (2, 6, 10, "cont_gs", "洲际杯正赛小组 第1轮"),
    (2, 6, 13, "cont_gs", "洲际杯正赛小组 第2轮"),
    (2, 6, 16, "cont_gs", "洲际杯正赛小组 第3轮"),
    (2, 6, 19, "cont_ko", "洲际杯 1/8决赛"),
    (2, 6, 22, "cont_ko", "洲际杯 1/4决赛"),
    (2, 6, 25, "cont_ko", "洲际杯 半决赛"),
    (2, 6, 28, "cont_ko", "洲际杯 决赛"),
    (2, 9, 21, "prelim", "洲内附加赛 第1轮"),
    (2, 9, 24, "prelim", "洲内附加赛 第2轮"),
    (2, 9, 27, "prelim", "洲内附加赛 第3轮"),
    (2, 9, 30, "league", "联赛+挑战者杯 第1轮"),
    (2, 10, 3, "league", "联赛+挑战者杯 第2轮"),
    (2, 11, 14, "league", "联赛+挑战者杯 第3轮"),
    (2, 11, 17, "league", "联赛+挑战者杯 第4轮"),
    (2, 11, 20, "league", "联赛+挑战者杯 第5轮"),
    (3, 3, 21, "league", "联赛+挑战者杯 第6轮"),
    (3, 3, 24, "league", "联赛+挑战者杯 第7轮"),
    (3, 3, 27, "league", "联赛+挑战者杯 第8轮"),
    (3, 6, 6, "league", "联赛+挑战者杯 第9轮"),
    (3, 6, 9, "league", "联赛+挑战者杯 第10轮"),
    (3, 6, 12, "league", "联赛+挑战者杯 第11轮"),
    (3, 6, 21, "cc", "联合会杯小组 第1轮"),
    (3, 6, 24, "cc", "联合会杯小组 第2轮"),
    (3, 6, 27, "cc", "联合会杯小组 第3轮"),
    (3, 6, 30, "cc", "联合会杯 1/4决赛"),
    (3, 7, 3, "cc", "联合会杯 半决赛"),
    (3, 7, 6, "cc", "联合会杯 决赛"),
    (3, 9, 27, "league", "联赛+挑战者杯 第12轮"),
    (3, 9, 30, "league", "联赛+挑战者杯 第13轮"),
    (3, 10, 3, "league", "联赛+挑战者杯 第14轮"),
    (3, 10, 6, "league", "联赛+挑战者杯 第15轮"),
    (3, 11, 15, "league", "联赛+挑战者杯 第16轮"),
    (3, 11, 18, "league", "联赛+挑战者杯 第17轮"),
    (3, 11, 21, "league", "联赛+挑战者杯 第18轮"),
    (4, 3, 26, "ic_po", "洲际附加赛"),
    (4, 6, 13, "world", "三大杯小组 第1轮"),
    (4, 6, 16, "world", "三大杯小组 第2轮"),
    (4, 6, 19, "world", "三大杯小组 第3轮"),
    (4, 6, 22, "world", "三大杯小组 第4轮"),
    (4, 6, 25, "world", "三大杯小组 第5轮"),
    (4, 6, 28, "world", "24强附加赛"),
    (4, 7, 1, "world", "1/8决赛"),
    (4, 7, 4, "world", "1/4决赛"),
    (4, 7, 7, "world", "半决赛"),
    (4, 7, 10, "world", "决赛"),
]

# 预选出赛日（年偏移, 月, 日）；皆含末轮 09-23
QUAL_DATES_BY_ROUNDS: dict[int, List[Tuple[int, int, int]]] = {
    14: [
        (0, 9, 24), (0, 9, 27), (0, 9, 30), (0, 10, 3),
        (0, 11, 11), (0, 11, 14), (0, 11, 17),
        (1, 3, 24), (1, 3, 27), (1, 3, 30),
        (1, 6, 9), (1, 6, 12), (1, 6, 15),
        (1, 9, 23),
    ],
    12: [
        (0, 9, 24), (0, 9, 27), (0, 10, 3),
        (0, 11, 11), (0, 11, 14), (0, 11, 17),
        (1, 3, 24), (1, 3, 30),
        (1, 6, 9), (1, 6, 12), (1, 6, 15),
        (1, 9, 23),
    ],
    10: [
        (0, 9, 24), (0, 9, 27), (0, 10, 3),
        (0, 11, 11), (0, 11, 17),
        (1, 3, 24), (1, 3, 30),
        (1, 6, 9), (1, 6, 15),
        (1, 9, 23),
    ],
    8: [
        (0, 9, 24), (0, 10, 3), (0, 11, 11),
        (1, 3, 24), (1, 3, 30),
        (1, 6, 9), (1, 6, 15),
        (1, 9, 23),
    ],
}

# 联赛出赛日；皆含末轮 2029-11-21
LEAGUE_DATES_BY_ROUNDS: dict[int, List[Tuple[int, int, int]]] = {
    18: [
        (2, 9, 30), (2, 10, 3), (2, 11, 14), (2, 11, 17), (2, 11, 20),
        (3, 3, 21), (3, 3, 24), (3, 3, 27), (3, 6, 6), (3, 6, 9), (3, 6, 12),
        (3, 9, 27), (3, 9, 30), (3, 10, 3), (3, 10, 6), (3, 11, 15), (3, 11, 18), (3, 11, 21),
    ],
    12: [
        (2, 9, 30), (2, 11, 14), (2, 11, 20),
        (3, 3, 21), (3, 3, 27), (3, 6, 6), (3, 6, 12),
        (3, 9, 27), (3, 10, 6), (3, 11, 15), (3, 11, 18), (3, 11, 21),
    ],
    8: [
        (2, 9, 30), (2, 11, 17),
        (3, 3, 24), (3, 6, 12),
        (3, 9, 27), (3, 10, 6), (3, 11, 15), (3, 11, 21),
    ],
}

CONFED_WINDOW: dict[str, Tuple[str, List[str]]] = {
    "UEFA": ("Europe/Paris", ["18:00", "20:45"]),
    "CAF": ("Africa/Lagos", ["14:00", "17:00", "20:00"]),
    "AFC": ("Asia/Qatar", ["16:00", "19:30"]),
    "OFC": ("Pacific/Auckland", ["19:00"]),
    "CONMEBOL": ("America/Sao_Paulo", ["20:00", "21:30"]),
    "CONCACAF": ("America/Mexico_City", ["19:00", "21:00"]),
}

TOURNAMENT_TZ = "Europe/Berlin"
TOURNAMENT_GS_SLOTS = ["12:00", "15:00", "18:00", "21:00"]
TOURNAMENT_KO_SLOTS = ["18:00", "21:00"]
TOURNAMENT_FINAL_SLOT = "21:00"

_FALLBACK_OFFSETS = {
    "Europe/Paris": 2,
    "Europe/Berlin": 2,
    "Africa/Lagos": 1,
    "Asia/Qatar": 3,
    "Pacific/Auckland": 12,
    "America/Sao_Paulo": -3,
    "America/Mexico_City": -6,
}


@dataclass(frozen=True)
class MatchdayInfo:
    day_num: int
    date: date
    kind: str
    label: str


def cycle_start_year(cycle_index: int) -> int:
    return CYCLE_BASE_YEAR + (max(1, int(cycle_index or 1)) - 1) * 4


def cycle_label(start_year: int) -> str:
    return f"{start_year}–{start_year + 4}"


def _spec_to_date(start_year: int, spec: Tuple[int, int, int]) -> date:
    yo, month, day = spec
    return date(int(start_year) + int(yo), int(month), int(day))


def matchday_info(start_year: int, day_num: int) -> MatchdayInfo:
    n = int(day_num)
    if 1 <= n <= N_MATCHDAYS:
        yo, month, day, kind, label = CYCLE_MATCHDAYS[n - 1]
        return MatchdayInfo(n, date(int(start_year) + yo, month, day), kind, label)
    last = matchday_info(start_year, N_MATCHDAYS)
    extra = n - N_MATCHDAYS
    return MatchdayInfo(n, last.date + timedelta(days=3 * extra), "extra", "加赛日")


def matchday_date(start_year: int, day_num: int) -> date:
    return matchday_info(start_year, day_num).date


def dates_from_spec(start_year: int, specs: Sequence[Tuple[int, int, int]]) -> List[date]:
    return [_spec_to_date(start_year, s) for s in specs]


def qual_dates_for_rounds(n_rounds: int, start_year: int) -> List[date]:
    specs = QUAL_DATES_BY_ROUNDS.get(int(n_rounds))
    if specs is None:
        raise KeyError(f"无预选出赛日表: {n_rounds} 轮")
    return dates_from_spec(start_year, specs)


def league_dates_for_rounds(n_rounds: int, start_year: int) -> List[date]:
    specs = LEAGUE_DATES_BY_ROUNDS.get(int(n_rounds))
    if specs is None:
        raise KeyError(f"无联赛出赛日表: {n_rounds} 轮")
    return dates_from_spec(start_year, specs)


def format_kickoff(iso: str) -> str:
    if not iso:
        return ""
    raw = iso.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw[:16].replace("T", " ")


def _localize(d: date, hour: int, minute: int, tz_name: str) -> str:
    naive = datetime(d.year, d.month, d.day, hour, minute, 0)
    if ZoneInfo is not None:
        try:
            return naive.replace(tzinfo=ZoneInfo(tz_name)).isoformat()
        except Exception:
            pass
    off = _FALLBACK_OFFSETS.get(tz_name, 0)
    return naive.replace(tzinfo=timezone(timedelta(hours=off))).isoformat()


def _parse_hhmm(slot: str) -> Tuple[int, int]:
    hh, mm = slot.split(":")
    return int(hh), int(mm)


def _is_final_stage(stage: str) -> bool:
    st = stage or ""
    if st == "决赛":
        return True
    if not st.endswith("决赛"):
        return False
    if "半" in st or "1/" in st or "附加" in st:
        return False
    return True


def _is_tournament_match(m: Any) -> bool:
    c = getattr(m, "comp", "") or ""
    if c == "FRIENDLY":
        return False
    if c.startswith(("WCC", "WORLD-", "CC-", "WSC")):
        return True
    if any(c.startswith(p) for p in ("EURO", "AFCON", "APAC", "AMERICA")):
        if "-QUAL" in c or "-PO" in c:
            return False
        if "-GS-" in c or "-KO" in c:
            return True
    return bool(getattr(m, "neutral", False))


def _slots_for(m: Any) -> Tuple[str, List[str]]:
    if _is_tournament_match(m):
        if _is_final_stage(getattr(m, "stage", "") or ""):
            return TOURNAMENT_TZ, [TOURNAMENT_FINAL_SLOT]
        kind = getattr(m, "kind", "") or ""
        stage = getattr(m, "stage", "") or ""
        if kind == "knockout" or "-KO" in (getattr(m, "comp", "") or "") or any(
            x in stage for x in ("1/8", "1/4", "半决赛", "附加赛")
        ):
            return TOURNAMENT_TZ, list(TOURNAMENT_KO_SLOTS)
        return TOURNAMENT_TZ, list(TOURNAMENT_GS_SLOTS)
    home = getattr(m, "home", None)
    confed = getattr(home, "confed", "") if home is not None else ""
    tz_name, slots = CONFED_WINDOW.get(confed, ("Europe/Berlin", ["18:00", "21:00"]))
    return tz_name, list(slots)


def assign_kickoffs(matches: Sequence[Any], d: date) -> None:
    """按 (赛事, 阶段, 轮次, 主队) 排序后轮转各窗口开球时刻。"""
    if not matches:
        return
    ordered = sorted(
        matches,
        key=lambda m: (
            getattr(m, "comp", "") or "",
            getattr(m, "stage", "") or "",
            int(getattr(m, "round_num", 0) or 0),
            getattr(getattr(m, "home", None), "name", "") or "",
        ),
    )
    counters: dict[Tuple[str, Tuple[str, ...]], int] = {}
    for m in ordered:
        tz_name, slots = _slots_for(m)
        key = (tz_name, tuple(slots))
        i = counters.get(key, 0)
        hh, mm = _parse_hhmm(slots[i % len(slots)])
        counters[key] = i + 1
        m.kickoff = _localize(d, hh, mm, tz_name)


def draw_friendly_pairs(
    teams: Sequence[Any],
    rng: Any,
    recent_pairs: Optional[Set[frozenset]] = None,
) -> List[Tuple[Any, Any]]:
    """互相邀请：约 3/4 出场，优先异洲，主场=发起方，尽量不重复同一对。"""
    recent = recent_pairs if recent_pairs is not None else set()
    pool = list(teams)
    rng.shuffle(pool)
    n_play = int(round(len(pool) * 0.75))
    if n_play % 2:
        n_play -= 1
    n_play = max(0, n_play)
    chosen = pool[:n_play]
    remaining = {t.name: t for t in chosen}
    order = list(chosen)
    pairs: List[Tuple[Any, Any]] = []

    def _pick_opp(inviter: Any, cands: List[Any]) -> Optional[Any]:
        if not cands:
            return None

        def fresh(xs: List[Any]) -> List[Any]:
            return [t for t in xs if frozenset({inviter.name, t.name}) not in recent]

        if rng.random() < 0.6:
            other = [t for t in cands if getattr(t, "confed", "") != getattr(inviter, "confed", "")]
            pick_from = fresh(other) or other
            if pick_from:
                return rng.choice(pick_from)
        pick_from = fresh(cands) or cands
        return rng.choice(pick_from) if pick_from else None

    for inviter in order:
        if inviter.name not in remaining:
            continue
        del remaining[inviter.name]
        cands = list(remaining.values())
        rng.shuffle(cands)
        opp = _pick_opp(inviter, cands)
        if opp is None:
            remaining[inviter.name] = inviter
            continue
        remaining.pop(opp.name, None)
        pairs.append((inviter, opp))
    return pairs


def validate_calendar() -> None:
    if len(CYCLE_MATCHDAYS) != N_MATCHDAYS:
        raise RuntimeError(f"CYCLE_MATCHDAYS 应为 {N_MATCHDAYS} 行，实际 {len(CYCLE_MATCHDAYS)}")
    prev: Optional[date] = None
    for i, spec in enumerate(CYCLE_MATCHDAYS, start=1):
        yo, month, day, _kind, _label = spec
        if month == 1:
            raise RuntimeError(f"比赛日 {i} 落在 1 月")
        d = date(CYCLE_BASE_YEAR + yo, month, day)
        if prev is not None:
            gap = (d - prev).days
            if gap < 3:
                raise RuntimeError(f"比赛日 {i-1}→{i} 间隔 {gap} 天，应 ≥ 3")
        prev = d
    friendly_md = {(s[1], s[2]) for s in CYCLE_MATCHDAYS if s[3] == "friendly"}
    if friendly_md != {(9, 29), (11, 10), (11, 16)}:
        raise RuntimeError(f"友谊赛日期应为 09-29/11-10/11-16，实际 {friendly_md}")
    for n, specs in QUAL_DATES_BY_ROUNDS.items():
        if len(specs) != n:
            raise RuntimeError(f"QUAL {n} 轮日期数 {len(specs)}")
        if specs[-1] != (1, 9, 23):
            raise RuntimeError(f"QUAL {n} 轮末日期应为 09-23")
    for n, specs in LEAGUE_DATES_BY_ROUNDS.items():
        if len(specs) != n:
            raise RuntimeError(f"LEAGUE {n} 轮日期数 {len(specs)}")
        if specs[-1] != (3, 11, 21):
            raise RuntimeError(f"LEAGUE {n} 轮末日期应为 11-21")


validate_calendar()
