import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";

const API_BASE = process.env.REACT_APP_API_URL || "http://localhost:8000";

// ── HEAT MAP ──────────────────────────────────────────────────────────────────
const METRIC_RANGES = {
  hr_score:       { low: 20,    high: 70,    invert: false },
  ceiling:        { low: 20,    high: 75,    invert: false },
  zone_fit:       { low: 30,    high: 75,    invert: false },
  khr:            { low: 2,     high: 10,    invert: false },
  iso:            { low: 0.100, high: 0.280, invert: false },
  xwoba:          { low: 0.250, high: 0.420, invert: false },
  xwoba_con:      { low: 0.280, high: 0.500, invert: false },
  swstr_pct:      { low: 8,     high: 18,    invert: true  },
  pulled_brl_pct: { low: 1,     high: 8,     invert: false },
  brl_bip_pct:    { low: 3,     high: 16,    invert: false },
  sweet_spot_pct: { low: 25,    high: 45,    invert: false },
  fb_pct:         { low: 25,    high: 50,    invert: false },
  hh_pct:         { low: 30,    high: 55,    invert: false },
  avg_la:         { low: 5,     high: 20,    invert: false },
  split_xwoba:    { low: 0.250, high: 0.420, invert: false },
  park_factor:    { low: 88,    high: 130,   invert: false },
};

function getHeatColor(metric, value) {
  const range = METRIC_RANGES[metric];
  if (!range || value === null || value === undefined) return "transparent";
  const { low, high, invert } = range;
  let t = (value - low) / (high - low);
  t = Math.max(0, Math.min(1, t));
  if (invert) t = 1 - t;
  if (t >= 0.5) {
    const r = Math.round(255 * (1 - (t - 0.5) * 2));
    return `rgba(${r},255,60,0.32)`;
  } else {
    const g = Math.round(255 * (t * 2));
    return `rgba(255,${g},60,0.32)`;
  }
}

// ── FACTOR SCORECARD ──────────────────────────────────────────────────────────
function computeFactorScores(row, bpParkData) {
  // 1. Barrel Power (0-10)
  const brlScore = Math.min(10, (row.brl_bip_pct / 16) * 10);
  const brlLabel = row.brl_bip_pct >= 12 ? "Elite barrel rate" :
                   row.brl_bip_pct >= 8  ? "Above avg barrels" :
                   row.brl_bip_pct >= 5  ? "Average barrels"   : "Below avg barrels";

  // 2. Fly Ball Profile (0-10) — combo of FB% + sweet spot + avg LA
  const fbRaw = (row.fb_pct / 50 * 4) + (row.sweet_spot_pct / 45 * 3) + (Math.min(row.avg_la, 22) / 22 * 3);
  const fbScore = Math.min(10, fbRaw);
  const fbLabel = row.fb_pct >= 42 ? "Strong fly ball hitter" :
                  row.fb_pct >= 33 ? "Average fly ball rate" : "Ground ball tendency";

  // 3. Pitcher Quality (0-10) — inverted from pitcher tier modifier
  const pitchMod = row.pitcher_tier === "elite"     ? 0  :
                   row.pitcher_tier === "above_avg"  ? 3  :
                   row.pitcher_tier === "average"    ? 5  :
                   row.pitcher_tier === "below_avg"  ? 7  :
                   row.pitcher_tier === "weak"       ? 10 : 5;
  const pitchLabel = row.pitcher_label || "Average pitcher";

  // 4. Handedness Split (0-10)
  const splitDelta = row.split_xwoba - row.xwoba;
  const splitRaw   = 5 + (splitDelta / 0.060) * 5;
  const splitScore = Math.min(10, Math.max(0, splitRaw));
  const splitLabel = splitDelta > 0.030  ? `Thrives ${row.split_label}` :
                     splitDelta < -0.030 ? `Struggles ${row.split_label}` :
                     `Neutral ${row.split_label}`;

  // 5. Park Factor (0-10) — from Ballpark Pal if available, else base
  let parkScore, parkLabel, parkValue;
  const bpEntry = bpParkData[row.park_name] || bpParkData[row.team];
  if (bpEntry) {
    // bpEntry.hr_mod is a % modifier e.g. +26 or -11
    parkValue = bpEntry.hr_mod;
    parkScore = Math.min(10, Math.max(0, 5 + (parkValue / 30) * 5));
    parkLabel = `BP Pal: ${parkValue > 0 ? "+" : ""}${parkValue}% HR today`;
  } else {
    parkScore = Math.min(10, ((row.park_factor - 85) / 55) * 10);
    parkLabel = `${row.park_name} — factor ${row.park_factor}`;
  }

  // 6. HR Form (0-10)
  const formScore = row.hr_form === "↑" ? 8 : row.hr_form === "→" ? 5 : 2;
  const formLabel = row.hr_form === "↑" ? "Hot — HRs trending up last 15 games" :
                    row.hr_form === "↓" ? "Cold — HRs trending down" : "Steady — consistent pace";

  return [
    { label: "Barrel Power",      score: brlScore,   max: 10, detail: brlLabel,   color: "#f59e0b" },
    { label: "Fly Ball Profile",  score: fbScore,    max: 10, detail: fbLabel,    color: "#60a5fa" },
    { label: "Pitcher Quality",   score: pitchMod,   max: 10, detail: pitchLabel, color: "#f87171" },
    { label: "Handedness Split",  score: splitScore, max: 10, detail: splitLabel, color: "#a78bfa" },
    { label: "Park Factor",       score: parkScore,  max: 10, detail: parkLabel,  color: "#2dd4bf" },
    { label: "HR Form",           score: formScore,  max: 10, detail: formLabel,  color: "#4ade80" },
  ];
}

