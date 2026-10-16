import { useCallback, useEffect, useState } from "react";
import { getDismissedSkills, getEvidenceView, getProjectContexts } from "../api/profile";
import type { DismissedSkill, EvidenceView, ProjectContext } from "../types";

/**
 * What the evidence map (#136) and the area map (#320) both read from the server: the view,
 * the skills the user dismissed (#321) and where each project was done (#322). `reload`
 * re-reads all three, after the panel saves an edit.
 */
export function useEvidenceData(failure = "Failed to load the evidence map") {
  const [view, setView] = useState<EvidenceView | null>(null);
  const [dismissed, setDismissed] = useState<DismissedSkill[]>([]);
  const [contexts, setContexts] = useState<ProjectContext[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [next, gone, places] = await Promise.all([getEvidenceView(), getDismissedSkills(), getProjectContexts()]);
      setView(next);
      setDismissed(gone);
      setContexts(places);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : failure);
    }
  }, [failure]);

  useEffect(() => { void reload(); }, [reload]);

  return { view, dismissed, contexts, error, reload };
}
