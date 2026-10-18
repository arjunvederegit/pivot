import type { Metadata } from "next";
import { Geist_Mono, Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";
import { AppShell } from "@/components/shell";
import { ToastProvider } from "@/components/toast";

/** One application voice: Inter — a calm, normal-width, slightly rounded sans — for headings,
 *  navigation, controls, names, tables, figures and chart labels. Hierarchy comes from size,
 *  weight and spacing, not from competing display faces. Inter is a practical stand-in for the
 *  OpenAI Sans look we were pointed at (no licensed OpenAI Sans asset is available to Pivot); it
 *  is served by next/font from Google Fonts under the SIL Open Font License 1.1 (see
 *  docs/redesign/THIRD_PARTY_NOTICES.md). Geist Mono is kept only where something is code. The
 *  Pivot logo is an image and is unaffected. */
const sans = Inter({
  variable: "--font-sans-app",
  subsets: ["latin"],
  display: "swap",
});
const mono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  // One convention: the homepage is exactly "Pivot"; every tool renders "Pivot - <Tool>" through
  // the template, from the `metadata` its own route (or route layout) exports.
  title: { default: "Pivot", template: "Pivot - %s" },
  description:
    "Understand a roster, find what it is missing, test a move, and read why — on real NBA data, with every number traced to how it was produced and what it cannot support.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${sans.variable} ${mono.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <Providers>
          <ToastProvider>
            <a
              href="#main"
              className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-3 focus:z-[100] focus:rounded-md focus:bg-volt focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:text-ink"
            >
              Skip to content
            </a>
            <AppShell>{children}</AppShell>
          </ToastProvider>
        </Providers>
      </body>
    </html>
  );
}
