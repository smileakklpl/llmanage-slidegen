"""
API layer package.

Architecture constraint:
    API route modules in this package MUST NOT import directly from
    `app.repositories.*`. All data access must go through the service layer
    (`app.services.*`), which in turn interacts with repositories.

    Correct dependency flow:
        api -> services -> repositories

    This ensures the repository implementation can be swapped (e.g., from
    in-memory to S3) without modifying any API route code.
"""
