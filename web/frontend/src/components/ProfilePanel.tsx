import { useState, useEffect, type CSSProperties, type ChangeEvent } from "react";
import type { ProfileData } from "../types";
import { colors, font } from "../theme";
import { getProfile, updateProfile } from "../api/profile";
import { getGithubStatus, disconnectGithub } from "../api/auth";
import { ProgressBar } from "./ProgressBar";

export function ProfilePanel() {
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Partial<ProfileData>>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [githubConnected, setGithubConnected] = useState(false);
  const [oauthConfigured, setOauthConfigured] = useState(false);
  const [githubWorking, setGithubWorking] = useState(false);
  const [githubUsername, setGithubUsername] = useState<string | null>(null);

  useEffect(() => {
    getProfile()
      .then(p => { setProfile(p); setForm(p); })
      .catch(e => setLoadError(e instanceof Error ? e.message : "Failed to load profile"))
      .finally(() => setLoading(false));
    getGithubStatus()
      .then(s => { setGithubConnected(s.connected); setOauthConfigured(s.oauth_configured); setGithubUsername(s.github_username); })
      .catch(() => {});
  }, []);

  // Poll while a background LinkedIn import is running so status updates live.
  useEffect(() => {
    if (profile?.linkedin_ingest_status !== "importing") return;
    const id = setInterval(() => {
      getProfile()
        .then(p => { setProfile(p); if (!editing) setForm(p); })
        .catch(() => {});
    }, 4000);
    return () => clearInterval(id);
  }, [profile?.linkedin_ingest_status, editing]);

  function onChange(field: keyof ProfileData) {
    return (e: ChangeEvent<HTMLInputElement>) =>
      setForm(prev => ({ ...prev, [field]: e.target.value }));
  }

  async function handleSave() {
    setSaving(true);
    setMessage(null);
    try {
      await updateProfile(form);
      const updated = await getProfile();
      setProfile(updated);
      setForm(updated);
      setEditing(false);
      setMessage("Profile updated.");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleGithubDisconnect() {
    setGithubWorking(true);
    try {
      await disconnectGithub();
      setGithubConnected(false);
      setGithubUsername(null);
      setMessage("GitHub disconnected.");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Disconnect failed");
    } finally {
      setGithubWorking(false);
    }
  }

  if (loading) return <div style={s.page}><p style={s.muted}>Loading…</p></div>;

  const fields: { key: keyof ProfileData; label: string }[] = [
    { key: "name", label: "Name" },
    { key: "email", label: "Email" },
    { key: "phone", label: "Phone" },
    { key: "location", label: "Location" },
    { key: "linkedin_url", label: "LinkedIn URL" },
  ];

  return (
    <div style={s.page}>
      <div style={s.header}>
        <h2 style={s.title}>Profile</h2>
        {!editing ? (
          <button style={s.editBtn} onClick={() => { setEditing(true); setMessage(null); }}>Edit</button>
        ) : (
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button style={s.saveBtn} onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button style={s.cancelBtn} onClick={() => { setEditing(false); setForm(profile ?? {}); setMessage(null); }}>
              Cancel
            </button>
          </div>
        )}
      </div>

      {loadError && <p style={{ ...s.muted, color: colors.error }}>{loadError}</p>}
      {message && <p style={{ ...s.muted, color: message.includes("fail") || message.includes("Error") ? colors.error : colors.accent }}>{message}</p>}

      <div style={s.grid}>
        {fields.map(({ key, label }) => (
          <div key={key} style={s.field}>
            <span style={s.label}>{label}</span>
            {editing ? (
              <input
                style={s.input}
                value={(form[key] as string) ?? ""}
                onChange={onChange(key)}
              />
            ) : (
              <span style={s.value}>{(form[key] as string) || <span style={{ color: colors.textMuted }}>—</span>}</span>
            )}
          </div>
        ))}
      </div>

      {profile?.linkedin_ingest_status === "importing" && (
        <div style={{ margin: "0.75rem 0" }}>
          <ProgressBar label="LinkedIn import in progress…" showElapsed={false} />
        </div>
      )}
      {profile?.linkedin_ingest_status === "done" && (
        <p style={{ ...s.muted, color: colors.accent }}>LinkedIn profile imported.</p>
      )}
      {profile?.linkedin_ingest_status === "failed" && (
        <p style={{ ...s.muted, color: colors.error }}>
          LinkedIn import failed{profile.linkedin_ingest_error ? `: ${profile.linkedin_ingest_error}` : ""}.
        </p>
      )}

      {profile && (
        <div style={s.stats}>
          <span style={s.statChip}>{profile.skills} skills</span>
          <span style={s.statDot}>·</span>
          <span style={s.statChip}>{profile.experiences} experiences</span>
          <span style={s.statDot}>·</span>
          <span style={s.statChip}>{profile.projects} projects</span>
          {profile.sources.length > 0 && (
            <>
              <span style={s.statDot}>·</span>
              <span style={{ ...s.statChip, color: colors.textMuted }}>
                Sources: {profile.sources.join(", ")}
              </span>
            </>
          )}
        </div>
      )}

      {oauthConfigured && (
        <div style={s.githubSection}>
          <p style={s.githubLabel}>GitHub</p>
          {githubConnected ? (
            <div style={s.githubRow}>
              <span style={{ ...s.muted, color: colors.accent }}>
                {githubUsername ? `@${githubUsername}` : "Connected"}
              </span>
              <button
                style={{ ...s.cancelBtn, marginLeft: "1rem" }}
                onClick={handleGithubDisconnect}
                disabled={githubWorking}
              >
                {githubWorking ? "Disconnecting…" : "Disconnect"}
              </button>
            </div>
          ) : (
            <div style={s.githubRow}>
              <span style={s.muted}>Not connected</span>
              <a href="/api/auth/github" style={s.connectBtn}>
                Connect GitHub
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  page: { padding: "1.5rem", maxWidth: "60ch" },
  header: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.25rem" },
  title: { margin: 0, color: colors.accent, fontSize: font.size.xl, fontWeight: 700 },
  editBtn: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.sm, padding: "0.25rem 0.75rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  saveBtn: {
    background: colors.accent, border: "none", color: colors.background,
    fontWeight: 700, fontSize: font.size.sm, padding: "0.25rem 0.75rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  cancelBtn: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.sm, padding: "0.25rem 0.75rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  grid: { display: "flex", flexDirection: "column", gap: "0.625rem", marginBottom: "1.5rem" },
  field: { display: "grid", gridTemplateColumns: "14ch 1fr", alignItems: "center", gap: "0.75rem" },
  label: { color: colors.textMuted, fontSize: font.size.sm },
  value: { color: colors.text, fontSize: font.size.base },
  input: {
    background: colors.background, border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.base, padding: "0.25rem 0.5rem",
    fontFamily: "inherit", outline: "none", borderRadius: 0,
  },
  stats: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" },
  statChip: { color: colors.text, fontSize: font.size.sm },
  statDot: { color: colors.textMuted },
  muted: { color: colors.textMuted, fontSize: font.size.sm },
  githubSection: { marginTop: "1.25rem", borderTop: `1px solid ${colors.primary}`, paddingTop: "1rem" },
  githubLabel: { margin: "0 0 0.5rem", color: colors.textMuted, fontSize: font.size.sm, fontWeight: 600 },
  githubRow: { display: "flex", alignItems: "center", gap: "0.75rem" },
  connectBtn: {
    background: colors.accent, border: "none", color: colors.background,
    fontWeight: 700, fontSize: font.size.sm, padding: "0.25rem 0.75rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
    textDecoration: "none", display: "inline-block",
  },
};
