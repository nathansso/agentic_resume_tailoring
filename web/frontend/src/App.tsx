import { Routes, Route, Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ThemeProvider } from "./context/ThemeContext";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { MainPage } from "./pages/MainPage";

function Splash() {
  return (
    <div className="grid min-h-screen place-items-center bg-background text-muted-foreground">
      Loading…
    </div>
  );
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <Splash />;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/**
 * `/` is the public landing page for signed-out visitors (issue #83), and the
 * app shell for signed-in ones — a returning user never sees marketing.
 */
function Root() {
  const { user, loading, localMode } = useAuth();
  if (loading) return <Splash />;
  if (user) return <MainPage />;
  // `art ui` (#204) has no login and no marketing page to fall back to.
  return localMode ? <LocalUnavailable /> : <LandingPage />;
}

function LocalUnavailable() {
  return (
    <div className="grid min-h-screen place-items-center bg-background px-4 text-center text-muted-foreground">
      The local ART server has no profile bound. Restart it with{" "}
      <code className="font-mono">python -m web.local_ui --user-id &lt;id&gt;</code>.
    </div>
  );
}

/** Signed-out pages. In local mode there is nothing to sign in to. */
function SignedOut({ children }: { children: ReactNode }) {
  // Render straight away while capabilities load, as before #204; only a
  // confirmed local mode redirects.
  const { loading, localMode } = useAuth();
  if (!loading && localMode) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Root />} />
      <Route path="/login" element={<SignedOut><LoginPage /></SignedOut>} />
      <Route path="/register" element={<SignedOut><RegisterPage /></SignedOut>} />
      <Route path="/forgot-password" element={<SignedOut><ForgotPasswordPage /></SignedOut>} />
      <Route path="/reset-password" element={<SignedOut><ResetPasswordPage /></SignedOut>} />
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
    <ThemeProvider>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </ThemeProvider>
  );
}
