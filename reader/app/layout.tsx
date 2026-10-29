import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "文献启发 · 今日文献",
  description: "每日五篇、粗读精读和逐级通俗解释。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
