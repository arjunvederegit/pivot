import type { Metadata } from "next";

/** Browser-tab title: the root layout's template renders this as "Pivot - Player Profile". */
export const metadata: Metadata = { title: "Player Profile" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
