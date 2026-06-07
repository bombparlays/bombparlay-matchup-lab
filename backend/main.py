from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import date, timedelta
import pandas as pd
import numpy as np
from pybaseball import statcast_batter, statcast_pitcher
import requests
import warnings
import pybaseball
warnings.filterwarnings("ignore")

pybaseball.cache.enable()

app = FastAPI(title="MLB Matchup API v2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ── PARK FACTORS (HR index, 100 = league avg, >100 = hitter friendly) ────────
# Based on multi-year Statcast park HR rates. Both teams use the home park.
PARK_FACTORS = {
    # Team name substring → (hr_factor, park_name, tier)
    # tier: "great" | "good" | "neutral" | "bad" | "pitcher"
    "Rockies":      (140, "Coors Field",          "great"),
    "Reds":         (122, "Great American Ball Park", "great"),
    "Yankees":      (118, "Yankee Stadium",        "great"),
    "Phillies":     (116, "Citizens Bank Park",    "great"),
    "Red Sox":      (112, "Fenway Park",           "good"),
    "Cubs":         (110, "Wrigley Field",         "good"),
    "Brewers":      (108, "American Family Field", "good"),
    "Braves":       (107, "Truist Park",           "good"),
    "Rangers":      (106, "Globe Life Field",      "good"),
    "Blue Jays":    (105, "Rogers Centre",         "good"),
    "Astros":       (104, "Minute Maid Park",      "good"),
    "Orioles":      (103, "Camden Yards",          "good"),
    "Twins":        (102, "Target Field",          "neutral"),
    "Cardinals":    (101, "Busch Stadium",         "neutral"),
    "Angels":       (100, "Angel Stadium",         "neutral"),
    "Tigers":       (99,  "Comerica Park",         "neutral"),
    "White Sox":    (98,  "Guaranteed Rate Field", "neutral"),
    "Royals":       (97,  "Kauffman Stadium",      "neutral"),
    "Indians":      (97,  "Progressive Field",     "neutral"),
    "Guardians":    (97,  "Progressive Field",     "neutral"),
    "Nationals":    (96,  "Nationals Park",        "neutral"),
    "Athletics":    (95,  "Oakland Coliseum",      "bad"),
    "Diamondbacks": (95,  "Chase Field",           "bad"),
    "Mariners":     (94,  "T-Mobile Park",         "bad"),
    "Rays":         (93,  "Tropicana Field",       "bad"),
    "Padres":       (92,  "Petco Park",            "bad"),
    "Giants":       (88,  "Oracle Park",           "pitcher"),
    "Dodgers":      (90,  "Dodger Stadium",        "bad"),
    "Mets":         (96,  "Citi Field",            "neutral"),
    "Pirates":      (95,  "PNC Park",              "bad"),
    "Marlins":      (91,  "loanDepot park",        "pitcher"),
}

def get_park_info(home_team: str) -> dict:
    for key, val in PARK_FACTORS.items():
        if key.lower() in home_team.lower():
            factor, park_name, tier = val
            return {"factor": factor, "park_name": park_name, "tier": tier}
    return {"factor": 100, "park_name": home_team, "tier": "neutral"}

def park_score_modifier(park_factor: int) -> float:
    """Convert park factor to HR Score modifier (-10 to +12)."""
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
    """
    Return tier label, icon, and HR Score modifier based on pitcher quality.
    Uses xwOBA allowed + SwStr% to estimate quality.
    Tiers: elite | above_avg | average | below_avg | weak
    """
    if not pitcher_stats:
        return {"tier": "average", "label": "Average", "icon": "", "modifier": 0}

    xa  = pitcher_stats.get("xwoba_against", 0.320)
    sw  = pitcher_stats.get("swstr_pct_pitcher", 10)
    brl = pitcher_stats.get("brl_bip_allowed", 8)

    # Score pitcher: lower xwOBA allowed + higher SwStr% = better pitcher
    quality = (0.320 - xa) * 200 + (sw - 10) * 2 - (brl - 8) * 1.5

    if quality > 15:
        return {"tier": "elite",     "label": "Elite Pitcher",        "icon": "⚠",  "modifier": -11}
    if quality > 6:
        return {"tier": "above_avg", "label": "Above Avg Pitcher",    "icon": "",   "modifier": -4}
    if quality > -6:
        return {"tier": "average",   "label": "Average Pitcher",      "icon": "",   "modifier": 0}
    if quality > -15:
        return {"tier": "below_avg", "label": "Below Avg Pitcher",    "icon": "",   "modifier": +4}
    return     {"tier": "weak",      "label": "Weak Pitcher",         "icon": "🔥", "modifier": +11}

# ── HANDEDNESS / MATCHUP SPLIT ────────────────────────────────────────────────
def get_handedness_split(batter_id: int, pitcher_hand: str,
                         start_dt: str, end_dt: str) -> dict:
    """
    Pull batter's splits vs same/opposite hand and return:
    - split_xwoba: xwOBA vs this pitcher's hand
    - split_iso: ISO vs this pitcher's hand
    - split_label: e.g. "vs LHP" or "vs RHP"
    - split_modifier: HR Score adjustment (-5 to +5)
    """
    try:
        df = statcast_batter(start_dt, end_dt, batter_id)
        if df is None or df.empty or "p_throws" not in df.columns:
            return {"split_xwoba": 0, "split_iso": 0, "split_label": "", "split_modifier": 0}

        hand = pitcher_hand.upper() if pitcher_hand else "R"
        split_df = df[df["p_throws"] == hand]
        overall_df = df

        if split_df.empty:
            return {"split_xwoba": 0, "split_iso": 0, "split_label": f"vs {'LHP' if hand=='L' else 'RHP'}", "split_modifier": 0}

        split_xwoba = safe_float(split_df["estimated_woba_using_speedangle"].mean()
                                 if "estimated_woba_using_speedangle" in split_df.columns else 0)
        overall_xwoba = safe_float(df["estimated_woba_using_speedangle"].mean()
                                   if "estimated_woba_using_speedangle" in df.columns else 0.320)

        # ISO vs this hand
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

        # Modifier: how much better/worse vs this hand relative to overall
        delta = split_xwoba - overall_xwoba
        if delta > 0.040:   modifier = 5
        elif delta > 0.020: modifier = 3
        elif delta > 0.000: modifier = 1
        elif delta > -0.020: modifier = -1
        elif delta > -0.040: modifier = -3
        else:               modifier = -5

        label = f"vs {'LHP' if hand=='L' else 'RHP'}"
        return {
            "split_xwoba":   round(split_xwoba, 3),
            "split_iso":     split_iso,
            "split_label":   label,
            "split_modifier": modifier,
        }
    except Exception as e:
        print(f"Split error for {batter_id}: {e}")
        return {"split_xwoba": 0, "split_iso": 0, "split_label": "", "split_modifier": 0}

# ── HELPERS ───────────────────────────────────────────────────────────────────
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
        singles  = int((ab["events"] == "single").sum())
        doubles_ = int((ab["events"] == "double").sum())
        triples  = int((ab["events"] == "triple").sum())
        hrs      = int((ab["events"] == "home_run").sum())
        ab_count = len(ab)
        iso = (doubles_ + 2*triples + 3*hrs) / ab_count if ab_count else 0

        # Batter hand
        stand = df["stand"].mode()[0] if "stand" in df.columns and not df["stand"].empty else "R"

        # HR Form: rolling 15-game trend
        df2 = df.copy()
        df2["game_date"] = pd.to_datetime(df2["game_date"])
        recent   = df2.sort_values("game_date")
        game_hrs = recent.groupby("game_date").apply(
            lambda g: int((g["events"] == "home_run").sum())).reset_index()
        game_hrs.columns = ["game_date", "hrs"]
        last15   = game_hrs.tail(15)["hrs"].tolist()
        if len(last15) >= 6:
            mid = len(last15) // 2
            hr_form = "↑" if sum(last15[mid:]) > sum(last15[:mid]) else ("↓" if sum(last15[mid:]) < sum(last15[:mid]) else "→")
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
            "stand": stand,
        }
    except Exception as e:
        print(f"Error fetching batter {player_id}: {e}")
        return {}

