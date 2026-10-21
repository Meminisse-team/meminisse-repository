"use client";
import { useState } from "react";
import { autobiographiesApi } from "../api/autobiographies";
import { Button } from "@/components/ui/Button";

/** Fetch a fresh capability at click time; a saved page may outlive the 5-minute URL. */
export function PdfDownloadButton({ autobiographyId, children = "책 PDF 열어보기" }: { autobiographyId: string; children?: React.ReactNode }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function openPdf() {
    if (loading) return;
    const tab = window.open("about:blank", "_blank");
    if (tab) tab.opener = null;
    setLoading(true); setError(null);
    try {
      const { url } = await autobiographiesApi.getPdfUrl(autobiographyId);
      if (tab) tab.location.href = url;
      else window.location.assign(url);
    } catch {
      tab?.close(); setError("PDF를 열지 못했습니다. 다시 시도해주세요.");
    } finally { setLoading(false); }
  }
  return <div><Button onClick={() => void openPdf()} disabled={loading}>{loading ? "PDF 준비 중..." : children}</Button>{error && <p role="alert">{error}</p>}</div>;
}
