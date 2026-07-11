import SwiftUI
import AppKit

/// "Your Constellation" — the interactive picture of who the user is: the people, projects,
/// topics and sources Cortex has learned about, and how they connect.
///
/// Readability model (why this is not just dots on a ring):
/// - Layout is a deterministic force-directed simulation (`MemoryMapLayout`): connected nodes
///   pull together along their edges, unrelated nodes push apart, and communities gather around
///   their own centroids — so proximity on the map MEANS relatedness, which is the whole point
///   of a graph view. The simulation is seeded from stable id hashes and runs a fixed number of
///   iterations with no randomness, so the map never shuffles between renders or launches.
/// - The camera zooms (pinch, scroll-wheel buttons) and pans (drag), anchored at the canvas
///   center. Labels keep a constant on-screen size; the label budget grows as you zoom in, so
///   zooming is how you read a dense neighborhood.
/// - Selecting a node enters focus mode: its direct connections stay bright with accent-colored
///   edges, everything else recedes. That is how "see the connections" actually works on a map
///   with hundreds of edges.
/// - The legend is honest: when the backend's community analysis names clusters, the legend
///   shows those cluster names in their real colors (clickable to spotlight one); otherwise it
///   falls back to node-type colors. It never claims a color mapping the canvas isn't using.
struct MemoryMapView: View {
    @ObservedObject var state: AppState

    /// Canvas height. Defaults to the compact Home embed; the full-screen Constellation overlay
    /// passes a larger value so the same interactive map fills the summoned card.
    var canvasHeight: CGFloat = 320

    /// Optional "drill into this node" action. When set, the node detail panel shows an
    /// "Explore in Ask" button. Home leaves it nil (read-only map); the Constellation overlay wires
    /// it to run an Ask for the node and dismiss.
    var onExplore: ((GraphNode) -> Void)? = nil

    /// The node the user tapped (by id); nil shows no detail panel.
    @State private var selectedNodeID: String?
    /// The node under the cursor, for a quiet hover highlight.
    @State private var hoveredNodeID: String?
    /// Free-text filter; matching nodes stay bright, the rest dim (layout is unchanged so the map
    /// doesn't jump around while typing).
    @State private var searchText: String = ""
    /// The cited neighborhood of the selected entity node, fetched on tap (nil for non-entity nodes).
    @State private var neighborhood: EntityNeighborhood?
    @State private var loadingNeighborhood = false

    // MARK: Camera — zoom anchored at canvas center, plus pan.

    /// Committed zoom (1 = fit). Pinch and the +/- controls change it; clamped to 0.6...4.
    @State private var zoomScale: CGFloat = 1.0
    /// Committed pan offset in screen points.
    @State private var panOffset: CGSize = .zero
    /// Live pinch multiplier while the gesture is active.
    @GestureState private var gestureZoom: CGFloat = 1.0
    /// Live drag translation while the gesture is active.
    @GestureState private var gesturePan: CGSize = .zero

    /// Legend spotlight: dims everything outside one community (or one type in the fallback
    /// type legend). Tapping the active chip again clears it.
    @State private var spotlight: MapSpotlight? = nil

    /// The constellation defaults to the ENTITY layer — people, projects, topics, sources —
    /// because that is the readable "who/what" picture. Raw memory/task sentence-nodes (often
    /// hundreds) turn the map into a blob; they stay one toggle away for users who want density.
    @State private var showDetailLayer = false

    private static let minZoom: CGFloat = 0.6
    private static let maxZoom: CGFloat = 4.0

    /// Types that belong to the entity layer (always shown). Everything else — memory kinds
    /// like fact/preference/decision, and task kinds — is the detail layer.
    private static let entityLayerTypes: Set<String> = [
        "person", "people", "project", "topic", "theme", "concept",
        "organization", "org", "company", "place", "tool", "skill",
        "source", "notes", "note", "document",
    ]

    private static func isEntityLayer(_ node: GraphNode) -> Bool {
        node.centrality != nil || entityLayerTypes.contains(node.type.lowercased())
    }

    /// The nodes the map draws under the current layer toggle.
    private var mapNodes: [GraphNode] {
        showDetailLayer ? state.graphNodes : state.graphNodes.filter { Self.isEntityLayer($0) }
    }

    /// Edges for the visible layer. In the entity layer, most raw edges route THROUGH hidden
    /// memory/task nodes (memory→entity), so plain endpoint filtering yields an edgeless map.
    /// Instead, edges are projected: entities that share a hidden node become directly
    /// connected ("co-mentioned"), with weight scaled by how many memories they share. Direct
    /// entity-entity edges pass through unchanged.
    private var mapEdges: [GraphEdge] {
        if showDetailLayer { return state.graphEdges }
        var visible = Set<String>()
        for node in state.graphNodes where Self.isEntityLayer(node) { visible.insert(node.id) }

        var direct: [GraphEdge] = []
        // hidden node id -> visible entity endpoints it touches
        var throughHidden: [String: [String]] = [:]
        for edge in state.graphEdges {
            let sourceVisible = visible.contains(edge.source_id)
            let targetVisible = visible.contains(edge.target_id)
            if sourceVisible && targetVisible {
                direct.append(edge)
            } else if sourceVisible != targetVisible {
                let hidden = sourceVisible ? edge.target_id : edge.source_id
                let shown = sourceVisible ? edge.source_id : edge.target_id
                throughHidden[hidden, default: []].append(shown)
            }
        }

        // Count co-mentions per entity pair (deterministic key ordering).
        var pairCounts: [String: (a: String, b: String, count: Int)] = [:]
        for (_, entities) in throughHidden {
            let unique = Array(Set(entities)).sorted()
            guard unique.count > 1 else { continue }
            for i in 0..<(unique.count - 1) {
                for j in (i + 1)..<unique.count {
                    let key = unique[i] + "→" + unique[j]
                    var entry = pairCounts[key] ?? (unique[i], unique[j], 0)
                    entry.count += 1
                    pairCounts[key] = entry
                }
            }
        }

        var existing = Set(direct.map { [$0.source_id, $0.target_id].sorted().joined(separator: "→") })
        var projected: [GraphEdge] = []
        for (key, entry) in pairCounts.sorted(by: { $0.key < $1.key }) {
            guard !existing.contains(key) else { continue }
            existing.insert(key)
            projected.append(GraphEdge(
                id: "proj-" + key,
                source_id: entry.a,
                target_id: entry.b,
                kind: "co_mentioned",
                weight: min(1.0, 0.25 + Double(entry.count) * 0.15),
                is_bridge: nil
            ))
        }
        return direct + projected
    }

