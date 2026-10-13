import type { FactCheck } from "@/lib/api";
import { Card } from "./ui";

const TARGET = 0.8;

export default function FactCheckPanel({ factCheck }: { factCheck: FactCheck | null }) {
  if (!factCheck) {
    return (
      <Card>
        <h2 className="text-lg font-semibold">Fact-check</h2>
        <p className="mt-2 text-sm text-muted">No fact-check data for this report.</p>
      </Card>
    );
  }
  const pct = Math.round(factCheck.pass_rate * 100);
  const ok = factCheck.pass_rate >= TARGET;
  return (
    <Card className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">Fact-check</h2>
          <p className="mt-1 text-sm text-muted">
            A sample of claims was re-checked against the original sources.
          </p>
        </div>
        <div className="text-right">
          <div className={`text-3xl font-semibold ${ok ? "text-good" : "text-warn"}`}>{pct}%</div>
          <div className="text-xs text-muted">pass rate (target {TARGET * 100}%)</div>
        </div>
      </div>

      <div
        className="h-2 overflow-hidden rounded-full bg-background"
        role="img"
        aria-label={`${pct}% of sampled claims verified`}
      >
        <div className={`h-full ${ok ? "bg-good" : "bg-warn"}`} style={{ width: `${pct}%` }} />
      </div>

      <dl className="grid grid-cols-3 gap-3 text-center text-sm">
        <div className="rounded-md bg-background py-2">
          <dt className="text-xs text-muted">Checked</dt>
          <dd className="font-semibold">{factCheck.total_checked}</dd>
        </div>
        <div className="rounded-md bg-good-bg py-2">
          <dt className="text-xs text-good">Verified</dt>
          <dd className="font-semibold text-good">{factCheck.passed}</dd>
        </div>
        <div className="rounded-md bg-warn-bg py-2">
          <dt className="text-xs text-warn">Unverified</dt>
          <dd className="font-semibold text-warn">{factCheck.failed}</dd>
        </div>
      </dl>

      {factCheck.unverified_claims.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-medium">Claims that could not be verified</h3>
          <ul className="space-y-2 text-sm">
            {factCheck.unverified_claims.map((c) => (
              <li key={c} className="rounded border-l-4 border-warn bg-warn-bg px-3 py-2">
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
