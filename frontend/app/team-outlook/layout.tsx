import type { Metadata } from "next";

/** Browser-tab title: the root layout's template renders this as "Pivot - Team Outlook". */
export const metadata: Metadata = { title: "Team Outlook" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
