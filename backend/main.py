from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from datetime import date, timedelta, datetime
import pandas as pd
import numpy as np
from pybaseball import statcast_batter, statcast_pitcher
import requests
import warnings
import pybaseball
import os
import base64
import json as json_lib
import threading
import time
warnings.filterwarnings("ignore")

pybaseball.cache.enable()

app = FastAPI(title="Bomb Parlays Lab API v4")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ── IN-MEMORY DAILY CACHE ─────────────────────────────────────────────────────
# All cache entries are keyed by date string "YYYY-MM-DD"
# They reset automatically when the date changes
_cache = {
    "targets":      {},   # date -> {players, cached_at, total_players}
    "matchups":     {},   # "date:game_id" -> {home, away, game, park_info, cached_at}
    "park_data":    {},   # date -> {park_map, uploaded_at}
    "batter_stats": {},   # "date:player_id" -> stats dict
    "pitcher_stats":{},   # "date:player_id" -> stats dict
    "build_status": {},   # date -> {status, started_at, progress, total}
}
_cache_lock = threading.Lock()

def today_key():
    return str(date.today())

def cache_get(bucket: str, key: str):
    with _cache_lock:
        entry = _cache[bucket].get(key)
        if not entry:
            return None
        # Auto-expire if the date embedded in key no longer matches today
        if key.startswith(today_key()) or key == today_key():
            return entry
        return None

def cache_set(bucket: str, key: str, value):
    with _cache_lock:
        _cache[bucket][key] = value

def cache_clear_old():
    """Remove any entries from previous days."""
    tk = today_key()
    with _cache_lock:
        for bucket in _cache:
            stale = [k for k in _cache[bucket] if not k.startswith(tk) and k != tk]
            for k in stale:
                del _cache[bucket][k]

# ── PARK FACTORS ──────────────────────────────────────────────────────────────
PARK_FACTORS = {
    "Rockies":      (140, "Coors Field",               "great"),
    "Reds":         (122, "Great American Ball Park",   "great"),
    "Yankees":      (118, "Yankee Stadium",             "great"),
    "Phillies":     (116, "Citizens Bank Park",         "great"),
    "Red Sox":      (112, "Fenway Park",                "good"),
    "Cubs":         (110, "Wrigley Field",              "good"),
    "Brewers":      (108, "American Family Field",      "good"),
    "Braves":       (107, "Truist Park",                "good"),
    "Rangers":      (106, "Globe Life Field",           "good"),
    "Blue Jays":    (105, "Rogers Centre",              "good"),
    "Astros":       (104, "Minute Maid Park",           "good"),
    "Orioles":      (103, "Camden Yards",               "good"),
    "Twins":        (102, "Target Field",               "neutral"),
    "Cardinals":    (101, "Busch Stadium",              "neutral"),
    "Angels":       (100, "Angel Stadium",              "neutral"),
    "Tigers":       (99,  "Comerica Park",              "neutral"),
    "White Sox":    (98,  "Guaranteed Rate Field",      "neutral"),
    "Royals":       (97,  "Kauffman Stadium",           "neutral"),
    "Guardians":    (97,  "Progressive Field",          "neutral"),
    "Indians":      (97,  "Progressive Field",          "neutral"),
    "Nationals":    (96,  "Nationals Park",             "neutral"),
    "Mets":         (96,  "Citi Field",                 "neutral"),
    "Athletics":    (95,  "Oakland Coliseum",           "bad"),
    "Diamondbacks": (95,  "Chase Field",                "bad"),
    "Pirates":      (95,  "PNC Park",                   "bad"),
    "Mariners":     (94,  "T-Mobile Park",              "bad"),
    "Rays":         (93,  "Tropicana Field",            "bad"),
    "Padres":       (92,  "Petco Park",                 "bad"),
    "Marlins":      (91,  "loanDepot park",             "pitcher"),
    "Dodgers":      (90,  "Dodger Stadium",             "bad"),
    "Giants":       (88,  "Oracle Park",                "pitcher"),
}

def get_park_info(home_team: str) -> dict:
    for key, val in PARK_FACTORS.items():
        if key.lower() in home_team.lower():
            factor, park_name, tier = val
            return {"factor": factor, "park_name": park_name, "tier": tier}
    return {"factor": 100, "park_name": home_team, "tier": "neutral"}

