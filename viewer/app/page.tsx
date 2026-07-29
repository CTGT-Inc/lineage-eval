import type { Metadata } from "next";
import { headers } from "next/headers";
import { EvaluationViewer } from "./components/EvaluationViewer";

const description =
  "Compare all 304 matched-v2 prompts across six model arms with v2 judgments.";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host =
    requestHeaders.get("x-forwarded-host") ??
    requestHeaders.get("host") ??
    "localhost:3000";
  const protocol =
    requestHeaders.get("x-forwarded-proto") ??
    (host.startsWith("localhost") ? "http" : "https");
  const imageUrl = `${protocol}://${host}/og-v2.png`;

  return {
    title: { absolute: "Matched-v2 Response Viewer" },
    description,
    openGraph: {
      title: "Matched-v2 Response Viewer",
      description,
      type: "website",
      images: [{ url: imageUrl, width: 1731, height: 909 }],
    },
    twitter: {
      card: "summary_large_image",
      title: "Matched-v2 Response Viewer",
      description,
      images: [imageUrl],
    },
  };
}

export default function Home() {
  return <EvaluationViewer />;
}
