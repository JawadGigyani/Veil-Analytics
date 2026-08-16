import type { Metadata } from "next";
import { Backdrop } from "@/components/backdrop";
import "./globals.css";

export const metadata: Metadata = { title: "Veil Analytics", description: "Privacy-preserving analytics workspace" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* Self-hosted so a build never depends on a font CDN being reachable. */}
        <link rel="preload" href="/fonts/archivo-400-900-latin.woff2" as="font" type="font/woff2" crossOrigin="anonymous" />
        <link rel="preload" href="/fonts/plexmono-400-latin.woff2" as="font" type="font/woff2" crossOrigin="anonymous" />
      </head>
      <body>
        {/* THESIS: a privacy ledger, not a generic dashboard. OWN-WORLD: Swiss editorial grid, neo-brutalist ink blocks, developer terminal annotations -- bone paper, hard rules, one amber accent for cost. STORY: compose a useful question, see its privacy cost, release only a protected answer. FIRST VIEWPORT: workspace rail, dataset status, finite budget, and query action. FORM: assigned data-sublime field translated into a printed instrument panel. FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md */}
        <Backdrop />
        {children}
      </body>
    </html>
  );
}
