import type { Metadata } from "next";

import { AuthProvider } from "@/components/auth-provider";
import { LiveUpdatesProvider } from "@/components/live-updates-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Ledger",
    template: "%s | Ledger",
  },
  description: "Payment operations dashboard",
  manifest: "/site.webmanifest?v=20260707",
  icons: {
    icon: [
      { url: "/favicon.ico?v=20260707", sizes: "any" },
      { url: "/favicon-32x32.png?v=20260707", sizes: "32x32", type: "image/png" },
      { url: "/favicon-16x16.png?v=20260707", sizes: "16x16", type: "image/png" },
    ],
    apple: [
      { url: "/apple-touch-icon.png?v=20260707", sizes: "180x180", type: "image/png" },
    ],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <LiveUpdatesProvider>{children}</LiveUpdatesProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
