# API Lifecycle and Compatibility

Status: **Current beta policy**

Last reviewed: 2026-07-30

## Supported Surfaces

During beta, the compatibility contract covers only:

- the `/v1/context`, `/v1/search`, `/v1/ask`, and `/v1/tools/*` HTTP surfaces
  exercised by both SDK and contract tests;
- the generated OpenAPI document at `/openapi.json`;
- the Python package `doppl-cortex-client`;
- the TypeScript package `@doppl-tech/cortex-client`;
- the documented MCP tools and their JSON schemas.

Other `/v1` routes are documented beta surfaces but do not yet carry a
repository-wide compatibility guarantee. Internal Python modules, SQLite
tables, vault implementation details, and routes without `/v1` are not stable
extension points unless another current document explicitly says otherwise.
Experimental branch documentation is not part of the released contract.

## Versioning

Cortex is currently beta. Additive response fields, new endpoints, new optional
request fields, and new enum values may ship in a minor release. Consumers must
ignore response fields they do not recognize.

The API/SDK compatibility version is currently `0.1.0`; it is independent from
the desktop application release version (`0.2.0`, build 51 at the time of this
review). OpenAPI, MCP server metadata, and backend diagnostics all read the
shared `BACKEND_VERSION` constant. Do not infer API compatibility from a DMG or
Sparkle build number.

A change is breaking when an existing valid request stops working, a field is
removed or changes meaning/type, an authentication scope becomes insufficient,
or an SDK method changes incompatibly. Breaking changes require one of:

1. a new API namespace such as `/v2`;
2. a new SDK major version; or
3. a documented security emergency where preserving the old behavior would
   leave user data or credentials exposed.

Security tightening may reject requests that were never within the documented
trust boundary, such as credential-bearing redirects to another origin.

## Deprecation

For non-emergency changes:

1. mark the surface deprecated in the OpenAPI description, SDK docs, and
   changelog;
2. keep it functional for at least one published minor release;
3. provide a replacement and migration example;
4. remove it only in a major SDK release or a new API namespace.

The HTTP response may include `Deprecation`, `Sunset`, and `Link` headers once a
removal date is known. A deprecation is not complete if only a source-code
comment announces it.

## Contract Verification

CI generates the OpenAPI schema and fails on duplicate operation IDs. Operation
IDs are derived from the public HTTP method and path (for example,
`post_v1_context`), never from a Python handler name, so internal refactors do
not rename client operations. Contract tests also require the bearer security
scheme and typed bounded request model on protected SDK surfaces. SDK tests pin
method, path, authentication header, query, body, and same-origin redirect
behavior for their supported calls. A proposed API change should update:

- FastAPI and standalone-runtime contract tests where both expose the surface;
- Python and TypeScript SDK tests;
- the changelog and affected current documentation;
- migration notes when compatibility is not additive.

The generated schema is necessary but not sufficient: the loopback standard
library runtime does not derive its routes from FastAPI, so parity requires
explicit cross-runtime tests.

A checked-in breaking-schema diff is still required before the API exits beta.
Until then this policy protects the highest-use surfaces but is not evidence of
complete historical compatibility.

## Authentication and Redirects

Clients must treat tokens as origin-bound credentials. First-party HTTP clients
do not forward them to a different scheme, hostname, or effective port during a
redirect. API deployments should use a stable canonical origin rather than
depending on redirects.
