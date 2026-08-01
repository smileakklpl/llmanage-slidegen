import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { queryClient } from "@/app/queryClient";
import { router } from "@/app/router";
import { AuthProvider } from "@/auth";
import { I18nProvider } from "@/i18n";

export function App() {
  return (
    <I18nProvider>
      <AuthProvider>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </AuthProvider>
    </I18nProvider>
  );
}
