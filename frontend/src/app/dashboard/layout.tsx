import { RequireAuth } from "@/components/auth/RequireAuth";
import { BottomNav } from "@/components/dashboard/BottomNav";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <div className="flex min-h-screen flex-col">
        <div className="flex-1">{children}</div>
        <BottomNav />
      </div>
    </RequireAuth>
  );
}
