import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Matched-v2 Response Viewer",
    template: "%s · Matched-v2 Response Viewer",
  },
  description:
    "Compare full matched-v2 responses from GPT-OSS base, self-distilled, Flash-distilled teacher-forward, and V4 Flash.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