    /// How many nodes the entity layer hides (for the honest toggle label).
    private var hiddenDetailCount: Int {
        state.graphNodes.count - state.graphNodes.filter { Self.isEntityLayer($0) }.count
    }

    /// nil = no filter (everything bright). Otherwise the set of node ids whose label matches.
    private var matchedNodeIDs: Set<String>? {
        let query = searchText.trimmingCharacters(in: .whitespaces).lowercased()
        guard !query.isEmpty else { return nil }
        return Set(mapNodes.filter { $0.label.lowercased().contains(query) }.map(\.id))
    }

    /// Direct neighbors of the selected node, straight from the edge list — the set that stays
    /// bright in focus mode.
    private var selectedNeighborIDs: Set<String> {
        guard let id = selectedNodeID else { return [] }
        var out: Set<String> = []
        for edge in mapEdges where edge.source_id == id || edge.target_id == id {
            out.insert(edge.source_id == id ? edge.target_id : edge.source_id)
        }
        return out
    }

    /// The selected node's edges resolved to human rows ("Sarah — works_with"), sorted by weight.
    /// This is what makes a selection USEFUL: the panel names every connection on the map, not
    /// just entity-API neighbors.
    private var selectedEdgeSummaries: [NodeEdgeSummary] {
        guard let id = selectedNodeID else { return [] }
        var byID: [String: GraphNode] = [:]
        for node in mapNodes { byID[node.id] = node }
        return mapEdges
            .filter { $0.source_id == id || $0.target_id == id }
            .compactMap { edge -> NodeEdgeSummary? in
                let otherID = edge.source_id == id ? edge.target_id : edge.source_id
                guard let other = byID[otherID] else { return nil }
                return NodeEdgeSummary(
                    id: edge.id,
                    otherNodeID: otherID,
                    label: other.label,
                    kind: edge.kind,
                    weight: edge.weight,
                    isBridge: edge.is_bridge == true
                )
            }
            .sorted { ($0.weight ?? 0) > ($1.weight ?? 0) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            if state.graphNodes.isEmpty {
                CortexEmptyState(
                    systemImage: "point.3.connected.trianglepath.dotted",
                    title: "Your Constellation",
                    message: "Your memory map appears as Cortex learns about you."
                )
            } else {
                if mapNodes.count > 8 {
                    searchField
                }
                mapCanvas
                compositionLine
                if let node = selectedNode {
                    NodeDetailPanel(
                        node: node,
                        communityName: communityName(for: node),
                        neighborhood: neighborhood,
                        edgeSummaries: selectedEdgeSummaries,
                        loading: loadingNeighborhood,
                        onExplore: onExplore,
                        onSelectConnection: { entityID in selectNode(entityID) }
                    )
                    .transition(.opacity)
                }
                legend
            }
        }
        .animation(.easeInOut(duration: 0.2), value: selectedNodeID)
        .onAppear {
            Task { await state.loadGraph() }
        }
        // If the graph reloads and the selected node vanishes, drop the stale selection.
        .onChange(of: state.graphNodes) { _ in
            if let id = selectedNodeID, !state.graphNodes.contains(where: { $0.id == id }) {
                selectedNodeID = nil
                neighborhood = nil
            }
            hoveredNodeID = nil
        }
    }

    private var searchField: some View {
        HStack(spacing: CortexDesign.Space.xs) {
            Image(systemName: "magnifyingglass")
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkFaint)
            TextField("Find a person, project, or topic", text: $searchText)
                .textFieldStyle(.plain)
                .font(CortexDesign.Typography.caption)
            if !searchText.isEmpty {
                Button {
                    searchText = ""
                } label: {
                    Image(systemName: "xmark.circle.fill").foregroundColor(CortexDesign.inkFaint)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, CortexDesign.Space.sm)
        .padding(.vertical, 6)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
    }

    /// Select a node by id and, for an entity node, fetch its cited neighborhood for the drill panel.
    private func selectNode(_ id: String) {
        selectedNodeID = id
        neighborhood = nil
        guard let node = state.graphNodes.first(where: { $0.id == id }), node.centrality != nil else { return }
        loadingNeighborhood = true
        Task {
            let result = await state.loadNeighborhood(id)
            await MainActor.run {
                if selectedNodeID == id { neighborhood = result }
                loadingNeighborhood = false
            }
        }
    }

    private var selectedNode: GraphNode? {
        guard let id = selectedNodeID else { return nil }
        return state.graphNodes.first { $0.id == id }
    }

    // MARK: Camera math

    private var effectiveZoom: CGFloat {
        min(Self.maxZoom, max(Self.minZoom, zoomScale * gestureZoom))
    }

    private var effectiveOffset: CGSize {
        CGSize(width: panOffset.width + gesturePan.width, height: panOffset.height + gesturePan.height)
    }

    /// layout space → screen space, zoom anchored at the canvas center.
    private func screenPoint(_ p: CGPoint, in size: CGSize) -> CGPoint {
        let c = CGPoint(x: size.width / 2, y: size.height / 2)
        let s = effectiveZoom
        let o = effectiveOffset
        return CGPoint(x: c.x + (p.x - c.x) * s + o.width, y: c.y + (p.y - c.y) * s + o.height)
    }

    /// screen space → layout space (inverse of `screenPoint`), for hit-testing taps and hovers.
    private func layoutPoint(_ p: CGPoint, in size: CGSize) -> CGPoint {
        let c = CGPoint(x: size.width / 2, y: size.height / 2)
        let s = effectiveZoom
        let o = effectiveOffset
        return CGPoint(x: c.x + (p.x - c.x - o.width) / s, y: c.y + (p.y - c.y - o.height) / s)
    }

    private func resetCamera() {
        withAnimation(.easeInOut(duration: 0.2)) {
            zoomScale = 1
            panOffset = .zero
        }
    }

    private func stepZoom(_ factor: CGFloat) {
        withAnimation(.easeInOut(duration: 0.15)) {
            zoomScale = min(Self.maxZoom, max(Self.minZoom, zoomScale * factor))
        }
    }

    private var mapCanvas: some View {
        GeometryReader { geo in
            let layout = MemoryMapLayout.layout(
                nodes: mapNodes,
                edges: mapEdges,
                size: geo.size
            )
            let matched = matchedNodeIDs
            let neighborIDs = selectedNeighborIDs
            ZStack(alignment: .bottomTrailing) {
                Canvas { context, size in
                    drawEdges(in: &context, size: size, layout: layout, matched: matched, neighborIDs: neighborIDs)
                    drawNodes(in: &context, size: size, layout: layout, matched: matched, neighborIDs: neighborIDs)
                }
                .contentShape(Rectangle())
                .onTapGesture { location in
                    let p = layoutPoint(location, in: geo.size)
                    if let hit = layout.nearestNode(to: p, tolerance: 12 / effectiveZoom) {
                        if selectedNodeID == hit.id {
                            selectedNodeID = nil
                            neighborhood = nil
                        } else {
                            selectNode(hit.id)
                        }
                    } else {
                        selectedNodeID = nil
                        neighborhood = nil
                    }
                }
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let point):
                        let p = layoutPoint(point, in: geo.size)
                        hoveredNodeID = layout.nearestNode(to: p, tolerance: 12 / effectiveZoom)?.id
                    case .ended:
                        hoveredNodeID = nil
                    }
                }
                // Pan (drag) + pinch zoom run simultaneously; taps still land because the drag
                // needs 3pt of travel before it claims the gesture.
                .gesture(
                    DragGesture(minimumDistance: 3)
                        .updating($gesturePan) { value, pan, _ in
                            pan = value.translation
                        }
                        .onEnded { value in
                            panOffset.width += value.translation.width
                            panOffset.height += value.translation.height
                        }
                        .simultaneously(
                            with: MagnificationGesture()
                                .updating($gestureZoom) { value, zoom, _ in
                                    zoom = value
                                }
                                .onEnded { value in
                                    zoomScale = min(Self.maxZoom, max(Self.minZoom, zoomScale * value))
                                }
                        )
                )

                cameraControls
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
        .frame(height: canvasHeight)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Memory map with \(mapNodes.count) points and \(mapEdges.count) connections. Pinch or use the zoom buttons to zoom, drag to pan, tap a point to see its connections.")
    }

