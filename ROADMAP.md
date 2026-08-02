# Cortex Roadmap

This roadmap communicates direction, not guaranteed dates. Current behavior is
defined by executable tests, the application code, and the current-product
documents indexed in [docs/README.md](docs/README.md).

## Now — Make the Beta Trustworthy

- Improve installation success and clean-profile first run.
- Keep Review understandable under large, noisy imports.
- Increase cited-answer quality and honest abstention.
- Validate the thirteen read-only connector contracts against real accounts.
- Harden backup, restore, deletion, support bundles, and update handoff.
- Keep local and hosted API behavior aligned.
- Maintain versioned, reproducible benchmark reports and runnable integration
  examples as the implementation evolves.

## Next — Prove the Operating Model

- Run the pairwise digital-twin owner study and satisfy its admission gates.
- Improve whole-person adaptation without weakening source provenance.
- Configure and validate managed OAuth providers release by release.
- Validate retrieval, context packing, latency, and answerability against
  consented real-user tasks in addition to deterministic synthetic fixtures.

Pairwise evaluation remains experimental on `feat/pairwise-twin-eval`; see its
[overview](https://github.com/trace-cortex/cortex-app/blob/feat/pairwise-twin-eval/docs/PAIRWISE_TWIN_EVALUATION.md).

## Later — Scale Without Losing Ownership

- Production hardening for hosted auth, storage, monitoring, and incident
  response.
- Multi-device encrypted sync with a recovery experience users can understand.
- A stable extension/plugin ecosystem around the portable-memory protocol.
- Published, versioned Python and TypeScript SDKs.
- Team and enterprise features only after the personal product is reliably
  useful.

## Non-Goals

- Ambient surveillance or background capture without explicit consent.
- Uncited answers presented as personal memory.
- Moving the authoritative personal vault into a proprietary format.
- Making every experimental feature part of the default product.

Propose roadmap changes through the feature-request template with user evidence,
scope, alternatives, and success criteria.