def fetch_pitcher_stats(player_id: int, start_dt: str, end_dt: str) -> dict:
    try:
        df = statcast_pitcher(start_dt, end_dt, player_id)
        if df is None or df.empty:
            return {}

        total  = len(df)
        fb_types = ["FF","SI","FC","FT"]
        fb_df  = df[df["pitch_type"].isin(fb_types)] if "pitch_type" in df.columns else pd.DataFrame()
        fb_rate = len(fb_df) / total if total else 0

        zone_counts = df["zone"].value_counts(normalize=True).to_dict() if "zone" in df.columns else {}

        batted = df[df["type"] == "X"]
        bip    = len(batted)
        brl_allowed    = int(batted["launch_speed_angle"].isin([6]).sum()) if "launch_speed_angle" in batted.columns and bip else 0
        brl_bip_allowed = brl_allowed / bip if bip else 0

        xwoba_against = safe_float(df["estimated_woba_using_speedangle"].mean()
                                   if "estimated_woba_using_speedangle" in df.columns else 0)
        whiff  = df[df["description"] == "swinging_strike"]
        swstr  = len(whiff) / total if total else 0

        # Pitcher hand
        p_throws = df["p_throws"].mode()[0] if "p_throws" in df.columns and not df["p_throws"].empty else "R"

        # Pitch type classification: power vs contact pitcher
        high_velo = fb_df[fb_df["release_speed"] >= 95] if "release_speed" in fb_df.columns and len(fb_df) else pd.DataFrame()
        power_pitcher = (len(high_velo) / len(fb_df) > 0.5) if len(fb_df) > 0 else False
        pitcher_type  = "Power" if (power_pitcher or swstr > 0.12) else "Contact"

        return {
            "fb_rate":           round(fb_rate * 100, 1),
            "zone_distribution": zone_counts,
            "brl_bip_allowed":   round(brl_bip_allowed * 100, 1),
            "xwoba_against":     round(xwoba_against, 3),
            "swstr_pct_pitcher": round(swstr * 100, 1),
            "total_pitches":     total,
            "bip_allowed":       bip,
            "p_throws":          p_throws,
            "pitcher_type":      pitcher_type,
        }
    except Exception as e:
        print(f"Error fetching pitcher {player_id}: {e}")
        return {}