    /// Quiet zoom in / zoom out / reset controls in the canvas corner — pinch is not
    /// discoverable on a Mac, buttons are.
    private var cameraControls: some View {
        HStack(spacing: 2) {
            Button { stepZoom(1 / 1.35) } label: {
                Image(systemName: "minus.magnifyingglass")
            }
            .buttonStyle(.plain)
            .frame(width: 26, height: 24)
            .help("Zoom out")
            .accessibilityLabel("Zoom out")
            Button { stepZoom(1.35) } label: {
                Image(systemName: "plus.magnifyingglass")
            }
            .buttonStyle(.plain)
            .frame(width: 26, height: 24)
            .help("Zoom in")
            .accessibilityLabel("Zoom in")
            if zoomScale != 1 || panOffset != .zero {
                Button { resetCamera() } label: {
                    Image(systemName: "arrow.counterclockwise")
                }
                .buttonStyle(.plain)
                .frame(width: 26, height: 24)
                .help("Reset view")
                .accessibilityLabel("Reset view")
            }
        }
        .font(.system(size: 12, weight: .medium))
        .foregroundColor(CortexDesign.inkSecondary)
        .padding(3)
        .background(CortexDesign.panelBackground.opacity(0.92))
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.sm, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
        .padding(8)
    }

    // MARK: Emphasis — one place decides how bright each node/edge is.

    /// Node emphasis combines every active lens: focus mode (selection), search filter, and the
    /// legend spotlight. 1.0 = full, lower = receded. Focus mode wins because it is the most
    /// explicit user intent.
    private func nodeEmphasis(_ node: GraphNode, matched: Set<String>?, neighborIDs: Set<String>) -> Double {
        if let selectedID = selectedNodeID {
            if node.id == selectedID { return 1.0 }
            return neighborIDs.contains(node.id) ? 0.95 : 0.15
        }
        if let matched, !matched.contains(node.id) { return 0.18 }
        if let spotlight, !spotlight.contains(node) { return 0.18 }
        return 0.85
    }