function FactorScorecard({ row, bpParkData }) {
  const factors = computeFactorScores(row, bpParkData);
  return (
    <div style={{
      background: "#080d18", padding: "14px 18px 14px 48px",
      borderBottom: "1px solid #1e293b", display: "grid",
      gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10,
    }}>
      {factors.map(f => {
        const pct = Math.round((f.score / f.max) * 100);
        const barColor = pct >= 70 ? "#4ade80" : pct >= 40 ? "#facc15" : "#f87171";
        return (
          <div key={f.label} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 10, color: f.color, fontWeight: 700, letterSpacing: "0.5px" }}>
                {f.label}
              </span>
              <span style={{ fontSize: 11, fontWeight: 700, color: barColor }}>
                {f.score.toFixed(1)}<span style={{ color: "#374151", fontSize: 9 }}>/10</span>
              </span>
            </div>
            <div style={{ height: 5, background: "#1e293b", borderRadius: 3, overflow: "hidden" }}>
              <div style={{ width: `${pct}%`, height: "100%", background: barColor,
                            borderRadius: 3, transition: "width 0.4s ease" }} />
            </div>
            <span style={{ fontSize: 9, color: "#64748b" }}>{f.detail}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── COLUMNS ───────────────────────────────────────────────────────────────────
const COLUMNS = [
  { key: "_expand",        label: "",             sticky: true,  numeric: false, format: () => "" },
  { key: "player",         label: "Player",       sticky: true,  numeric: false, format: v => v },
  { key: "opposing_pitcher", label: "vs Pitcher", sticky: false, numeric: false, format: v => v },
  { key: "p_throws",       label: "Hand",         sticky: false, numeric: false, format: v => v },
  { key: "hr_score",       label: "HR Score",     sticky: false, numeric: true,  format: v => v.toFixed(1) },
  { key: "ceiling",        label: "Ceiling",      sticky: false, numeric: true,  format: v => v.toFixed(1) },
  { key: "zone_fit",       label: "Zone Fit",     sticky: false, numeric: true,  format: v => v.toFixed(1) },
  { key: "park_factor",    label: "Park",         sticky: false, numeric: true,  format: v => v },
  { key: "split_xwoba",    label: "Split xwOBA",  sticky: false, numeric: true,  format: v => v.toFixed(3) },
  { key: "hr_form",        label: "HR Form",      sticky: false, numeric: false, format: v => v },
  { key: "khr",            label: "kHR%",         sticky: false, numeric: true,  format: v => v.toFixed(2) + "%" },
  { key: "pitches",        label: "Pitches",      sticky: false, numeric: true,  format: v => v.toLocaleString() },
  { key: "bip",            label: "BIP",          sticky: false, numeric: true,  format: v => v.toLocaleString() },
  { key: "iso",            label: "ISO",          sticky: false, numeric: true,  format: v => v.toFixed(3) },
  { key: "xwoba",          label: "xwOBA",        sticky: false, numeric: true,  format: v => v.toFixed(3) },
  { key: "xwoba_con",      label: "xwOBAcon",     sticky: false, numeric: true,  format: v => v.toFixed(3) },
  { key: "swstr_pct",      label: "SwStr%",       sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "pulled_brl_pct", label: "PulledBrl%",   sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "brl_bip_pct",    label: "Brl/BIP%",     sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "sweet_spot_pct", label: "SweetSpot%",   sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "fb_pct",         label: "FB%",          sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "hh_pct",         label: "HH%",          sticky: false, numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "avg_la",         label: "Avg LA",       sticky: false, numeric: true,  format: v => v.toFixed(1) + "°" },
  { key: "hrs",            label: "HR",           sticky: false, numeric: true,  format: v => v },
];

// ── STYLES ────────────────────────────────────────────────────────────────────
const C = {
  bg: "#0a0e1a", surface: "#0d1424", surface2: "#111827",
  border: "#1e3a5f", border2: "#1e293b", text: "#e8eaf0", muted: "#64748b",
  blue: "#1a56db", blueLt: "#93c5fd", green: "#4ade80", yellow: "#facc15",
  red: "#f87171", teal: "#2dd4bf", amber: "#f59e0b", purple: "#a78bfa",
};

const s = {
  app:      { minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "'IBM Plex Mono', monospace" },
  header:   { background: "linear-gradient(135deg,#0d1424 0%,#111827 50%,#0a1628 100%)",
              borderBottom: `1px solid ${C.border}`, padding: "18px 24px",
              display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 },
  logoText: { fontSize: 20, fontWeight: 700, color: "#f0f6ff", letterSpacing: "-0.5px" },
  logoSub:  { fontSize: 10, color: C.muted, letterSpacing: "2px", textTransform: "uppercase", marginTop: 2 },
  main:     { padding: "16px", maxWidth: 1900, margin: "0 auto" },
  tabBar:   { display: "flex", gap: 4, marginBottom: 16, borderBottom: `1px solid ${C.border}` },
  tab:      (a) => ({ padding: "10px 20px", border: "none", background: "transparent",
              color: a ? C.blueLt : C.muted,
              borderBottom: a ? `2px solid ${C.blue}` : "2px solid transparent",
              cursor: "pointer", fontSize: 12, fontFamily: "inherit", fontWeight: a ? 700 : 400,
              letterSpacing: "1px", textTransform: "uppercase", marginBottom: -1, transition: "all 0.15s" }),
  slateBar:   { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16, alignItems: "center" },
  slateLabel: { fontSize: 10, color: C.muted, letterSpacing: "2px", textTransform: "uppercase", whiteSpace: "nowrap" },
  gameChip:   (a, st) => ({ padding: "7px 13px", borderRadius: 6,
              border: a ? `1px solid ${C.blue}` : `1px solid ${C.border2}`,
              background: a ? "rgba(26,86,219,0.2)" : st === "Final" ? "rgba(20,20,30,0.5)" : C.surface,
              color: a ? C.blueLt : st === "Final" ? "#374151" : "#94a3b8",
              cursor: "pointer", fontSize: 10, fontFamily: "inherit", transition: "all 0.15s", whiteSpace: "nowrap" }),
  input:    { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6,
              padding: "8px 12px", color: C.text, fontSize: 12, fontFamily: "inherit", outline: "none" },
  btn:      (v = "primary") => ({ padding: "8px 16px", borderRadius: 6, border: "none",
              background: v === "primary" ? `linear-gradient(135deg,${C.blue},#0ea5e9)` : C.surface2,
              color: "#f0f6ff", cursor: "pointer", fontSize: 11, fontFamily: "inherit", fontWeight: 600, letterSpacing: "0.5px" }),
  tableWrap:  { overflowX: "auto", borderRadius: 8, border: `1px solid ${C.border2}`, background: C.surface },
  table:      { width: "100%", borderCollapse: "collapse", fontSize: 11 },
  th:         (sticky, active) => ({ padding: "10px 11px", textAlign: "left",
              background: C.surface2, color: active ? C.blueLt : C.muted,
              fontSize: 9, letterSpacing: "1px", textTransform: "uppercase",
              cursor: "pointer", userSelect: "none", whiteSpace: "nowrap",
              borderBottom: `1px solid ${C.border2}`,
              position: sticky ? "sticky" : undefined, left: sticky ? 0 : undefined,
              zIndex: sticky ? 3 : 1, boxShadow: sticky ? "2px 0 8px rgba(0,0,0,0.5)" : undefined }),
  td:         (sticky, bg) => ({ padding: "8px 11px", borderBottom: "1px solid rgba(30,41,59,0.5)",
              background: sticky ? C.surface : (bg || "transparent"), whiteSpace: "nowrap",
              position: sticky ? "sticky" : undefined, left: sticky ? 0 : undefined,
              zIndex: sticky ? 1 : undefined, boxShadow: sticky ? "2px 0 6px rgba(0,0,0,0.3)" : undefined }),
  sectionHeader: { padding: "10px 14px", background: `linear-gradient(90deg,#0f172a,${C.surface})`,
                   borderTop: `1px solid ${C.border}`, borderBottom: `1px solid ${C.border2}`,
                   display: "flex", alignItems: "center", gap: 10 },
  sectionLabel:  { fontSize: 10, fontWeight: 700, letterSpacing: "2px", textTransform: "uppercase", color: C.blueLt },
  parkBadge:  (tier) => {
    const m = { great:[C.green,"#052e16"], good:[C.teal,"#042f2e"], neutral:[C.muted,"#1e293b"], bad:[C.yellow,"#2d1a00"], pitcher:[C.red,"#2d0a0a"] };
    const [c,bg] = m[tier] || [C.muted, C.surface2];
    return { fontSize: 9, padding: "2px 6px", borderRadius: 3, background: bg, color: c, fontWeight: 700, border: `1px solid ${c}33` };
  },
  pitcherBadge: (tier) => {
    const m = { elite:[C.red,"#2d0a0a"], above_avg:[C.amber,"#2d1a00"], average:[C.muted,C.surface2], below_avg:[C.teal,"#042f2e"], weak:[C.green,"#052e16"] };
    const [c,bg] = m[tier] || [C.muted, C.surface2];
    return { fontSize: 9, padding: "2px 6px", borderRadius: 3, background: bg, color: c, fontWeight: 700, border: `1px solid ${c}33` };
  },
  scoreCell:  (sc) => ({ fontWeight: 700, fontSize: 12, color: sc >= 65 ? C.green : sc >= 45 ? C.yellow : C.red }),
  formArrow:  (f)  => ({ color: f === "↑" ? C.green : f === "↓" ? C.red : C.muted, fontWeight: 700, fontSize: 13 }),
  loading:    { textAlign: "center", padding: "60px 20px", color: C.muted, fontSize: 12, letterSpacing: "1px" },
  noData:     { padding: "50px 20px", textAlign: "center", color: "#374151", fontSize: 12 },
  errorBox:   { background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
                borderRadius: 8, padding: "12px 16px", color: "#fca5a5", fontSize: 11, marginBottom: 14 },
  rankBadge:  (r) => r === 1 ? { color: "#FFD700", fontWeight: 700, fontSize: 13 }
                   : r === 2 ? { color: "#C0C0C0", fontWeight: 700, fontSize: 13 }
                   : r === 3 ? { color: "#cd7c3a", fontWeight: 700, fontSize: 13 }
                   : { color: C.muted, fontSize: 11 },
};

// ── MATCHUP TABLE ─────────────────────────────────────────────────────────────
function MatchupTable({ rows, sortKey, sortDir, onSort, bpParkData }) {
  const [expanded, setExpanded] = useState({});
  const toggle = (i) => setExpanded(prev => ({ ...prev, [i]: !prev[i] }));

  const sorted = useMemo(() => {
    const col = COLUMNS.find(c => c.key === sortKey);
    return [...rows].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (!col?.numeric) return sortDir === "asc" ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
      return sortDir === "asc" ? av - bv : bv - av;
    });
  }, [rows, sortKey, sortDir]);

  if (!rows.length) return <div style={s.noData}>No lineup data yet — posts 3-4 hrs before first pitch</div>;

  const renderCell = (col, row, i) => {
    const val = row[col.key];
    if (col.key === "_expand") return (
      <td key="_expand" style={{ ...s.td(true), width: 28, cursor: "pointer", color: C.muted, fontSize: 13, userSelect: "none" }}
        onClick={() => toggle(i)}>
        {expanded[i] ? "▼" : "▶"}
      </td>
    );
    const bg = col.numeric && METRIC_RANGES[col.key] ? getHeatColor(col.key, val) : undefined;
    return (
      <td key={col.key} style={s.td(col.sticky, bg)}>
        {col.key === "hr_score" ? (
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={s.scoreCell(val)}>{col.format(val)}</span>
            {row.pitcher_icon && <span title={row.pitcher_label} style={{ fontSize: 13 }}>{row.pitcher_icon}</span>}
          </span>
        ) : col.key === "hr_form" ? <span style={s.formArrow(val)}>{val}</span>
          : col.key === "player"  ? <span style={{ fontWeight: 600, color: "#e2e8f0" }}>{val}</span>
          : col.key === "opposing_pitcher" ? (
            <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ color: C.muted }}>{val}</span>
              {row.pitcher_tier && row.pitcher_tier !== "average" && (
                <span style={s.pitcherBadge(row.pitcher_tier)}>{row.pitcher_label?.split(" ")[0]}</span>
              )}
            </span>
          ) : col.key === "park_factor" ? (
            <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ color: C.muted, fontSize: 10 }}>{row.park_name?.split(" ").slice(-1)[0]}</span>
              <span style={s.parkBadge(row.park_tier)}>{val}</span>
            </span>
          ) : col.key === "p_throws" ? (
            <span style={{ color: val === "L" ? C.purple : C.blueLt, fontWeight: 600 }}>{val === "L" ? "LHP" : "RHP"}</span>
          ) : col.key === "split_xwoba" ? (
            <span>
              <span style={{ color: C.muted, fontSize: 9, marginRight: 4 }}>{row.split_label}</span>
              <span style={{ color: val > 0.360 ? C.green : val > 0.300 ? C.yellow : C.red }}>{col.format(val)}</span>
            </span>
          ) : <span>{col.format(val)}</span>}
      </td>
    );
  };

  return (
    <div style={s.tableWrap}>
      <table style={s.table}>
        <thead>
          <tr>
            {COLUMNS.map(col => col.key === "_expand"
              ? <th key="_expand" style={{ ...s.th(true, false), width: 28 }} />
              : <th key={col.key} style={s.th(col.sticky, sortKey === col.key)} onClick={() => onSort(col.key)}>
                  {col.label}{sortKey === col.key ? (sortDir === "asc" ? " ▲" : " ▼") : " ↕"}
                </th>
            )}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <React.Fragment key={i}>
              <tr onMouseEnter={e => e.currentTarget.style.background = "rgba(30,58,95,0.25)"}
                  onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                {COLUMNS.map(col => renderCell(col, row, i))}
              </tr>
              {expanded[i] && (
                <tr>
                  <td colSpan={COLUMNS.length} style={{ padding: 0, border: "none" }}>
                    <FactorScorecard row={row} bpParkData={bpParkData} />
                  </td>
                </tr>
              )}
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── TOP TARGETS TABLE ─────────────────────────────────────────────────────────
const TOP_COLS = [
  { key: "rank",            label: "#",           numeric: true,  format: v => v },
  { key: "player",          label: "Player",      numeric: false, format: v => v },
  { key: "team",            label: "Team",        numeric: false, format: v => v },
  { key: "game",            label: "Game",        numeric: false, format: v => v },
  { key: "park_name",       label: "Park",        numeric: false, format: v => v },
  { key: "park_factor",     label: "Park Fac.",   numeric: true,  format: v => v },
  { key: "opposing_pitcher",label: "vs Pitcher",  numeric: false, format: v => v },
  { key: "p_throws",        label: "Hand",        numeric: false, format: v => v },
  { key: "hr_score",        label: "HR Score",    numeric: true,  format: v => v.toFixed(1) },
  { key: "ceiling",         label: "Ceiling",     numeric: true,  format: v => v.toFixed(1) },
  { key: "hr_form",         label: "Form",        numeric: false, format: v => v },
  { key: "brl_bip_pct",     label: "Brl/BIP%",   numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "iso",             label: "ISO",         numeric: true,  format: v => v.toFixed(3) },
  { key: "xwoba",           label: "xwOBA",       numeric: true,  format: v => v.toFixed(3) },
  { key: "split_xwoba",     label: "Split xwOBA", numeric: true,  format: v => v.toFixed(3) },
  { key: "fb_pct",          label: "FB%",         numeric: true,  format: v => v.toFixed(1) + "%" },
  { key: "hh_pct",          label: "HH%",         numeric: true,  format: v => v.toFixed(1) + "%" },
];

function TopTargetsTable({ targets, bpParkData }) {
  const [sortKey, setSortKey] = useState("hr_score");
  const [sortDir, setSortDir] = useState("desc");
  const [expanded, setExpanded] = useState({});
  const toggle = (i) => setExpanded(prev => ({ ...prev, [i]: !prev[i] }));

  const handleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("desc"); }
  };

  const sorted = useMemo(() => {
    const col = TOP_COLS.find(c => c.key === sortKey);
    return [...targets].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (!col?.numeric) return sortDir === "asc" ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
      return sortDir === "asc" ? av - bv : bv - av;
    });
  }, [targets, sortKey, sortDir]);

  return (
    <div style={s.tableWrap}>
      <table style={s.table}>
        <thead>
          <tr>
            <th style={{ ...s.th(false, false), width: 28 }} />
            {TOP_COLS.map(col => (
              <th key={col.key} style={s.th(false, sortKey === col.key)} onClick={() => handleSort(col.key)}>
                {col.label}{sortKey === col.key ? (sortDir === "asc" ? " ▲" : " ▼") : " ↕"}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => {
            const origRank = targets.indexOf(row) + 1;
            return (
              <React.Fragment key={i}>
                <tr onMouseEnter={e => e.currentTarget.style.background = "rgba(30,58,95,0.25)"}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                  <td style={{ ...s.td(false), width: 28, cursor: "pointer", color: C.muted, fontSize: 13 }}
                    onClick={() => toggle(i)}>{expanded[i] ? "▼" : "▶"}</td>
                  {TOP_COLS.map(col => {
                    const val = col.key === "rank" ? origRank : row[col.key];
                    const bg  = col.numeric && METRIC_RANGES[col.key] ? getHeatColor(col.key, val) : undefined;
                    return (
                      <td key={col.key} style={s.td(false, bg)}>
                        {col.key === "rank" ? <span style={s.rankBadge(origRank)}>#{origRank}</span>
                          : col.key === "hr_score" ? (
                            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                              <span style={s.scoreCell(val)}>{col.format(val)}</span>
                              {row.pitcher_icon && <span style={{ fontSize: 13 }}>{row.pitcher_icon}</span>}
                            </span>
                          ) : col.key === "hr_form"  ? <span style={s.formArrow(val)}>{val}</span>
                            : col.key === "player"   ? <span style={{ fontWeight: 700, color: "#e2e8f0" }}>{val}</span>
                            : col.key === "park_factor" ? <span style={s.parkBadge(row.park_tier)}>{val}</span>
                            : col.key === "p_throws" ? <span style={{ color: val === "L" ? C.purple : C.blueLt, fontWeight: 600 }}>{val === "L" ? "LHP" : "RHP"}</span>
                            : col.key === "split_xwoba" ? (
                              <span>
                                <span style={{ color: C.muted, fontSize: 9, marginRight: 3 }}>{row.split_label}</span>
                                <span style={{ color: val > 0.360 ? C.green : val > 0.300 ? C.yellow : C.red }}>{col.format(val)}</span>
                              </span>
                            ) : col.key === "opposing_pitcher" ? (
                              <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                                <span style={{ color: C.muted }}>{val}</span>
                                {row.pitcher_tier && row.pitcher_tier !== "average" && (
                                  <span style={s.pitcherBadge(row.pitcher_tier)}>{row.pitcher_label?.split(" ")[0]}</span>
                                )}
                              </span>
                            ) : <span style={{ color: C.text }}>{col.format(val)}</span>}
                      </td>
                    );
                  })}
                </tr>
                {expanded[i] && (
                  <tr>
                    <td colSpan={TOP_COLS.length + 1} style={{ padding: 0, border: "none" }}>
                      <FactorScorecard row={row} bpParkData={bpParkData} />
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── BALLPARK PAL UPLOADER ─────────────────────────────────────────────────────
function BallparkPalUploader({ onDataLoaded }) {
  const [uploading, setUploading] = useState(false);
  const [status, setStatus]       = useState(null);
  const [preview, setPreview]     = useState(null);
  const fileRef = useRef();

  const handleFile = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setPreview(URL.createObjectURL(file));
    setUploading(true);
    setStatus(null);
    try {
      // Send image directly to our backend — avoids CORS issues with Anthropic API
      const formData = new FormData();
      formData.append("file", file);

      const response = await fetch(`${API_BASE}/parse-park-image`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || `Server error ${response.status}`);
      }

      const parsed = await response.json();

      const parkMap = {};
      (parsed.park_data || []).forEach(entry => {
        parkMap[entry.park] = { hr_mod: entry.hr_mod };
      });

      onDataLoaded(parkMap);
      setStatus({ ok: true, count: Object.keys(parkMap).length, data: parsed.park_data });
    } catch (err) {
      console.error(err);
      setStatus({ ok: false, error: err.message });
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={{ background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 18px", marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: C.blueLt, marginBottom: 3 }}>
            📊 Ballpark Pal Daily Factors
          </div>
          <div style={{ fontSize: 10, color: C.muted }}>
            Upload today's screenshot from ballparkpal.com to apply live park adjustments
          </div>
        </div>
        <input ref={fileRef} type="file" accept="image/*" style={{ display: "none" }} onChange={handleFile} />
        <button style={s.btn()} onClick={() => fileRef.current?.click()} disabled={uploading}>
          {uploading ? "Reading image..." : "📷 Upload Screenshot"}
        </button>
        {status?.ok && (
          <span style={{ fontSize: 10, color: C.green }}>
            ✓ Loaded {status.count} parks — factors applied to all scores
          </span>
        )}
        {status?.ok === false && (
          <span style={{ fontSize: 10, color: C.red }}>✗ {status.error}</span>
        )}
      </div>

      {/* Preview + extracted data */}
      {(preview || status?.ok) && (
        <div style={{ display: "flex", gap: 14, marginTop: 12, flexWrap: "wrap" }}>
          {preview && (
            <img src={preview} alt="Ballpark Pal upload"
              style={{ height: 120, borderRadius: 6, border: `1px solid ${C.border}`, objectFit: "contain", background: "#000" }} />
          )}
          {status?.ok && status.data && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", alignContent: "flex-start" }}>
              {status.data.map((d, i) => (
                <span key={i} style={{ fontSize: 10, color: d.hr_mod > 0 ? C.green : d.hr_mod < 0 ? C.red : C.muted }}>
                  {d.park}: {d.hr_mod > 0 ? "+" : ""}{d.hr_mod}%
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── MAIN APP ──────────────────────────────────────────────────────────────────
export default function App() {
  const [activeTab, setActiveTab]             = useState("targets");
  const [slate, setSlate]                     = useState([]);
  const [selectedGame, setSelectedGame]       = useState(null);
  const [homeRows, setHomeRows]               = useState([]);
  const [awayRows, setAwayRows]               = useState([]);
  const [targets, setTargets]                 = useState([]);
  const [loadingSlate, setLoadingSlate]       = useState(true);
  const [loadingMatchups, setLoadingMatchups] = useState(false);
  const [loadingTargets, setLoadingTargets]   = useState(false);
  const [sortKey, setSortKey]                 = useState("hr_score");
  const [sortDir, setSortDir]                 = useState("desc");
  const [searchName, setSearchName]           = useState("");
  const [searchResult, setSearchResult]       = useState(null);
  const [searchLoading, setSearchLoading]     = useState(false);
  const [error, setError]                     = useState(null);
  const [gameDate, setGameDate]               = useState("");
  const [bpParkData, setBpParkData]           = useState({});

  const today = new Date().toLocaleDateString("en-US", {
    weekday: "long", year: "numeric", month: "long", day: "numeric"
  });

  useEffect(() => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600;700&display=swap";
    document.head.appendChild(link);
    const style = document.createElement("style");
    style.textContent = `@keyframes spin{to{transform:rotate(360deg)}}
    body{margin:0;padding:0;background:#0a0e1a}*{box-sizing:border-box}
    ::-webkit-scrollbar{width:5px;height:5px}
    ::-webkit-scrollbar-track{background:#0a0e1a}
    ::-webkit-scrollbar-thumb{background:#1e3a5f;border-radius:3px}
    input::placeholder{color:#374151}`;
    document.head.appendChild(style);
    loadSlate();
  }, []);

  useEffect(() => {
    if (activeTab === "targets" && slate.length > 0 && targets.length === 0 && !loadingTargets) {
      loadTopTargets();
    }
  }, [activeTab, slate]);

  const loadSlate = async (d = null) => {
    setLoadingSlate(true); setError(null);
    try {
      const res  = await fetch(d ? `${API_BASE}/slate?game_date=${d}` : `${API_BASE}/slate`);
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();
      setSlate(data.games || []); setTargets([]);
    } catch (e) { setError(`Could not load slate: ${e.message}`); }
    finally { setLoadingSlate(false); }
  };

  const loadTopTargets = async () => {
    setLoadingTargets(true); setError(null);
    try {
      const url  = gameDate ? `${API_BASE}/top-targets?game_date=${gameDate}` : `${API_BASE}/top-targets`;
      const res  = await fetch(url);
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();
      setTargets(data.targets || []);
    } catch (e) { setError(`Could not load targets: ${e.message}`); }
    finally { setLoadingTargets(false); }
  };

  const loadMatchups = useCallback(async (game) => {
    setSelectedGame(game); setHomeRows([]); setAwayRows([]);
    setSearchResult(null); setLoadingMatchups(true); setError(null);
    setActiveTab("matchups");
    try {
      const res  = await fetch(`${API_BASE}/matchups?game_id=${game.game_id}`);
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      const data = await res.json();
      setHomeRows(data.home || []); setAwayRows(data.away || []);
    } catch (e) { setError(`Could not load matchups: ${e.message}`); }
    finally { setLoadingMatchups(false); }
  }, []);

  const handleSearch = async () => {
    if (!searchName.trim()) return;
    setSearchLoading(true); setSearchResult(null); setError(null);
    try {
      const res  = await fetch(`${API_BASE}/player?name=${encodeURIComponent(searchName)}`);
      if (!res.ok) throw new Error("Player not found");
      const data = await res.json();
      setSearchResult(data);
    } catch (e) { setError(e.message); }
    finally { setSearchLoading(false); }
  };

  const handleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("desc"); }
  };

  const formatGame = (g) => `${g.away_team.split(" ").pop()} @ ${g.home_team.split(" ").pop()}`;
  const parkTierLabel = (t) => ({ great:"HR PARK", good:"Good Park", neutral:"Neutral", bad:"Pitcher Friendly", pitcher:"PITCHER PARK" })[t] || t;
  const Spinner = () => (
    <div style={{ width:26,height:26,border:`3px solid ${C.border}`,borderTop:`3px solid ${C.blue}`,
                  borderRadius:"50%",animation:"spin 0.8s linear infinite",margin:"0 auto 14px" }} />
  );

  return (
    <div style={s.app}>
      {/* HEADER */}
      <header style={s.header}>
        <div style={{ display:"flex", alignItems:"center", gap:12 }}>
          <img src="/logo.png" alt="Bomb Parlays" style={{ width:48, height:48, borderRadius:8, objectFit:"cover" }} />
          <div>
            <div style={s.logoText}>Bomb Parlays Lab</div>
            <div style={s.logoSub}>Statcast HR Intelligence</div>
          </div>
        </div>
        <div style={{ fontSize:11, color:C.muted, letterSpacing:"1px" }}>{today}</div>
      </header>

      <main style={s.main}>
        {error && <div style={s.errorBox}>⚠ {error}</div>}

        {/* BALLPARK PAL UPLOADER */}
        <BallparkPalUploader onDataLoaded={setBpParkData} />

        {/* DATE + SEARCH */}
        <div style={{ display:"flex", gap:8, flexWrap:"wrap", marginBottom:14, alignItems:"center" }}>
          <span style={s.slateLabel}>Date:</span>
          <input type="date" style={{ ...s.input, maxWidth:160 }} value={gameDate} onChange={e => setGameDate(e.target.value)} />
          <button style={s.btn("secondary")} onClick={() => { loadSlate(gameDate||null); setTargets([]); }}>Load Slate</button>
          <button style={s.btn("secondary")} onClick={() => { setGameDate(""); loadSlate(); setTargets([]); }}>Today</button>
          <div style={{ flex:1 }} />
          <input style={{ ...s.input, width:220 }} placeholder="Search player name..."
            value={searchName} onChange={e => setSearchName(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleSearch()} />
          <button style={s.btn()} onClick={handleSearch} disabled={searchLoading}>
            {searchLoading ? "Searching..." : "Search"}
          </button>
        </div>

        {/* SLATE CHIPS */}
        <div style={s.slateBar}>
          <span style={s.slateLabel}>Slate:</span>
          {loadingSlate ? <span style={{ color:"#374151", fontSize:11 }}>Loading...</span>
            : slate.length === 0 ? <span style={{ color:"#374151", fontSize:11 }}>No games found</span>
            : slate.map(g => (
              <button key={g.game_id} style={s.gameChip(selectedGame?.game_id===g.game_id, g.status)} onClick={() => loadMatchups(g)}>
                {formatGame(g)}
                {g.park_tier === "great"   && <span style={{ marginLeft:4, color:C.green, fontSize:9 }}>●</span>}
                {g.park_tier === "pitcher" && <span style={{ marginLeft:4, color:C.red,   fontSize:9 }}>●</span>}
              </button>
            ))}
        </div>

        {/* TABS */}
        <div style={s.tabBar}>
          <button style={s.tab(activeTab==="targets")}  onClick={() => setActiveTab("targets")}>🎯 Top Targets</button>
          <button style={s.tab(activeTab==="matchups")} onClick={() => setActiveTab("matchups")}>⚾ Game Matchups</button>
          <button style={s.tab(activeTab==="search")}   onClick={() => setActiveTab("search")}>🔍 Player Search</button>
        </div>

        {/* TOP TARGETS */}
        {activeTab === "targets" && (
          <div>
            <div style={{ display:"flex", alignItems:"center", gap:12, marginBottom:12 }}>
              <span style={{ fontSize:10, color:C.muted, letterSpacing:"2px", textTransform:"uppercase" }}>
                Top 15 HR Targets — Full Slate
              </span>
              <button style={s.btn()} onClick={loadTopTargets} disabled={loadingTargets}>
                {loadingTargets ? "Loading..." : "↻ Refresh"}
              </button>
            </div>
            <div style={{ display:"flex", gap:14, marginBottom:12, flexWrap:"wrap" }}>
              {[["▶ Click any row to expand the 6-factor scorecard",C.blueLt],
                ["⚠ Elite pitcher — HR score downgraded",C.red],
                ["🔥 Weak pitcher — HR score boosted",C.green],
                ["● Green = HR park  ● Red = pitcher park",C.muted]].map(([l,c]) => (
                <span key={l} style={{ fontSize:10, color:c }}>{l}</span>
              ))}
            </div>
            {loadingTargets ? (
              <div style={s.loading}><Spinner /><div>Loading top targets — 60-90 seconds...</div>
                <div style={{ marginTop:8, fontSize:10, color:"#374151" }}>Pulling Statcast data for every batter on every team</div>
              </div>
            ) : targets.length === 0 ? (
              <div style={s.noData}><div style={{ fontSize:28, marginBottom:10 }}>🎯</div>
                <div>Click Refresh to load today's top HR targets</div></div>
            ) : (
              <TopTargetsTable targets={targets} bpParkData={bpParkData} />
            )}
          </div>
        )}

        {/* MATCHUPS */}
        {activeTab === "matchups" && (
          <div>
            {!selectedGame ? (
              <div style={s.noData}><div style={{ fontSize:28, marginBottom:10 }}>⚾</div>
                <div>Select a game from the slate above</div></div>
            ) : loadingMatchups ? (
              <div style={s.loading}><Spinner /><div>Loading Statcast matchup data — 30-60 seconds...</div></div>
            ) : (
              <>
                {/* Park banner */}
                <div style={{ display:"flex", gap:10, alignItems:"center", marginBottom:12,
                              padding:"8px 14px", background:C.surface2, borderRadius:6, border:`1px solid ${C.border}` }}>
                  <span style={{ fontSize:10, color:C.muted, textTransform:"uppercase", letterSpacing:"1px" }}>Park:</span>
                  <span style={{ fontSize:12, color:C.text }}>
                    {slate.find(g=>g.game_id===selectedGame.game_id)?.park_name || selectedGame.home_team}
                  </span>
                  {(() => {
                    const g = slate.find(x=>x.game_id===selectedGame.game_id);
                    if (!g) return null;
                    return (<>
                      <span style={s.parkBadge(g.park_tier)}>Factor: {g.park_factor}</span>
                      <span style={s.parkBadge(g.park_tier)}>{parkTierLabel(g.park_tier)}</span>
                    </>);
                  })()}
                  <div style={{ flex:1 }} />
                  <span style={{ fontSize:10, color:C.muted }}>▶ Expand row for factor scorecard &nbsp;|&nbsp; ⚠ Elite &nbsp; 🔥 Weak</span>
                </div>

                {/* AWAY */}
                <div style={s.sectionHeader}>
                  <span style={{ fontSize:16 }}>✈</span>
                  <span style={s.sectionLabel}>Away — {selectedGame.away_team}</span>
                  <span style={{ fontSize:10, color:C.muted, marginLeft:4 }}>vs {selectedGame.away_pitcher_name}</span>
                </div>
                <MatchupTable rows={awayRows} sortKey={sortKey} sortDir={sortDir} onSort={handleSort} bpParkData={bpParkData} />
                <div style={{ height:20 }} />

                {/* HOME */}
                <div style={s.sectionHeader}>
                  <span style={{ fontSize:16 }}>🏠</span>
                  <span style={s.sectionLabel}>Home — {selectedGame.home_team}</span>
                  <span style={{ fontSize:10, color:C.muted, marginLeft:4 }}>vs {selectedGame.home_pitcher_name}</span>
                </div>
                <MatchupTable rows={homeRows} sortKey={sortKey} sortDir={sortDir} onSort={handleSort} bpParkData={bpParkData} />
              </>
            )}
          </div>
        )}

        {/* SEARCH */}
        {activeTab === "search" && (
          <div>
            <div style={{ fontSize:10, color:C.muted, letterSpacing:"2px", textTransform:"uppercase", marginBottom:12 }}>
              Individual Player Lookup
            </div>
            {searchResult ? (
              <div style={{ background:C.surface2, border:`1px solid ${C.border}`, borderRadius:8, padding:"16px 20px" }}>
                <div style={{ fontWeight:700, fontSize:14, marginBottom:12, color:C.blueLt }}>{searchResult.name}</div>
                <div style={{ display:"flex", flexWrap:"wrap", gap:"8px 20px" }}>
                  {Object.entries(searchResult.stats)
                    .filter(([k]) => ["iso","xwoba","xwoba_con","brl_bip_pct","sweet_spot_pct",
                                      "fb_pct","hh_pct","avg_la","hr_form","hrs","swstr_pct","stand"].includes(k))
                    .map(([k,v]) => (
                      <div key={k} style={{ fontSize:11 }}>
                        <span style={{ color:C.muted, textTransform:"uppercase", letterSpacing:"1px" }}>{k}: </span>
                        <span style={{ color:"#e2e8f0", fontWeight:600 }}>{typeof v==="number" ? v.toFixed(3) : v}</span>
                      </div>
                    ))}
                </div>
              </div>
            ) : (
              <div style={s.noData}><div style={{ fontSize:28, marginBottom:10 }}>🔍</div>
                <div>Use the search bar above to look up any MLB player</div></div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
