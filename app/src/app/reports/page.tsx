import ReportsList from "@/components/ReportsList";

export default function ReportsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
      <ReportsList />
    </div>
  );
}
