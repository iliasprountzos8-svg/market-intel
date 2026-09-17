import "./globals.css";
import type { Metadata } from "next";
import Masthead from "@/components/Masthead";
import TickerBar from "@/components/TickerBar";
import CommandPalette from "@/components/CommandPalette";
import BootSequence from "@/components/BootSequence";
import { UIFeedback } from "@/components/UIFeedback";
import { ServiceWorkerRegister } from "@/components/ServiceWorkerRegister";

export const metadata: Metadata = {
  title: "Market Intel",
  description: "Live market news + AI analysis terminal",
  manifest: "/manifest.json",
  icons: {
    icon: [
      { url: "/favicon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/favicon-16.png", sizes: "16x16", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
};

export const viewport = {
  themeColor: "#0a0a0a",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ServiceWorkerRegister />
        <UIFeedback />
        <BootSequence />
        <CommandPalette />
        <TickerBar />
        <Masthead />
        <main className="page-main">{children}</main>
      </body>
    </html>
  );
}
