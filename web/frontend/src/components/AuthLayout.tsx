import { Link } from "react-router-dom";
import type { ReactNode } from "react";

/**
 * Shared chrome for the signed-out pages (login, register, forgot, reset).
 * The four pages were near-identical copies before the Tailwind migration
 * (#134); the card, brand mark, and control styling now live here only.
 */

export const inputClass =
  "w-full rounded-md border border-input bg-background px-3 py-2.5 text-[15px] outline-none transition-colors placeholder:text-muted-foreground/60 focus:border-primary";

export const buttonClass =
  "w-full rounded-md bg-primary px-4 py-2.5 font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60";

export const linkClass = "font-medium text-accent underline-offset-2 transition-colors hover:underline";

export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background px-6 py-12">
      <div className="absolute left-1/2 top-0 h-[380px] w-[700px] -translate-x-1/2 rounded-full bg-primary/10 blur-[130px]" />

      <div className="relative w-full max-w-md">
        <Link
          to="/"
          className="mb-8 flex items-center justify-center gap-2 text-lg font-bold tracking-tight"
        >
          <span className="grid h-8 w-8 place-items-center rounded-md bg-primary text-primary-foreground">
            A
          </span>
          ARTie
        </Link>

        <div className="rounded-lg border border-border bg-card p-8">
          <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
          {subtitle && (
            <p className="mt-2 text-sm text-muted-foreground">{subtitle}</p>
          )}
          <div className="mt-6">{children}</div>
        </div>

        {footer && (
          <p className="mt-6 text-center text-sm text-muted-foreground">{footer}</p>
        )}
      </div>
    </div>
  );
}
