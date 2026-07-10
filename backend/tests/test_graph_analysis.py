"""Tests for backend/app/graph_analysis.py — the pure, deterministic graph core.

These tests are fully pure: they construct node/edge lists by hand (no store, no
DB, no network) and assert on the deterministic output of
:func:`analyze_entity_graph` — centrality, communities, ranking, and bridges.
"""

from __future__ import annotations

import unittest

from backend.app.graph_analysis import analyze_entity_graph, personalized_page_rank


# --- Tiny builders matching the input contract ------------------------------


def node(node_id: str, *, label: str = "", kind: str = "topic", weight: float = 1.0):
    return {
        "id": node_id,
        "label": label or node_id,
        "kind": kind,
        "weight": weight,
    }


def edge(
    source: str,
    target: str,
    *,
    weight: float = 1.0,
    relation: str = "co_occurs",
    confidence: str = "EXTRACTED",
):
    return {
        "source": source,
        "target": target,
        "weight": weight,
        "relation": relation,
        "confidence": confidence,
    }


class GraphAnalysisTests(unittest.TestCase):
    # --- Shape ---------------------------------------------------------------

    def test_output_shape_and_keys(self):
        result = analyze_entity_graph(
            [node("a"), node("b")], [edge("a", "b", weight=2.0)]
        )
        self.assertEqual(
            set(result.keys()),
            {"centrality", "community", "communities", "ranked", "bridges"},
        )
        self.assertIsInstance(result["centrality"], dict)
        self.assertIsInstance(result["community"], dict)
        self.assertIsInstance(result["communities"], dict)
        self.assertIsInstance(result["ranked"], list)
        self.assertIsInstance(result["bridges"], list)

    # --- Empty graph ---------------------------------------------------------

    def test_empty_graph_is_all_empty(self):
        result = analyze_entity_graph([], [])
        self.assertEqual(
            result,
            {
                "centrality": {},
                "community": {},
                "communities": {},
                "ranked": [],
                "bridges": [],
            },
        )

    def test_empty_nodes_with_edges_is_all_empty(self):
        # Edges referencing nodes that do not exist must not fabricate output.
        result = analyze_entity_graph([], [edge("x", "y", weight=5.0)])
        self.assertEqual(
            result,
            {
                "centrality": {},
                "community": {},
                "communities": {},
                "ranked": [],
                "bridges": [],
            },
        )

    # --- Determinism ---------------------------------------------------------

    def test_determinism_identical_input_identical_output(self):
        nodes = [
            node("a", weight=3.0),
            node("b", weight=1.0),
            node("c", weight=2.0),
            node("d", weight=1.0),
            node("iso", weight=1.0),
        ]
        edges = [
            edge("a", "b", weight=2.0),
            edge("b", "c", weight=1.0),
            edge("c", "a", weight=3.0),
            edge("c", "d", weight=1.0),
        ]
        first = analyze_entity_graph(nodes, edges)
        second = analyze_entity_graph(nodes, edges)
        self.assertEqual(first, second)

    def test_determinism_input_order_does_not_matter(self):
        nodes = [node("a"), node("b"), node("c")]
        edges = [
            edge("a", "b", weight=2.0),
            edge("b", "c", weight=1.0),
        ]
        result = analyze_entity_graph(nodes, edges)
        shuffled_nodes = [node("c"), node("a"), node("b")]
        shuffled_edges = [
            edge("c", "b", weight=1.0),  # reversed direction too
            edge("b", "a", weight=2.0),
        ]
        result_shuffled = analyze_entity_graph(shuffled_nodes, shuffled_edges)
        self.assertEqual(result, result_shuffled)

    # --- Two clusters joined by one weak bridge ------------------------------

    def test_two_clusters_one_weak_bridge(self):
        # Cluster 1: {a, b, c} densely connected. Cluster 2: {x, y, z} densely
        # connected. A single WEAK edge (c—x, weight 0.1) joins them.
        nodes = [node(n) for n in ("a", "b", "c", "x", "y", "z")]
        edges = [
            # cluster 1 (strong internal weights)
            edge("a", "b", weight=5.0),
            edge("b", "c", weight=5.0),
            edge("a", "c", weight=5.0),
            # cluster 2 (strong internal weights)
            edge("x", "y", weight=5.0),
            edge("y", "z", weight=5.0),
            edge("x", "z", weight=5.0),
            # the one weak bridge
            edge("c", "x", weight=0.1),
        ]
        result = analyze_entity_graph(nodes, edges)

        # Exactly two communities.
        distinct_communities = set(result["community"].values())
        self.assertEqual(len(distinct_communities), 2)
        self.assertEqual(set(result["communities"].keys()), {0, 1})

        # a, b, c share one community; x, y, z share the other.
        self.assertEqual(
            result["community"]["a"], result["community"]["b"]
        )
        self.assertEqual(
            result["community"]["a"], result["community"]["c"]
        )
        self.assertEqual(
            result["community"]["x"], result["community"]["y"]
        )
        self.assertEqual(
            result["community"]["x"], result["community"]["z"]
        )
        self.assertNotEqual(
            result["community"]["c"], result["community"]["x"]
        )

        # The joining edge is the only bridge, canonicalized (min id = source).
        self.assertEqual(len(result["bridges"]), 1)
        bridge = result["bridges"][0]
        self.assertEqual(bridge["source"], "c")
        self.assertEqual(bridge["target"], "x")
        self.assertEqual(bridge["weight"], 0.1)
        self.assertEqual(
            bridge["source_community"], result["community"]["c"]
        )
        self.assertEqual(
            bridge["target_community"], result["community"]["x"]
        )
        self.assertNotEqual(
            bridge["source_community"], bridge["target_community"]
        )

    def test_community_ids_renumbered_from_zero(self):
        nodes = [node(n) for n in ("a", "b", "x", "y")]
        edges = [
            edge("a", "b", weight=5.0),
            edge("x", "y", weight=5.0),
        ]
        result = analyze_entity_graph(nodes, edges)
        # Two components -> ids exactly {0, 1}, numbered by smallest member id.
        self.assertEqual(sorted(set(result["community"].values())), [0, 1])
        # 'a' is the globally-smallest member, so its community is 0.
        self.assertEqual(result["community"]["a"], 0)
        self.assertEqual(result["community"]["b"], 0)
        self.assertEqual(result["community"]["x"], 1)
        self.assertEqual(result["community"]["y"], 1)

    # --- Centrality ----------------------------------------------------------

    def test_centrality_ranking_highest_degree_first(self):
        # Star graph: 'hub' connects to three leaves. hub has the highest
        # weighted degree and must rank first.
        nodes = [node(n) for n in ("hub", "l1", "l2", "l3")]
        edges = [
            edge("hub", "l1", weight=1.0),
            edge("hub", "l2", weight=1.0),
            edge("hub", "l3", weight=1.0),
        ]
        result = analyze_entity_graph(nodes, edges)
        self.assertEqual(result["ranked"][0], "hub")
        # Hub is the max -> normalized to exactly 1.0; leaves strictly lower.
        self.assertEqual(result["centrality"]["hub"], 1.0)
        for leaf in ("l1", "l2", "l3"):
            self.assertLess(result["centrality"][leaf], result["centrality"]["hub"])

    def test_centrality_normalized_to_unit_interval(self):
        nodes = [node(n) for n in ("a", "b", "c")]
        edges = [
            edge("a", "b", weight=4.0),
            edge("b", "c", weight=2.0),
        ]
        result = analyze_entity_graph(nodes, edges)
        for value in result["centrality"].values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)
        # 'b' touches both edges (4 + 2 = 6) and is the unique max.
        self.assertEqual(result["centrality"]["b"], 1.0)
        self.assertEqual(result["centrality"]["a"], 4.0 / 6.0)
        self.assertEqual(result["centrality"]["c"], 2.0 / 6.0)

    def test_ranked_tie_break_by_weight_then_id(self):
        # Two nodes with identical centrality but different node weights: the
        # heavier one ranks first; equal-weight ties fall back to id order.
        nodes = [
            node("a", weight=1.0),
            node("b", weight=5.0),
            node("hub", weight=1.0),
        ]
        edges = [
            edge("hub", "a", weight=1.0),
            edge("hub", "b", weight=1.0),
        ]
        result = analyze_entity_graph(nodes, edges)
        # hub is most central; among the two equal-centrality leaves, 'b'
        # (weight 5) precedes 'a' (weight 1).
        self.assertEqual(result["ranked"], ["hub", "b", "a"])

    # --- Duplicate / reversed edges summed -----------------------------------

    def test_duplicate_edges_summed_by_weight(self):
        nodes = [node("a"), node("b"), node("c")]
        edges = [
            edge("a", "b", weight=1.0),
            edge("b", "a", weight=2.0),  # same undirected pair, reversed
            edge("a", "b", weight=0.5),  # same pair again
            edge("b", "c", weight=1.0),
        ]
        result = analyze_entity_graph(nodes, edges)
        # a—b summed weight = 3.5; b touches a (3.5) + c (1.0) = 4.5 -> max.
        # a raw = 3.5, c raw = 1.0. Normalized by 4.5.
        self.assertEqual(result["centrality"]["b"], 1.0)
        self.assertEqual(result["centrality"]["a"], 3.5 / 4.5)
        self.assertEqual(result["centrality"]["c"], 1.0 / 4.5)
        self.assertEqual(result["ranked"][0], "b")

    # --- Isolated / singleton nodes ------------------------------------------

    def test_isolated_node_is_zero_centrality_own_community_no_bridge(self):
        nodes = [node("a"), node("b"), node("lonely")]
        edges = [edge("a", "b", weight=2.0)]
        result = analyze_entity_graph(nodes, edges)

        self.assertEqual(result["centrality"]["lonely"], 0.0)
        # 'lonely' is its own singleton community.
        self.assertEqual(
            result["communities"][result["community"]["lonely"]], ["lonely"]
        )
        self.assertNotEqual(
            result["community"]["lonely"], result["community"]["a"]
        )
        # An isolated node never appears in a bridge.
        for bridge in result["bridges"]:
            self.assertNotIn("lonely", (bridge["source"], bridge["target"]))

    def test_all_isolated_nodes_all_zero_centrality(self):
        nodes = [node("a"), node("b"), node("c")]
        result = analyze_entity_graph(nodes, [])
        self.assertEqual(
            result["centrality"], {"a": 0.0, "b": 0.0, "c": 0.0}
        )
        # Each is its own singleton community: 3 distinct ids.
        self.assertEqual(len(set(result["community"].values())), 3)
        self.assertEqual(result["bridges"], [])

    # --- Robustness to bad input ---------------------------------------------

    def test_edges_referencing_unknown_nodes_ignored(self):
        nodes = [node("a"), node("b")]
        edges = [
            edge("a", "b", weight=1.0),
            edge("a", "ghost", weight=9.0),  # unknown target -> ignored
            edge("phantom", "spirit", weight=9.0),  # both unknown -> ignored
        ]
        result = analyze_entity_graph(nodes, edges)
        # Only the a—b edge counts; both endpoints share the max -> 1.0 each.
        self.assertEqual(result["centrality"]["a"], 1.0)
        self.assertEqual(result["centrality"]["b"], 1.0)
        self.assertNotIn("ghost", result["centrality"])
        self.assertNotIn("phantom", result["centrality"])
        self.assertEqual(result["bridges"], [])

    def test_self_loops_ignored(self):
        nodes = [node("a"), node("b")]
        edges = [
            edge("a", "a", weight=99.0),  # self-loop -> ignored
            edge("a", "b", weight=1.0),
        ]
        result = analyze_entity_graph(nodes, edges)
        # Self-loop must not inflate 'a' centrality; a and b are symmetric.
        self.assertEqual(result["centrality"]["a"], 1.0)
        self.assertEqual(result["centrality"]["b"], 1.0)
        self.assertEqual(result["community"]["a"], result["community"]["b"])

    def test_missing_and_garbage_weights_tolerated(self):
        nodes = [
            {"id": "a"},  # no weight/label/kind
            {"id": "b", "weight": "not-a-number"},
        ]
        edges = [
            {"source": "a", "target": "b"},  # no weight -> defaults to 0.0
        ]
        result = analyze_entity_graph(nodes, edges)
        # Edge weight 0.0 -> raw degree 0 for both -> all-zero centrality guard.
        self.assertEqual(result["centrality"], {"a": 0.0, "b": 0.0})
        # Both nodes still present and rankable (id tie-break).
        self.assertEqual(result["ranked"], ["a", "b"])

    def test_duplicate_node_ids_collapsed(self):
        nodes = [node("a", weight=1.0), node("a", weight=2.0), node("b")]
        edges = [edge("a", "b", weight=1.0)]
        result = analyze_entity_graph(nodes, edges)
        self.assertEqual(set(result["centrality"].keys()), {"a", "b"})

    def test_bridges_sorted_by_weight_then_ids(self):
        # Three strong internal clusters joined by weak bridges of DIFFERENT
        # weights, so the ordering (-weight, source, target) is genuinely
        # exercised with more than one bridge present.
        #   cluster A = {a1, a2}, cluster B = {b1, b2}, cluster C = {c1, c2}
        nodes = [node(n) for n in ("a1", "a2", "b1", "b2", "c1", "c2")]
        edges = [
            # strong internal edges keep each pair its own community
            edge("a1", "a2", weight=10.0),
            edge("b1", "b2", weight=10.0),
            edge("c1", "c2", weight=10.0),
            # weak cross-cluster bridges of distinct weights
            edge("a2", "b1", weight=1.0),
            edge("b2", "c1", weight=3.0),
            edge("a1", "c2", weight=2.0),
        ]
        result = analyze_entity_graph(nodes, edges)

        # Exactly three communities.
        self.assertEqual(len(set(result["community"].values())), 3)

        bridges = result["bridges"]
        self.assertEqual(len(bridges), 3)

        weights = [b["weight"] for b in bridges]
        # Sorted strictly descending by weight (3.0, 2.0, 1.0).
        self.assertEqual(weights, [3.0, 2.0, 1.0])
        # Every bridge's source id < target id (canonical order).
        for bridge in bridges:
            self.assertLess(bridge["source"], bridge["target"])
            self.assertNotEqual(
                bridge["source_community"], bridge["target_community"]
            )

    def test_bridges_equal_weight_tie_break_by_ids(self):
        # Two cross-community bridges of EQUAL weight must order by source id
        # then target id, so the tie-break is deterministic.
        nodes = [node(n) for n in ("a1", "a2", "b1", "b2")]
        edges = [
            edge("a1", "a2", weight=10.0),
            edge("b1", "b2", weight=10.0),
            # two equal-weight bridges between the clusters
            edge("a2", "b1", weight=1.0),
            edge("a1", "b2", weight=1.0),
        ]
        result = analyze_entity_graph(nodes, edges)
        bridges = result["bridges"]
        self.assertEqual(len(bridges), 2)
        # Equal weight -> order by (source, target): a1/b2 before a2/b1.
        self.assertEqual(
            [(b["source"], b["target"]) for b in bridges],
            [("a1", "b2"), ("a2", "b1")],
        )


