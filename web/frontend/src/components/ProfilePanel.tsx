import { useState, useEffect, type ChangeEvent } from "react";
import type { ProfileData } from "../types";
import { cn } from "../lib/utils";
import { getProfile, updateProfile } from "../api/profile";
import { getGithubStatus, disconnectGithub } from "../api/auth";
import { ProgressBar } from "./ProgressBar";

const btnGhost =
  "rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-60";
const btnPrimary =
  "rounded-md bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60";

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

  if (loading) {
    return (
      <div className="max-w-[60ch] p-6">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  const fields: { key: keyof ProfileData; label: string }[] = [
    { key: "name", label: "Name" },
    { key: "email", label: "Email" },
    { key: "phone", label: "Phone" },
    { key: "location", label: "Location" },
    { key: "linkedin_url", label: "LinkedIn URL" },
  ];

  const messageIsError = !!message && (message.includes("fail") || message.includes("Error"));

  return (
    <div className="max-w-[60ch] p-6">
      <div className="mb-5 flex items-center justify-between">
        <h2 className="text-xl font-bold tracking-tight">Profile</h2>
        {!editing ? (
          <button className={btnGhost} onClick={() => { setEditing(true); setMessage(null); }}>
            Edit
          </button>
        ) : (
          <div className="flex gap-2">
            <button className={btnPrimary} onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              className={btnGhost}
              onClick={() => { setEditing(false); setForm(profile ?? {}); setMessage(null); }}
            >
              Cancel
            </button>
          </div>
        )}
      </div>

      {loadError && <p className="text-sm text-destructive">{loadError}</p>}
      {message && (
        <p className={cn("text-sm", messageIsError ? "text-destructive" : "text-success")}>
          {message}
        </p>
      )}

      <div className="mb-6 flex flex-col gap-2.5">
        {fields.map(({ key, label }) => (
          <div key={key} className="grid grid-cols-[14ch_1fr] items-center gap-3">
            <span className="text-sm text-muted-foreground">{label}</span>
            {editing ? (
              <input
                className="rounded-md border border-input bg-background px-2 py-1.5 outline-none transition-colors focus:border-primary"
                value={(form[key] as string) ?? ""}
                onChange={onChange(key)}
              />
            ) : (
              <span>
                {(form[key] as string) || <span className="text-muted-foreground">—</span>}
              </span>
            )}
          </div>
        ))}
      </div>

      {profile?.linkedin_ingest_status === "importing" && (
        <div className="my-3">
          <ProgressBar label="LinkedIn import in progress…" showElapsed={false} />
        </div>
      )}
      {profile?.linkedin_ingest_status === "done" && (
        <p className="text-sm text-success">LinkedIn profile imported.</p>
      )}
      {profile?.linkedin_ingest_status === "failed" && (
        <p className="text-sm text-destructive">
          LinkedIn import failed{profile.linkedin_ingest_error ? `: ${profile.linkedin_ingest_error}` : ""}.
        </p>
      )}

      {profile && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>{profile.skills} skills</span>
          <span className="text-muted-foreground">·</span>
          <span>{profile.experiences} experiences</span>
          <span className="text-muted-foreground">·</span>
          <span>{profile.projects} projects</span>
          {profile.sources.length > 0 && (
            <>
              <span className="text-muted-foreground">·</span>
              <span className="text-muted-foreground">
                Sources: {profile.sources.join(", ")}
              </span>
            </>
          )}
        </div>
      )}

      {oauthConfigured && (
        <div className="mt-5 border-t border-border pt-4">
          <p className="mb-2 text-sm font-semibold text-muted-foreground">GitHub</p>
          {githubConnected ? (
            <div className="flex items-center gap-3">
              <span className="text-sm text-success">
                {githubUsername ? `@${githubUsername}` : "Connected"}
              </span>
              <button
                className={btnGhost}
                onClick={handleGithubDisconnect}
                disabled={githubWorking}
              >
                {githubWorking ? "Disconnecting…" : "Disconnect"}
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <span className="text-sm text-muted-foreground">Not connected</span>
              <a href="/api/auth/github" className={cn(btnPrimary, "inline-block no-underline")}>
                Connect GitHub
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
