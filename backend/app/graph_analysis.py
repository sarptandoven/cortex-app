"""Graph analysis for Cortex's personal knowledge graph — the "graphify" core.

Given a person's entity graph (nodes = people/projects/orgs/topics; edges = how
those entities co-occur), this module surfaces the three things that make a graph
legible to a human:

1. **The "god nodes"** — the most central entities, via weighted-degree
   centrality. These are the hubs the rest of the graph orbits.
2. **Communities** — natural clusters of entities that hang together, via
   deterministic label propagation.
3. **Surprising connections** — the cross-community "bridge" edges that link two
   otherwise-separate clusters. These are the links worth pointing at.

The module follows the same discipline as :mod:`backend.app.mirror` and
:mod:`backend.app.profile`:

1. **Pure.** No DB access, no LLM, no network, no randomness. It accepts already
   materialized node/edge lists and returns plain data structures. It never
   mutates its inputs.
2. **Deterministic.** The same input always produces byte-identical output. All
   ordering is fully specified with stable, content-derived tie-breaks (node ids)
   so results never flap. Label propagation iterates nodes in a fixed order and
   tie-breaks on the smallest label id; community ids are renumbered by ascending
   smallest-member-id so the numbering is stable too.
3. **Dependency-light.** Standard library only. ``numpy`` is imported behind a
   guard purely as an optional accelerator; the module is correct and complete
   without it (the graphs are small — tens to low-hundreds of nodes — so pure
   Python is plenty). We deliberately do NOT use ``networkx``.

Input contract (built elsewhere; we only read it)::

    Node = {"id": str, "label": str, "kind": str, "weight": float}
    Edge = {"source": str, "target": str, "weight": float,
            "relation": str, "confidence": "EXTRACTED"|"INFERRED"|"AMBIGUOUS"}

Edges are treated as undirected; ``source``/``target`` order is arbitrary.
Self-loops are ignored. Edges referencing unknown node ids are ignored. Duplicate
edges between the same unordered pair are summed by weight.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

# Optional accelerator only — every code path below is correct without it. The
# import is guarded so the module works in a bare stdlib environment.
try:  # pragma: no cover - trivial import guard
    import numpy as _np  # noqa: F401

    _HAS_NUMPY = True
except Exception:  # pragma: no cover - numpy absent is a supported configuration
    _np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


# Iteration cap for label propagation. Convergence on graphs of this size is fast;
# the cap only guards against a pathological non-converging (oscillating) case.
_MAX_ITERATIONS = 50


def analyze_entity_graph(nodes: list[dict], edges: list[dict]) -> dict:
    """Analyze a personal entity graph into centrality, communities, and bridges.

    Args:
        nodes: list of Node dicts (see module docstring). Only ``id`` and
            ``weight`` are read here; ``label``/``kind`` are carried by callers.
        edges: list of Edge dicts. Treated as undirected, deduped (summed by
            weight per unordered pair), self-loops and unknown-id edges dropped.

    Returns a dict with the exact shape::

        {
          "centrality":  {node_id: float},   # weighted-degree, normalized to [0,1]
          "community":   {node_id: int},     # community id per node
          "communities": {int: [node_id]},   # inverse map, members ranked
          "ranked":      [node_id, ...],     # all nodes, "god nodes" first
          "bridges":     [{...}, ...],       # cross-community edges
        }

    Pure and deterministic: identical input yields identical output.
    """
    node_ids = _node_ids(nodes)
    node_weights = _node_weights(nodes, node_ids)

    # Empty graph -> all-empty structures (fixed shape, never None).
    if not node_ids:
        return {
            "centrality": {},
            "community": {},
            "communities": {},
            "ranked": [],
            "bridges": [],
        }

    id_set = set(node_ids)
    # {frozenset({a, b}): summed_weight} — the deduped undirected edge set.
    pair_weights = _dedup_edges(edges, id_set)
    # {node_id: {neighbour_id: weight}} adjacency for centrality + propagation.
    adjacency = _build_adjacency(node_ids, pair_weights)

    centrality = _weighted_degree_centrality(node_ids, adjacency)
    labels = _label_propagation(node_ids, adjacency)
    community = _renumber_communities(node_ids, labels)

    ranked = _rank_nodes(node_ids, centrality, node_weights)
    communities = _communities_map(community, ranked)
    bridges = _find_bridges(pair_weights, community)

    return {
        "centrality": centrality,
        "community": community,
        "communities": communities,
        "ranked": ranked,
        "bridges": bridges,
    }


def personalized_page_rank(
    nodes: list[Any],
    edges: list[dict],
    seeds: Mapping[Any, Any],
    *,
    damping: float = 0.85,
    max_iterations: int = 100,
    tolerance: float = 1e-9,
) -> dict:
    """Weighted undirected personalized PageRank over a materialized graph.

    The API is pure, deterministic, and stdlib-only. ``nodes`` may be either a
    list of node ids or node dicts containing ``id``. ``edges`` are dicts with
    ``source``, ``target``, and positive finite ``weight``. Edges that are
    malformed, self loops, reference unknown nodes, or have non-positive/non-
    finite weights are ignored. Duplicate undirected edges are combined by
    summing their weights.

    ``seeds`` maps seed ids to nonnegative weights. Unknown seed ids are ignored;
    at least one known seed id must remain for non-empty graphs. If all known
    seed weights are zero, the personalization vector is uniform over those
    known seed ids. Dangling mass is redistributed to that personalization
    vector on every iteration, not uniformly over all nodes.

    Returns::

        {
          "scores": {node_id: probability},
          "ranked": [node_id, ...],
          "iterations": int,
          "converged": bool,
        }

    Scores are finite, normalized probabilities in stable id-key order. Ranking
    tie-breaks by ``(-score rounded to 12 decimals, node_id)``.
    """
    damping = _validate_probability_damping(damping)
    max_iterations = _validate_max_iterations(max_iterations)
    tolerance = _validate_tolerance(tolerance)

    node_ids = _pagerank_node_ids(nodes)
    if not node_ids:
        return {"scores": {}, "ranked": [], "iterations": 0, "converged": True}

    personalization = _personalization_vector(node_ids, seeds)
    pair_weights = _dedup_pagerank_edges(edges, set(node_ids))
    adjacency = _build_adjacency(node_ids, pair_weights)
    weighted_degree = {nid: sum(adjacency[nid].values()) for nid in node_ids}

    scores = dict(personalization)
    converged = False
    iterations = 0

    for iteration in range(1, max_iterations + 1):
        next_scores = {
            nid: (1.0 - damping) * personalization[nid] for nid in node_ids
        }

        dangling_mass = sum(
            scores[nid] for nid in node_ids if weighted_degree[nid] <= 0.0
        )
        if dangling_mass:
            for nid in node_ids:
                next_scores[nid] += damping * dangling_mass * personalization[nid]

        for source in node_ids:
            degree = weighted_degree[source]
            if degree <= 0.0:
                continue
            distributable = damping * scores[source] / degree
            for target in sorted(adjacency[source]):
                next_scores[target] += distributable * adjacency[source][target]

        next_scores = _normalize_probability_scores(node_ids, next_scores)
        delta = sum(abs(next_scores[nid] - scores[nid]) for nid in node_ids)
        scores = next_scores
        iterations = iteration
        if delta <= tolerance:
            converged = True
            break

    scores = _normalize_probability_scores(node_ids, scores)
    ranked = sorted(node_ids, key=lambda nid: (-round(scores[nid], 12), nid))
    return {
        "scores": scores,
        "ranked": ranked,
        "iterations": iterations,
        "converged": converged,
    }


# --- Personalized PageRank helpers ------------------------------------------


def _pagerank_node_ids(nodes: list[Any]) -> list[str]:
    """Distinct PageRank node ids from node dicts or raw id values, sorted."""
    seen: set[str] = set()
    ids: list[str] = []
    if not isinstance(nodes, list):
        return ids
    for node in nodes:
        nid = _norm_id(node.get("id") if isinstance(node, dict) else node)
        if not nid or nid in seen:
            continue
        seen.add(nid)
        ids.append(nid)
    ids.sort()
    return ids


def _personalization_vector(
    node_ids: list[str], seeds: Mapping[Any, Any]
) -> dict[str, float]:
    """Normalize known nonnegative seed weights into a probability vector."""
    if not isinstance(seeds, Mapping):
        raise ValueError("seeds must be a mapping of node id to nonnegative weight")

    id_set = set(node_ids)
    known_seed_ids: set[str] = set()
    positive_weights: dict[str, float] = {nid: 0.0 for nid in node_ids}

    for raw_seed_id, raw_weight in seeds.items():
        seed_id = _norm_id(raw_seed_id)
        weight = _seed_weight(raw_weight)
        if seed_id not in id_set:
            continue
        known_seed_ids.add(seed_id)
        positive_weights[seed_id] += weight

    if not known_seed_ids:
        raise ValueError("seeds must include at least one known node id")

    total_positive = sum(positive_weights.values())
    if total_positive > 0.0:
        return {nid: positive_weights[nid] / total_positive for nid in node_ids}

    uniform = 1.0 / len(known_seed_ids)
    return {nid: (uniform if nid in known_seed_ids else 0.0) for nid in node_ids}


def _dedup_pagerank_edges(
    edges: list[dict], id_set: set[str]
) -> dict[frozenset, float]:
    """Collapse valid positive-weight PageRank edges by unordered pair."""
    pair_weights: dict[frozenset, float] = {}
    if not isinstance(edges, list):
        return pair_weights
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        a = _norm_id(edge.get("source"))
        b = _norm_id(edge.get("target"))
        if not a or not b or a == b:
            continue
        if a not in id_set or b not in id_set:
            continue
        if "weight" not in edge:
            continue
        weight = _as_float(edge.get("weight"), default=0.0)
        if weight <= 0.0:
            continue
        key = frozenset((a, b))
        pair_weights[key] = pair_weights.get(key, 0.0) + weight
    return pair_weights


def _normalize_probability_scores(
    node_ids: list[str], scores: dict[str, float]
) -> dict[str, float]:
    """Return finite scores normalized to sum to one in stable node order."""
    cleaned = {nid: _finite_nonnegative(scores.get(nid, 0.0)) for nid in node_ids}
    total = sum(cleaned[nid] for nid in node_ids)
    if total <= 0.0:
        uniform = 1.0 / len(node_ids)
        return {nid: uniform for nid in node_ids}
    return {nid: cleaned[nid] / total for nid in node_ids}


def _validate_probability_damping(value: Any) -> float:
    damping = _as_float(value, default=float("nan"))
    if damping != damping or damping < 0.0 or damping >= 1.0:
        raise ValueError("damping must be finite and satisfy 0 <= damping < 1")
    return damping


def _validate_max_iterations(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("max_iterations must be a positive integer")
    return value


def _validate_tolerance(value: Any) -> float:
    tolerance = _as_float(value, default=float("nan"))
    if tolerance != tolerance or tolerance < 0.0:
        raise ValueError("tolerance must be finite and nonnegative")
    return tolerance


def _seed_weight(value: Any) -> float:
    weight = _as_float(value, default=float("nan"))
    if weight != weight or weight < 0.0:
        raise ValueError("seed weights must be finite and nonnegative")
    return weight


def _finite_nonnegative(value: Any) -> float:
    result = _as_float(value, default=0.0)
    return result if result > 0.0 else 0.0


# --- Input normalization ----------------------------------------------------


def _node_ids(nodes: list[dict]) -> list[str]:
    """Distinct node ids, in ascending id order.

    Ignores nodes with no usable id and collapses duplicate ids (first wins for
    ordering — order is derived purely from the id string, so it is stable).
    """
    seen: set[str] = set()
    ids: list[str] = []
    if not isinstance(nodes, list):
        return ids
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = _norm_id(node.get("id"))
        if not nid or nid in seen:
            continue
        seen.add(nid)
        ids.append(nid)
    ids.sort()
    return ids


def _node_weights(nodes: list[dict], node_ids: list[str]) -> dict[str, float]:
    """Map each kept node id to its (last-seen) weight, defaulting to 0.0.

    Weight is the entity's supporting-memory count; it is only used as a
    secondary tie-break in ``ranked``, so a missing/garbage weight degrades
    gracefully to ``0.0`` rather than raising.
    """
    id_set = set(node_ids)
    weights: dict[str, float] = {nid: 0.0 for nid in node_ids}
    if not isinstance(nodes, list):
        return weights
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = _norm_id(node.get("id"))
        if nid in id_set:
            weights[nid] = _as_float(node.get("weight"), default=0.0)
    return weights


def _dedup_edges(edges: list[dict], id_set: set[str]) -> dict[frozenset, float]:
    """Collapse edges into ``{frozenset({a, b}): summed_weight}``.

    - Undirected: ``(a, b)`` and ``(b, a)`` are the same key.
    - Self-loops (``a == b``) are ignored.
    - Edges referencing an unknown node id (either endpoint) are ignored.
    - Duplicate pairs are summed by weight.
    """
    pair_weights: dict[frozenset, float] = {}
    if not isinstance(edges, list):
        return pair_weights
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        a = _norm_id(edge.get("source"))
        b = _norm_id(edge.get("target"))
        if not a or not b or a == b:
            continue
        if a not in id_set or b not in id_set:
            continue
        key = frozenset((a, b))
        pair_weights[key] = pair_weights.get(key, 0.0) + _as_float(
            edge.get("weight"), default=0.0
        )
    return pair_weights


def _build_adjacency(
    node_ids: list[str], pair_weights: dict[frozenset, float]
) -> dict[str, dict[str, float]]:
    """Symmetric adjacency ``{node: {neighbour: weight}}`` from deduped pairs."""
    adjacency: dict[str, dict[str, float]] = {nid: {} for nid in node_ids}
    for pair, weight in pair_weights.items():
        a, b = _unordered_pair(pair)
        adjacency[a][b] = weight
        adjacency[b][a] = weight
    return adjacency


# --- Centrality -------------------------------------------------------------


def _weighted_degree_centrality(
    node_ids: list[str], adjacency: dict[str, dict[str, float]]
) -> dict[str, float]:
    """Weighted-degree centrality, normalized to ``[0, 1]`` by the max.

    Each node's raw score is the sum of its incident (deduped, weight-summed)
    edge weights. We normalize by the maximum raw score across nodes so the most
    central entity scores ``1.0``. Isolated nodes score ``0.0``; if *every* node
    is isolated (max == 0) all scores are ``0.0`` (divide-by-zero guard).
    """
    raw: dict[str, float] = {
        nid: sum(adjacency[nid].values()) for nid in node_ids
    }
    max_raw = max(raw.values()) if raw else 0.0
    if max_raw <= 0.0:
        return {nid: 0.0 for nid in node_ids}
    return {nid: raw[nid] / max_raw for nid in node_ids}


# --- Community detection (deterministic label propagation) ------------------


def _label_propagation(
    node_ids: list[str], adjacency: dict[str, dict[str, float]]
) -> dict[str, str]:
    """Deterministic label propagation. Returns ``{node_id: label_id}``.

    Standard label propagation, made fully deterministic:

    - Each node is initialized with its own id as its label.
    - We sweep nodes in ascending id order (a fixed, content-derived order).
    - Each node adopts the label carrying the greatest summed incident edge
      weight among its neighbours; ties are broken by the smallest label id.
    - A node's own current label participates in the vote only implicitly (via
      its neighbours' labels); an isolated node has no neighbours and therefore
      keeps its own label, i.e. it stays a singleton community.
    - We update labels in place during the sweep (asynchronous propagation),
      which converges faster and — because the sweep order and tie-break are
      fixed — remains deterministic.
    - We repeat sweeps until a full sweep changes nothing, or until a fixed
      iteration cap (guards the pathological oscillating case). Because updates
      are strictly determined by (fixed sweep order, weight, smallest-label tie
      break), the same input always yields the same labels.

    Labels here are raw node-id strings; :func:`_renumber_communities` turns them
    into stable integer community ids afterward.
    """
    labels: dict[str, str] = {nid: nid for nid in node_ids}

    for _ in range(_MAX_ITERATIONS):
        changed = False
        for nid in node_ids:  # ascending id order (node_ids is pre-sorted)
            neighbours = adjacency[nid]
            if not neighbours:
                continue  # isolated node keeps its singleton label
            best_label = _dominant_neighbour_label(neighbours, labels)
            if best_label != labels[nid]:
                labels[nid] = best_label
                changed = True
        if not changed:
            break

    return labels


def _dominant_neighbour_label(
    neighbours: dict[str, float], labels: dict[str, str]
) -> str:
    """Label with the greatest summed incident weight among ``neighbours``.

    Ties (equal summed weight) are broken by the smallest label id, so the choice
    is fully determined by the current label assignment — never by dict order.
    """
    weight_by_label: dict[str, float] = {}
    for neighbour_id, weight in neighbours.items():
        label = labels[neighbour_id]
        weight_by_label[label] = weight_by_label.get(label, 0.0) + weight

    best_label = None
    best_weight = 0.0
    for label in sorted(weight_by_label):  # ascending label id = tie-break
        weight = weight_by_label[label]
        if best_label is None or weight > best_weight:
            best_label = label
            best_weight = weight
    # neighbours is non-empty here, so best_label is always set.
    return best_label  # type: ignore[return-value]


def _renumber_communities(
    node_ids: list[str], labels: dict[str, str]
) -> dict[str, int]:
    """Renumber raw string labels into stable integer community ids ``0..k-1``.

    Communities are ordered by their ascending smallest member id, so the id a
    community receives depends only on its membership — not on iteration order or
    the raw label value. Isolated nodes (their own singleton label) simply become
    their own singleton community.
    """
    # Smallest member id per raw label (node_ids is sorted, so the first node
    # observed for a label is that label's smallest member).
    smallest_member: dict[str, str] = {}
    for nid in node_ids:
        label = labels[nid]
        if label not in smallest_member:
            smallest_member[label] = nid

    ordered_labels = sorted(smallest_member, key=lambda lab: smallest_member[lab])
    label_to_cid = {label: cid for cid, label in enumerate(ordered_labels)}
    return {nid: label_to_cid[labels[nid]] for nid in node_ids}


# --- Ranking & inverse community map ----------------------------------------


def _rank_nodes(
    node_ids: list[str],
    centrality: dict[str, float],
    node_weights: dict[str, float],
) -> list[str]:
    """All node ids sorted by ``(-centrality, -node_weight, id)`` — hubs first.

    Centrality is compared at 12 decimal places so floating-point noise from the
    normalization division cannot reorder otherwise-equal nodes (which would flap
    the tie-break); the id is the final, absolutely-stable discriminator.
    """
    return sorted(
        node_ids,
        key=lambda nid: (
            -round(centrality[nid], 12),
            -node_weights[nid],
            nid,
        ),
    )


def _communities_map(
    community: dict[str, int], ranked: list[str]
) -> dict[int, list[str]]:
    """Inverse ``{community_id: [member ids]}`` with members in ``ranked`` order.

    Because ``ranked`` is already sorted by ``(-centrality, -node_weight, id)``,
    iterating it and appending gives each community's member list that same
    ordering for free — the most-central member of a community comes first.
    """
    out: dict[int, list[str]] = {}
    for nid in ranked:
        out.setdefault(community[nid], []).append(nid)
    # Return keyed by ascending community id for a stable, readable mapping.
    return {cid: out[cid] for cid in sorted(out)}


# --- Bridges (cross-community "surprising connections") ---------------------


def _find_bridges(
    pair_weights: dict[frozenset, float], community: dict[str, int]
) -> list[dict]:
    """Edges whose endpoints ended in different communities, ranked.

    Each bridge is emitted with a canonical ``source``/``target`` order (the
    smaller id as ``source``) so the output is stable regardless of the original
    arbitrary edge direction. Sorted by ``(-weight, source_id, target_id)``.
    """
    bridges: list[dict] = []
    for pair, weight in pair_weights.items():
        a, b = _unordered_pair(pair)  # already (min, max) by id
        ca = community[a]
        cb = community[b]
        if ca == cb:
            continue
        bridges.append(
            {
                "source": a,
                "target": b,
                "weight": weight,
                "source_community": ca,
                "target_community": cb,
            }
        )

    bridges.sort(
        key=lambda e: (-round(e["weight"], 12), e["source"], e["target"])
    )
    return bridges


# --- Typed relationships (#24: works_at / reports_to / part_of / blocks) ----

# The typed relations Cortex extracts. `blocks` is asymmetric and everything here is DIRECTED
# (source -> target has meaning), unlike the undirected co-occurrence edges the analysis above
# consumes. Kept as a tuple so the summary's relation ordering is stable and content-derived.
TYPED_RELATIONS: tuple[str, ...] = ("works_at", "reports_to", "part_of", "blocks")


def summarize_typed_relationships(edges: list[dict]) -> dict:
    """Group directed, typed relationship edges into a deterministic per-relation summary.

    Pure and deterministic, matching the discipline of the rest of this module. Each edge is read
    for ``source``, ``target``, ``relation`` (must be one of :data:`TYPED_RELATIONS`), and optional
    ``evidence`` (a memory id string) and ``weight``. Malformed edges, self-loops, and edges whose
    relation is not a typed relation are ignored. Because these relations are directed, ``(a, b)``
    and ``(b, a)`` are distinct; duplicate directed pairs are merged (weights summed, evidence
    unioned in first-seen order).

    Returns::

        {
          relation: [
            {"source": str, "target": str, "weight": float, "evidence": [memory_id, ...]},
            ...
          ],
          ...
        }

    Relations are keyed in :data:`TYPED_RELATIONS` order (only non-empty ones are present); each
    relation's edge list is sorted by ``(-weight, source, target)`` so the strongest, most stable
    links come first. Identical input always yields identical output.
    """
    # {relation: {(source, target): {"weight": float, "evidence": [ids]}}}
    grouped: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    if not isinstance(edges, list):
        edges = []
    valid_relations = set(TYPED_RELATIONS)
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        relation = _norm_id(edge.get("relation"))
        if relation not in valid_relations:
            continue
        source = _norm_id(edge.get("source"))
        target = _norm_id(edge.get("target"))
        if not source or not target or source == target:
            continue
        weight = _as_float(edge.get("weight"), default=1.0)
        if weight <= 0.0:
            weight = 1.0
        bucket = grouped.setdefault(relation, {})
        pair = (source, target)
        entry = bucket.get(pair)
        if entry is None:
            entry = {"weight": 0.0, "evidence": []}
            bucket[pair] = entry
        entry["weight"] += weight
        evidence_id = _norm_id(edge.get("evidence"))
        if evidence_id and evidence_id not in entry["evidence"]:
            entry["evidence"].append(evidence_id)

    summary: dict[str, list[dict]] = {}
    for relation in TYPED_RELATIONS:
        bucket = grouped.get(relation)
        if not bucket:
            continue
        rows = [
            {
                "source": source,
                "target": target,
                "weight": entry["weight"],
                "evidence": list(entry["evidence"]),
            }
            for (source, target), entry in bucket.items()
        ]
        rows.sort(key=lambda row: (-round(row["weight"], 12), row["source"], row["target"]))
        summary[relation] = rows
    return summary


# --- Small deterministic helpers --------------------------------------------


def _unordered_pair(pair: frozenset) -> tuple[str, str]:
    """Return the two members of an edge pair as ``(min_id, max_id)``.

    Canonicalizing on the id ordering makes every downstream emission (adjacency
    writes, bridge source/target) independent of the arbitrary original edge
    direction, which is what keeps the output deterministic.
    """
    a, b = tuple(pair)
    return (a, b) if a <= b else (b, a)


def _norm_id(value: Any) -> str:
    """Normalize a node/edge id to a trimmed string; empty if unusable."""
    if value is None:
        return ""
    return str(value).strip()


def _as_float(value: Any, *, default: float) -> float:
    """Coerce a value to float, falling back to ``default`` on bad input."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    # Guard against NaN/inf sneaking into sort keys and normalization.
    if result != result or result in (float("inf"), float("-inf")):
        return default
    return result
