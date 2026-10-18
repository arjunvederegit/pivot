import type { Metadata } from "next";

/** Browser-tab title: the root layout's template renders this as "Pivot - Cap Center". */
export const metadata: Metadata = { title: "Cap Center" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