class PersonalizedPageRankTests(unittest.TestCase):
    def assert_valid_pagerank(self, result, expected_nodes):
        self.assertEqual(
            set(result.keys()), {"scores", "ranked", "iterations", "converged"}
        )
        self.assertEqual(set(result["scores"].keys()), set(expected_nodes))
        self.assertEqual(set(result["ranked"]), set(expected_nodes))
        self.assertEqual(len(result["ranked"]), len(expected_nodes))
        self.assertIsInstance(result["iterations"], int)
        self.assertIsInstance(result["converged"], bool)
        total = sum(result["scores"].values())
        self.assertAlmostEqual(total, 1.0, places=12)
        for score in result["scores"].values():
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)
            self.assertEqual(score, score)  # not NaN
            self.assertNotIn(score, (float("inf"), float("-inf")))

    def test_pagerank_accepts_node_ids_or_dicts_and_is_deterministic(self):
        node_ids = ["c", "a", "b", "d"]
        edges = [
            edge("a", "b", weight=2.0),
            edge("b", "c", weight=1.0),
            edge("c", "d", weight=0.5),
        ]
        result = personalized_page_rank(
            node_ids, edges, {"a": 1.0}, max_iterations=200, tolerance=1e-12
        )
        repeated = personalized_page_rank(
            node_ids, edges, {"a": 1.0}, max_iterations=200, tolerance=1e-12
        )
        dict_nodes_reordered = [node("d"), node("b"), node("a"), node("c")]
        reversed_edges_reordered = [
            edge("d", "c", weight=0.5),
            edge("c", "b", weight=1.0),
            edge("b", "a", weight=2.0),
        ]
        reordered = personalized_page_rank(
            dict_nodes_reordered,
            reversed_edges_reordered,
            {"a": 1.0},
            max_iterations=200,
            tolerance=1e-12,
        )

        self.assertEqual(result, repeated)
        self.assertEqual(result, reordered)
        self.assert_valid_pagerank(result, node_ids)
        self.assertTrue(result["converged"])

    def test_pagerank_seed_localization_and_multi_hop_propagation(self):
        nodes = ["seed", "hop1", "hop2", "hop3"]
        edges = [
            edge("seed", "hop1", weight=1.0),
            edge("hop1", "hop2", weight=1.0),
            edge("hop2", "hop3", weight=1.0),
        ]
        result = personalized_page_rank(
            nodes, edges, {"seed": 1.0}, max_iterations=300, tolerance=1e-13
        )

        self.assert_valid_pagerank(result, nodes)
        self.assertTrue(result["converged"])
        local_mass = result["scores"]["seed"] + result["scores"]["hop1"]
        remote_mass = result["scores"]["hop2"] + result["scores"]["hop3"]
        self.assertGreater(local_mass, remote_mass)
        self.assertGreater(result["scores"]["seed"], result["scores"]["hop2"])
        self.assertGreater(result["scores"]["hop2"], result["scores"]["hop3"])
        self.assertGreater(result["scores"]["hop3"], 0.0)

    def test_pagerank_weighted_edges_prefer_stronger_neighbor(self):
        nodes = ["seed", "heavy", "light"]
        edges = [
            edge("seed", "heavy", weight=10.0),
            edge("seed", "light", weight=1.0),
        ]
        result = personalized_page_rank(
            nodes, edges, {"seed": 1.0}, max_iterations=200, tolerance=1e-12
        )

        self.assert_valid_pagerank(result, nodes)
        self.assertGreater(result["scores"]["heavy"], result["scores"]["light"])
        self.assertLess(result["ranked"].index("heavy"), result["ranked"].index("light"))

    def test_pagerank_seed_weights_bias_personalization(self):
        nodes = ["alpha", "beta"]
        result = personalized_page_rank(
            nodes,
            [],
            {"alpha": 3.0, "beta": 1.0},
            max_iterations=50,
            tolerance=1e-12,
        )

        self.assert_valid_pagerank(result, nodes)
        self.assertTrue(result["converged"])
        self.assertAlmostEqual(result["scores"]["alpha"], 0.75, places=12)
        self.assertAlmostEqual(result["scores"]["beta"], 0.25, places=12)

    def test_pagerank_combines_duplicates_and_ignores_malformed_edges(self):
        nodes = ["a", "b", "c"]
        noisy_edges = [
            edge("a", "b", weight=1.0),
            edge("b", "a", weight=2.0),
            edge("a", "c", weight=1.0),
            edge("a", "a", weight=100.0),  # self loop ignored
            edge("a", "ghost", weight=100.0),  # unknown ignored
            {"source": "a", "target": "b"},  # missing weight ignored
            {"source": "a", "target": "b", "weight": "bad"},
            {"source": "a", "target": "b", "weight": -5.0},
            {"target": "b", "weight": 5.0},
            "not-an-edge",
        ]
        clean_edges = [edge("a", "b", weight=3.0), edge("a", "c", weight=1.0)]

        noisy = personalized_page_rank(
            nodes, noisy_edges, {"a": 1.0}, max_iterations=200, tolerance=1e-12
        )
        clean = personalized_page_rank(
            nodes, clean_edges, {"a": 1.0}, max_iterations=200, tolerance=1e-12
        )

        self.assertEqual(noisy, clean)
        self.assert_valid_pagerank(noisy, nodes)
        self.assertGreater(noisy["scores"]["b"], noisy["scores"]["c"])

    def test_pagerank_dangling_mass_returns_to_personalization(self):
        nodes = ["seed", "neighbor", "dangling", "disconnected"]
        edges = [edge("seed", "neighbor", weight=1.0)]
        result = personalized_page_rank(
            nodes,
            edges,
            {"dangling": 1.0},
            max_iterations=50,
            tolerance=1e-12,
        )

        self.assert_valid_pagerank(result, nodes)
        self.assertTrue(result["converged"])
        self.assertAlmostEqual(result["scores"]["dangling"], 1.0, places=12)
        self.assertEqual(result["scores"]["seed"], 0.0)
        self.assertEqual(result["scores"]["neighbor"], 0.0)
        self.assertEqual(result["scores"]["disconnected"], 0.0)

    def test_pagerank_zero_seed_weights_are_uniform_over_known_seeds(self):
        nodes = ["a", "b", "c"]
        result = personalized_page_rank(
            nodes, [], {"a": 0.0, "c": 0.0}, max_iterations=20, tolerance=1e-12
        )

        self.assert_valid_pagerank(result, nodes)
        self.assertAlmostEqual(result["scores"]["a"], 0.5, places=12)
        self.assertEqual(result["scores"]["b"], 0.0)
        self.assertAlmostEqual(result["scores"]["c"], 0.5, places=12)
        self.assertEqual(result["ranked"], ["a", "c", "b"])

    def test_pagerank_invalid_parameters_raise(self):
        nodes = ["a", "b"]
        valid_edges = [edge("a", "b", weight=1.0)]
        invalid_calls = [
            lambda: personalized_page_rank(nodes, valid_edges, {"a": 1.0}, damping=-0.1),
            lambda: personalized_page_rank(nodes, valid_edges, {"a": 1.0}, damping=1.0),
            lambda: personalized_page_rank(
                nodes, valid_edges, {"a": 1.0}, damping=float("inf")
            ),
            lambda: personalized_page_rank(
                nodes, valid_edges, {"a": 1.0}, max_iterations=0
            ),
            lambda: personalized_page_rank(
                nodes, valid_edges, {"a": 1.0}, max_iterations=True
            ),
            lambda: personalized_page_rank(
                nodes, valid_edges, {"a": 1.0}, tolerance=-1e-9
            ),
            lambda: personalized_page_rank(nodes, valid_edges, {"a": -1.0}),
            lambda: personalized_page_rank(nodes, valid_edges, {}),
            lambda: personalized_page_rank(nodes, valid_edges, {"ghost": 1.0}),
            lambda: personalized_page_rank(nodes, valid_edges, [("a", 1.0)]),
        ]
        for call in invalid_calls:
            with self.assertRaises(ValueError):
                call()

    def test_pagerank_empty_nodes_return_empty_result(self):
        result = personalized_page_rank([], [edge("a", "b")], {"a": 1.0})
        self.assertEqual(
            result, {"scores": {}, "ranked": [], "iterations": 0, "converged": True}
        )


if __name__ == "__main__":
    unittest.main()