    private func drawEdges(in context: inout GraphicsContext, size: CGSize, layout: MemoryMapLayout, matched: Set<String>?, neighborIDs: Set<String>) {
        // Draw ordinary ties first, bridges (cross-community "surprising connections") next, and
        // the selected node's edges last and brightest, so the focused connections always sit on top.
        let ordered = mapEdges.sorted { lhs, rhs in
            func rank(_ e: GraphEdge) -> Int {
                if let id = selectedNodeID, e.source_id == id || e.target_id == id { return 2 }
                return e.is_bridge == true ? 1 : 0
            }
            return rank(lhs) < rank(rhs)
        }
        for edge in ordered {
            guard let la = layout.position(of: edge.source_id),
                  let lb = layout.position(of: edge.target_id) else { continue }
            let a = screenPoint(la, in: size)
            let b = screenPoint(lb, in: size)
            // Skip edges entirely outside the visible canvas (zoomed in).
            let edgeBounds = CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(a.x - b.x), height: abs(a.y - b.y))
            guard edgeBounds.intersects(CGRect(origin: .zero, size: size).insetBy(dx: -40, dy: -40)) else { continue }
            var path = Path()
            path.move(to: a)
            path.addLine(to: b)

            let weight = edge.weight ?? 0.5
            if let selectedID = selectedNodeID {
                // Focus mode: incident edges bright in accent, the rest nearly gone.
                if edge.source_id == selectedID || edge.target_id == selectedID {
                    let lineWidth = 1.4 + CGFloat(max(0, min(1, weight))) * 1.6
                    context.stroke(path, with: .color(CortexDesign.accent.opacity(0.85)), lineWidth: lineWidth)
                } else {
                    context.stroke(path, with: .color(CortexDesign.ink.opacity(0.05)), lineWidth: 0.8)
                }
                continue
            }

            // Dim an edge only when BOTH endpoints are filtered out, so a match keeps its context.
            let dimmed = matched != nil && !(matched!.contains(edge.source_id) || matched!.contains(edge.target_id))
            let dim: CGFloat = dimmed ? 0.28 : 1.0
            if edge.is_bridge == true {
                let style = StrokeStyle(lineWidth: 1.6, dash: [4, 3])
                context.stroke(path, with: .color(CortexDesign.gold.opacity(0.85 * dim)), style: style)
            } else {
                let lineWidth = 0.6 + CGFloat(max(0, min(1, weight))) * 1.4
                let color = weight >= 0.66 ? CortexDesign.gold : CortexDesign.ink
                let opacity = (0.14 + min(0.34, weight * 0.34)) * dim
                context.stroke(path, with: .color(color.opacity(opacity)), lineWidth: lineWidth)
            }
        }
    }

    private func drawNodes(in context: inout GraphicsContext, size: CGSize, layout: MemoryMapLayout, matched: Set<String>?, neighborIDs: Set<String>) {
        // Label budget grows with zoom: at fit-zoom only the most prominent nodes carry a label
        // (overlap-checked), zoomed in every visible node can be read. Hover/selection/neighbors
        // always label. Labels keep constant on-screen size — they never scale with the map.
        let budget = max(12, Int(14 * effectiveZoom))
        let labeledIDs = layout.topLabelNodeIDs(budget: budget)
        var occupiedLabelRects: [CGRect] = []
        let visibleRect = CGRect(origin: .zero, size: size).insetBy(dx: -20, dy: -20)

        // Draw circles first so no label ever sits under a later circle.
        for node in mapNodes {
            guard let lp = layout.position(of: node.id) else { continue }
            let point = screenPoint(lp, in: size)
            guard visibleRect.contains(point) else { continue }
            // Node size grows gently with zoom (sqrt) so zooming in separates clusters without
            // turning hubs into balloons.
            let radius = layout.radius(of: node) * max(1, effectiveZoom.squareRoot())
            let isSelected = node.id == selectedNodeID
            let isHovered = node.id == hoveredNodeID
            let emphasis = nodeEmphasis(node, matched: matched, neighborIDs: neighborIDs)
            let fill = MemoryMapView.color(for: node)

            let rect = CGRect(x: point.x - radius, y: point.y - radius, width: radius * 2, height: radius * 2)
            let circle = Path(ellipseIn: rect)

            if isSelected || isHovered {
                let haloRadius = radius + (isSelected ? 6 : 3)
                let haloRect = CGRect(x: point.x - haloRadius, y: point.y - haloRadius, width: haloRadius * 2, height: haloRadius * 2)
                context.fill(Path(ellipseIn: haloRect), with: .color(fill.opacity(isSelected ? 0.22 : 0.14)))
            }

            context.fill(circle, with: .color(fill.opacity(isSelected ? 1.0 : emphasis)))
            context.stroke(circle, with: .color(CortexDesign.panelBackground), lineWidth: 1)
        }

        // Labels second, most prominent first, greedily skipping collisions.
        let labelCandidates = mapNodes
            .filter { node in
                let isSelected = node.id == selectedNodeID
                let isHovered = node.id == hoveredNodeID
                let isNeighbor = selectedNodeID != nil && neighborIDs.contains(node.id)
                let emphasis = nodeEmphasis(node, matched: matched, neighborIDs: neighborIDs)
                guard emphasis > 0.3 else { return false }
                return labeledIDs.contains(node.id) || isSelected || isHovered || isNeighbor
            }
            .sorted { lhs, rhs in
                // Selection/hover win outright, then neighbors of the selection, then bigger nodes.
                func priority(_ node: GraphNode) -> CGFloat {
                    if node.id == selectedNodeID || node.id == hoveredNodeID { return .greatestFiniteMagnitude }
                    if selectedNodeID != nil && neighborIDs.contains(node.id) { return 100_000 + layout.radius(of: node) }
                    return layout.radius(of: node)
                }
                return priority(lhs) > priority(rhs)
            }
        for node in labelCandidates {
            guard let lp = layout.position(of: node.id) else { continue }
            let point = screenPoint(lp, in: size)
            guard visibleRect.contains(point) else { continue }
            let radius = layout.radius(of: node) * max(1, effectiveZoom.squareRoot())
            let isSelected = node.id == selectedNodeID
            // Whole-sentence labels (memories/tasks) are display-truncated; the detail panel
            // shows the full text on selection.
            let displayLabel = node.label.count > 42 ? String(node.label.prefix(40)) + "…" : node.label
            let text = Text(displayLabel)
                .font(CortexDesign.Typography.caption)
                .foregroundColor(isSelected ? CortexDesign.ink : CortexDesign.inkSecondary)
            let resolved = context.resolve(text)
            let textSize = resolved.measure(in: CGSize(width: 140, height: 40))
            var textX = point.x + radius + 4
            if textX + textSize.width > size.width - 4 {
                textX = point.x - radius - 4 - textSize.width
            }
            let textPoint = CGPoint(x: textX, y: point.y - textSize.height / 2)
            let labelRect = CGRect(origin: textPoint, size: textSize).insetBy(dx: -3, dy: -2)
            // A label that would overlap an already-placed one is dropped (except the
            // selected node's, which always shows) — fewer, readable labels beat many
            // colliding ones. The node itself stays visible and hoverable.
            if !isSelected && occupiedLabelRects.contains(where: { $0.intersects(labelRect) }) {
                continue
            }
            occupiedLabelRects.append(labelRect)
            context.draw(resolved, in: CGRect(origin: textPoint, size: textSize))
        }
    }

    // MARK: Composition line — what is actually on this map, in numbers.

    /// "42 people · 18 projects · 65 topics · 210 connections" — real counts of what is DRAWN,
    /// so the user knows what they are looking at — plus an honest toggle for the hidden
    /// memory/task detail layer.
    private var compositionLine: some View {
        let nodes = mapNodes
        let people = nodes.filter { ["person", "people"].contains($0.type.lowercased()) }.count
        let projects = nodes.filter { $0.type.lowercased() == "project" }.count
        let topics = nodes.filter { ["topic", "theme", "concept"].contains($0.type.lowercased()) }.count
        var parts: [String] = []
        if people > 0 { parts.append("\(people) \(people == 1 ? "person" : "people")") }
        if projects > 0 { parts.append("\(projects) project\(projects == 1 ? "" : "s")") }
        if topics > 0 { parts.append("\(topics) topic\(topics == 1 ? "" : "s")") }
        let others = nodes.count - people - projects - topics
        if others > 0 { parts.append("\(others) other\(others == 1 ? "" : "s")") }
        parts.append("\(mapEdges.count) connection\(mapEdges.count == 1 ? "" : "s")")
        return HStack(spacing: CortexDesign.Space.sm) {
            Text(parts.joined(separator: " · "))
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkFaint)
                .accessibilityLabel("Map shows \(parts.joined(separator: ", "))")
            if hiddenDetailCount > 0 {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { showDetailLayer.toggle() }
                } label: {
                    Text(showDetailLayer
                         ? "Hide memories & tasks"
                         : "Show \(hiddenDetailCount) memories & tasks")
                        .font(CortexDesign.Typography.caption)
                        .underline()
                        .foregroundColor(CortexDesign.inkSecondary)
                }
                .buttonStyle(.plain)
                .help(showDetailLayer
                      ? "Return to the readable entity map"
                      : "Overlay every raw memory and task node")
            }
            Spacer(minLength: 0)
        }
    }

    // MARK: Legend — honest, adaptive, clickable.

    /// The backend's community labels ("Work", "Family") keyed by community id, when the
    /// analysis produced any. This is what actually drives node color, so it is what the
    /// legend must show.
    private var communityLegendEntries: [(community: Int, label: String, color: Color)] {
        guard let labels = state.graphAnalysis?.community_labels, !labels.isEmpty else { return [] }
        return labels
            .compactMap { key, value -> (Int, String, Color)? in
                guard let id = Int(key), let label = value, !label.isEmpty else { return nil }
                return (id, label, MemoryMapView.communityColor(id))
            }
            .sorted { $0.0 < $1.0 }
            .prefix(5)
            .map { $0 }
    }

    private func communityName(for node: GraphNode) -> String? {
        guard let community = node.community,
              let labels = state.graphAnalysis?.community_labels,
              let label = labels[String(community)] ?? nil else { return nil }
        return label
    }

    private var legend: some View {
        let communities = communityLegendEntries
        return HStack(spacing: CortexDesign.Space.sm) {
            if !communities.isEmpty {
                // Community legend: the map is colored by cluster, so name the clusters.
                // Clicking a chip spotlights that cluster; clicking again clears it.
                ForEach(communities, id: \.community) { entry in
                    legendChip(
                        label: entry.label,
                        color: entry.color,
                        active: spotlight == .community(entry.community)
                    ) {
                        spotlight = spotlight == .community(entry.community) ? nil : .community(entry.community)
                    }
                }
            } else {
                ForEach(MemoryMapView.legendEntries, id: \.label) { entry in
                    legendChip(
                        label: entry.label,
                        color: entry.color,
                        active: spotlight == .type(entry.label)
                    ) {
                        spotlight = spotlight == .type(entry.label) ? nil : .type(entry.label)
                    }
                }
            }
            Spacer(minLength: 0)
            Text(selectedNodeID == nil ? "Tap a point to see its connections" : "Tap empty space to clear")
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkFaint)
        }
    }

    private func legendChip(label: String, color: Color, active: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: CortexDesign.Space.xs) {
                Circle()
                    .fill(color)
                    .frame(width: 7, height: 7)
                Text(label)
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .lineLimit(1)
                    .foregroundColor(active ? CortexDesign.ink : CortexDesign.inkFaint)
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .background(active ? color.opacity(0.14) : Color.clear)
            .clipShape(Capsule())
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .help(active ? "Show everything" : "Spotlight \(label)")
        .accessibilityLabel(active ? "Clear \(label) spotlight" : "Spotlight \(label)")
    }

    // MARK: Palette — Archive colors keyed to node type.

    /// Maps a backend node `type` to an Archive palette color. Unknown types fall to secondary ink
    /// so the map degrades gracefully as the taxonomy grows.
    static func color(for type: String) -> Color {
        switch type.lowercased() {
        case "person", "people":
            return CortexDesign.accent
        case "project":
            return CortexDesign.gold
        case "topic", "theme", "concept":
            return CortexDesign.sealMoss
        case "source", "notes", "note", "document":
            return CortexDesign.inkSecondary
        default:
            return CortexDesign.inkSecondary
        }
    }

    /// A fixed, deterministic palette so each community (life/work area) keeps its color across
    /// renders, cycled by community id. Nodes with no community (captures/memories/tasks, or an
    /// empty analysis) fall back to the type color, so the map degrades gracefully.
    static let communityPalette: [Color] = [
        CortexDesign.accent,
        CortexDesign.gold,
        CortexDesign.sealMoss,
        Color(red: 0.42, green: 0.35, blue: 0.62),
        Color(red: 0.70, green: 0.44, blue: 0.30),
        Color(red: 0.28, green: 0.52, blue: 0.55),
    ]

    static func communityColor(_ community: Int) -> Color {
        let count = communityPalette.count
        return communityPalette[((community % count) + count) % count]
    }

    static func color(for node: GraphNode) -> Color {
        if let community = node.community {
            return communityColor(community)
        }
        return color(for: node.type)
    }

    static let legendEntries: [(label: String, color: Color)] = [
        ("People", CortexDesign.accent),
        ("Projects", CortexDesign.gold),
        ("Topics", CortexDesign.sealMoss),
        ("Sources", CortexDesign.inkSecondary),
    ]
}

