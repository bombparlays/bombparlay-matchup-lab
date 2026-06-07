import React, { useState, useEffect, useCallback, useMemo } from "react";

// ── CONFIG ────────────────────────────────────────────────────────────────────
// Replace with your Render backend URL after deploying
const API_BASE = process.env.REACT_APP_API_URL || "http://localhost:8000";

// ── HEAT MAP COLORING ─────────────────────────────────────────────────────────
const METRIC_RANGES = {
  hr_score:       { low: 20, high: 70,  invert: false },
  ceiling:        { low: 20, high: 75,  invert: false },
  zone_fit:       { low: 30, high: 75,  invert: false },
  khr:            { low: 2,  high: 10,  invert: false },
  iso:            { low: 0.100, high: 0.280, invert: false },
  xwoba:          { low: 0.250, high: 0.420, invert: false },
  xwoba_con:      { low: 0.280, high: 0.500, invert: false },
  swstr_pct:      { low: 8,  high: 18,  invert: true  },
  pulled_brl_pct: { low: 1,  high: 8,   invert: false },
  brl_bip_pct:    { low: 3,  high: 16,  invert: false },
  sweet_spot_pct: { low: 25, high: 45,  invert: false },
  fb_pct:         { low: 25, high: 50,  invert: false },
  hh_pct:         { low: 30, high: 55,  invert: false },
  avg_la:         { low: 5,  high: 20,  invert: false },
};

function getHeatColor(metric, value) {
  const range = METRIC_RANGES[metric];
  if (!range || value === null || value === undefined) return "transparent";
  const { low, high, invert } = range;
  let t = (value - low) / (high - low);
  t = Math.max(0, Math.min(1, t));
  if (invert) t = 1 - t;

  // green → yellow → red palette
  if (t >= 0.5) {
    const g = Math.round(255);
    const r = Math.round(255 * (1 - (t - 0.5) * 2));
    return `rgba(${r}, ${g}, 60, 0.35)`;
  } else {
    const r = Math.round(255);
    const g = Math.round(255 * (t * 2));
    return `rgba(${r}, ${g}, 60, 0.35)`;
  }
}

// ── COLUMN DEFINITIONS ────────────────────────────────────────────────────────
const COLUMNS = [
  { key: "player",         label: "Player",         sticky: true,  numeric: false, format: v => v },
  { key: "opposing_pitcher", label: "vs Pitcher",   sticky: false, numeric: false, format: v => v },
  { key: "hr_score",       label: "HR Score",       sticky: false, numeric: true,  format: v => v.toFixed(1) },
  { key: "ceiling",        label: "Ceiling",        sticky: false, numeric: true,  format: v => v.toFixed(1) },
  { key: "zone_fit",       label: "Zone Fit",       sticky: false, numeric: true,  format: v => v.toFixed(1) },
  { key: "hr_form",        label: "HR Form",        sticky: false, numeric: false, format: v => v },
  { key: "khr",            label: "kHR%",           sticky: false, numeric: true,  format: v => v.toFixed(2) + "%" },
  { key: "pitches",        label: "Pitches",        sticky: false, numeric: true,  format: v => v.toLocaleString() },
  { key: "bip",            label: "BIP",            sticky: false, numeric: true,  format: v => v.toLocaleString() },
  { key: "iso",            label: "ISO",            sticky: false, numeric: true,  format: v => v.toFixed(3) },
  { key: "xwoba",          label: "xwOBA",          sticky: false, numeric: true,  format: v => v.toFixed(3) },
  { key: "xwoba_con",      label: "xwOBAcon",       sticky: false, numeric: true,  format: v => v.toFixed(3) },
  { key: "swstr_pct",      label: "SwStr%",         sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "pulled_brl_pct", label: "PulledBrl%",     sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "brl_bip_pct",    label: "Brl/BIP%",       sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "sweet_spot_pct", label: "SweetSpot%",     sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "fb_pct",         label: "FB%",            sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "hh_pct",         label: "HH%",            sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "avg_la",         label: "Avg LA",         sticky: false, numeric: true,  format: v => v.toFixed(1) + "°" },
  { key: "hrs",            label: "HR",             sticky: false, numeric: true,  format: v => v },
];

