import ReportLoader from "@/components/ReportLoader";

export default async function SharedReportPage({ params }: PageProps<"/share/[token]">) {
  const { token } = await params;
  return <ReportLoader shareToken={token} />;
}
