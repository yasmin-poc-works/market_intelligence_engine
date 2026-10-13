import BriefPanel from "@/components/BriefPanel";
import ReportsList from "@/components/ReportsList";

export default function Home() {
  return (
    <div className="mx-auto max-w-3xl space-y-10">
      <section className="space-y-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Market intelligence, with receipts</h1>
          <p className="mt-2 text-muted">
            Describe what you need to know. Agents research the web and your documents, then write a cited report and
            fact-check it.
          </p>
        </div>
        <BriefPanel />
      </section>
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Recent reports</h2>
        <ReportsList limit={5} />
      </section>
    </div>
  );
}
