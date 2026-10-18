import type { Metadata } from "next";
import { Geist, Geist_Mono, Source_Serif_4 } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";
import { AppShell } from "@/components/shell";
import { ToastProvider } from "@/components/toast";

/** Two voices. A refined serif for statements — page titles and selected headings — and
 *  a precise UI sans for everything a person operates or reads as data. Geist Mono
 *  appears only where something is literally code. */
const serif = Source_Serif_4({
  variable: "--font-display-serif",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
  display: "swap",
});
const sans = Geist({
  variable: "--font-geist",
  subsets: ["latin"],
  display: "swap",
});
const mono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Pivot — Basketball Intelligence for Better Decisions",
  description:
    "Understand a roster, find what it is missing, test a move, and read why — on real NBA data, with every number traced to how it was produced and what it cannot support.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${serif.variable} ${sans.variable} ${mono.variable} h-full antialiased`}
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