def park_score_modifier(park_factor: int) -> float:
    if park_factor >= 130: return 12.0
    if park_factor >= 115: return 8.0
    if park_factor >= 108: return 5.0
    if park_factor >= 103: return 2.0
    if park_factor >= 97:  return 0.0
    if park_factor >= 92:  return -3.0
    if park_factor >= 88:  return -6.0
    return -10.0

# ── PITCHER TIER ──────────────────────────────────────────────────────────────
def classify_pitcher(pitcher_stats: dict) -> dict:
    if not pitcher_stats:
        return {"tier": "average", "label": "Average", "icon": "", "modifier": 0}
    xa  = pitcher_stats.get("xwoba_against", 0.320)
    sw  = pitcher_stats.get("swstr_pct_pitcher", 10)
    brl = pitcher_stats.get("brl_bip_allowed", 8)
    quality = (0.320 - xa) * 200 + (sw - 10) * 2 - (brl - 8) * 1.5
    if quality > 15:  return {"tier": "elite",     "label": "Elite Pitcher",     "icon": "⚠",  "modifier": -11}
    if quality > 6:   return {"tier": "above_avg", "label": "Above Avg Pitcher", "icon": "",   "modifier": -4}
    if quality > -6:  return {"tier": "average",   "label": "Average Pitcher",   "icon": "",   "modifier": 0}
    if quality > -15: return {"tier": "below_avg", "label": "Below Avg Pitcher", "icon": "",   "modifier": +4}
    return              {"tier": "weak",      "label": "Weak Pitcher",      "icon": "🔥", "modifier": +11}

# ── HELPERS ───────────────────────────────────────────────────────────────────
def safe_float(val, default=0.0):
    try:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return default
        return float(val)
    except Exception:
        return default

def get_date_range(days_back=60):
    end   = date.today()
    start = end - timedelta(days=days_back)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

def fetch_batter_stats(player_id: int, start_dt: str, end_dt: str) -> dict:
    cache_key = f"{today_key()}:{player_id}"
    cached = cache_get("batter_stats", cache_key)
    if cached:
        return cached

    try:
        df = statcast_batter(start_dt, end_dt, player_id)
        if df is None or df.empty:
            return {}
        batted = df[df["type"] == "X"].copy()
        total_pitches = len(df)
        swstr = len(df[df["description"] == "swinging_strike"]) / total_pitches if total_pitches else 0
        bip = len(batted)
        barrels   = batted["launch_speed_angle"].isin([6]) if "launch_speed_angle" in batted.columns else pd.Series(dtype=bool)
        brl_count = int(barrels.sum()) if len(barrels) else 0
        brl_bip   = brl_count / bip if bip else 0
        sweet_spot = batted[(batted["launch_angle"] >= 8) & (batted["launch_angle"] <= 32)] if "launch_angle" in batted.columns else pd.DataFrame()
        sweet_pct  = len(sweet_spot) / bip if bip else 0
        fb     = batted[batted["bb_type"] == "fly_ball"] if "bb_type" in batted.columns else pd.DataFrame()
        fb_pct = len(fb) / bip if bip else 0
        hh     = batted[batted["launch_speed"] >= 95] if "launch_speed" in batted.columns else pd.DataFrame()
        hh_pct = len(hh) / bip if bip else 0
        avg_la = float(batted["launch_angle"].mean()) if "launch_angle" in batted.columns and bip else 0
        pulled = batted[
            ((batted["stand"] == "R") & (batted["hc_x"] < 125)) |
            ((batted["stand"] == "L") & (batted["hc_x"] > 125))
        ] if "hc_x" in batted.columns and "stand" in batted.columns else pd.DataFrame()
        pulled_brl     = pulled[pulled["launch_speed_angle"].isin([6])] if "launch_speed_angle" in pulled.columns and len(pulled) else pd.DataFrame()
        pulled_brl_pct = len(pulled_brl) / bip if bip else 0
        xwoba     = safe_float(df["estimated_woba_using_speedangle"].mean()) if "estimated_woba_using_speedangle" in df.columns else 0
        xwoba_con = safe_float(batted["estimated_woba_using_speedangle"].mean()) if "estimated_woba_using_speedangle" in batted.columns and bip else 0
        ab = df[df["events"].notna() & df["events"].isin([
            "single","double","triple","home_run","strikeout","field_out",
            "grounded_into_double_play","force_out","fielders_choice","sac_fly"
        ])]
        doubles_ = int((ab["events"] == "double").sum())
        triples  = int((ab["events"] == "triple").sum())
        hrs      = int((ab["events"] == "home_run").sum())
        ab_count = len(ab)
        iso = (doubles_ + 2*triples + 3*hrs) / ab_count if ab_count else 0
        stand = df["stand"].mode()[0] if "stand" in df.columns and not df["stand"].empty else "R"
        df2 = df.copy()
        df2["game_date"] = pd.to_datetime(df2["game_date"])
        game_hrs = df2.sort_values("game_date").groupby("game_date").apply(
            lambda g: int((g["events"] == "home_run").sum())).reset_index()
        game_hrs.columns = ["game_date", "hrs"]
        last15 = game_hrs.tail(15)["hrs"].tolist()
        if len(last15) >= 6:
            mid = len(last15) // 2
            hr_form = "↑" if sum(last15[mid:]) > sum(last15[:mid]) else ("↓" if sum(last15[mid:]) < sum(last15[:mid]) else "→")
        else:
            hr_form = "→"
        khr = hrs / ab_count if ab_count else 0
        result = {
            "iso": round(iso, 3), "xwoba": round(xwoba, 3), "xwoba_con": round(xwoba_con, 3),
            "swstr_pct": round(swstr * 100, 1), "brl_bip_pct": round(brl_bip * 100, 1),
            "pulled_brl_pct": round(pulled_brl_pct * 100, 1), "sweet_spot_pct": round(sweet_pct * 100, 1),
            "fb_pct": round(fb_pct * 100, 1), "hh_pct": round(hh_pct * 100, 1),
            "avg_la": round(avg_la, 1), "hr_form": hr_form, "khr": round(khr * 100, 2),
            "total_pitches": total_pitches, "bip": bip, "hrs": hrs, "ab": ab_count, "stand": stand,
        }
        cache_set("batter_stats", cache_key, result)
        return result
    except Exception as e:
        print(f"Error fetching batter {player_id}: {e}")
        return {}