// ── STYLES ────────────────────────────────────────────────────────────────────
const styles = {
  app: {
    minHeight: "100vh",
    background: "#0a0e1a",
    color: "#e8eaf0",
    fontFamily: "'IBM Plex Mono', 'Courier New', monospace",
  },
  header: {
    background: "linear-gradient(135deg, #0d1424 0%, #111827 50%, #0a1628 100%)",
    borderBottom: "1px solid #1e3a5f",
    padding: "20px 28px",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    flexWrap: "wrap",
    gap: 12,
  },
  logo: {
    display: "flex",
    alignItems: "center",
    gap: 12,
  },
  logoIcon: {
    width: 42,
    height: 42,
    background: "linear-gradient(135deg, #1a56db, #0ea5e9)",
    borderRadius: 8,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 22,
  },
  logoText: {
    fontSize: 22,
    fontWeight: 700,
    color: "#f0f6ff",
    letterSpacing: "-0.5px",
  },
  logoSub: {
    fontSize: 11,
    color: "#64748b",
    letterSpacing: "2px",
    textTransform: "uppercase",
    marginTop: 2,
  },
  dateBadge: {
    background: "#111827",
    border: "1px solid #1e3a5f",
    borderRadius: 6,
    padding: "6px 14px",
    fontSize: 12,
    color: "#94a3b8",
    letterSpacing: "1px",
  },
  main: {
    padding: "20px 16px",
    maxWidth: 1800,
    margin: "0 auto",
  },
  slateBar: {
    display: "flex",
    gap: 10,
    flexWrap: "wrap",
    marginBottom: 20,
    alignItems: "center",
  },
  slateLabel: {
    fontSize: 11,
    color: "#64748b",
    letterSpacing: "2px",
    textTransform: "uppercase",
    marginRight: 4,
    whiteSpace: "nowrap",
  },
  gameChip: (active, status) => ({
    padding: "8px 14px",
    borderRadius: 6,
    border: active ? "1px solid #1a56db" : "1px solid #1e293b",
    background: active ? "rgba(26,86,219,0.2)" : status === "Final" ? "rgba(30,30,30,0.5)" : "#0f172a",
    color: active ? "#93c5fd" : status === "Final" ? "#4b5563" : "#94a3b8",
    cursor: "pointer",
    fontSize: 11,
    fontFamily: "inherit",
    transition: "all 0.15s",
    whiteSpace: "nowrap",
  }),
  searchBar: {
    display: "flex",
    gap: 10,
    marginBottom: 20,
    alignItems: "center",
  },
  input: {
    flex: 1,
    maxWidth: 320,
    background: "#0f172a",
    border: "1px solid #1e3a5f",
    borderRadius: 6,
    padding: "9px 14px",
    color: "#e8eaf0",
    fontSize: 13,
    fontFamily: "inherit",
    outline: "none",
  },
  btn: (variant = "primary") => ({
    padding: "9px 18px",
    borderRadius: 6,
    border: "none",
    background: variant === "primary"
      ? "linear-gradient(135deg, #1a56db, #0ea5e9)"
      : "#1e293b",
    color: "#f0f6ff",
    cursor: "pointer",
    fontSize: 12,
    fontFamily: "inherit",
    fontWeight: 600,
    letterSpacing: "0.5px",
    transition: "opacity 0.15s",
  }),
  tableWrap: {
    overflowX: "auto",
    borderRadius: 10,
    border: "1px solid #1e293b",
    background: "#0d1424",
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: 12,
  },
  th: (sticky, sortActive, sortDir) => ({
    padding: "11px 12px",
    textAlign: "left",
    background: "#111827",
    color: sortActive ? "#60a5fa" : "#64748b",
    fontSize: 10,
    letterSpacing: "1px",
    textTransform: "uppercase",
    cursor: "pointer",
    userSelect: "none",
    whiteSpace: "nowrap",
    borderBottom: "1px solid #1e293b",
    position: sticky ? "sticky" : undefined,
    left: sticky ? 0 : undefined,
    zIndex: sticky ? 2 : undefined,
    boxShadow: sticky ? "2px 0 8px rgba(0,0,0,0.4)" : undefined,
  }),
  td: (sticky, bg) => ({
    padding: "9px 12px",
    borderBottom: "1px solid rgba(30,41,59,0.6)",
    background: sticky ? "#0d1424" : bg || "transparent",
    whiteSpace: "nowrap",
    position: sticky ? "sticky" : undefined,
    left: sticky ? 0 : undefined,
    zIndex: sticky ? 1 : undefined,
    boxShadow: sticky ? "2px 0 8px rgba(0,0,0,0.3)" : undefined,
  }),
  formArrow: (form) => ({
    color: form === "↑" ? "#4ade80" : form === "↓" ? "#f87171" : "#94a3b8",
    fontWeight: 700,
    fontSize: 14,
  }),
  scoreCell: (score) => ({
    fontWeight: 700,
    fontSize: 13,
    color: score >= 65 ? "#4ade80" : score >= 45 ? "#facc15" : "#f87171",
  }),
  loading: {
    textAlign: "center",
    padding: "80px 20px",
    color: "#64748b",
    fontSize: 13,
    letterSpacing: "1px",
  },
  spinner: {
    display: "inline-block",
    width: 28,
    height: 28,
    border: "3px solid #1e293b",
    borderTop: "3px solid #1a56db",
    borderRadius: "50%",
    animation: "spin 0.8s linear infinite",
    marginBottom: 16,
  },
  legend: {
    display: "flex",
    gap: 16,
    alignItems: "center",
    marginBottom: 14,
    fontSize: 10,
    color: "#64748b",
    letterSpacing: "1px",
    textTransform: "uppercase",
  },
  legendDot: (color) => ({
    width: 10,
    height: 10,
    borderRadius: 2,
    background: color,
    display: "inline-block",
    marginRight: 4,
  }),
  noData: {
    padding: "60px 20px",
    textAlign: "center",
    color: "#374151",
    fontSize: 13,
  },
  sectionTitle: {
    fontSize: 11,
    color: "#475569",
    letterSpacing: "2px",
    textTransform: "uppercase",
    marginBottom: 12,
    paddingLeft: 2,
  },
  errorBox: {
    background: "rgba(239,68,68,0.1)",
    border: "1px solid rgba(239,68,68,0.3)",
    borderRadius: 8,
    padding: "14px 18px",
    color: "#fca5a5",
    fontSize: 12,
    marginBottom: 16,
  },
};

