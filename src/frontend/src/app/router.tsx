import { createBrowserRouter } from "react-router-dom";
import { Layout } from "@/app/Layout";
import { ProtectedRoute } from "@/auth/ProtectedRoute";
import { GeneratePage } from "@/pages/GeneratePage";
import { JobPage } from "@/pages/JobPage";
import { JobReviewPage } from "@/pages/JobReviewPage";
import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ReviewPage } from "@/pages/ReviewPage";

export const router = createBrowserRouter([
  // Public routes (no auth required)
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/register",
    element: <RegisterPage />,
  },
  // Protected routes (auth required)
  {
    element: <ProtectedRoute />,
    children: [
      {
        element: <Layout />,
        children: [
          {
            path: "/",
            element: <GeneratePage />,
          },
          {
            path: "/review",
            element: <ReviewPage />,
          },
          {
            path: "/jobs/:jobId",
            element: <JobPage />,
          },
          {
            path: "/jobs/:jobId/review",
            element: <JobReviewPage />,
          },
          {
            path: "*",
            element: <NotFoundPage />,
          },
        ],
      },
    ],
  },
]);
