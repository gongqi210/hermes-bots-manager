"""Services — orchestration layer between API routers and adapters/DB.

Each service composes one or more adapters + the DB session into a single
business-flow API surface. Subprocess calls happen OUTSIDE the SQL transaction
(Pitfall #4: SQLite serializes writes; long subprocess calls under tx hold the
write lock and starve other requests).
"""
