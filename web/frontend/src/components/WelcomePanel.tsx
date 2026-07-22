import { FileUp, Github, Database, MessageSquare } from "lucide-react";

interface Props {
  onViewChange: (view: string) => void;
}

const CTAS = [
  { label: "Ingest Resume", view: "ingest", desc: "Upload a PDF, DOCX, or Markdown resume", icon: FileUp },
  { label: "Connect GitHub", view: "ingest", desc: "Pull in repos and extract skills", icon: Github },
  { label: "Browse Data", view: "data", desc: "View your skills, experiences, and projects", icon: Database },
  { label: "Open Chat", view: "chat", desc: "Start chatting without a job selected", icon: MessageSquare },
];

export function WelcomePanel({ onViewChange }: Props) {
  return (
    <div className="flex h-full flex-col items-center justify-center p-8">
      <h1 className="text-4xl font-extrabold tracking-tight">
        Welcome to <span className="text-accent">ARTie</span>
      </h1>
      <p className="mt-3 max-w-[46ch] text-center text-muted-foreground">
        Create a job in the sidebar to get started, or use the actions below to
        build your profile.
      </p>

      <div className="mt-8 grid w-full max-w-3xl gap-3 sm:grid-cols-2">
        {CTAS.map(cta => (
          <button
            key={cta.label}
            className="group flex items-start gap-3 rounded-lg border border-border bg-card p-4 text-left transition-colors hover:border-primary/50 hover:bg-elevated"
            onClick={() => onViewChange(cta.view)}
          >
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-accent/10 text-accent">
              <cta.icon className="h-4 w-4" />
            </span>
            <span className="flex flex-col gap-0.5">
              <span className="text-sm font-semibold">{cta.label}</span>
              <span className="text-xs text-muted-foreground">{cta.desc}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