def compute_zone_fit(batter_stats: dict, pitcher_stats: dict) -> float:
    score = 50.0
    fb_rate    = pitcher_stats.get("fb_rate", 0)
    batter_brl = batter_stats.get("brl_bip_pct", 0)

    if fb_rate > 55 and batter_brl > 10: score += 15
    elif fb_rate > 45 and batter_brl > 7: score += 8

    if batter_stats.get("sweet_spot_pct", 0) > 35: score += 5

    xa = pitcher_stats.get("xwoba_against", 0.320)
    if xa > 0.380:   score += 10
    elif xa > 0.340: score += 5
    elif xa < 0.280: score -= 10

    if batter_stats.get("pulled_brl_pct", 0) > 5: score += 5

    return round(min(max(score, 0), 100), 1)

def compute_hr_score(batter: dict, pitcher: dict, zone_fit: float,
                     pitcher_tier: dict, park_factor: int,
                     split_modifier: float) -> float:
    weights = {
        "brl_bip_pct":    0.25,
        "xwoba":          0.18,
        "fb_pct":         0.15,
        "sweet_spot_pct": 0.12,
        "pulled_brl_pct": 0.10,
        "hh_pct":         0.08,
        "iso":            0.12,
    }
    norms = {
        "brl_bip_pct":    min(batter.get("brl_bip_pct", 0)    / 20    * 100, 100),
        "xwoba":          min(batter.get("xwoba", 0)           / 0.500 * 100, 100),
        "fb_pct":         min(batter.get("fb_pct", 0)          / 60    * 100, 100),
        "sweet_spot_pct": min(batter.get("sweet_spot_pct", 0)  / 50    * 100, 100),
        "pulled_brl_pct": min(batter.get("pulled_brl_pct", 0)  / 10    * 100, 100),
        "hh_pct":         min(batter.get("hh_pct", 0)          / 60    * 100, 100),
        "iso":            min(batter.get("iso", 0)              / 0.350 * 100, 100),
    }
    base     = sum(weights[k] * norms[k] for k in weights)
    zf_bonus = (zone_fit / 100) * 12
    park_mod = park_score_modifier(park_factor)
    pitch_mod = pitcher_tier.get("modifier", 0)

    total = base + zf_bonus + park_mod + pitch_mod + split_modifier
    return round(min(max(total, 0), 100), 1)

