import { Routes, Route, Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { MainPage } from "./pages/MainPage";
import { colors } from "./theme";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return (
    <div style={{ padding: "2rem", color: colors.textMuted, background: colors.background, minHeight: "100vh" }}>
      Loading…
    </div>
  );
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/*"
        element={
          <RequireAuth>
            <MainPage />
          </RequireAuth>
        }
      />
    </Routes>
  );
}

export function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}
