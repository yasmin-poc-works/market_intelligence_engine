import RunProgress from "@/components/RunProgress";

export default async function RunPage({ params }: PageProps<"/runs/[id]">) {
  const { id } = await params;
  return <RunProgress runId={id} />;
}