def compute_ceiling(hr_score: float, batter: dict) -> float:
    peak = (
        batter.get("brl_bip_pct",   0) / 20 * 100 * 0.4 +
        batter.get("pulled_brl_pct",0) / 10 * 100 * 0.3 +
        batter.get("hh_pct",        0) / 60 * 100 * 0.3
    )
    return round((hr_score * 0.6 + peak * 0.4), 1)

# ── MLB Stats API ─────────────────────────────────────────────────────────────
def get_todays_games(game_date: str = None) -> list:
    if not game_date:
        game_date = date.today().strftime("%Y-%m-%d")
    url = (f"https://statsapi.mlb.com/api/v1/schedule?sportId=1"
           f"&date={game_date}&hydrate=lineups,probablePitcher")
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    games = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            gid    = game["gamePk"]
            status = game.get("status", {}).get("abstractGameState", "")
            home   = game["teams"]["home"]
            away   = game["teams"]["away"]
            hp     = home.get("probablePitcher", {})
            ap     = away.get("probablePitcher", {})
            venue  = game.get("venue", {}).get("name", "")
            games.append({
                "game_id":           gid,
                "status":            status,
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
                "venue":             venue,
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
        lineup  = []
        for pid in batters:
            key = f"ID{pid}"
            p   = players.get(key, {})
            lineup.append({
                "name":   p.get("person", {}).get("fullName", "Unknown"),
                "mlb_id": p.get("person", {}).get("id"),
            })
        return lineup
    except Exception:
        return []

def search_player_id(name: str):
    url = f"https://statsapi.mlb.com/api/v1/people/search?names={requests.utils.quote(name)}&sportId=1"
    try:
        r    = requests.get(url, timeout=10)
        data = r.json()
        ppl  = data.get("people", [])
        if ppl:
            return ppl[0]["id"]
    except Exception:
        pass
    return None

def build_player_row(batter: dict, bstats: dict, pitcher_stats: dict,
                     pitcher_name: str, pitcher_tier: dict,
                     team_name: str, side: str,
                     park_info: dict, split_data: dict,
                     game_label: str) -> dict:
    zone_fit = compute_zone_fit(bstats, pitcher_stats)
    hr_score = compute_hr_score(
        bstats, pitcher_stats, zone_fit,
        pitcher_tier, park_info["factor"], split_data.get("split_modifier", 0)
    )
    ceiling = compute_ceiling(hr_score, bstats)

    return {
        "player":           batter["name"],
        "team":             team_name,
        "side":             side,
        "game":             game_label,
        "opposing_pitcher": pitcher_name,
        "pitcher_tier":     pitcher_tier["tier"],
        "pitcher_label":    pitcher_tier["label"],
        "pitcher_icon":     pitcher_tier["icon"],
        "pitcher_type":     pitcher_stats.get("pitcher_type", ""),
        "p_throws":         pitcher_stats.get("p_throws", "R"),
        "park_name":        park_info["park_name"],
        "park_factor":      park_info["factor"],
        "park_tier":        park_info["tier"],
        "split_label":      split_data.get("split_label", ""),
        "split_xwoba":      split_data.get("split_xwoba", 0),
        "split_iso":        split_data.get("split_iso", 0),
        "split_modifier":   split_data.get("split_modifier", 0),
        "hr_score":         hr_score,
        "ceiling":          ceiling,
        "zone_fit":         zone_fit,
        "hr_form":          bstats.get("hr_form", "→"),
        "khr":              bstats.get("khr", 0),
        "pitches":          bstats.get("total_pitches", 0),
        "bip":              bstats.get("bip", 0),
        "iso":              bstats.get("iso", 0),
        "xwoba":            bstats.get("xwoba", 0),
        "xwoba_con":        bstats.get("xwoba_con", 0),
        "swstr_pct":        bstats.get("swstr_pct", 0),
        "pulled_brl_pct":   bstats.get("pulled_brl_pct", 0),
        "brl_bip_pct":      bstats.get("brl_bip_pct", 0),
        "sweet_spot_pct":   bstats.get("sweet_spot_pct", 0),
        "fb_pct":           bstats.get("fb_pct", 0),
        "hh_pct":           bstats.get("hh_pct", 0),
        "avg_la":           bstats.get("avg_la", 0),
        "hrs":              bstats.get("hrs", 0),
    }

# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "date": str(date.today())}

@app.get("/slate")
def get_slate(game_date: str = Query(default=None)):
    try:
        games = get_todays_games(game_date)
        # Attach park info to each game
        for g in games:
            pi = get_park_info(g["home_team"])
            g["park_name"]   = pi["park_name"]
            g["park_factor"] = pi["factor"]
            g["park_tier"]   = pi["tier"]
        return {"games": games, "date": game_date or str(date.today())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/matchups")
def get_matchups(game_id: int, game_date: str = Query(default=None)):
    try:
        games     = get_todays_games(game_date)
        game      = next((g for g in games if g["game_id"] == game_id), None)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")

        start_dt, end_dt = get_date_range(60)
        park_info = get_park_info(game["home_team"])
        game_label = f"{game['away_team']} @ {game['home_team']}"

        home_results = []
        away_results = []

        # home team's pitcher (home_pitcher) faces AWAY batters
        # away team's pitcher (away_pitcher) faces HOME batters
        sides = [
            ("away", game["away_team_id"], game["away_team"],
             game["home_pitcher_id"],  game["home_pitcher_name"], game["home_pitcher_hand"]),
            ("home", game["home_team_id"], game["home_team"],
             game["away_pitcher_id"],  game["away_pitcher_name"], game["away_pitcher_hand"]),
        ]

        for side_name, team_id, team_name, opp_pitcher_id, opp_pitcher_name, opp_pitcher_hand in sides:
            lineup         = get_lineup(game_id, team_id)
            pitcher_stats  = fetch_pitcher_stats(opp_pitcher_id, start_dt, end_dt) if opp_pitcher_id else {}
            pitcher_tier   = classify_pitcher(pitcher_stats)

            for batter in lineup:
                if not batter["mlb_id"]:
                    continue
                bstats = fetch_batter_stats(batter["mlb_id"], start_dt, end_dt)
                if not bstats:
                    continue
                split_data = get_handedness_split(batter["mlb_id"], opp_pitcher_hand, start_dt, end_dt)
                row = build_player_row(
                    batter, bstats, pitcher_stats,
                    opp_pitcher_name, pitcher_tier,
                    team_name, side_name,
                    park_info, split_data, game_label
                )
                if side_name == "home":
                    home_results.append(row)
                else:
                    away_results.append(row)

        home_results.sort(key=lambda x: x["hr_score"], reverse=True)
        away_results.sort(key=lambda x: x["hr_score"], reverse=True)

        return {
            "game":      game,
            "park_info": park_info,
            "home":      home_results,
            "away":      away_results,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/top-targets")
def get_top_targets(game_date: str = Query(default=None), limit: int = 15):
    """Pull all games on the slate and return the top N HR targets across the board."""
    try:
        games    = get_todays_games(game_date)
        start_dt, end_dt = get_date_range(60)
        all_players = []

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
                    row = build_player_row(
                        batter, bstats, pitcher_stats,
                        opp_pname, pitcher_tier,
                        team_name, side_name,
                        park_info, split_data, game_label
                    )
                    all_players.append(row)

        all_players.sort(key=lambda x: x["hr_score"], reverse=True)
        return {"targets": all_players[:limit], "total_players": len(all_players)}

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
