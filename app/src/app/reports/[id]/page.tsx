import ReportLoader from "@/components/ReportLoader";

export default async function ReportPage({ params }: PageProps<"/reports/[id]">) {
  const { id } = await params;
  return <ReportLoader reportId={id} />;
}
