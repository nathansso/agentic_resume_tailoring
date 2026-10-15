import { useEffect, useState } from "react";
import { getJobSkills } from "../api/jobs";
import type { JobSkillRow } from "../types";

/**
 * The skills a job asks for, for the evidence map's job overlay and the bullet picker
 * (issues #136, #319). `skills` is null until loaded, and while `jobId` is null; an
 * answer for a job the user has since left is dropped.
 */
export function useJobSkills(jobId: string | null): { skills: JobSkillRow[] | null; error: string | null } {
  const [skills, setSkills] = useState<JobSkillRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    if (!jobId) { setSkills(null); return; }
    let live = true;
    getJobSkills(jobId)
      .then((rows) => { if (live) setSkills(rows); })
      .catch((e) => {
        if (live) { setSkills(null); setError(e instanceof Error ? e.message : "Failed to load the job's skills"); }
      });
    return () => { live = false; };
  }, [jobId]);

  return { skills, error };
}
