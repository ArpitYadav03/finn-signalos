import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { Nav } from "@/components/Nav";
import { EvidenceProvider } from "@/components/EvidenceSheet";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono-face" });

export const metadata: Metadata = {
  title: "FINN SignalOS",
  description: "Signal first. Evidence always. Portfolio intelligence grounded in primary filings.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon.svg" },
  appleWebApp: { capable: true, title: "FINN", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#f6f6f4",
  colorScheme: "light",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${mono.variable}`}>
        <EvidenceProvider>
          <div className="mx-auto flex min-h-dvh max-w-6xl">
            <Nav />
            <main className="rise min-w-0 flex-1 px-4 pb-32 pt-4 md:border-l md:border-line md:px-10 md:pb-12 md:pt-10">{children}</main>
          </div>
        </EvidenceProvider>
      </body>
    </html>
  );
}