/// Legend spotlight lens: one community (analysis-colored maps) or one node type (fallback maps).
private enum MapSpotlight: Equatable {
    case community(Int)
    case type(String)

    func contains(_ node: GraphNode) -> Bool {
        switch self {
        case .community(let id):
            return node.community == id
        case .type(let label):
            switch label {
            case "People":
                return ["person", "people"].contains(node.type.lowercased())
            case "Projects":
                return node.type.lowercased() == "project"
            case "Topics":
                return ["topic", "theme", "concept"].contains(node.type.lowercased())
            case "Sources":
                return ["source", "notes", "note", "document"].contains(node.type.lowercased())
            default:
                return true
            }
        }
    }
}

/// One resolved edge row for the detail panel: the other endpoint's label plus the edge's own
/// kind and weight — the map's connections, in words.
struct NodeEdgeSummary: Identifiable, Hashable {
    let id: String
    let otherNodeID: String
    let label: String
    let kind: String
    let weight: Double?
    let isBridge: Bool
}

/// The quiet detail card shown when a node is selected: its label, a human type word, the cluster
/// it belongs to, the backend's detail text, and every connection the map draws for it.
private struct NodeDetailPanel: View {
    let node: GraphNode
    var communityName: String? = nil
    var neighborhood: EntityNeighborhood? = nil
    var edgeSummaries: [NodeEdgeSummary] = []
    var loading: Bool = false
    var onExplore: ((GraphNode) -> Void)? = nil
    /// Tapping a connection re-centers the drill on that entity/node.
    var onSelectConnection: ((String) -> Void)? = nil

