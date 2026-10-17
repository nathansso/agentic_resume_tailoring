"""ARTie LinkedIn gateway: one hosted endpoint that fetches a public LinkedIn
profile through Bright Data on ART's key, for callers who prove who they are
with a GitHub token.

Standard library only, on purpose. It deploys as its own Vercel project and
must never import the repo's other packages (`ingestion`, `harness`, `config`).
"""
