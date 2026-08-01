import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "@/auth";

/**
 * Wrapper route element that redirects to /login if not authenticated.
 */
export function ProtectedRoute() {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}