    private var typeWord: String {
        switch node.type.lowercased() {
        case "person", "people": return "Person"
        case "project": return "Project"
        case "topic", "theme", "concept": return "Topic"
        case "source", "notes", "note", "document": return "Source"
        default: return node.type.capitalized
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.xs) {
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.sm) {
                Circle()
                    .fill(MemoryMapView.color(for: node))
                    .frame(width: 8, height: 8)
                Text(node.label)
                    .font(CortexDesign.Typography.title)
                    .foregroundColor(CortexDesign.ink)
                Spacer(minLength: 0)
                if node.is_hub == true {
                    Text("HUB")
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.gold)
                }
                Text(typeWord.uppercased())
                    .font(CortexDesign.Typography.stamp)
                    .kerning(0.8)
                    .foregroundColor(CortexDesign.inkFaint)
            }
            if let communityName, !communityName.isEmpty {
                // Name the cluster this node belongs to — same words as the legend chips.
                Text("In the \(communityName) cluster")
                    .font(CortexDesign.Typography.caption)
                    .foregroundColor(CortexDesign.inkSecondary)
            }
            if let detail = node.detail, !detail.isEmpty {
                Text(detail)
                    .font(CortexDesign.Typography.prose(13))
                    .lineSpacing(3)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            connectionsSection
            if let onExplore {
                Button {
                    onExplore(node)
                } label: {
                    Label("Explore in Ask", systemImage: "sparkle.magnifyingglass")
                        .font(CortexDesign.Typography.caption.weight(.semibold))
                }
                .buttonStyle(.bordered)
                .tint(CortexDesign.accent)
            }
        }
        .cortexCard(padding: CortexDesign.Space.md)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(node.label), \(typeWord)\(node.detail.map { ". \($0)" } ?? "")")
    }

    @ViewBuilder
    private var connectionsSection: some View {
        if loading {
            Text("Loading connections…")
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkFaint)
        } else if let connections = neighborhood?.connections, !connections.isEmpty {
            // Entity nodes get the cited neighborhood (relation + example evidence).
            Divider().overlay(CortexDesign.hairline)
            Text("CONNECTED")
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            ForEach(connections.prefix(6)) { connection in
                Button {
                    onSelectConnection?(connection.entity_id)
                } label: {
                    HStack(spacing: CortexDesign.Space.xs) {
                        Text(connection.label ?? connection.entity_id)
                            .font(CortexDesign.Typography.caption.weight(.medium))
                            .foregroundColor(CortexDesign.ink)
                        if let relation = connection.relation, !relation.isEmpty {
                            Text(relation.replacingOccurrences(of: "_", with: " "))
                                .font(CortexDesign.Typography.stamp)
                                .foregroundColor(CortexDesign.inkFaint)
                        }
                        Spacer(minLength: 0)
                        if let example = connection.example, !example.isEmpty {
                            Image(systemName: "quote.opening")
                                .font(.system(size: 8))
                                .foregroundColor(CortexDesign.inkFaint)
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        } else if !edgeSummaries.isEmpty {
            // Every other node still names its map connections from the edge list — a selection
            // must never leave the user guessing what the highlighted lines mean.
            Divider().overlay(CortexDesign.hairline)
            Text("CONNECTED ON THE MAP")
                .font(CortexDesign.Typography.stamp)
                .kerning(0.8)
                .foregroundColor(CortexDesign.inkFaint)
            ForEach(edgeSummaries.prefix(6)) { summary in
                Button {
                    onSelectConnection?(summary.otherNodeID)
                } label: {
                    HStack(spacing: CortexDesign.Space.xs) {
                        Text(summary.label)
                            .font(CortexDesign.Typography.caption.weight(.medium))
                            .foregroundColor(CortexDesign.ink)
                            .lineLimit(1)
                        Text(summary.kind.replacingOccurrences(of: "_", with: " "))
                            .font(CortexDesign.Typography.stamp)
                            .foregroundColor(CortexDesign.inkFaint)
                        Spacer(minLength: 0)
                        if summary.isBridge {
                            Text("BRIDGE")
                                .font(CortexDesign.Typography.stamp)
                                .kerning(0.8)
                                .foregroundColor(CortexDesign.gold)
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
    }
}

/// Deterministic force-directed layout for the memory map.
///
/// Structure over decoration: connected nodes attract along their edges, all nearby nodes repel
/// (via a uniform spatial grid, so the simulation stays O(n·k) per pass), each community pulls
/// gently toward its own centroid, and everything feels a weak centering gravity scaled by
/// prominence (hubs settle centrally). Initial positions are seeded from a stable FNV-1a hash of
/// each node id and the simulation runs a fixed number of cooling iterations with no randomness,
/// so the same graph always produces the same map. A final overlap-relaxation pass separates
/// touching circles, and the result is normalized to fill the canvas.
///
/// Layout is memoized per (nodes, edges, size) so pan/zoom/hover re-renders never re-run the
/// simulation.
struct MemoryMapLayout {
    private var positions: [String: CGPoint] = [:]
    private var radii: [String: CGFloat] = [:]
    private let nodesByID: [String: GraphNode]

    // MARK: Memoized entry point

    /// Small one-slot cache: the map re-renders constantly while hovering/zooming, but the
    /// layout only changes when the data or canvas size changes.
    private static var cachedKey: UInt64 = 0
    private static var cachedLayout: MemoryMapLayout?
    private static let cacheLock = NSLock()

    static func layout(nodes: [GraphNode], edges: [GraphEdge], size: CGSize) -> MemoryMapLayout {
        var hash: UInt64 = 0xcbf29ce484222325
        func mix(_ string: String) {
            for byte in string.utf8 {
                hash ^= UInt64(byte)
                hash = hash &* 0x100000001b3
            }
        }
        for node in nodes {
            mix(node.id)
            mix(String(node.importance ?? 0))
        }
        for edge in edges { mix(edge.id) }
        mix("\(Int(size.width))x\(Int(size.height))")

        cacheLock.lock()
        defer { cacheLock.unlock() }
        if let cached = cachedLayout, cachedKey == hash {
            return cached
        }
        let fresh = MemoryMapLayout(nodes: nodes, edges: edges, size: size)
        cachedKey = hash
        cachedLayout = fresh
        return fresh
    }

    init(nodes: [GraphNode], edges: [GraphEdge], size: CGSize) {
        var byID: [String: GraphNode] = [:]
        for node in nodes { byID[node.id] = node }
        self.nodesByID = byID

        guard !nodes.isEmpty, size.width > 0, size.height > 0 else { return }

        // Order matters for determinism: every pass iterates nodes sorted by id.
        let ordered = nodes.sorted { $0.id < $1.id }
        let count = ordered.count
        var indexByID: [String: Int] = [:]
        for (index, node) in ordered.enumerated() { indexByID[node.id] = index }

        let importances = nodes.map { CGFloat($0.importance ?? 1) }
        let maxImportance = max(1, importances.max() ?? 1)

        // Prefer graph centrality (the "god node" signal) for size + centre-pull; fall back to
        // importance (damped, so real hubs still dominate) for non-entity nodes that carry none.
        func prominence(_ node: GraphNode) -> CGFloat {
            if let c = node.centrality { return max(0, min(1, CGFloat(c))) }
            return max(0, min(1, (CGFloat(node.importance ?? 1) / maxImportance) * 0.6))
        }

        // --- Seed: deterministic radial scatter from the id hash. ---
        let world = CGSize(width: max(size.width, 320), height: max(size.height, 320))
        let center = CGPoint(x: world.width / 2, y: world.height / 2)
        let seedRadius = min(world.width, world.height) / 2 - 24
        var x = [CGFloat](repeating: 0, count: count)
        var y = [CGFloat](repeating: 0, count: count)
        var prom = [CGFloat](repeating: 0, count: count)
        var community = [Int?](repeating: nil, count: count)
        for (index, node) in ordered.enumerated() {
            let seed = MemoryMapLayout.stableHash(node.id)
            let p = prominence(node)
            prom[index] = p
            community[index] = node.community
            let baseAngle = (CGFloat(index) / CGFloat(count)) * 2 * .pi
            let jitter = (CGFloat(seed % 1000) / 1000.0 - 0.5) * (2 * .pi / CGFloat(max(count, 1)))
            let angle = baseAngle + jitter
            let ringJitter = CGFloat((seed / 1000) % 1000) / 1000.0
            let distance = seedRadius * (0.25 + ((1 - p) * 0.6 + ringJitter * 0.4) * 0.75)
            x[index] = center.x + cos(angle) * distance
            y[index] = center.y + sin(angle) * distance
        }

        // Edge springs between laid-out endpoints; stronger edges want shorter links.
        struct Spring { let a: Int; let b: Int; let length: CGFloat; let strength: CGFloat }
        var springs: [Spring] = []
        springs.reserveCapacity(edges.count)
        for edge in edges.sorted(by: { $0.id < $1.id }) {
            guard let a = indexByID[edge.source_id], let b = indexByID[edge.target_id], a != b else { continue }
            let weight = CGFloat(max(0, min(1, edge.weight ?? 0.5)))
            springs.append(Spring(a: a, b: b, length: 120 - weight * 60, strength: 0.02 + weight * 0.03))
        }

        // --- Simulate: fixed iterations, cooling displacement cap, spatial-grid repulsion. ---
        let iterations = 90
        let repulsionRadius: CGFloat = 96
        let repulsionStrength: CGFloat = 1_500
        let cell = repulsionRadius
        for iteration in 0..<iterations {
            let temperature = 1 - CGFloat(iteration) / CGFloat(iterations)
            let maxStep = 18 * temperature + 1.5
            var fx = [CGFloat](repeating: 0, count: count)
            var fy = [CGFloat](repeating: 0, count: count)

            // Repulsion within the grid neighborhood.
            var grid: [Int64: [Int]] = [:]
            for i in 0..<count {
                let key = MemoryMapLayout.gridKey(x[i], y[i], cell: cell)
                grid[key, default: []].append(i)
            }
            for i in 0..<count {
                let cx = Int64((x[i] / cell).rounded(.down))
                let cy = Int64((y[i] / cell).rounded(.down))
                for dx in -1...1 {
                    for dy in -1...1 {
                        guard let bucket = grid[(cx + Int64(dx)) &* 73_856_093 ^ (cy + Int64(dy)) &* 19_349_663] else { continue }
                        for j in bucket where j != i {
                            let ddx = x[i] - x[j]
                            let ddy = y[i] - y[j]
                            let distSq = max(ddx * ddx + ddy * ddy, 4)
                            guard distSq < repulsionRadius * repulsionRadius else { continue }
                            let force = repulsionStrength / distSq
                            let dist = distSq.squareRoot()
                            fx[i] += (ddx / dist) * force
                            fy[i] += (ddy / dist) * force
                        }
                    }
                }
            }

            // Spring attraction along edges.
            for spring in springs {
                let ddx = x[spring.b] - x[spring.a]
                let ddy = y[spring.b] - y[spring.a]
                let dist = max((ddx * ddx + ddy * ddy).squareRoot(), 0.5)
                let stretch = dist - spring.length
                let force = stretch * spring.strength
                let ux = ddx / dist
                let uy = ddy / dist
                fx[spring.a] += ux * force
                fy[spring.a] += uy * force
                fx[spring.b] -= ux * force
                fy[spring.b] -= uy * force
            }

            // Community gravity: members drift toward their cluster's centroid so clusters read
            // as clusters; recomputed each pass from current positions.
            var centroids: [Int: (x: CGFloat, y: CGFloat, n: CGFloat)] = [:]
            for i in 0..<count {
                guard let c = community[i] else { continue }
                var acc = centroids[c] ?? (0, 0, 0)
                acc.x += x[i]; acc.y += y[i]; acc.n += 1
                centroids[c] = acc
            }
            for i in 0..<count {
                if let c = community[i], let acc = centroids[c], acc.n > 1 {
                    fx[i] += (acc.x / acc.n - x[i]) * 0.02
                    fy[i] += (acc.y / acc.n - y[i]) * 0.02
                }
                // Weak centering gravity; prominent nodes feel more of it, so hubs settle centrally.
                let gravity = 0.004 + prom[i] * 0.012
                fx[i] += (center.x - x[i]) * gravity
                fy[i] += (center.y - y[i]) * gravity
            }

            // Apply with the cooling cap.
            for i in 0..<count {
                let mag = (fx[i] * fx[i] + fy[i] * fy[i]).squareRoot()
                guard mag > 0.01 else { continue }
                let step = min(mag, maxStep)
                x[i] += (fx[i] / mag) * step
                y[i] += (fy[i] / mag) * step
            }
        }

        // --- Radii (before overlap relaxation, which needs them). ---
        var radius = [CGFloat](repeating: 5, count: count)
        for (index, node) in ordered.enumerated() {
            radius[index] = 5 + prom[index] * 9
            if node.is_hub == true { radius[index] = max(radius[index], 13) }
        }

        // --- Overlap relaxation: push apart touching circles (deterministic sweep). ---
        for _ in 0..<10 {
            var moved = false
            var grid: [Int64: [Int]] = [:]
            for i in 0..<count {
                grid[MemoryMapLayout.gridKey(x[i], y[i], cell: 48), default: []].append(i)
            }
            for i in 0..<count {
                let cx = Int64((x[i] / 48).rounded(.down))
                let cy = Int64((y[i] / 48).rounded(.down))
                for dx in -1...1 {
                    for dy in -1...1 {
                        guard let bucket = grid[(cx + Int64(dx)) &* 73_856_093 ^ (cy + Int64(dy)) &* 19_349_663] else { continue }
                        for j in bucket where j > i {
                            let minDist = radius[i] + radius[j] + 3
                            let ddx = x[j] - x[i]
                            let ddy = y[j] - y[i]
                            let distSq = ddx * ddx + ddy * ddy
                            guard distSq < minDist * minDist else { continue }
                            let dist = max(distSq.squareRoot(), 0.1)
                            let push = (minDist - dist) / 2
                            let ux = ddx / dist
                            let uy = ddy / dist
                            x[i] -= ux * push; y[i] -= uy * push
                            x[j] += ux * push; y[j] += uy * push
                            moved = true
                        }
                    }
                }
            }
            if !moved { break }
        }

        // --- Normalize into the canvas with padding, preserving aspect ratio. ---
        var minX = CGFloat.greatestFiniteMagnitude, maxX = -CGFloat.greatestFiniteMagnitude
        var minY = CGFloat.greatestFiniteMagnitude, maxY = -CGFloat.greatestFiniteMagnitude
        for i in 0..<count {
            minX = min(minX, x[i]); maxX = max(maxX, x[i])
            minY = min(minY, y[i]); maxY = max(maxY, y[i])
        }
        let pad: CGFloat = 24
        let spanX = max(maxX - minX, 1)
        let spanY = max(maxY - minY, 1)
        let scale = min((size.width - pad * 2) / spanX, (size.height - pad * 2) / spanY)
        let offsetX = (size.width - spanX * scale) / 2
        let offsetY = (size.height - spanY * scale) / 2
        for (index, node) in ordered.enumerated() {
            positions[node.id] = CGPoint(
                x: (x[index] - minX) * scale + offsetX,
                y: (y[index] - minY) * scale + offsetY
            )
            radii[node.id] = radius[index]
        }
    }

    private static func gridKey(_ x: CGFloat, _ y: CGFloat, cell: CGFloat) -> Int64 {
        let cx = Int64((x / cell).rounded(.down))
        let cy = Int64((y / cell).rounded(.down))
        return cx &* 73_856_093 ^ cy &* 19_349_663
    }

    func position(of id: String) -> CGPoint? { positions[id] }

    /// The node ids that deserve an always-on label: the `budget` nodes with the largest radii
    /// (hubs and top entities). The caller grows the budget with zoom, because a zoomed-in map
    /// has room for far more text than the fit view.
    func topLabelNodeIDs(budget: Int) -> Set<String> {
        Set(radii.sorted { $0.value > $1.value }.prefix(max(budget, 0)).map(\.key))
    }

    func radius(of node: GraphNode) -> CGFloat { radii[node.id] ?? 5 }

    /// Hit-tests a layout-space point to the nearest node within `tolerance` beyond each node's
    /// radius; nil when the tap landed on empty space (so callers can deselect). Callers convert
    /// screen taps into layout space first, dividing tolerance by the zoom so the finger target
    /// stays constant on screen.
    func nearestNode(to point: CGPoint, tolerance: CGFloat = 12) -> GraphNode? {
        var best: (node: GraphNode, distance: CGFloat)?
        for (id, nodePoint) in positions {
            guard let node = nodesByID[id] else { continue }
            let dx = nodePoint.x - point.x
            let dy = nodePoint.y - point.y
            let distance = (dx * dx + dy * dy).squareRoot()
            let hitRadius = (radii[id] ?? 5) + tolerance
            guard distance <= hitRadius else { continue }
            if best == nil || distance < best!.distance {
                best = (node, distance)
            }
        }
        return best?.node
    }

    /// A small, stable, platform-independent hash of a string (FNV-1a) — unlike `String.hashValue`,
    /// this is deterministic across runs, so the layout never shuffles between launches.
    private static func stableHash(_ string: String) -> UInt64 {
        var hash: UInt64 = 0xcbf29ce484222325
        for byte in string.utf8 {
            hash ^= UInt64(byte)
            hash = hash &* 0x100000001b3
        }
        return hash
    }
}
