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