def fetch_pitcher_stats(player_id: int, start_dt: str, end_dt: str) -> dict:
    cache_key = f"{today_key()}:{player_id}"
    cached = cache_get("pitcher_stats", cache_key)
    if cached:
        return cached

    try:
        df = statcast_pitcher(start_dt, end_dt, player_id)
        if df is None or df.empty:
            return {}
        total   = len(df)
        fb_types = ["FF","SI","FC","FT"]
        fb_df   = df[df["pitch_type"].isin(fb_types)] if "pitch_type" in df.columns else pd.DataFrame()
        fb_rate = len(fb_df) / total if total else 0
        batted  = df[df["type"] == "X"]
        bip     = len(batted)
        brl_allowed    = int(batted["launch_speed_angle"].isin([6]).sum()) if "launch_speed_angle" in batted.columns and bip else 0
        brl_bip_allowed = brl_allowed / bip if bip else 0
        xwoba_against = safe_float(df["estimated_woba_using_speedangle"].mean() if "estimated_woba_using_speedangle" in df.columns else 0)
        swstr = len(df[df["description"] == "swinging_strike"]) / total if total else 0
        p_throws = df["p_throws"].mode()[0] if "p_throws" in df.columns and not df["p_throws"].empty else "R"
        high_velo = fb_df[fb_df["release_speed"] >= 95] if "release_speed" in fb_df.columns and len(fb_df) else pd.DataFrame()
        power_pitcher = (len(high_velo) / len(fb_df) > 0.5) if len(fb_df) > 0 else False
        pitcher_type  = "Power" if (power_pitcher or swstr > 0.12) else "Contact"
        result = {
            "fb_rate": round(fb_rate * 100, 1), "brl_bip_allowed": round(brl_bip_allowed * 100, 1),
            "xwoba_against": round(xwoba_against, 3), "swstr_pct_pitcher": round(swstr * 100, 1),
            "total_pitches": total, "bip_allowed": bip, "p_throws": p_throws, "pitcher_type": pitcher_type,
        }
        cache_set("pitcher_stats", cache_key, result)
        return result
    except Exception as e:
        print(f"Error fetching pitcher {player_id}: {e}")
        return {}

