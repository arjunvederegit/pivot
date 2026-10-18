import type { Metadata } from "next";

/** Browser-tab title: the root layout's template renders this as "Pivot - Trade Report". */
export const metadata: Metadata = { title: "Trade Report" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
