import type { Metadata } from "next";

/** Browser-tab title: the root layout's template renders this as "Pivot - Data Health". */
export const metadata: Metadata = { title: "Data Health" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