def get_handedness_split(batter_id: int, pitcher_hand: str, start_dt: str, end_dt: str) -> dict:
    try:
        df = statcast_batter(start_dt, end_dt, batter_id)
        if df is None or df.empty or "p_throws" not in df.columns:
            return {"split_xwoba": 0, "split_iso": 0, "split_label": "", "split_modifier": 0}
        hand     = pitcher_hand.upper() if pitcher_hand else "R"
        split_df = df[df["p_throws"] == hand]
        if split_df.empty:
            return {"split_xwoba": 0, "split_iso": 0, "split_label": f"vs {'LHP' if hand=='L' else 'RHP'}", "split_modifier": 0}
        split_xwoba   = safe_float(split_df["estimated_woba_using_speedangle"].mean() if "estimated_woba_using_speedangle" in split_df.columns else 0)
        overall_xwoba = safe_float(df["estimated_woba_using_speedangle"].mean() if "estimated_woba_using_speedangle" in df.columns else 0.320)
        ab_split = split_df[split_df["events"].notna() & split_df["events"].isin([
            "single","double","triple","home_run","strikeout","field_out",
            "grounded_into_double_play","force_out","fielders_choice","sac_fly"
        ])]
        ab_count = len(ab_split)
        if ab_count > 0:
            d = int((ab_split["events"] == "double").sum())
            t = int((ab_split["events"] == "triple").sum())
            h = int((ab_split["events"] == "home_run").sum())
            split_iso = round((d + 2*t + 3*h) / ab_count, 3)
        else:
            split_iso = 0
        delta = split_xwoba - overall_xwoba
        if delta > 0.040:    modifier = 5
        elif delta > 0.020:  modifier = 3
        elif delta > 0.000:  modifier = 1
        elif delta > -0.020: modifier = -1
        elif delta > -0.040: modifier = -3
        else:                modifier = -5
        label = f"vs {'LHP' if hand=='L' else 'RHP'}"
        return {"split_xwoba": round(split_xwoba, 3), "split_iso": split_iso,
                "split_label": label, "split_modifier": modifier}
    except Exception as e:
        print(f"Split error for {batter_id}: {e}")
        return {"split_xwoba": 0, "split_iso": 0, "split_label": "", "split_modifier": 0}

def compute_zone_fit(batter_stats: dict, pitcher_stats: dict) -> float:
    score   = 50.0
    fb_rate = pitcher_stats.get("fb_rate", 0)
    brl     = batter_stats.get("brl_bip_pct", 0)
    if fb_rate > 55 and brl > 10: score += 15
    elif fb_rate > 45 and brl > 7: score += 8
    if batter_stats.get("sweet_spot_pct", 0) > 35: score += 5
    xa = pitcher_stats.get("xwoba_against", 0.320)
    if xa > 0.380:   score += 10
    elif xa > 0.340: score += 5
    elif xa < 0.280: score -= 10
    if batter_stats.get("pulled_brl_pct", 0) > 5: score += 5
    return round(min(max(score, 0), 100), 1)

def compute_hr_score(batter: dict, pitcher: dict, zone_fit: float,
                     pitcher_tier: dict, park_factor: int, split_modifier: float) -> float:
    weights = {"brl_bip_pct": 0.25, "xwoba": 0.18, "fb_pct": 0.15,
               "sweet_spot_pct": 0.12, "pulled_brl_pct": 0.10, "hh_pct": 0.08, "iso": 0.12}
    norms = {
        "brl_bip_pct":    min(batter.get("brl_bip_pct", 0)    / 20    * 100, 100),
        "xwoba":          min(batter.get("xwoba", 0)           / 0.500 * 100, 100),
        "fb_pct":         min(batter.get("fb_pct", 0)          / 60    * 100, 100),
        "sweet_spot_pct": min(batter.get("sweet_spot_pct", 0)  / 50    * 100, 100),
        "pulled_brl_pct": min(batter.get("pulled_brl_pct", 0)  / 10    * 100, 100),
        "hh_pct":         min(batter.get("hh_pct", 0)          / 60    * 100, 100),
        "iso":            min(batter.get("iso", 0)              / 0.350 * 100, 100),
    }
    base  = sum(weights[k] * norms[k] for k in weights)
    total = base + (zone_fit / 100) * 12 + park_score_modifier(park_factor) + pitcher_tier.get("modifier", 0) + split_modifier
    return round(min(max(total, 0), 100), 1)

