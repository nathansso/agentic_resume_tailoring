import type { JobDetail } from "../types";

export type WelcomeJob = Pick<
  JobDetail,
  "status" | "description" | "retailor_count" | "retailor_limit"
>;

/** Job-scoped chat welcome, shown when a job chat opens with no history.
 *  Suggests the next useful action for the job's current state. */
export function jobWelcome(job: WelcomeJob): string {
  const runsLeft = Math.max(0, job.retailor_limit - job.retailor_count);
  const runsWord = runsLeft === 1 ? "run" : "runs";

  if (job.status === "created" && !job.description) {
    return (
      "This chat is scoped to this job.\n\n" +
      "Paste the job description in the panel on the right to analyze and tailor your resume automatically."
    );
  }
  if (job.status === "created" || job.status === "analyzed") {
    return (
      "This chat is scoped to this job.\n\n" +
      "Say \"tailor\" to generate a resume tailored to this job description, " +
      "or ask things like \"what skills am I missing?\"."
    );
  }
  // tailored / exported
  if (runsLeft > 0) {
    return (
      "Your tailored resume is ready — the LaTeX source and compiled preview are on the right.\n\n" +
      `Want changes? Tell the chat, e.g. "tailor emphasize Python more" (${runsLeft} ${runsWord} left).\n\n` +
      "You can also ask \"what skills am I missing?\" or edit the LaTeX source directly."
    );
  }
  return (
    "Your tailored resume is ready — the LaTeX source and compiled preview are on the right.\n\n" +
    `Re-tailor budget used (${job.retailor_count}/${job.retailor_limit}), ` +
    "but you can still edit the LaTeX source directly or ask questions about this job."
  );
}
