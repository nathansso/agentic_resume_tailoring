import { useState, useEffect, type CSSProperties } from "react";
import type { SkillRow, ExpRow, ProjectRow, GraphData } from "../types";
import { colors, font } from "../theme";
import { getSkills, getExperiences, getProjects, getGraph } from "../api/profile";

type Tab = "skills" | "experiences" | "projects" | "graph";

export function DataExplorer() {
  const [tab, setTab] = useState<Tab>("skills");
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [exps, setExps] = useState<ExpRow[]>([]);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([getSkills(), getExperiences(), getProjects(), getGraph()])
      .then(([s, e, p, g]) => { setSkills(s); setExps(e); setProjects(p); setGraph(g); })
      .catch(err => setError(err instanceof Error ? err.message : "Failed to load data"))
      .finally(() => setLoading(false));
  }, []);

  const tabs: { key: Tab; label: string }[] = [
    { key: "skills", label: `Skills (${skills.length})` },
    { key: "experiences", label: `Experiences (${exps.length})` },
    { key: "projects", label: `Projects (${projects.length})` },
    { key: "graph", label: "Graph" },
  ];

  return (
    <div style={s.panel}>
      <div style={s.tabStrip}>
        {tabs.map(({ key, label }) => (
          <button
            key={key}
            style={{ ...s.tabBtn, ...(tab === key ? s.tabBtnActive : {}) }}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div style={s.content}>
        {loading && <p style={s.muted}>Loading…</p>}
        {error && <p style={{ ...s.muted, color: colors.error }}>{error}</p>}
        {!loading && !error && tab === "skills" && <SkillsTab skills={skills} />}
        {!loading && !error && tab === "experiences" && <ExpsTab exps={exps} />}
        {!loading && !error && tab === "projects" && <ProjectsTab projects={projects} />}
        {!loading && !error && tab === "graph" && graph && <GraphTab graph={graph} />}
      </div>
    </div>
  );
}

function SkillsTab({ skills }: { skills: SkillRow[] }) {
  if (skills.length === 0) return <p style={sInner.empty}>No skills yet — ingest your resume to get started.</p>;

  const byCategory: Record<string, SkillRow[]> = {};
  for (const sk of skills) {
    const cat = sk.category || "Uncategorized";
    (byCategory[cat] = byCategory[cat] ?? []).push(sk);
  }

  return (
    <div style={sInner.skillsRoot}>
      {Object.keys(byCategory).sort().map(cat => (
        <div key={cat} style={sInner.catBlock}>
          <div style={sInner.catHeader}>{cat} <span style={sInner.catCount}>({byCategory[cat].length})</span></div>
          <div style={sInner.skillGrid}>
            {byCategory[cat].sort((a, b) => a.name.localeCompare(b.name)).map(sk => (
              <div key={sk.name} style={sInner.skillChip}>
                <span style={sInner.skillName}>{sk.name}</span>
                {sk.proficiency !== "N/A" && <span style={sInner.skillMeta}>lvl {sk.proficiency}</span>}
                <div style={sInner.confBarBg}>
                  <div style={{ ...sInner.confBarFill, width: `${Math.min(parseFloat(sk.confidence) * 10, 100)}%` }} />
                </div>
                {sk.source && <span style={sInner.skillSource}>{sk.source}</span>}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ExpsTab({ exps }: { exps: ExpRow[] }) {
  if (exps.length === 0) return <p style={sInner.empty}>No experiences yet — ingest your resume to get started.</p>;
  return (
    <div style={sInner.table}>
      <div style={sInner.tableHead}>
        <span>Title</span><span>Company</span><span>Start</span><span>End</span>
      </div>
      {exps.map((e, i) => (
        <div key={i} style={{ ...sInner.tableRow, background: i % 2 === 0 ? colors.surface : colors.boost }}>
          <span>{e.title}</span>
          <span style={{ color: colors.textMuted }}>{e.company}</span>
          <span style={{ color: colors.textMuted }}>{e.start}</span>
          <span style={{ color: colors.textMuted }}>{e.end}</span>
        </div>
      ))}
    </div>
  );
}

function ProjectsTab({ projects }: { projects: ProjectRow[] }) {
  if (projects.length === 0) return <p style={sInner.empty}>No projects yet — ingest GitHub repos to add projects.</p>;
  return (
    <div style={sInner.projGrid}>
      {projects.map((p, i) => (
        <div key={i} style={sInner.projCard}>
          <span style={sInner.projName}>{p.name}</span>
          {p.url !== "—" && (
            <a href={p.url} target="_blank" rel="noreferrer" style={sInner.projUrl}>{p.url}</a>
          )}
          {p.desc && <span style={sInner.projDesc}>{p.desc}</span>}
        </div>
      ))}
    </div>
  );
}

function GraphTab({ graph }: { graph: GraphData }) {
  return (
    <div style={sInner.graphRoot}>
      <section style={sInner.graphSection}>
        <h3 style={sInner.graphHeading}>By Category</h3>
        <ul style={sInner.list}>
          {Object.entries(graph.by_category).sort((a, b) => b[1] - a[1]).map(([cat, count]) => (
            <li key={cat} style={sInner.listItem}>
              <span style={sInner.graphCat}>{cat}</span>
              <span style={sInner.graphCount}>{count}</span>
            </li>
          ))}
        </ul>
      </section>

      <section style={sInner.graphSection}>
        <h3 style={sInner.graphHeading}>Top Skills by Connections</h3>
        <ul style={sInner.list}>
          {graph.top_skills.map(sk => (
            <li key={sk.name} style={sInner.listItem}>
              <span style={sInner.graphSkill}>{sk.name}</span>
              <span style={sInner.graphCount}>{sk.connections} connections</span>
            </li>
          ))}
        </ul>
      </section>

      {Object.keys(graph.evidence).length > 0 && (
        <section style={sInner.graphSection}>
          <h3 style={sInner.graphHeading}>Evidence (top skills)</h3>
          <ul style={sInner.list}>
            {Object.entries(graph.evidence).map(([skill, sources]) => (
              <li key={skill} style={{ ...sInner.listItem, flexDirection: "column", alignItems: "flex-start", gap: "0.125rem" }}>
                <span style={sInner.graphSkill}>{skill}</span>
                <ul style={{ ...sInner.list, paddingLeft: "1rem", marginTop: 0 }}>
                  {sources.map((src, i) => (
                    <li key={i} style={{ ...sInner.listItem, color: colors.textMuted, fontSize: font.size.sm }}>→ {src}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  panel: { display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" },
  tabStrip: {
    display: "flex",
    borderBottom: `1px solid ${colors.primary}`,
    background: colors.surface,
    flexShrink: 0,
    padding: "0 0.75rem",
    gap: "0.125rem",
  },
  tabBtn: {
    background: "transparent", border: "none", borderBottom: "2px solid transparent",
    color: colors.textMuted, fontSize: font.size.sm, padding: "0.5rem 0.75rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  tabBtnActive: { color: colors.accent, borderBottomColor: colors.accent },
  content: { flex: 1, overflowY: "auto", padding: "1rem" },
  muted: { color: colors.textMuted, fontSize: font.size.sm },
};

const sInner: Record<string, CSSProperties> = {
  empty: { color: colors.textMuted, fontSize: font.size.sm, margin: 0 },
  skillsRoot: { display: "flex", flexDirection: "column", gap: "1.25rem" },
  catBlock: {},
  catHeader: { fontWeight: 700, color: colors.accent, fontSize: font.size.sm, marginBottom: "0.5rem" },
  catCount: { color: colors.textMuted, fontWeight: 400 },
  skillGrid: { display: "flex", flexWrap: "wrap", gap: "0.5rem" },
  skillChip: {
    background: colors.surface, border: `1px solid ${colors.primary}`,
    padding: "0.375rem 0.625rem", display: "flex", flexDirection: "column", gap: "0.2rem", minWidth: "10ch",
  },
  skillName: { color: colors.text, fontSize: font.size.sm, fontWeight: 600 },
  skillMeta: { color: colors.textMuted, fontSize: "0.7rem" },
  skillSource: { color: colors.textMuted, fontSize: "0.7rem" },
  confBarBg: { height: "2px", background: colors.primary, width: "100%" },
  confBarFill: { height: "2px", background: colors.accent },
  table: { display: "flex", flexDirection: "column", gap: 0 },
  tableHead: {
    display: "grid", gridTemplateColumns: "2fr 1.5fr 1fr 1fr",
    padding: "0.375rem 0.75rem", fontWeight: 700,
    color: colors.textMuted, fontSize: font.size.sm,
    borderBottom: `1px solid ${colors.primary}`,
  },
  tableRow: {
    display: "grid", gridTemplateColumns: "2fr 1.5fr 1fr 1fr",
    padding: "0.375rem 0.75rem", fontSize: font.size.sm,
  },
  projGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(28ch, 1fr))", gap: "0.75rem" },
  projCard: {
    display: "flex", flexDirection: "column", gap: "0.25rem",
    border: `1px solid ${colors.primary}`, padding: "0.75rem",
    background: colors.surface,
  },
  projName: { fontWeight: 700, color: colors.text, fontSize: font.size.sm },
  projUrl: { color: colors.accent, fontSize: "0.7rem", wordBreak: "break-all" },
  projDesc: { color: colors.textMuted, fontSize: font.size.sm },
  graphRoot: { display: "flex", flexDirection: "column", gap: "1.5rem" },
  graphSection: {},
  graphHeading: { margin: "0 0 0.5rem", color: colors.accent, fontSize: font.size.base, fontWeight: 700 },
  list: { listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "0.25rem" },
  listItem: { display: "flex", alignItems: "center", gap: "0.75rem", fontSize: font.size.sm, paddingLeft: "0.5rem", borderLeft: `1px solid ${colors.primary}` },
  graphCat: { color: colors.text, flex: 1 },
  graphSkill: { color: colors.accent, flex: 1 },
  graphCount: { color: colors.textMuted },
};