def compute_ceiling(hr_score: float, batter: dict) -> float:
    peak = (batter.get("brl_bip_pct", 0) / 20 * 100 * 0.4 +
            batter.get("pulled_brl_pct", 0) / 10 * 100 * 0.3 +
            batter.get("hh_pct", 0) / 60 * 100 * 0.3)
    return round((hr_score * 0.6 + peak * 0.4), 1)

# ── MLB STATS API ─────────────────────────────────────────────────────────────
def get_todays_games(game_date: str = None) -> list:
    if not game_date:
        game_date = date.today().strftime("%Y-%m-%d")
    url  = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={game_date}&hydrate=lineups,probablePitcher"
    r    = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    games = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            hp   = home.get("probablePitcher", {})
            ap   = away.get("probablePitcher", {})
            games.append({
                "game_id":           game["gamePk"],
                "status":            game.get("status", {}).get("abstractGameState", ""),
                "home_team":         home["team"]["name"],
                "away_team":         away["team"]["name"],
                "home_team_id":      home["team"]["id"],
                "away_team_id":      away["team"]["id"],
                "home_pitcher_id":   hp.get("id"),
                "home_pitcher_name": hp.get("fullName", "TBD"),
                "home_pitcher_hand": hp.get("pitchHand", {}).get("code", "R") if isinstance(hp.get("pitchHand"), dict) else "R",
                "away_pitcher_id":   ap.get("id"),
                "away_pitcher_name": ap.get("fullName", "TBD"),
                "away_pitcher_hand": ap.get("pitchHand", {}).get("code", "R") if isinstance(ap.get("pitchHand"), dict) else "R",
                "venue":             game.get("venue", {}).get("name", ""),
                "game_time":         game.get("gameDate", ""),
            })
    return games

def get_lineup(game_id: int, team_id: int) -> list:
    url = f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore"
    try:
        r    = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        side = "home" if data["teams"]["home"]["team"]["id"] == team_id else "away"
        batters = data["teams"][side].get("battingOrder", [])
        players = data["teams"][side].get("players", {})
        return [{"name": players.get(f"ID{pid}", {}).get("person", {}).get("fullName", "Unknown"),
                 "mlb_id": players.get(f"ID{pid}", {}).get("person", {}).get("id")}
                for pid in batters]
    except Exception:
        return []

def search_player_id(name: str):
    url = f"https://statsapi.mlb.com/api/v1/people/search?names={requests.utils.quote(name)}&sportId=1"
    try:
        r = requests.get(url, timeout=10)
        ppl = r.json().get("people", [])
        return ppl[0]["id"] if ppl else None
    except Exception:
        return None

def build_player_row(batter, bstats, pitcher_stats, pitcher_name, pitcher_tier,
                     team_name, side, park_info, split_data, game_label) -> dict:
    zone_fit = compute_zone_fit(bstats, pitcher_stats)
    hr_score = compute_hr_score(bstats, pitcher_stats, zone_fit, pitcher_tier,
                                park_info["factor"], split_data.get("split_modifier", 0))
    ceiling  = compute_ceiling(hr_score, bstats)
    return {
        "player": batter["name"], "team": team_name, "side": side, "game": game_label,
        "opposing_pitcher": pitcher_name, "pitcher_tier": pitcher_tier["tier"],
        "pitcher_label": pitcher_tier["label"], "pitcher_icon": pitcher_tier["icon"],
        "pitcher_type": pitcher_stats.get("pitcher_type", ""), "p_throws": pitcher_stats.get("p_throws", "R"),
        "park_name": park_info["park_name"], "park_factor": park_info["factor"], "park_tier": park_info["tier"],
        "split_label": split_data.get("split_label", ""), "split_xwoba": split_data.get("split_xwoba", 0),
        "split_iso": split_data.get("split_iso", 0), "split_modifier": split_data.get("split_modifier", 0),
        "hr_score": hr_score, "ceiling": ceiling, "zone_fit": zone_fit,
        "hr_form": bstats.get("hr_form", "→"), "khr": bstats.get("khr", 0),
        "pitches": bstats.get("total_pitches", 0), "bip": bstats.get("bip", 0),
        "iso": bstats.get("iso", 0), "xwoba": bstats.get("xwoba", 0), "xwoba_con": bstats.get("xwoba_con", 0),
        "swstr_pct": bstats.get("swstr_pct", 0), "pulled_brl_pct": bstats.get("pulled_brl_pct", 0),
        "brl_bip_pct": bstats.get("brl_bip_pct", 0), "sweet_spot_pct": bstats.get("sweet_spot_pct", 0),
        "fb_pct": bstats.get("fb_pct", 0), "hh_pct": bstats.get("hh_pct", 0),
        "avg_la": bstats.get("avg_la", 0), "hrs": bstats.get("hrs", 0),
    }

