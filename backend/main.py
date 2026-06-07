from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, date, timedelta
import pandas as pd
import numpy as np
import pybaseball
from pybaseball import statcast, playerid_lookup, statcast_batter, statcast_pitcher
import requests
import warnings
warnings.filterwarnings("ignore")

pybaseball.cache.enable()

app = FastAPI(title="MLB Matchup API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── helpers ──────────────────────────────────────────────────────────────────

def safe_float(val, default=0.0):
    try:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return default
        return float(val)
    except Exception:
        return default

def get_date_range(days_back=60):
    end = date.today()
    start = end - timedelta(days=days_back)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

def fetch_batter_stats(player_id: int, start_dt: str, end_dt: str) -> dict:
    """Pull Statcast data for a batter and aggregate key metrics."""
    try:
        df = statcast_batter(start_dt, end_dt, player_id)
        if df is None or df.empty:
            return {}

        batted = df[df["type"] == "X"].copy()
        swings  = df[df["description"].isin([
            "swinging_strike", "foul", "hit_into_play",
            "swinging_strike_blocked", "foul_tip"
        ])].copy()

        total_pitches = len(df)
        total_swings  = len(swings)
        swstr = len(df[df["description"] == "swinging_strike"]) / total_pitches if total_pitches else 0

        bip = len(batted)
        barrels    = batted["launch_speed_angle"].isin([6]) if "launch_speed_angle" in batted.columns else pd.Series(dtype=bool)
        brl_count  = int(barrels.sum()) if len(barrels) else 0
        brl_bip    = brl_count / bip if bip else 0

        sweet_spot = batted[(batted["launch_angle"] >= 8) & (batted["launch_angle"] <= 32)] if "launch_angle" in batted.columns else pd.DataFrame()
        sweet_pct  = len(sweet_spot) / bip if bip else 0

        fb = batted[batted["bb_type"] == "fly_ball"] if "bb_type" in batted.columns else pd.DataFrame()
        fb_pct = len(fb) / bip if bip else 0

        hh = batted[batted["launch_speed"] >= 95] if "launch_speed" in batted.columns else pd.DataFrame()
        hh_pct = len(hh) / bip if bip else 0

        avg_la = float(batted["launch_angle"].mean()) if "launch_angle" in batted.columns and bip else 0

        pulled = batted[
            ((batted["stand"] == "R") & (batted["hc_x"] < 125)) |
            ((batted["stand"] == "L") & (batted["hc_x"] > 125))
        ] if "hc_x" in batted.columns and "stand" in batted.columns else pd.DataFrame()
        pulled_brl = pulled[pulled["launch_speed_angle"].isin([6])] if "launch_speed_angle" in pulled.columns and len(pulled) else pd.DataFrame()
        pulled_brl_pct = len(pulled_brl) / bip if bip else 0

        xwoba     = safe_float(df["estimated_woba_using_speedangle"].mean()) if "estimated_woba_using_speedangle" in df.columns else 0
        xwoba_con = safe_float(batted["estimated_woba_using_speedangle"].mean()) if "estimated_woba_using_speedangle" in batted.columns and bip else 0

        ab = df[df["events"].notna() & df["events"].isin([
            "single","double","triple","home_run","strikeout","field_out",
            "grounded_into_double_play","force_out","fielders_choice","sac_fly"
        ])]
        singles  = int((ab["events"] == "single").sum())
        doubles_ = int((ab["events"] == "double").sum())
        triples  = int((ab["events"] == "triple").sum())
        hrs      = int((ab["events"] == "home_run").sum())
        ab_count = len(ab)
        iso = (doubles_ + 2*triples + 3*hrs) / ab_count if ab_count else 0

        # HR Form: rolling 15-game trend
        df2 = df.copy()
        df2["game_date"] = pd.to_datetime(df2["game_date"])
        recent = df2.sort_values("game_date")
        game_hrs = recent.groupby("game_date").apply(lambda g: int((g["events"] == "home_run").sum())).reset_index()
        game_hrs.columns = ["game_date", "hrs"]
        last15 = game_hrs.tail(15)["hrs"].tolist()
        if len(last15) >= 6:
            first_half  = sum(last15[:len(last15)//2])
            second_half = sum(last15[len(last15)//2:])
            hr_form = "↑" if second_half > first_half else ("↓" if second_half < first_half else "→")
        else:
            hr_form = "→"

        khr = hrs / ab_count if ab_count else 0

        return {
            "iso": round(iso, 3),
            "xwoba": round(xwoba, 3),
            "xwoba_con": round(xwoba_con, 3),
            "swstr_pct": round(swstr * 100, 1),
            "brl_bip_pct": round(brl_bip * 100, 1),
            "pulled_brl_pct": round(pulled_brl_pct * 100, 1),
            "sweet_spot_pct": round(sweet_pct * 100, 1),
            "fb_pct": round(fb_pct * 100, 1),
            "hh_pct": round(hh_pct * 100, 1),
            "avg_la": round(avg_la, 1),
            "hr_form": hr_form,
            "khr": round(khr * 100, 2),
            "total_pitches": total_pitches,
            "bip": bip,
            "hrs": hrs,
            "ab": ab_count,
        }
    except Exception as e:
        print(f"Error fetching batter {player_id}: {e}")
        return {}

def fetch_pitcher_stats(player_id: int, start_dt: str, end_dt: str) -> dict:
    """Pull Statcast data for a pitcher and aggregate zone tendencies."""
    try:
        df = statcast_pitcher(start_dt, end_dt, player_id)
        if df is None or df.empty:
            return {}

        total = len(df)
        fb_types = ["FF","SI","FC","FT"]
        fb_df = df[df["pitch_type"].isin(fb_types)] if "pitch_type" in df.columns else pd.DataFrame()
        fb_rate = len(fb_df) / total if total else 0

        # Zone distribution: zones 1-9 are strike zone, 11-14 are chase zones
        if "zone" in df.columns:
            zone_counts = df["zone"].value_counts(normalize=True).to_dict()
        else:
            zone_counts = {}

        batted = df[df["type"] == "X"]
        bip = len(batted)
        brl_allowed = int(batted["launch_speed_angle"].isin([6]).sum()) if "launch_speed_angle" in batted.columns and bip else 0
        brl_bip_allowed = brl_allowed / bip if bip else 0

        xwoba_against = safe_float(df["estimated_woba_using_speedangle"].mean()) if "estimated_woba_using_speedangle" in df.columns else 0

        whiff = df[df["description"] == "swinging_strike"]
        swstr = len(whiff) / total if total else 0

        return {
            "fb_rate": round(fb_rate * 100, 1),
            "zone_distribution": zone_counts,
            "brl_bip_allowed": round(brl_bip_allowed * 100, 1),
            "xwoba_against": round(xwoba_against, 3),
            "swstr_pct_pitcher": round(swstr * 100, 1),
            "total_pitches": total,
            "bip_allowed": bip,
        }
    except Exception as e:
        print(f"Error fetching pitcher {player_id}: {e}")
        return {}

def compute_zone_fit(batter_stats: dict, pitcher_stats: dict) -> float:
    """
    Zone Fit score (0-100): how well the pitcher's tendencies
    align with the batter's strengths.
    Higher = better matchup for the hitter.
    """
    score = 50.0  # neutral baseline

    # If pitcher throws lots of FBs and batter has high FB/Brl metrics → good
    fb_rate = pitcher_stats.get("fb_rate", 0)
    batter_brl = batter_stats.get("brl_bip_pct", 0)
    batter_fb  = batter_stats.get("fb_pct", 0)

    if fb_rate > 55 and batter_brl > 10:
        score += 15
    elif fb_rate > 45 and batter_brl > 7:
        score += 8

    # Sweet spot alignment
    if batter_stats.get("sweet_spot_pct", 0) > 35:
        score += 5

    # Pitcher allows high xwOBA → easier matchup
    xwoba_against = pitcher_stats.get("xwoba_against", 0)
    if xwoba_against > 0.380:
        score += 10
    elif xwoba_against > 0.340:
        score += 5
    elif xwoba_against < 0.280:
        score -= 10

    # Batter pull power
    if batter_stats.get("pulled_brl_pct", 0) > 5:
        score += 5

    return round(min(max(score, 0), 100), 1)

def compute_hr_score(batter: dict, pitcher: dict, zone_fit: float) -> float:
    """Weighted composite HR Score (0-100)."""
    weights = {
        "brl_bip_pct":    0.25,
        "xwoba":          0.20,
        "fb_pct":         0.15,
        "sweet_spot_pct": 0.12,
        "pulled_brl_pct": 0.10,
        "hh_pct":         0.08,
        "iso":            0.10,
    }

    # Normalize each metric to 0-100
    norms = {
        "brl_bip_pct":    min(batter.get("brl_bip_pct", 0) / 20 * 100, 100),
        "xwoba":          min(batter.get("xwoba", 0) / 0.500 * 100, 100),
        "fb_pct":         min(batter.get("fb_pct", 0) / 60 * 100, 100),
        "sweet_spot_pct": min(batter.get("sweet_spot_pct", 0) / 50 * 100, 100),
        "pulled_brl_pct": min(batter.get("pulled_brl_pct", 0) / 10 * 100, 100),
        "hh_pct":         min(batter.get("hh_pct", 0) / 60 * 100, 100),
        "iso":            min(batter.get("iso", 0) / 0.350 * 100, 100),
    }

    base = sum(weights[k] * norms[k] for k in weights)

    # Zone fit bonus (up to 15 pts)
    zf_bonus = (zone_fit / 100) * 15

    # Pitcher penalty/bonus
    pitcher_mod = 0
    xa = pitcher.get("xwoba_against", 0.320)
    if xa > 0.380:
        pitcher_mod = 8
    elif xa > 0.350:
        pitcher_mod = 4
    elif xa < 0.280:
        pitcher_mod = -8
    elif xa < 0.300:
        pitcher_mod = -4

    total = base + zf_bonus + pitcher_mod
    return round(min(max(total, 0), 100), 1)

def compute_ceiling(hr_score: float, batter: dict) -> float:
    """Ceiling score: HR score weighted toward peak power metrics."""
    peak = (
        batter.get("brl_bip_pct", 0) / 20 * 100 * 0.4 +
        batter.get("pulled_brl_pct", 0) / 10 * 100 * 0.3 +
        batter.get("hh_pct", 0) / 60 * 100 * 0.3
    )
    return round((hr_score * 0.6 + peak * 0.4), 1)

# ── MLB Schedule / Roster via MLB Stats API (free, no key needed) ─────────────

def get_todays_games(game_date: str = None) -> list:
    if not game_date:
        game_date = date.today().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={game_date}&hydrate=lineups,probablePitcher"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    games = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            gid = game["gamePk"]
            status = game.get("status", {}).get("abstractGameState", "")
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            home_pitcher = home.get("probablePitcher", {})
            away_pitcher = away.get("probablePitcher", {})
            games.append({
                "game_id": gid,
                "status": status,
                "home_team": home["team"]["name"],
                "away_team": away["team"]["name"],
                "home_team_id": home["team"]["id"],
                "away_team_id": away["team"]["id"],
                "home_pitcher_id": home_pitcher.get("id"),
                "home_pitcher_name": home_pitcher.get("fullName", "TBD"),
                "away_pitcher_id": away_pitcher.get("id"),
                "away_pitcher_name": away_pitcher.get("fullName", "TBD"),
                "game_time": game.get("gameDate", ""),
            })
    return games

def get_lineup(game_id: int, team_id: int) -> list:
    """Fetch confirmed lineup from MLB Stats API."""
    url = f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        side = "home" if data["teams"]["home"]["team"]["id"] == team_id else "away"
        batters = data["teams"][side].get("battingOrder", [])
        players = data["teams"][side].get("players", {})
        lineup = []
        for pid in batters:
            key = f"ID{pid}"
            p = players.get(key, {})
            name = p.get("person", {}).get("fullName", "Unknown")
            mlb_id = p.get("person", {}).get("id")
            lineup.append({"name": name, "mlb_id": mlb_id})
        return lineup
    except Exception:
        return []

def search_player_id(name: str) -> int | None:
    """Look up a player's MLB ID via statsapi."""
    url = f"https://statsapi.mlb.com/api/v1/people/search?names={requests.utils.quote(name)}&sportId=1"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        people = data.get("people", [])
        if people:
            return people[0]["id"]
    except Exception:
        pass
    return None

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "date": str(date.today())}

@app.get("/slate")
def get_slate(game_date: str = Query(default=None)):
    """Return today's (or given date's) games with probable pitchers."""
    try:
        games = get_todays_games(game_date)
        return {"games": games, "date": game_date or str(date.today())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/matchups")
def get_matchups(game_id: int, game_date: str = Query(default=None)):
    """
    For a given game_id, fetch lineups + pitcher, pull Statcast stats,
    compute Zone Fit and HR Score for each batter.
    """
    try:
        games = get_todays_games(game_date)
        game = next((g for g in games if g["game_id"] == game_id), None)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")

        start_dt, end_dt = get_date_range(60)
        results = []

        for side in [("away", game["away_team_id"], game["away_pitcher_id"], game["away_pitcher_name"]),
                     ("home", game["home_team_id"], game["home_pitcher_id"], game["home_pitcher_name"])]:
            side_name, team_id, pitcher_id, pitcher_name = side
            lineup = get_lineup(game_id, team_id)

            # Get pitcher stats
            pitcher_stats = {}
            if pitcher_id:
                pitcher_stats = fetch_pitcher_stats(pitcher_id, start_dt, end_dt)

            for batter in lineup:
                if not batter["mlb_id"]:
                    continue
                bstats = fetch_batter_stats(batter["mlb_id"], start_dt, end_dt)
                if not bstats:
                    continue

                zone_fit   = compute_zone_fit(bstats, pitcher_stats)
                hr_score   = compute_hr_score(bstats, pitcher_stats, zone_fit)
                ceiling    = compute_ceiling(hr_score, bstats)

                results.append({
                    "player":          batter["name"],
                    "team":            game["home_team"] if side_name == "home" else game["away_team"],
                    "opposing_pitcher":pitcher_name,
                    "hr_score":        hr_score,
                    "ceiling":         ceiling,
                    "zone_fit":        zone_fit,
                    "hr_form":         bstats.get("hr_form", "→"),
                    "khr":             bstats.get("khr", 0),
                    "pitches":         bstats.get("total_pitches", 0),
                    "bip":             bstats.get("bip", 0),
                    "iso":             bstats.get("iso", 0),
                    "xwoba":           bstats.get("xwoba", 0),
                    "xwoba_con":       bstats.get("xwoba_con", 0),
                    "swstr_pct":       bstats.get("swstr_pct", 0),
                    "pulled_brl_pct":  bstats.get("pulled_brl_pct", 0),
                    "brl_bip_pct":     bstats.get("brl_bip_pct", 0),
                    "sweet_spot_pct":  bstats.get("sweet_spot_pct", 0),
                    "fb_pct":          bstats.get("fb_pct", 0),
                    "hh_pct":          bstats.get("hh_pct", 0),
                    "avg_la":          bstats.get("avg_la", 0),
                    "hrs":             bstats.get("hrs", 0),
                })

        results.sort(key=lambda x: x["hr_score"], reverse=True)
        return {"game": game, "matchups": results}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/player")
def get_player(name: str):
    """Search a player by name and return their stats."""
    try:
        mlb_id = search_player_id(name)
        if not mlb_id:
            raise HTTPException(status_code=404, detail=f"Player '{name}' not found")
        start_dt, end_dt = get_date_range(60)
        stats = fetch_batter_stats(mlb_id, start_dt, end_dt)
        if not stats:
            raise HTTPException(status_code=404, detail="No Statcast data found for player")
        return {"name": name, "mlb_id": mlb_id, "stats": stats}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
