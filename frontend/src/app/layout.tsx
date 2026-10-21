import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

const gowunBatang = localFont({
  src: [{ path: "./fonts/GowunBatang-Regular.woff2", weight: "400" }, { path: "./fonts/GowunBatang-Bold.woff2", weight: "700" }],
  variable: "--font-serif-kr", display: "swap",
});
const notoSansKr = localFont({
  src: [{ path: "./fonts/NotoSansKR.woff2", weight: "100 900" }],
  variable: "--font-sans-kr", display: "swap",
});

export const metadata: Metadata = {
  title: "Meminisse",
  description: "AI 대필 에이전트와의 대화로 완성하는 부모님의 자서전",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      className={`${gowunBatang.variable} ${notoSansKr.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-white text-black">{children}</body>
    </html>
  );
}