def _build_all_players(game_date: str = None) -> list:
    """Core logic to pull all players — used by both top-targets and background builder."""
    games    = get_todays_games(game_date)
    start_dt, end_dt = get_date_range(60)
    all_players = []
    tk = today_key()

    # Update build status
    total_batters = 0
    for game in games:
        for side in ["home", "away"]:
            team_id = game[f"{side}_team_id"]
            lineup  = get_lineup(game["game_id"], team_id)
            total_batters += len(lineup)

    cache_set("build_status", tk, {
        "status": "building", "started_at": datetime.now().isoformat(),
        "progress": 0, "total": total_batters
    })

    processed = 0
    for game in games:
        park_info  = get_park_info(game["home_team"])
        game_label = f"{game['away_team']} @ {game['home_team']}"
        sides = [
            ("away", game["away_team_id"], game["away_team"],
             game["home_pitcher_id"], game["home_pitcher_name"], game["home_pitcher_hand"]),
            ("home", game["home_team_id"], game["home_team"],
             game["away_pitcher_id"], game["away_pitcher_name"], game["away_pitcher_hand"]),
        ]
        for side_name, team_id, team_name, opp_pid, opp_pname, opp_phand in sides:
            lineup        = get_lineup(game["game_id"], team_id)
            pitcher_stats = fetch_pitcher_stats(opp_pid, start_dt, end_dt) if opp_pid else {}
            pitcher_tier  = classify_pitcher(pitcher_stats)
            for batter in lineup:
                if not batter["mlb_id"]:
                    continue
                bstats = fetch_batter_stats(batter["mlb_id"], start_dt, end_dt)
                if not bstats:
                    continue
                split_data = get_handedness_split(batter["mlb_id"], opp_phand, start_dt, end_dt)
                row = build_player_row(batter, bstats, pitcher_stats, opp_pname, pitcher_tier,
                                       team_name, side_name, park_info, split_data, game_label)
                all_players.append(row)
                processed += 1
                cache_set("build_status", tk, {
                    "status": "building", "started_at": _cache["build_status"].get(tk, {}).get("started_at", ""),
                    "progress": processed, "total": total_batters
                })

    all_players.sort(key=lambda x: x["hr_score"], reverse=True)

    # Store in cache
    cache_set("targets", tk, {
        "players":       all_players,
        "cached_at":     datetime.now().isoformat(),
        "cached_at_fmt": datetime.now().strftime("%-I:%M %p"),
        "total_players": len(all_players),
    })
    cache_set("build_status", tk, {
        "status": "complete", "progress": processed, "total": total_batters,
        "cached_at": datetime.now().isoformat(),
        "cached_at_fmt": datetime.now().strftime("%-I:%M %p"),
    })
    return all_players

def _background_build(game_date: str = None):
    """Run the full build in a background thread."""
    try:
        _build_all_players(game_date)
    except Exception as e:
        tk = today_key()
        cache_set("build_status", tk, {"status": "error", "error": str(e)})

# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    cache_clear_old()
    tk = today_key()
    targets_cached = cache_get("targets", tk) is not None
    park_cached    = cache_get("park_data", tk) is not None
    build_status   = cache_get("build_status", tk) or {}
    return {
        "status":         "ok",
        "date":           tk,
        "targets_cached": targets_cached,
        "park_cached":    park_cached,
        "build_status":   build_status,
    }

