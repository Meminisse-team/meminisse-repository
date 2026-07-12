import { RequireAuth } from "@/components/auth/RequireAuth";
import { BottomNav } from "@/components/dashboard/BottomNav";
import { DashboardHeader } from "@/components/dashboard/DashboardHeader";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <div className="flex min-h-screen flex-col">
        <DashboardHeader />
        <div className="flex-1">{children}</div>
        <BottomNav />
      </div>
    </RequireAuth>
  );
}
