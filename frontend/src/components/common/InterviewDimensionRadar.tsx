import React from "react";

/**
 * InterviewDimensionRadar — Shared SVG Radar / Dimension Bar Chart
 *
 * Extracted from the Phase 3G CandidatePracticeReport radar chart logic.
 * Reusable by both practice reports and official evaluation workspace.
 *
 * Rules:
 *   - Shows SVG polygon radar when 3 to 6 valid dimensions exist.
 *   - Falls back to horizontal dimension bars for fewer or more dimensions.
 *   - Includes an accessible text/table summary.
 *   - Does NOT represent personality, confidence, emotion, accent, etc.
 */

export interface DimensionEntry {
  name: string;
  score: number; // 0–5 scale
}

export interface InterviewDimensionRadarProps {
  dimensions: Record<string, number>;
  title?: string;
  maxScore?: number;
  className?: string;
}

export function InterviewDimensionRadar({
  dimensions,
  title = "Response profile",
  maxScore = 5,
  className = "",
}: InterviewDimensionRadarProps) {
  const entries: DimensionEntry[] = Object.entries(dimensions)
    .filter(([, score]) => score != null && !isNaN(score))
    .map(([name, score]) => ({ name, score }));

  if (entries.length === 0) {
    return (
      <div className={`eval-dimension-empty ${className}`}>
        No dimension scores computed yet.
      </div>
    );
  }

  const showRadar = entries.length >= 3 && entries.length <= 6;

  return (
    <div className={`eval-dimension-chart ${className}`}>
      {title && <h4 className="eval-dimension-chart-title">{title}</h4>}

      {showRadar ? (
        <RadarSVG entries={entries} maxScore={maxScore} />
      ) : (
        <>
          <div className="eval-incomplete-dimension-notice" style={{ padding: "8px 12px", background: "#f8fafc", borderRadius: "6px", marginBottom: "12px", fontSize: "12px", color: "var(--text-secondary)" }}>
            Fewer than 3 response dimensions validated. Showing available dimension breakdown.
          </div>
          <DimensionBars entries={entries} maxScore={maxScore} />
        </>
      )}

      {/* Accessible text summary table */}
      <table className="eval-dimension-table" role="table" aria-label={title}>
        <thead>
          <tr>
            <th>Dimension</th>
            <th>Score</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.name}>
              <td>{e.name}</td>
              <td>
                {e.score.toFixed(1)} / {maxScore.toFixed(1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ========== SVG Radar (3–6 dimensions) ========== */

function RadarSVG({
  entries,
  maxScore,
}: {
  entries: DimensionEntry[];
  maxScore: number;
}) {
  const size = 280;
  const center = size / 2;
  const radius = 90;
  const count = entries.length;
  const angleStep = (2 * Math.PI) / count;

  const gridLevels = [1, 2, 3, 4];

  const polygonPoints = (scaleVal: number) =>
    entries
      .map((_, i) => {
        const angle = i * angleStep - Math.PI / 2;
        const r = (scaleVal / gridLevels.length) * radius;
        return `${center + r * Math.cos(angle)},${center + r * Math.sin(angle)}`;
      })
      .join(" ");

  const dataPoints = entries.map((e, i) => {
    const angle = i * angleStep - Math.PI / 2;
    const r = (Math.min(e.score, maxScore) / maxScore) * radius;
    return {
      x: center + r * Math.cos(angle),
      y: center + r * Math.sin(angle),
      name: e.name,
      score: e.score,
    };
  });

  const dataPointsStr = dataPoints.map((p) => `${p.x},${p.y}`).join(" ");

  return (
    <div className="eval-radar-wrapper">
      <svg
        data-testid="dimension-radar-svg"
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`Dimension Radar Chart showing ${entries.map((e) => `${e.name}: ${e.score.toFixed(1)}`).join(", ")}`}
      >
        <title>Dimension Radar Chart</title>
        <desc>
          Visual polygon map of interview response dimensions.
        </desc>

        {/* Concentric grid rings */}
        {gridLevels.map((lvl) => (
          <polygon
            key={`grid-${lvl}`}
            points={polygonPoints(lvl)}
            fill={lvl === gridLevels.length ? "var(--bg-surface-subtle, #F0F2EF)" : "none"}
            stroke="var(--border-subtle, #D6DCD7)"
            strokeWidth="1"
            strokeDasharray={lvl < gridLevels.length ? "3,3" : "none"}
          />
        ))}

        {/* Axes from center to outer vertex */}
        {entries.map((_, i) => {
          const angle = i * angleStep - Math.PI / 2;
          const x = center + radius * Math.cos(angle);
          const y = center + radius * Math.sin(angle);
          return (
            <line
              key={`axis-${i}`}
              x1={center}
              y1={center}
              x2={x}
              y2={y}
              stroke="var(--border-control, #7B857F)"
              strokeWidth="1"
            />
          );
        })}

        {/* Data Polygon */}
        <polygon
          points={dataPointsStr}
          fill="rgba(23, 79, 74, 0.15)"
          stroke="var(--accent-primary, #174F4A)"
          strokeWidth="2.5"
        />

        {/* Data Points and Labels */}
        {dataPoints.map((p, i) => {
          const angle = i * angleStep - Math.PI / 2;
          const labelR = radius + 28;
          const lx = center + labelR * Math.cos(angle);
          const ly = center + labelR * Math.sin(angle);

          return (
            <g key={`point-${i}`}>
              <circle
                cx={p.x}
                cy={p.y}
                r={4.5}
                fill="var(--accent-primary, #174F4A)"
                stroke="#ffffff"
                strokeWidth="2"
              />
              <text
                x={lx}
                y={ly}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize="12"
                fontWeight="600"
                fill="var(--text-primary, #202824)"
              >
                {p.name.length > 18 ? `${p.name.slice(0, 16)}…` : p.name}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* ========== Horizontal Dimension Bars (< 3 or > 6 dimensions) ========== */

function DimensionBars({
  entries,
  maxScore,
}: {
  entries: DimensionEntry[];
  maxScore: number;
}) {
  return (
    <div className="eval-dimension-bars">
      {entries.map((e) => {
        const pct = Math.min(100, Math.max(8, (e.score / maxScore) * 100));
        return (
          <div key={e.name} className="eval-dimension-bar-row">
            <div className="eval-dimension-bar-header">
              <span className="eval-dimension-bar-label">{e.name}</span>
              <span className="eval-dimension-bar-value">
                {e.score.toFixed(1)} / {maxScore.toFixed(1)}
              </span>
            </div>
            <div className="eval-dimension-bar-track">
              <div
                className="eval-dimension-bar-fill"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