@app.get("/cache-status")
def cache_status():
    """Frontend polls this to show cache state and last-updated time."""
    tk = today_key()
    targets = cache_get("targets", tk)
    park    = cache_get("park_data", tk)
    build   = cache_get("build_status", tk) or {}
    return {
        "targets_ready":    targets is not None,
        "targets_cached_at": targets.get("cached_at_fmt", "") if targets else "",
        "total_players":    targets.get("total_players", 0) if targets else 0,
        "park_ready":       park is not None,
        "park_uploaded_at": park.get("uploaded_at_fmt", "") if park else "",
        "build_status":     build.get("status", "idle"),
        "build_progress":   build.get("progress", 0),
        "build_total":      build.get("total", 0),
    }

@app.get("/slate")
def get_slate(game_date: str = Query(default=None)):
    try:
        games = get_todays_games(game_date)
        for g in games:
            pi = get_park_info(g["home_team"])
            g["park_name"]   = pi["park_name"]
            g["park_factor"] = pi["factor"]
            g["park_tier"]   = pi["tier"]
        return {"games": games, "date": game_date or today_key()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/top-targets")
def get_top_targets(game_date: str = Query(default=None), limit: int = 15, force: bool = False):
    """
    Returns top HR targets. Uses cache if available.
    Pass force=true to rebuild from scratch.
    If cache is empty, starts a background build and returns status immediately.
    """
    tk = today_key()
    cache_clear_old()

    # Check if already cached and not forcing
    if not force:
        cached = cache_get("targets", tk)
        if cached:
            park = cache_get("park_data", tk)
            return {
                "targets":       cached["players"][:limit],
                "total_players": cached["total_players"],
                "cached_at":     cached.get("cached_at_fmt", ""),
                "from_cache":    True,
                "park_active":   park is not None,
            }

    # Check if a build is already running
    build = cache_get("build_status", tk) or {}
    if build.get("status") == "building":
        return {
            "targets":       [],
            "total_players": 0,
            "from_cache":    False,
            "building":      True,
            "build_progress": build.get("progress", 0),
            "build_total":    build.get("total", 0),
            "message":       f"Building: {build.get('progress',0)} / {build.get('total',0)} players processed...",
        }

    # Start background build
    cache_set("build_status", tk, {"status": "building", "progress": 0, "total": 0,
                                    "started_at": datetime.now().isoformat()})
    t = threading.Thread(target=_background_build, args=(game_date,), daemon=True)
    t.start()

    return {
        "targets":       [],
        "total_players": 0,
        "from_cache":    False,
        "building":      True,
        "build_progress": 0,
        "build_total":    0,
        "message":       "Build started — data will be ready in 4-6 minutes. Page will auto-refresh.",
    }

@app.get("/matchups")
def get_matchups(game_id: int, game_date: str = Query(default=None), force: bool = False):
    try:
        tk        = today_key()
        cache_key = f"{tk}:{game_id}"

        if not force:
            cached = cache_get("matchups", cache_key)
            if cached:
                return {**cached, "from_cache": True}

        games = get_todays_games(game_date)
        game  = next((g for g in games if g["game_id"] == game_id), None)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")

        start_dt, end_dt = get_date_range(60)
        park_info  = get_park_info(game["home_team"])
        game_label = f"{game['away_team']} @ {game['home_team']}"
        home_results, away_results = [], []

        sides = [
            ("away", game["away_team_id"], game["away_team"],
             game["home_pitcher_id"], game["home_pitcher_name"], game["home_pitcher_hand"]),
            ("home", game["home_team_id"], game["home_team"],
             game["away_pitcher_id"], game["away_pitcher_name"], game["away_pitcher_hand"]),
        ]
        for side_name, team_id, team_name, opp_pid, opp_pname, opp_phand in sides:
            lineup        = get_lineup(game_id, team_id)
            pitcher_stats = fetch_pitcher_stats(opp_pid, start_dt, end_dt) if opp_pid else {}
            pitcher_tier  = classify_pitcher(pitcher_stats)
            for batter in lineup:
                if not batter["mlb_id"]: continue
                bstats = fetch_batter_stats(batter["mlb_id"], start_dt, end_dt)
                if not bstats: continue
                split_data = get_handedness_split(batter["mlb_id"], opp_phand, start_dt, end_dt)
                row = build_player_row(batter, bstats, pitcher_stats, opp_pname, pitcher_tier,
                                       team_name, side_name, park_info, split_data, game_label)
                (home_results if side_name == "home" else away_results).append(row)

        home_results.sort(key=lambda x: x["hr_score"], reverse=True)
        away_results.sort(key=lambda x: x["hr_score"], reverse=True)

        result = {
            "game": game, "park_info": park_info,
            "home": home_results, "away": away_results,
            "cached_at": datetime.now().strftime("%-I:%M %p"),
        }
        cache_set("matchups", cache_key, result)
        return {**result, "from_cache": False}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/player")
def get_player(name: str):
    try:
        mlb_id = search_player_id(name)
        if not mlb_id:
            raise HTTPException(status_code=404, detail=f"Player '{name}' not found")
        start_dt, end_dt = get_date_range(60)
        stats = fetch_batter_stats(mlb_id, start_dt, end_dt)
        if not stats:
            raise HTTPException(status_code=404, detail="No Statcast data found")
        return {"name": name, "mlb_id": mlb_id, "stats": stats}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/park-data")
def store_park_data(data: dict):
    """Store Ballpark Pal park data for the day — uploaded once, shared all day."""
    tk = today_key()
    cache_set("park_data", tk, {
        "park_map":       data.get("park_map", {}),
        "uploaded_at":    datetime.now().isoformat(),
        "uploaded_at_fmt": datetime.now().strftime("%-I:%M %p"),
    })
    return {"status": "stored", "parks": len(data.get("park_map", {}))}

@app.get("/park-data")
def get_park_data():
    """Return today's stored Ballpark Pal data if available."""
    tk     = today_key()
    cached = cache_get("park_data", tk)
    if not cached:
        return {"available": False, "park_map": {}}
    return {"available": True, "park_map": cached["park_map"],
            "uploaded_at": cached.get("uploaded_at_fmt", "")}

@app.post("/parse-park-image")
async def parse_park_image(file: UploadFile = File(...)):
    """Parse Ballpark Pal screenshot via Claude vision and store result for the day."""
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set on Render")

        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        image_b64  = base64.b64encode(image_bytes).decode("utf-8")
        raw_type   = (file.content_type or "image/png").lower()
        if "jpeg" in raw_type or "jpg" in raw_type: media_type = "image/jpeg"
        elif "gif" in raw_type:  media_type = "image/gif"
        elif "webp" in raw_type: media_type = "image/webp"
        else:                    media_type = "image/png"

        payload = {
            "model": "claude-opus-4-5",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": (
                    "This is a Ballpark Pal screenshot showing daily MLB park factors.\n"
                    "Extract the HR modifier percentage for every park or team you can see.\n"
                    "Return ONLY raw JSON — no markdown, no explanation, no code fences.\n"
                    "Exact format: {\"park_data\": [{\"park\": \"Name\", \"hr_mod\": 12}, ...]}\n"
                    "For Daily Stadium Report: hr_mod is the HR column number (integer, e.g. 26 or -11).\n"
                    "For Totals table: hr_mod = round((total_hrs - 1.15) / 1.15 * 100).\n"
                    "Only integers for hr_mod. No percent signs. Extract every visible row."
                )}
            ]}]
        }

        resp = requests.post("https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": api_key,
                     "anthropic-version": "2023-06-01"},
            json=payload, timeout=45)

        if not resp.ok:
            raise HTTPException(status_code=resp.status_code,
                detail=f"Anthropic API error {resp.status_code}: {resp.text[:500]}")

        data  = resp.json()
        text  = "".join(b.get("text","") for b in data.get("content",[]) if b.get("type")=="text")
        clean = text.replace("```json","").replace("```","").strip()
        parsed = json_lib.loads(clean)

        # Build park_map and store for the day
        park_map = {}
        for entry in parsed.get("park_data", []):
            park_map[entry["park"]] = {"hr_mod": entry["hr_mod"]}

        tk = today_key()
        cache_set("park_data", tk, {
            "park_map":        park_map,
            "uploaded_at":     datetime.now().isoformat(),
            "uploaded_at_fmt": datetime.now().strftime("%-I:%M %p"),
        })

        return {**parsed, "stored": True, "park_count": len(park_map),
                "message": f"Loaded {len(park_map)} parks — active all day, no re-upload needed."}

    except HTTPException:
        raise
    except json_lib.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Could not parse Claude response as JSON: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image parse error: {str(e)}")
