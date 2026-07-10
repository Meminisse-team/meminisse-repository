import type { Metadata } from "next";
import { Gowun_Batang, Noto_Sans_KR } from "next/font/google";
import "./globals.css";

const gowunBatang = Gowun_Batang({
  variable: "--font-serif-kr",
  subsets: ["latin"],
  weight: ["400", "700"],
  display: "swap",
});

const notoSansKr = Noto_Sans_KR({
  variable: "--font-sans-kr",
  subsets: ["latin"],
  weight: ["300", "400", "500", "700"],
  display: "swap",
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