// ── COMPONENT ─────────────────────────────────────────────────────────────────
export default function App() {
  const [slate, setSlate] = useState([]);
  const [selectedGame, setSelectedGame] = useState(null);
  const [matchups, setMatchups] = useState([]);
  const [loadingSlate, setLoadingSlate] = useState(true);
  const [loadingMatchups, setLoadingMatchups] = useState(false);
  const [sortKey, setSortKey] = useState("hr_score");
  const [sortDir, setSortDir] = useState("desc");
  const [searchName, setSearchName] = useState("");
  const [searchResult, setSearchResult] = useState(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [error, setError] = useState(null);
  const [gameDate, setGameDate] = useState("");

  const today = new Date().toLocaleDateString("en-US", {
    weekday: "long", year: "numeric", month: "long", day: "numeric"
  });

  // Load slate on mount
  useEffect(() => {
    loadSlate();
    // inject font
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600;700&display=swap";
    document.head.appendChild(link);
    // inject spinner keyframe
    const style = document.createElement("style");
    style.textContent = `@keyframes spin { to { transform: rotate(360deg); } }
    body { margin: 0; padding: 0; }
    * { box-sizing: border-box; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0a0e1a; }
    ::-webkit-scrollbar-thumb { background: #1e3a5f; border-radius: 3px; }
    input::placeholder { color: #374151; }`;
    document.head.appendChild(style);
  }, []);

  const loadSlate = async (date = null) => {
    setLoadingSlate(true);
    setError(null);
    try {
      const url = date
        ? `${API_BASE}/slate?game_date=${date}`
        : `${API_BASE}/slate`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();
      setSlate(data.games || []);
    } catch (e) {
      setError(`Could not load slate: ${e.message}`);
    } finally {
      setLoadingSlate(false);
    }
  };

  const loadMatchups = useCallback(async (game) => {
    setSelectedGame(game);
    setMatchups([]);
    setSearchResult(null);
    setLoadingMatchups(true);
    setError(null);
    try {
      const url = `${API_BASE}/matchups?game_id=${game.game_id}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();
      setMatchups(data.matchups || []);
    } catch (e) {
      setError(`Could not load matchups: ${e.message}`);
    } finally {
      setLoadingMatchups(false);
    }
  }, []);

  const handleSearch = async () => {
    if (!searchName.trim()) return;
    setSearchLoading(true);
    setSearchResult(null);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/player?name=${encodeURIComponent(searchName)}`);
      if (!res.ok) throw new Error(`Player not found`);
      const data = await res.json();
      setSearchResult(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setSearchLoading(false);
    }
  };

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir(d => d === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const sortedMatchups = useMemo(() => {
    const col = COLUMNS.find(c => c.key === sortKey);
    return [...matchups].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (!col?.numeric) {
        return sortDir === "asc"
          ? String(av).localeCompare(String(bv))
          : String(bv).localeCompare(String(av));
      }
      return sortDir === "asc" ? av - bv : bv - av;
    });
  }, [matchups, sortKey, sortDir]);

  const formatGameLabel = (g) =>
    `${g.away_team.split(" ").pop()} @ ${g.home_team.split(" ").pop()}`;

  return (
    <div style={styles.app}>
      {/* ── HEADER ── */}
      <header style={styles.header}>
        <div style={styles.logo}>
          <div style={styles.logoIcon}>⚾</div>
          <div>
            <div style={styles.logoText}>MLB Matchup Lab</div>
            <div style={styles.logoSub}>Statcast HR Intelligence</div>
          </div>
        </div>
        <div style={styles.dateBadge}>{today}</div>
      </header>

      <main style={styles.main}>
        {error && <div style={styles.errorBox}>⚠ {error}</div>}

        {/* ── DATE PICKER ── */}
        <div style={{ ...styles.searchBar, marginBottom: 12 }}>
          <span style={styles.slateLabel}>Date:</span>
          <input
            type="date"
            style={styles.input}
            value={gameDate}
            onChange={e => setGameDate(e.target.value)}
          />
          <button style={styles.btn("secondary")} onClick={() => loadSlate(gameDate || null)}>
            Load Slate
          </button>
          <button style={styles.btn("secondary")} onClick={() => { setGameDate(""); loadSlate(); }}>
            Today
          </button>
        </div>

        {/* ── SLATE GAME CHIPS ── */}
        <div style={styles.slateBar}>
          <span style={styles.slateLabel}>Slate:</span>
          {loadingSlate ? (
            <span style={{ color: "#374151", fontSize: 12 }}>Loading games...</span>
          ) : slate.length === 0 ? (
            <span style={{ color: "#374151", fontSize: 12 }}>No games found</span>
          ) : slate.map(g => (
            <button
              key={g.game_id}
              style={styles.gameChip(selectedGame?.game_id === g.game_id, g.status)}
              onClick={() => loadMatchups(g)}
            >
              {formatGameLabel(g)}
            </button>
          ))}
        </div>

        {/* ── MANUAL SEARCH ── */}
        <div style={styles.searchBar}>
          <span style={styles.slateLabel}>Search:</span>
          <input
            style={styles.input}
            placeholder="Player name (e.g. Aaron Judge)"
            value={searchName}
            onChange={e => setSearchName(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleSearch()}
          />
          <button style={styles.btn()} onClick={handleSearch} disabled={searchLoading}>
            {searchLoading ? "Searching..." : "Search Player"}
          </button>
        </div>

        {/* ── SEARCH RESULT CARD ── */}
        {searchResult && (
          <div style={{
            background: "#0f172a",
            border: "1px solid #1e3a5f",
            borderRadius: 10,
            padding: "16px 20px",
            marginBottom: 20,
          }}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12, color: "#93c5fd" }}>
              {searchResult.name}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "10px 24px" }}>
              {Object.entries(searchResult.stats).filter(([k]) =>
                ["iso","xwoba","xwoba_con","brl_bip_pct","sweet_spot_pct",
                 "fb_pct","hh_pct","avg_la","hr_form","hrs","swstr_pct"].includes(k)
              ).map(([k, v]) => (
                <div key={k} style={{ fontSize: 11 }}>
                  <span style={{ color: "#475569", textTransform: "uppercase", letterSpacing: "1px" }}>{k}: </span>
                  <span style={{ color: "#e2e8f0", fontWeight: 600 }}>
                    {typeof v === "number" ? v.toFixed(3) : v}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── MATCHUP TABLE ── */}
        {selectedGame && (
          <>
            <div style={styles.sectionTitle}>
              {selectedGame.away_team} @ {selectedGame.home_team} — Matchup Analysis
            </div>

            <div style={styles.legend}>
              <span><span style={styles.legendDot("rgba(0,255,60,0.45)")} />Elite</span>
              <span><span style={styles.legendDot("rgba(255,255,60,0.45)")} />Average</span>
              <span><span style={styles.legendDot("rgba(255,0,60,0.35)")} />Below Avg</span>
            </div>

            {loadingMatchups ? (
              <div style={styles.loading}>
                <div style={styles.spinner} />
                <div>Loading Statcast data — this may take 30-60 seconds...</div>
                <div style={{ marginTop: 8, fontSize: 11, color: "#374151" }}>
                  Pulling 60 days of pitch-level data for each batter
                </div>
              </div>
            ) : sortedMatchups.length === 0 ? (
              <div style={styles.noData}>
                No lineup data available yet — lineups typically post 3-4 hours before game time.
              </div>
            ) : (
              <div style={styles.tableWrap}>
                <table style={styles.table}>
                  <thead>
                    <tr>
                      {COLUMNS.map(col => (
                        <th
                          key={col.key}
                          style={styles.th(col.sticky, sortKey === col.key, sortDir)}
                          onClick={() => handleSort(col.key)}
                        >
                          {col.label}
                          {sortKey === col.key ? (sortDir === "asc" ? " ▲" : " ▼") : " ↕"}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedMatchups.map((row, i) => (
                      <tr
                        key={i}
                        style={{ transition: "background 0.1s" }}
                        onMouseEnter={e => e.currentTarget.style.background = "rgba(30,58,95,0.3)"}
                        onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                      >
                        {COLUMNS.map(col => {
                          const val = row[col.key];
                          const bg = col.numeric && METRIC_RANGES[col.key]
                            ? getHeatColor(col.key, val)
                            : undefined;

                          return (
                            <td key={col.key} style={styles.td(col.sticky, bg)}>
                              {col.key === "hr_score" ? (
                                <span style={styles.scoreCell(val)}>{col.format(val)}</span>
                              ) : col.key === "hr_form" ? (
                                <span style={styles.formArrow(val)}>{val}</span>
                              ) : col.key === "player" ? (
                                <span style={{ fontWeight: 600, color: "#e2e8f0" }}>{val}</span>
                              ) : col.key === "opposing_pitcher" ? (
                                <span style={{ color: "#64748b" }}>{val}</span>
                              ) : (
                                <span>{col.format(val)}</span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        {!selectedGame && !searchResult && !loadingSlate && (
          <div style={styles.noData}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>⚾</div>
            <div style={{ color: "#374151", fontSize: 13 }}>
              Select a game from the slate above to load matchup data
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
