import { createBrowserRouter } from "react-router-dom";
import { Layout } from "@/app/Layout";
import { GeneratePage } from "@/pages/GeneratePage";
import { JobPage } from "@/pages/JobPage";
import { JobReviewPage } from "@/pages/JobReviewPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ReviewPage } from "@/pages/ReviewPage";

export const router = createBrowserRouter([
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
]);
