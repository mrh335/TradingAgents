import type { Metadata } from "next";
import { LiveTickerStrip } from "@/components/LiveTickerStrip";
import { Sidebar } from "@/components/Sidebar";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "TradingAgents",
  description:
    "Multi-agent LLM trading research dashboard. Recommendations only — not orders.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <div className="min-h-screen flex">
            <Sidebar />
            <main className="flex-1 max-w-[1400px] mx-auto w-full flex flex-col">
              <LiveTickerStrip />
              <div className="px-6 py-6">{children}</div>
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
