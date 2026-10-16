import { useEffect, useState } from "react";
import { listJobs } from "../api/jobs";
import type { JobListItem, JobSkillRow } from "../types";
import { useJobSkills } from "./useJobSkills";

/**
 * The "compare to a job" state the evidence map (#136) and the area map (#320) share: whether
 * the toggle is on, the saved jobs to pick from (listed the first time it goes on), the job
 * picked (the one open in the workspace, else the first), and its requirements.
 */
export function useJobCompare(defaultJobId: string | null) {
  const [jobOn, setJobOn] = useState(false);
  const [jobs, setJobs] = useState<JobListItem[] | null>(null);
  const [jobId, setJobId] = useState<string | null>(defaultJobId);
  const [jobListError, setJobListError] = useState<string | null>(null);
  const { skills: jobSkills, error: jobSkillsError } = useJobSkills(jobOn ? jobId : null);

  useEffect(() => {
    if (!jobOn || jobs !== null) return;
    listJobs().then((list) => {
      setJobs(list);
      setJobId((cur) => (cur && list.some((j) => j.job_id === cur) ? cur : list[0]?.job_id ?? null));
    }).catch((e) => setJobListError(e instanceof Error ? e.message : "Failed to load jobs"));
  }, [jobOn, jobs]);

  return {
    jobOn, setJobOn, jobs, jobId, setJobId,
    jobSkills: jobSkills as JobSkillRow[] | null,
    jobError: jobListError ?? jobSkillsError,
  };
}

export type JobCompare = ReturnType<typeof useJobCompare>;
