import type { Metadata } from "next";

/** Browser-tab title: the root layout's template renders this as "Pivot - Strategy Lab". */
export const metadata: Metadata = { title: "Strategy Lab" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
