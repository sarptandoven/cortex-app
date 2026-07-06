# Architecture Decisions

Decision: we decided to use PostgreSQL for the main store on Project Atlas.
We chose SQLite for the local-first cache inside Cortex so it runs fully offline.
We agreed to keep the backend stdlib-only to avoid dependency drift.
We will run everything on a single region until we cross ten thousand users.
Dana Kim owns the infrastructure decisions and signed off on the Postgres choice.
Marcus Feld pushed for the local-first stance, and we're going with it for Cortex.
