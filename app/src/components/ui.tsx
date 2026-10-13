import type { Confidence } from "@/lib/api";

const CONF_STYLE: Record<Confidence, string> = {
  HIGH: "bg-good-bg text-good",
  MEDIUM: "bg-warn-bg text-warn",
  LOW: "bg-bad-bg text-bad",
};

export function ConfidenceBadge({ level }: { level: Confidence }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${CONF_STYLE[level]}`}
      title={`Confidence: ${level.toLowerCase()} (lowest among the evidence cited)`}
    >
      {level}
    </span>
  );
}

export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-lg border border-line bg-surface p-5 ${className}`}>
      {children}
    </div>
  );
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-md bg-bad-bg px-3 py-2 text-sm text-bad">
      {message}
    </p>
  );
}

export const buttonClass =
  "inline-flex items-center justify-center rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";

export const ghostButtonClass =
  "inline-flex items-center justify-center rounded-md border border-line bg-surface px-3 py-1.5 text-sm hover:bg-background disabled:cursor-not-allowed disabled:opacity-50";
