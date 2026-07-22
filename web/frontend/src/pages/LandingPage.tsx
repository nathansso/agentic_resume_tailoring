import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import {
  ArrowRight,
  Check,
  FileText,
  Github,
  Layers,
  Linkedin,
  ListChecks,
  ShieldCheck,
  Sparkles,
  Target,
} from "lucide-react";
import { ThemeToggle } from "../components/ThemeToggle";

/**
 * Public marketing page shown at `/` to signed-out visitors (issue #83).
 *
 * Copy rule inherited from the root CLAUDE.md: never describe a capability the
 * product does not actually expose. Anything still on the roadmap is phrased as
 * a design principle ("grounded in what you did"), never as a shipped guarantee.
 */
export function LandingPage() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <SiteNav />
      <main>
        <Hero />
        <Audience />
        <HowItWorks />
        <NoFabrication />
        <ClosingCta />
      </main>
      <SiteFooter />
    </div>
  );
}

/* ------------------------------------------------------------------ nav -- */

function SiteNav() {
  return (
    <header className="sticky top-0 z-50 border-b border-border/60 bg-background/80 backdrop-blur-md">
      <nav className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <Link to="/" className="flex items-center gap-2 font-bold tracking-tight">
          <span className="grid h-8 w-8 place-items-center rounded-md bg-primary text-primary-foreground">
            A
          </span>
          <span className="text-lg">ARTie</span>
        </Link>

        <div className="hidden items-center gap-8 text-sm text-muted-foreground md:flex">
          <a href="#how-it-works" className="transition-colors hover:text-foreground">
            How it works
          </a>
          <a href="#grounded" className="transition-colors hover:text-foreground">
            Why trust it
          </a>
        </div>

        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Link
            to="/login"
            className="rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            Sign in
          </Link>
          <Link
            to="/register"
            className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90"
          >
            Get started
          </Link>
        </div>
      </nav>
    </header>
  );
}

/* ----------------------------------------------------------------- hero -- */

