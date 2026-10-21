"use client";


export function Spinner() {
  return (
    <span
      className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-black/20 border-t-black"
      aria-hidden="true"
    />
  );
}