function Hero() {
  return (
    <section className="relative overflow-hidden border-b border-border/60">
      <div className="bg-grid absolute inset-0 [mask-image:radial-gradient(ellipse_at_top,black,transparent_72%)]" />
      <div className="absolute left-1/2 top-0 h-[420px] w-[820px] -translate-x-1/2 rounded-full bg-primary/15 blur-[130px]" />

      <div className="relative mx-auto max-w-4xl px-6 py-24 text-center sm:py-32">
        <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card/70 px-4 py-1.5 text-xs font-medium text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5 text-accent" />
          For students and new grads chasing internships
        </span>

        <h1 className="mt-8 text-5xl font-extrabold leading-[1.05] tracking-tight sm:text-6xl md:text-7xl">
          Land your first role
          <br />
          <span className="text-gradient">with ARTie</span>
        </h1>

        <p className="mx-auto mt-6 max-w-2xl text-lg text-muted-foreground sm:text-xl">
          Don't know how to write a resume? We do. ARTie learns what you've
          actually built — then rewrites your resume for every single job you
          apply to.
        </p>

        <div className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            to="/register"
            className="group inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-7 py-3.5 font-semibold text-primary-foreground transition-opacity hover:opacity-90 sm:w-auto"
          >
            Build my resume
            <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
          </Link>
          <a
            href="#how-it-works"
            className="inline-flex w-full items-center justify-center rounded-lg border border-border bg-card px-7 py-3.5 font-semibold transition-colors hover:bg-elevated sm:w-auto"
          >
            See how it works
          </a>
        </div>

        <p className="mt-6 text-sm text-muted-foreground">
          Free to start · No resume required to sign up
        </p>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------- audience -- */

const PAINS = [
  {
    title: "You have no idea what \"good\" looks like",
    body: "Nobody teaches this. You've seen ten conflicting templates and none of them tell you what actually belongs on the page.",
  },
  {
    title: "Your experience feels too small to write down",
    body: "Coursework, side projects, a part-time job, a hackathon. It counts — it's just phrased like a diary instead of a resume.",
  },
  {
    title: "One resume, eighty applications",
    body: "Tailoring each one by hand is the advice everyone gives and nobody follows, because it takes an hour per job.",
  },
];

function Audience() {
  return (
    <section className="border-b border-border/60 py-24">
      <div className="mx-auto max-w-6xl px-6">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
            Writing your first real resume is hard
          </h2>
          <p className="mt-4 text-lg text-muted-foreground">
            Not because you haven't done anything. Because turning what you've
            done into what a recruiter scans for is a separate skill entirely.
          </p>
        </div>

        <div className="mt-16 grid gap-6 md:grid-cols-3">
          {PAINS.map((p) => (
            <div
              key={p.title}
              className="rounded-lg border border-border bg-card p-7 transition-colors hover:border-primary/40"
            >
              <h3 className="font-semibold leading-snug">{p.title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                {p.body}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* --------------------------------------------------------- how it works -- */

const STEPS = [
  {
    icon: Layers,
    label: "Understand you",
    title: "Everything you've done, in one place",
    body: "Upload a resume, connect GitHub, link LinkedIn. ARTie pulls your roles, projects, and skills into a knowledge graph — a single structured picture of your experience that every future application draws from.",
    points: ["Resume, GitHub, and LinkedIn ingestion", "Editable — you correct anything it got wrong"],
  },
  {
    icon: Target,
    label: "Understand the job",
    title: "Read the posting properly, once",
    body: "Each job description gets broken down into what the role actually requires, separated from the boilerplate. That structured read is what the rest of the process aims at.",
    points: ["Requirements pulled out of the noise", "Saved per job, so re-tailoring is instant"],
  },
  {
    icon: ListChecks,
    label: "Plan the edits",
    title: "Decide what to change — and what to leave alone",
    body: "Instead of regenerating your resume from scratch, ARTie plans specific edits: which bullets to rewrite, which projects to surface, how to rank your skills, what order the sections go in. Work that already landed well is kept.",
    points: ["Targeted edits, not a blank-page rewrite", "Section and project ordering by relevance"],
  },
  {
    icon: ShieldCheck,
    label: "Check and score",
    title: "Measure it, then improve the next one",
    body: "Every version is scored on how well it covers what the job asked for, and checked against your real history before you see it. Those scores tell ARTie which editing strategies actually work, so the system gets better with use.",
    points: ["Keyword and requirement coverage scoring", "Export to PDF or LaTeX when you're happy"],
  },
];

function HowItWorks() {
  return (
    <section id="how-it-works" className="border-b border-border/60 py-24">
      <div className="mx-auto max-w-6xl px-6">
        <div className="mx-auto max-w-2xl text-center">
          <span className="text-sm font-semibold uppercase tracking-widest text-accent">
            The method
          </span>
          <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">
            How ARTie tailors a resume
          </h2>
          <p className="mt-4 text-lg text-muted-foreground">
            Four steps. Not one big prompt that hopes for the best.
          </p>
        </div>

        <ol className="mt-16 space-y-5">
          {STEPS.map((step, i) => (
            <li
              key={step.label}
              className="grid gap-6 rounded-lg border border-border bg-card p-8 md:grid-cols-[10rem_1fr] md:gap-10"
            >
              <div className="flex items-center gap-4 md:flex-col md:items-start md:gap-3">
                <div className="grid h-12 w-12 shrink-0 place-items-center rounded-lg bg-accent/10 text-accent">
                  <step.icon className="h-6 w-6" />
                </div>
                <div className="md:mt-1">
                  <div className="font-mono text-xs text-muted-foreground">
                    Step {i + 1}
                  </div>
                  <div className="text-sm font-semibold text-accent">
                    {step.label}
                  </div>
                </div>
              </div>

              <div>
                <h3 className="text-xl font-semibold tracking-tight">
                  {step.title}
                </h3>
                <p className="mt-3 leading-relaxed text-muted-foreground">
                  {step.body}
                </p>
                <ul className="mt-5 flex flex-wrap gap-x-6 gap-y-2">
                  {step.points.map((pt) => (
                    <li
                      key={pt}
                      className="flex items-center gap-2 text-sm text-muted-foreground"
                    >
                      <Check className="h-4 w-4 shrink-0 text-success" />
                      {pt}
                    </li>
                  ))}
                </ul>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* -------------------------------------------------------- no fabrication -- */

function NoFabrication() {
  return (
    <section id="grounded" className="border-b border-border/60 py-24">
      <div className="mx-auto grid max-w-6xl gap-14 px-6 md:grid-cols-2 md:items-center">
        <div>
          <span className="text-sm font-semibold uppercase tracking-widest text-accent">
            Why you can trust it
          </span>
          <h2 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">
            It never invents experience you don't have
          </h2>
          <p className="mt-5 leading-relaxed text-muted-foreground">
            The fastest way to make a resume score well is to lie on it. That's
            also the fastest way to fall apart in an interview, so ARTie won't do
            it.
          </p>
          <p className="mt-4 leading-relaxed text-muted-foreground">
            Tailoring is constrained to what's already in your knowledge graph.
            ARTie rephrases, reorders, and reprioritises what you genuinely did —
            surfacing the parts of your history a particular employer cares
            about. If something isn't in your history, it doesn't reach the page.
          </p>
          <p className="mt-4 leading-relaxed text-muted-foreground">
            You review every change before it ships, and you can edit any line by
            hand.
          </p>
        </div>

        <div className="rounded-lg border border-border bg-card p-8">
          <div className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
            What tailoring changes
          </div>
          <ul className="mt-6 space-y-4">
            {[
              ["Wording", "Your bullets, rewritten in the language the posting uses"],
              ["Emphasis", "The most relevant projects and roles move up the page"],
              ["Skills", "Ranked and trimmed to what the job actually asks for"],
              ["Layout", "Section order chosen for the role, kept to one page"],
            ].map(([k, v]) => (
              <li key={k} className="flex gap-4">
                <span className="w-20 shrink-0 text-sm font-semibold">{k}</span>
                <span className="text-sm text-muted-foreground">{v}</span>
              </li>
            ))}
          </ul>
          <div className="mt-8 border-t border-border pt-6">
            <div className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
              What it never changes
            </div>
            <p className="mt-3 text-sm text-muted-foreground">
              The facts. Titles, dates, employers, numbers, and what you actually
              built stay exactly as you recorded them.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ final cta -- */

function ClosingCta() {
  return (
    <section className="relative overflow-hidden py-28">
      <div className="absolute left-1/2 top-1/2 h-[320px] w-[720px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/15 blur-[130px]" />
      <div className="relative mx-auto max-w-2xl px-6 text-center">
        <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
          Your experience is enough.
          <br />
          Let's write it down properly.
        </h2>
        <p className="mt-5 text-lg text-muted-foreground">
          Start with whatever you have — even if it's just a half-finished draft
          and a GitHub account.
        </p>
        <Link
          to="/register"
          className="group mt-9 inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-8 py-4 font-semibold text-primary-foreground transition-opacity hover:opacity-90"
        >
          Get started free
          <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
        </Link>
        <div className="mt-10 flex items-center justify-center gap-7 text-sm text-muted-foreground">
          <SourceChip icon={<FileText className="h-4 w-4" />} label="Resume" />
          <SourceChip icon={<Github className="h-4 w-4" />} label="GitHub" />
          <SourceChip icon={<Linkedin className="h-4 w-4" />} label="LinkedIn" />
        </div>
      </div>
    </section>
  );
}

function SourceChip({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <span className="flex items-center gap-2">
      {icon}
      {label}
    </span>
  );
}

/* -------------------------------------------------------------- footer -- */

function SiteFooter() {
  return (
    <footer className="border-t border-border/60 py-10">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 text-sm text-muted-foreground sm:flex-row">
        <div className="flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded bg-primary text-xs font-bold text-primary-foreground">
            A
          </span>
          <span className="font-semibold text-foreground">ARTie</span>
          <span className="hidden sm:inline">— Agentic Resume Tailoring</span>
        </div>
        <div className="flex items-center gap-6">
          <Link to="/login" className="transition-colors hover:text-foreground">
            Sign in
          </Link>
          <Link to="/register" className="transition-colors hover:text-foreground">
            Create account
          </Link>
        </div>
      </div>
    </footer>
  );
}
