import SwiftUI
import AppKit
import UniformTypeIdentifiers

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

    // MARK: Drag — Obsidian-style: press on a node to move it, press on empty space to pan.

    /// The mutable working copy of node positions (canvas space) the Canvas actually draws. Seeded
    /// from the memoized layout whenever the layout identity changes, then mutated live while a node
    /// is being dragged. Never fed back into the deterministic layout/cache.
    @State private var workingPositions: [String: CGPoint] = [:]
    /// The layout identity `workingPositions` was seeded from; a mismatch triggers a reseed.
    @State private var seededLayoutKey: UInt64 = 0
    /// What the in-flight drag is doing: dragging a node (by id) or panning the camera. Decided on
    /// the first drag change by hit-testing the drag's start location; nil when no drag is active.
    @State private var activeDrag: DragMode? = nil

    private enum DragMode: Equatable {
        /// Dragging a node, identified by id.
        case node(id: String)
        /// Panning the camera; carries the committed pan offset captured at drag start.
        case pan(base: CGSize)
    }

    /// The id of the node currently being dragged, if any (nil when panning or idle).
    private var draggedNodeID: String? {
        if case .node(let id) = activeDrag { return id }
        return nil
    }

    /// Legend spotlight: dims everything outside one community (or one type in the fallback
    /// type legend). Tapping the active chip again clears it.
    @State private var spotlight: MapSpotlight? = nil

    /// The constellation defaults to the ENTITY layer — people, projects, topics, sources —
    /// because that is the readable "who/what" picture. Raw memory/task sentence-nodes (often
    /// hundreds) turn the map into a blob; they stay one toggle away for users who want density.
    @State private var showDetailLayer = false

    /// Whether the share-card sheet is up. The card view, its layout pass and the 2× render are
    /// all built inside the sheet, so the map pays nothing until the user actually asks to share.
    @State private var showShareCard = false

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

    /// Direct neighbors of a node, straight from a precomputed edge list — the set that stays bright
    /// in focus mode (selection) or hover highlight. Takes the edges so the caller can compute the
    /// (potentially projected) `mapEdges` once per render and reuse it for both lenses.
    private func directNeighbors(of id: String?, in edges: [GraphEdge]) -> Set<String> {
        guard let id else { return [] }
        var out: Set<String> = []
        for edge in edges where edge.source_id == id || edge.target_id == id {
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
        // The share card is explicitly user-triggered (node labels are personal), and everything
        // about it — data snapshot, layout, 2× raster — is built only when this sheet presents.
        .sheet(isPresented: $showShareCard) {
            ConstellationShareSheet(
                nodes: mapNodes,
                edges: mapEdges,
                analysis: state.graphAnalysis,
                memoriesCount: state.stats?.memories
            )
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
        // Pan is committed directly to `panOffset` during a pan drag (see the drag gesture), so no
        // separate live-gesture term is needed here.
        panOffset
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

    // MARK: Position resolution — the Canvas reads the mutable working copy, falling back to the
    // canonical layout so the very first render (before seeding) and any missing node still draw.

    private func resolvedPosition(of id: String, layout: MemoryMapLayout) -> CGPoint? {
        workingPositions[id] ?? layout.position(of: id)
    }

    /// Hit-test a layout-space point against the CURRENT (possibly dragged) positions. Mirrors
    /// `MemoryMapLayout.nearestNode` but reads the working copy so a dragged node stays grabbable.
    private func nearestNode(to point: CGPoint, tolerance: CGFloat, layout: MemoryMapLayout, nodes: [GraphNode]) -> GraphNode? {
        var best: (node: GraphNode, distance: CGFloat)?
        for node in nodes {
            guard let np = resolvedPosition(of: node.id, layout: layout) else { continue }
            let dx = np.x - point.x
            let dy = np.y - point.y
            let distance = (dx * dx + dy * dy).squareRoot()
            let hitRadius = layout.radius(of: node) + tolerance
            guard distance <= hitRadius else { continue }
            if best == nil || distance < best!.distance {
                best = (node, distance)
            }
        }
        return best?.node
    }

    /// Seed / reseed the mutable working positions from the canonical layout when its identity
    /// changes (new graph data or canvas size). Called from `.onChange`/`.onAppear`, never during
    /// a draw, so it never mutates state mid-render.
    private func syncWorkingPositions(to layout: MemoryMapLayout) {
        guard layout.identityKey != seededLayoutKey || workingPositions.isEmpty else { return }
        workingPositions = layout.allPositions()
        seededLayoutKey = layout.identityKey
        activeDrag = nil
    }

    // MARK: Drag handling — node-drag vs camera-pan, decided once at drag start.

    private func handleDragChanged(_ value: DragGesture.Value, size: CGSize, layout: MemoryMapLayout) {
        // Ensure the working copy is seeded (first interaction might precede an onAppear reseed).
        if workingPositions.isEmpty { syncWorkingPositions(to: layout) }

        // First change of this drag: decide node-drag vs pan by hit-testing the start location.
        if activeDrag == nil {
            let startLayout = layoutPoint(value.startLocation, in: size)
            if let hit = nearestNode(to: startLayout, tolerance: 10 / effectiveZoom, layout: layout, nodes: mapNodes) {
                activeDrag = .node(id: hit.id)
                hoveredNodeID = hit.id
                NSCursor.closedHand.set()
            } else {
                activeDrag = .pan(base: panOffset)
            }
        }

        switch activeDrag {
        case .node(let id):
            // Pin the dragged node under the pointer, in layout/canvas space. Guard against NaN.
            let p = layoutPoint(value.location, in: size)
            guard p.x.isFinite, p.y.isFinite else { return }
            workingPositions[id] = p
        case .pan(let base):
            panOffset = CGSize(width: base.width + value.translation.width,
                               height: base.height + value.translation.height)
        case .none:
            break
        }
    }

    private func handleDragEnded(_ value: DragGesture.Value, size: CGSize, layout: MemoryMapLayout) {
        switch activeDrag {
        case .node(let id):
            // Final pinned position, then a short settle so neighbors ease into place around it.
            let p = layoutPoint(value.location, in: size)
            if p.x.isFinite, p.y.isFinite { workingPositions[id] = p }
            let settled = layout.settle(
                current: workingPositions,
                edges: mapEdges,
                pinned: id,
                size: size,
                iterations: 20
            )
            withAnimation(.easeOut(duration: 0.25)) {
                workingPositions = settled
            }
            NSCursor.pointingHand.set()
        case .pan:
            // Pan was committed live to `panOffset` in `handleDragChanged`; nothing to finalize.
            break
        case .none:
            break
        }
        activeDrag = nil
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
            // Compute the (potentially projected) edge list ONCE per render, and derive both the
            // selected- and hovered-neighbor sets from it, so nothing recomputes it per node.
            let edges = mapEdges
            let matched = matchedNodeIDs
            let neighborIDs = directNeighbors(of: selectedNodeID, in: edges)
            let hoverNeighbors = directNeighbors(of: hoveredNodeID, in: edges)
            ZStack(alignment: .bottomTrailing) {
                Canvas { context, size in
                    drawEdges(in: &context, size: size, layout: layout, edges: edges, matched: matched)
                    drawNodes(in: &context, size: size, layout: layout, matched: matched, neighborIDs: neighborIDs, hoverNeighbors: hoverNeighbors)
                }
                .contentShape(Rectangle())
                .onTapGesture { location in
                    let p = layoutPoint(location, in: geo.size)
                    if let hit = nearestNode(to: p, tolerance: 12 / effectiveZoom, layout: layout, nodes: mapNodes) {
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
                        // Don't retarget the hover while a node is being dragged (the pointer is
                        // "carrying" that node); leave the dragged node highlighted.
                        if case .node = activeDrag { return }
                        let p = layoutPoint(point, in: geo.size)
                        let hit = nearestNode(to: p, tolerance: 12 / effectiveZoom, layout: layout, nodes: mapNodes)
                        hoveredNodeID = hit?.id
                        // A pointing hand over a grabbable node, arrow over empty space.
                        if hit != nil { NSCursor.pointingHand.set() } else { NSCursor.arrow.set() }
                    case .ended:
                        hoveredNodeID = nil
                        NSCursor.arrow.set()
                    }
                }
                // One drag gesture handles BOTH node-drag and camera-pan: the first change hit-tests
                // the start location to decide which. Pinch zoom runs simultaneously. Taps still land
                // because the drag needs 3pt of travel before it claims the gesture.
                .gesture(
                    DragGesture(minimumDistance: 3)
                        .onChanged { value in handleDragChanged(value, size: geo.size, layout: layout) }
                        .onEnded { value in handleDragEnded(value, size: geo.size, layout: layout) }
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

                shareControl
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
            }
            .frame(width: geo.size.width, height: geo.size.height)
            .onAppear { syncWorkingPositions(to: layout) }
            .onChange(of: layout.identityKey) { _ in syncWorkingPositions(to: layout) }
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

    /// The share affordance, opposite the zoom cluster. Opening it is the ONLY path that ever
    /// produces the share card — labels are personal content, so sharing stays an explicit act.
    private var shareControl: some View {
        CortexIconButton(
            systemImage: "square.and.arrow.up",
            role: .secondary,
            size: .small,
            help: "Share your constellation"
        ) {
            showShareCard = true
        }
        .accessibilityLabel("Share your constellation")
        .padding(8)
    }

    // MARK: Emphasis — one place decides how bright each node/edge is.

    /// Node emphasis combines every active lens: focus mode (selection), hover highlight, search
    /// filter, and the legend spotlight. 1.0 = full, lower = receded. Selection wins (most explicit
    /// intent); hover is the next-strongest lens so pointing at a node lights up its neighborhood
    /// even without a click. Both are draw-time only — they never move a node. `neighborIDs` /
    /// `hoverNeighbors` are precomputed once per render by the caller.
    private func nodeEmphasis(_ node: GraphNode, matched: Set<String>?, neighborIDs: Set<String>, hoverNeighbors: Set<String>) -> Double {
        if let selectedID = selectedNodeID {
            if node.id == selectedID { return 1.0 }
            if neighborIDs.contains(node.id) { return 0.95 }
            // Even in focus mode, a hovered node + its neighbors stay legible rather than fully dim.
            if node.id == hoveredNodeID || hoverNeighbors.contains(node.id) { return 0.6 }
            return 0.15
        }
        if let hoveredID = hoveredNodeID {
            if node.id == hoveredID { return 1.0 }
            return hoverNeighbors.contains(node.id) ? 0.95 : 0.16
        }
        if let matched, !matched.contains(node.id) { return 0.18 }
        if let spotlight, !spotlight.contains(node) { return 0.18 }
        return 0.85
    }

    /// Whether an edge should render as "highlighted" (bright) under the current lenses: it touches
    /// the selected node, or (when nothing is selected) it touches the hovered node.
    private func edgeIsHighlighted(_ edge: GraphEdge) -> Bool {
        if let selectedID = selectedNodeID {
            return edge.source_id == selectedID || edge.target_id == selectedID
        }
        if let hoveredID = hoveredNodeID {
            return edge.source_id == hoveredID || edge.target_id == hoveredID
        }
        return false
    }

    private func drawEdges(in context: inout GraphicsContext, size: CGSize, layout: MemoryMapLayout, edges: [GraphEdge], matched: Set<String>?) {
        // A "focus lens" is active when a node is selected OR (with nothing selected) hovered:
        // its incident edges draw bright on top, everything else recedes.
        let focusActive = selectedNodeID != nil || hoveredNodeID != nil
        // Draw ordinary ties first, bridges (cross-community "surprising connections") next, and
        // the focused node's edges last and brightest, so the focused connections always sit on top.
        let ordered = edges.sorted { lhs, rhs in
            func rank(_ e: GraphEdge) -> Int {
                if edgeIsHighlighted(e) { return 2 }
                return e.is_bridge == true ? 1 : 0
            }
            return rank(lhs) < rank(rhs)
        }
        for edge in ordered {
            guard let la = resolvedPosition(of: edge.source_id, layout: layout),
                  let lb = resolvedPosition(of: edge.target_id, layout: layout) else { continue }
            let a = screenPoint(la, in: size)
            let b = screenPoint(lb, in: size)
            // Skip edges entirely outside the visible canvas (zoomed in).
            let edgeBounds = CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(a.x - b.x), height: abs(a.y - b.y))
            guard edgeBounds.intersects(CGRect(origin: .zero, size: size).insetBy(dx: -40, dy: -40)) else { continue }
            var path = Path()
            path.move(to: a)
            path.addLine(to: b)

            let weight = edge.weight ?? 0.5
            if focusActive {
                // Focus mode: incident edges bright in accent, the rest nearly gone. Selection reads
                // full-strength accent; a hover-only focus is a touch softer so it feels like a preview.
                if edgeIsHighlighted(edge) {
                    let lineWidth = 1.4 + CGFloat(max(0, min(1, weight))) * 1.6
                    let strength: Double = selectedNodeID != nil ? 0.85 : 0.7
                    context.stroke(path, with: .color(CortexDesign.accent.opacity(strength)), lineWidth: lineWidth)
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

    private func drawNodes(in context: inout GraphicsContext, size: CGSize, layout: MemoryMapLayout, matched: Set<String>?, neighborIDs: Set<String>, hoverNeighbors: Set<String>) {
        let visibleRect = CGRect(origin: .zero, size: size).insetBy(dx: -20, dy: -20)

        // Draw circles first so no label ever sits under a later circle. Positions come from the
        // mutable working copy (so a dragged node follows the pointer), falling back to the layout.
        for node in mapNodes {
            guard let lp = resolvedPosition(of: node.id, layout: layout) else { continue }
            let point = screenPoint(lp, in: size)
            guard visibleRect.contains(point) else { continue }
            // Node size grows gently with zoom (sqrt) so zooming in separates clusters without
            // turning hubs into balloons.
            let radius = layout.radius(of: node) * max(1, effectiveZoom.squareRoot())
            let isSelected = node.id == selectedNodeID
            let isHovered = node.id == hoveredNodeID
            let isDragged = draggedNodeID == node.id
            let emphasis = nodeEmphasis(node, matched: matched, neighborIDs: neighborIDs, hoverNeighbors: hoverNeighbors)
            let fill = MemoryMapView.color(for: node)

            let rect = CGRect(x: point.x - radius, y: point.y - radius, width: radius * 2, height: radius * 2)
            let circle = Path(ellipseIn: rect)

            if isSelected || isHovered || isDragged {
                let haloRadius = radius + (isSelected || isDragged ? 6 : 3)
                let haloRect = CGRect(x: point.x - haloRadius, y: point.y - haloRadius, width: haloRadius * 2, height: haloRadius * 2)
                context.fill(Path(ellipseIn: haloRect), with: .color(fill.opacity(isSelected || isDragged ? 0.22 : 0.14)))
            }

            let fullBright = isSelected || isHovered || isDragged
            context.fill(circle, with: .color(fill.opacity(fullBright ? 1.0 : emphasis)))
            context.stroke(circle, with: .color(CortexDesign.panelBackground), lineWidth: 1)
        }

        drawLabels(in: &context, size: size, layout: layout, matched: matched, neighborIDs: neighborIDs, hoverNeighbors: hoverNeighbors, visibleRect: visibleRect)
    }

    /// Labels with a smooth zoom fade instead of a hard budget cutoff. A node's label opacity ramps
    /// with both zoom and the node's prominence (hubs fade in first), and hovered/selected/neighbor
    /// nodes always render at full strength. Overlapping labels are still greedily dropped, most
    /// prominent first, so the map never turns into a wall of text.
    private func drawLabels(in context: inout GraphicsContext, size: CGSize, layout: MemoryMapLayout, matched: Set<String>?, neighborIDs: Set<String>, hoverNeighbors: Set<String>, visibleRect: CGRect) {
        // Radius range across the currently-drawn nodes, for a normalized prominence per node.
        var maxRadius: CGFloat = 1
        for node in mapNodes { maxRadius = max(maxRadius, layout.radius(of: node)) }

        // A node is "always labeled" when it is the current focus/context, regardless of zoom.
        func isPinnedLabel(_ node: GraphNode) -> Bool {
            if node.id == selectedNodeID || node.id == hoveredNodeID { return true }
            if node.is_hub == true { return true }
            if selectedNodeID != nil && neighborIDs.contains(node.id) { return true }
            if hoveredNodeID != nil && hoverNeighbors.contains(node.id) { return true }
            return false
        }

        // Label opacity for a node: 1 for pinned labels; otherwise a ramp of zoom × prominence so
        // labels fade in gradually as you zoom and as nodes get more prominent.
        func labelOpacity(_ node: GraphNode) -> Double {
            if isPinnedLabel(node) { return 1 }
            let prominence = Double(layout.radius(of: node) / maxRadius) // 0...1
            // Zoom term: nothing extra at fit, ramping to full by ~2.2× zoom.
            let zoomTerm = Double(max(0, min(1, (effectiveZoom - 0.9) / 1.3)))
            let raw = zoomTerm * (0.35 + prominence * 0.65) + prominence * 0.25
            return max(0, min(1, raw))
        }

        var occupiedLabelRects: [CGRect] = []

        // Most prominent / most focused first, so they win the greedy overlap check.
        let ordered = mapNodes.sorted { lhs, rhs in
            func priority(_ node: GraphNode) -> CGFloat {
                if node.id == selectedNodeID || node.id == hoveredNodeID { return .greatestFiniteMagnitude }
                if (selectedNodeID != nil && neighborIDs.contains(node.id)) ||
                   (hoveredNodeID != nil && hoverNeighbors.contains(node.id)) {
                    return 100_000 + layout.radius(of: node)
                }
                return layout.radius(of: node)
            }
            return priority(lhs) > priority(rhs)
        }

        for node in ordered {
            let baseOpacity = labelOpacity(node)
            guard baseOpacity > 0.06 else { continue }
            // Never label a node the active lens has fully dimmed (search/spotlight/focus).
            let emphasis = nodeEmphasis(node, matched: matched, neighborIDs: neighborIDs, hoverNeighbors: hoverNeighbors)
            let isFocus = node.id == selectedNodeID || node.id == hoveredNodeID
            guard isFocus || emphasis > 0.3 else { continue }

            guard let lp = resolvedPosition(of: node.id, layout: layout) else { continue }
            let point = screenPoint(lp, in: size)
            guard visibleRect.contains(point) else { continue }
            let radius = layout.radius(of: node) * max(1, effectiveZoom.squareRoot())
            let isSelected = node.id == selectedNodeID
            // Whole-sentence labels (memories/tasks) are display-truncated; the detail panel
            // shows the full text on selection.
            let displayLabel = node.label.count > 42 ? String(node.label.prefix(40)) + "…" : node.label
            let baseColor = isSelected ? CortexDesign.ink : CortexDesign.inkSecondary
            let text = Text(displayLabel)
                .font(CortexDesign.Typography.caption)
                .foregroundColor(baseColor.opacity(baseOpacity))
            let resolved = context.resolve(text)
            let textSize = resolved.measure(in: CGSize(width: 140, height: 40))
            var textX = point.x + radius + 4
            if textX + textSize.width > size.width - 4 {
                textX = point.x - radius - 4 - textSize.width
            }
            let textPoint = CGPoint(x: textX, y: point.y - textSize.height / 2)
            let labelRect = CGRect(origin: textPoint, size: textSize).insetBy(dx: -3, dy: -2)
            // A label that would overlap an already-placed one is dropped (except the focus
            // node's, which always shows) — fewer, readable labels beat many colliding ones.
            if !isFocus && occupiedLabelRects.contains(where: { $0.intersects(labelRect) }) {
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
    /// Deterministic identity of the (nodes, edges, size) this layout was computed for. The view
    /// uses it to know when to (re)seed its mutable drag working-copy of the positions — it must
    /// NOT change on hover/zoom/pan, only when the underlying graph or canvas size changes.
    private(set) var identityKey: UInt64 = 0

    // MARK: Memoized entry point

    /// Small one-slot cache: the map re-renders constantly while hovering/zooming, but the
    /// layout only changes when the data or canvas size changes.
    private static var cachedKey: UInt64 = 0
    private static var cachedLayout: MemoryMapLayout?
    private static let cacheLock = NSLock()

    /// The deterministic cache/identity key for a given input. Shared by the memoization check and
    /// the layout's own `identityKey`, so both always agree.
    static func signature(nodes: [GraphNode], edges: [GraphEdge], size: CGSize) -> UInt64 {
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
            // The seed clusters by community and the sizing/centre-pull reads centrality, so a
            // re-analysis that keeps the same node ids but re-partitions the graph MUST invalidate
            // the memoized layout — otherwise the map keeps a stale arrangement after re-clustering.
            mix(String(node.community ?? -1))
            mix(String(node.centrality ?? 0))
        }
        for edge in edges { mix(edge.id) }
        mix("\(Int(size.width))x\(Int(size.height))")
        return hash
    }

    static func layout(nodes: [GraphNode], edges: [GraphEdge], size: CGSize) -> MemoryMapLayout {
        let hash = signature(nodes: nodes, edges: edges, size: size)

        cacheLock.lock()
        defer { cacheLock.unlock() }
        if let cached = cachedLayout, cachedKey == hash {
            return cached
        }
        var fresh = MemoryMapLayout(nodes: nodes, edges: edges, size: size)
        fresh.identityKey = hash
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

        // --- Degree per node (Obsidian sizes nodes by connection count). Counted from the edge
        // list against the laid-out node set, then normalized so the busiest node reads as 1. ---
        var degree = [CGFloat](repeating: 0, count: count)
        for edge in edges {
            if let a = indexByID[edge.source_id] { degree[a] += 1 }
            if let b = indexByID[edge.target_id] { degree[b] += 1 }
        }
        let maxDegree = max(1, degree.max() ?? 1)

        // --- Community anchors: give each distinct community a stable angle around the canvas so
        // the seed already reads as separated clusters (the sim then only has to refine). Nodes
        // with no community fan out on the outer ring by id-hash. ---
        let distinctCommunities = Array(Set(ordered.compactMap { $0.community })).sorted()
        var communityAnchorAngle: [Int: CGFloat] = [:]
        for (i, c) in distinctCommunities.enumerated() {
            communityAnchorAngle[c] = (CGFloat(i) / CGFloat(max(distinctCommunities.count, 1))) * 2 * .pi
        }

        // --- Seed: community-aware deterministic scatter from the id hash. ---
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
            let hashAngle = CGFloat(seed % 10_000) / 10_000.0 * 2 * .pi
            let ringJitter = CGFloat((seed / 10_000) % 1000) / 1000.0
            if let c = node.community, let anchor = communityAnchorAngle[c] {
                // Cluster nodes seed in a wedge around their community's anchor angle, on a mid ring.
                let spread = ( (CGFloat(seed % 1000) / 1000.0) - 0.5 ) * 0.9
                let angle = anchor + spread
                let distance = seedRadius * (0.35 + (1 - p) * 0.35 + ringJitter * 0.2)
                x[index] = center.x + cos(angle) * distance
                y[index] = center.y + sin(angle) * distance
            } else {
                // Communityless nodes (raw memories/tasks) fan the outer ring by hash so they don't
                // crowd the entity clusters.
                let distance = seedRadius * (0.6 + (1 - p) * 0.3 + ringJitter * 0.1)
                x[index] = center.x + cos(hashAngle) * distance
                y[index] = center.y + sin(hashAngle) * distance
            }
        }

        // Edge springs between laid-out endpoints; stronger edges want shorter links.
        // Softened relative to the classic tuning so clusters breathe instead of collapsing to a knot.
        struct Spring { let a: Int; let b: Int; let length: CGFloat; let strength: CGFloat }
        var springs: [Spring] = []
        springs.reserveCapacity(edges.count)
        for edge in edges.sorted(by: { $0.id < $1.id }) {
            guard let a = indexByID[edge.source_id], let b = indexByID[edge.target_id], a != b else { continue }
            let weight = CGFloat(max(0, min(1, edge.weight ?? 0.5)))
            springs.append(Spring(a: a, b: b, length: 120 - weight * 60, strength: 0.015 + weight * 0.02))
        }

        // --- Simulate: fixed iterations, cooling displacement cap, spatial-grid repulsion. ---
        // Repulsion is boosted (~1.75× the classic 1_500) over a wider radius so communities
        // visibly separate the way Obsidian's default graph does, instead of packing into a ball.
        let iterations = 90
        let repulsionRadius: CGFloat = 120
        let repulsionStrength: CGFloat = 2_600
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
                    // Softened cluster pull (was 0.02) so members gather without collapsing onto
                    // the centroid — the clusters keep some internal air.
                    fx[i] += (acc.x / acc.n - x[i]) * 0.012
                    fy[i] += (acc.y / acc.n - y[i]) * 0.012
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
        // Obsidian-style sizing: a node's size reads its connectedness. Blend graph prominence
        // (centrality/importance) with normalized degree so a well-connected hub is clearly large
        // and a leaf clearly small, mapped onto a fixed min/max so the range is legible.
        let minRadius: CGFloat = 4.5
        let maxRadius: CGFloat = 20
        var radius = [CGFloat](repeating: minRadius, count: count)
        for (index, node) in ordered.enumerated() {
            let degreeNorm = (degree[index] / maxDegree).squareRoot() // sqrt so the busiest node isn't the only big one
            // Weight prominence and degree evenly; both are 0...1.
            let weight = max(0, min(1, prom[index] * 0.55 + degreeNorm * 0.45))
            radius[index] = minRadius + weight * (maxRadius - minRadius)
            if node.is_hub == true { radius[index] = max(radius[index], 14) }
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

    /// The full canonical position map, in canvas space. The view seeds its mutable drag
    /// working-copy from this whenever the layout identity changes.
    func allPositions() -> [String: CGPoint] { positions }

    /// A short runtime relaxation used after a node drag: springs, light repulsion and overlap
    /// separation ease the dragged node's neighbors into place while the dragged node stays pinned.
    /// Pure and deterministic given its inputs; it does NOT touch the memoized layout or the cache,
    /// and it operates directly in canvas space on the working positions the view hands it.
    ///
    /// `pinned` is held fixed. Positions absent from `current` fall back to the canonical layout.
    /// Returns a new position map (or the input unchanged if there is nothing to do). Guards against
    /// NaN/inf so a drag can never corrupt the map.
    func settle(current: [String: CGPoint], edges: [GraphEdge], pinned: String, size: CGSize, iterations: Int = 20) -> [String: CGPoint] {
        // Stable node ordering for determinism.
        let ids = nodesByID.keys.sorted()
        guard !ids.isEmpty, size.width > 0, size.height > 0 else { return current }
        var indexByID: [String: Int] = [:]
        for (i, id) in ids.enumerated() { indexByID[id] = i }
        let count = ids.count

        func sanitize(_ p: CGPoint, fallback: CGPoint) -> CGPoint {
            if p.x.isFinite && p.y.isFinite { return p }
            return fallback
        }

        var x = [CGFloat](repeating: 0, count: count)
        var y = [CGFloat](repeating: 0, count: count)
        var rad = [CGFloat](repeating: 5, count: count)
        for (i, id) in ids.enumerated() {
            let fallback = positions[id] ?? CGPoint(x: size.width / 2, y: size.height / 2)
            let p = sanitize(current[id] ?? fallback, fallback: fallback)
            x[i] = p.x; y[i] = p.y
            rad[i] = radii[id] ?? 5
        }
        guard let pinnedIndex = indexByID[pinned] else { return current }
        // Minimum center-to-center gap per pair, so the settle also separates overlapping circles.
        func minGap(_ i: Int, _ j: Int) -> CGFloat { rad[i] + rad[j] + 3 }

        struct Spring { let a: Int; let b: Int; let length: CGFloat; let strength: CGFloat }
        var springs: [Spring] = []
        for edge in edges.sorted(by: { $0.id < $1.id }) {
            guard let a = indexByID[edge.source_id], let b = indexByID[edge.target_id], a != b else { continue }
            let weight = CGFloat(max(0, min(1, edge.weight ?? 0.5)))
            springs.append(Spring(a: a, b: b, length: 120 - weight * 60, strength: 0.015 + weight * 0.02))
        }

        let repulsionRadius: CGFloat = 120
        let repulsionStrength: CGFloat = 2_600
        let cell = repulsionRadius
        for _ in 0..<max(0, iterations) {
            var fx = [CGFloat](repeating: 0, count: count)
            var fy = [CGFloat](repeating: 0, count: count)

            var grid: [Int64: [Int]] = [:]
            for i in 0..<count {
                grid[MemoryMapLayout.gridKey(x[i], y[i], cell: cell), default: []].append(i)
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
                            var force = repulsionStrength / distSq
                            let dist = distSq.squareRoot()
                            // Extra push when circles actually overlap, so a settle also separates
                            // touching nodes (a lightweight stand-in for a full overlap sweep).
                            let gap = minGap(i, j)
                            if dist < gap { force += (gap - dist) * 0.6 }
                            fx[i] += (ddx / dist) * force
                            fy[i] += (ddy / dist) * force
                        }
                    }
                }
            }

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

            for i in 0..<count where i != pinnedIndex {
                let mag = (fx[i] * fx[i] + fy[i] * fy[i]).squareRoot()
                guard mag.isFinite, mag > 0.01 else { continue }
                let step = min(mag, 12)
                let nx = x[i] + (fx[i] / mag) * step
                let ny = y[i] + (fy[i] / mag) * step
                if nx.isFinite && ny.isFinite {
                    // Keep inside a generous margin of the canvas so a settle can't fling a node away.
                    x[i] = min(max(nx, -size.width), size.width * 2)
                    y[i] = min(max(ny, -size.height), size.height * 2)
                }
            }
        }

        var out = current
        for (i, id) in ids.enumerated() {
            out[id] = CGPoint(x: x[i], y: y[i])
        }
        return out
    }

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

// MARK: - Constellation share card — "Spotify Wrapped for your knowledge".
//
// A screenshot-native, social-ratio picture of the user's REAL graph: real positions (the same
// deterministic force layout the live map runs), real community colors, real god nodes, real
// counts. Built entirely behind the map's Share button — data snapshot, layout pass, and 2× raster
// all happen when the sheet opens, never during normal map rendering.

/// Everything the share card renders, computed ONCE when the share sheet opens: the capped
/// prominence-first node subset, the edges between them, a fresh deterministic layout at card
/// scale, and the headline numbers. Plain data, so the card view itself is trivial.
struct ConstellationShareCardModel {
    struct Stat {
        let value: String
        let label: String
    }

    let nodes: [GraphNode]
    let edges: [GraphEdge]
    let layout: MemoryMapLayout
    /// Up to three god-node labels — "Your world orbits: X · Y · Z".
    let orbitLabels: [String]
    let stats: [Stat]
    /// The mono stamp line, e.g. "YOUR CONSTELLATION · 11 JUL 2026".
    let stampLine: String

    /// Legibility caps: a few hundred nodes reads as a constellation; thousands reads as noise.
    private static let nodeBudget = 280
    private static let edgeBudget = 700

    static func build(
        nodes allNodes: [GraphNode],
        edges allEdges: [GraphEdge],
        analysis: GraphAnalysis?,
        memoriesCount: Int?,
        graphSize: CGSize
    ) -> ConstellationShareCardModel {
        // Degree from the full edge list — a prominence tie-break so well-connected nodes win a
        // spot on the card even when centrality is missing.
        var degree: [String: Int] = [:]
        for edge in allEdges {
            degree[edge.source_id, default: 0] += 1
            degree[edge.target_id, default: 0] += 1
        }

        /// The god-node signal, mirroring the live map's sizing: hubs first, then centrality,
        /// then degree/importance. Ties break on id so the card is deterministic.
        func prominence(_ node: GraphNode) -> Double {
            var score = node.centrality ?? 0
            if node.is_hub == true { score += 1 }
            score += Double(degree[node.id] ?? 0) * 0.001
            score += Double(node.importance ?? 0) * 0.000_1
            return score
        }

        let ranked = allNodes.sorted { lhs, rhs in
            let lp = prominence(lhs)
            let rp = prominence(rhs)
            if lp != rp { return lp > rp }
            return lhs.id < rhs.id
        }
        let kept = Array(ranked.prefix(nodeBudget))
        let keptIDs = Set(kept.map(\.id))
        let keptEdges = Array(
            allEdges
                .filter { keptIDs.contains($0.source_id) && keptIDs.contains($0.target_id) }
                .sorted { lhs, rhs in
                    let lw = lhs.weight ?? 0
                    let rw = rhs.weight ?? 0
                    if lw != rw { return lw > rw }
                    return lhs.id < rhs.id
                }
                .prefix(edgeBudget)
        )

        // Direct init ON PURPOSE: `MemoryMapLayout.layout(...)` owns the one-slot memo cache the
        // LIVE map depends on every render. Going around it means opening the share sheet never
        // evicts the map's cached layout — and the card's own layout runs exactly once, here.
        let layout = MemoryMapLayout(nodes: kept, edges: keptEdges, size: graphSize)

        // Top 3 god-node labels. `ranked` already leads with hubs/centrality; skip empty or
        // sentence-length labels, dedupe, and truncate so the headline stays one line.
        var orbitLabels: [String] = []
        var seenLabels = Set<String>()
        for node in ranked {
            guard orbitLabels.count < 3 else { break }
            let label = node.label.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !label.isEmpty, label.count <= 32 else { continue }
            let key = label.lowercased()
            guard !seenLabels.contains(key) else { continue }
            seenLabels.insert(key)
            orbitLabels.append(label.count > 22 ? String(label.prefix(20)) + "…" : label)
        }

        // Honest headline numbers: never fabricate. Memories come from /v1/stats when known;
        // otherwise the card counts what it actually draws from ("points").
        var stats: [Stat] = []
        if let memoriesCount, memoriesCount > 0 {
            stats.append(Stat(value: memoriesCount.formatted(), label: memoriesCount == 1 ? "MEMORY" : "MEMORIES"))
        } else {
            stats.append(Stat(value: allNodes.count.formatted(), label: allNodes.count == 1 ? "POINT" : "POINTS"))
        }
        stats.append(Stat(value: allEdges.count.formatted(), label: allEdges.count == 1 ? "CONNECTION" : "CONNECTIONS"))
        let clusterCount = analysis?.community_count ?? Set(allNodes.compactMap(\.community)).count
        if clusterCount >= 1 {
            stats.append(Stat(value: clusterCount.formatted(), label: clusterCount == 1 ? "CLUSTER" : "CLUSTERS"))
        }

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "d MMM yyyy"
        let stampLine = "YOUR CONSTELLATION · " + formatter.string(from: Date()).uppercased()

        return ConstellationShareCardModel(
            nodes: kept,
            edges: keptEdges,
            layout: layout,
            orbitLabels: orbitLabels,
            stats: stats,
            stampLine: stampLine
        )
    }
}

/// The fixed-size (1200×630, standard social-card ratio) share card. Renders the real graph in
/// the Archive's DARK identity — a night sky of the user's own constellation — with headline
/// stats and quiet Cortex branding. Never blank: a young graph still gets its star field, stats,
/// and stamp. Exported at 2× (2400×1260) via ImageRenderer.
struct ConstellationShareCard: View {
    static let size = CGSize(width: 1200, height: 630)
    /// The region the graph layout fills; the scrimmed margins belong to the text chrome.
    static let graphSize = CGSize(width: 1104, height: 470)
    private static let graphOrigin = CGPoint(x: 48, y: 104)

    let model: ConstellationShareCardModel

    /// The Archive's dark palette, fixed by VALUE: the app window is pinned light, and the
    /// adaptive `CortexDesign` tokens would resolve light here — the card must always be the
    /// night-sky identity, so it carries the dark hex values directly.
    private enum Night {
        static let paper = Color(red: 0.110, green: 0.102, blue: 0.090)          // #1C1A17
        static let panel = Color(red: 0.149, green: 0.137, blue: 0.125)          // #262320
        static let ink = Color(red: 0.910, green: 0.890, blue: 0.851)            // #E8E3D9
        static let inkSecondary = Color(red: 0.690, green: 0.663, blue: 0.616)   // #B0A99D
        static let inkFaint = Color(red: 0.549, green: 0.522, blue: 0.478)       // #8C857A
        static let accent = Color(red: 0.788, green: 0.420, blue: 0.341)         // #C96B57
        static let gold = Color(red: 0.827, green: 0.627, blue: 0.298)           // #D3A04C
        static let moss = Color(red: 0.494, green: 0.604, blue: 0.447)           // #7E9A72
    }

    /// Dark siblings of `MemoryMapView.communityPalette`, index-aligned so each cluster keeps
    /// the same hue family on the card as on the live map.
    private static let communityPalette: [Color] = [
        Night.accent,
        Night.gold,
        Night.moss,
        Color(red: 0.58, green: 0.51, blue: 0.78),
        Color(red: 0.82, green: 0.56, blue: 0.42),
        Color(red: 0.42, green: 0.66, blue: 0.69),
    ]

    private static func nodeColor(_ node: GraphNode) -> Color {
        if let community = node.community {
            let count = communityPalette.count
            return communityPalette[((community % count) + count) % count]
        }
        switch node.type.lowercased() {
        case "person", "people": return Night.accent
        case "project": return Night.gold
        case "topic", "theme", "concept": return Night.moss
        default: return Night.inkSecondary
        }
    }

    var body: some View {
        ZStack {
            Night.paper
            // A soft center glow so the sky has depth instead of flat black.
            RadialGradient(
                colors: [Night.panel.opacity(0.9), Night.paper],
                center: .center,
                startRadius: 40,
                endRadius: 620
            )
            graphCanvas
            scrims
            chrome
            // The archive plate: a quiet inner hairline framing the card.
            Rectangle()
                .strokeBorder(Night.ink.opacity(0.12), lineWidth: 1)
                .padding(16)
        }
        .frame(width: Self.size.width, height: Self.size.height)
    }

    /// layout space → card space (the layout fills `graphSize`, inset by `graphOrigin`).
    private static func cardPoint(_ p: CGPoint) -> CGPoint {
        CGPoint(x: graphOrigin.x + p.x, y: graphOrigin.y + p.y)
    }

    private var graphCanvas: some View {
        Canvas { context, _ in
            drawDust(&context)
            // Painter's order matches the live map: edges, then circles, then labels.
            for edge in model.edges {
                guard let la = model.layout.position(of: edge.source_id),
                      let lb = model.layout.position(of: edge.target_id) else { continue }
                let a = Self.cardPoint(la)
                let b = Self.cardPoint(lb)
                var path = Path()
                path.move(to: a)
                path.addLine(to: b)
                let weight = max(0, min(1, edge.weight ?? 0.5))
                if edge.is_bridge == true {
                    context.stroke(path, with: .color(Night.gold.opacity(0.45)), style: StrokeStyle(lineWidth: 1.2, dash: [4, 3]))
                } else {
                    context.stroke(path, with: .color(Night.ink.opacity(0.07 + weight * 0.12)), lineWidth: 0.8 + CGFloat(weight) * 1.2)
                }
            }
            for node in model.nodes {
                guard let lp = model.layout.position(of: node.id) else { continue }
                let p = Self.cardPoint(lp)
                let radius = model.layout.radius(of: node) * 1.45
                let fill = Self.nodeColor(node)
                // The god nodes glow.
                if model.layout.radius(of: node) >= 12 {
                    let halo = radius + 12
                    context.fill(
                        Path(ellipseIn: CGRect(x: p.x - halo, y: p.y - halo, width: halo * 2, height: halo * 2)),
                        with: .color(fill.opacity(0.16))
                    )
                }
                context.fill(
                    Path(ellipseIn: CGRect(x: p.x - radius, y: p.y - radius, width: radius * 2, height: radius * 2)),
                    with: .color(fill.opacity(0.95))
                )
            }
            drawTopLabels(&context)
        }
        .frame(width: Self.size.width, height: Self.size.height)
    }

    /// Faint deterministic star-dust (FNV-seeded, like the layout itself — no randomness) so a
    /// young, small graph still renders as a living night sky. The card is never blank.
    private func drawDust(_ context: inout GraphicsContext) {
        for index in 0..<110 {
            var hash: UInt64 = 0xcbf29ce484222325
            for byte in "dust-\(index)".utf8 {
                hash ^= UInt64(byte)
                hash = hash &* 0x100000001b3
            }
            let x = CGFloat(hash % UInt64(Self.size.width))
            let y = CGFloat((hash >> 16) % UInt64(Self.size.height))
            let alpha = 0.04 + Double((hash >> 32) % 90) / 1_500
            let radius: CGFloat = (hash >> 44) % 5 == 0 ? 1.5 : 0.9
            context.fill(
                Path(ellipseIn: CGRect(x: x - radius, y: y - radius, width: radius * 2, height: radius * 2)),
                with: .color(Night.ink.opacity(alpha))
            )
        }
    }

    /// Label only the most prominent nodes (largest radii — hubs and high-centrality entities),
    /// greedily dropping collisions, so the card reads as a constellation, not a word cloud.
    private func drawTopLabels(_ context: inout GraphicsContext) {
        let ordered = model.nodes
            .sorted { lhs, rhs in
                let lr = model.layout.radius(of: lhs)
                let rr = model.layout.radius(of: rhs)
                if lr != rr { return lr > rr }
                return lhs.id < rhs.id
            }
            .prefix(12)
        var occupied: [CGRect] = []
        for node in ordered {
            let raw = node.label.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !raw.isEmpty else { continue }
            guard let lp = model.layout.position(of: node.id) else { continue }
            let p = Self.cardPoint(lp)
            let radius = model.layout.radius(of: node) * 1.45
            let label = raw.count > 26 ? String(raw.prefix(24)) + "…" : raw
            let text = Text(label)
                .font(.system(size: 15, weight: .medium))
                .foregroundColor(Night.ink.opacity(0.92))
            let resolved = context.resolve(text)
            let textSize = resolved.measure(in: CGSize(width: 240, height: 44))
            var textX = p.x + radius + 7
            if textX + textSize.width > Self.size.width - 40 {
                textX = p.x - radius - 7 - textSize.width
            }
            let origin = CGPoint(x: textX, y: p.y - textSize.height / 2)
            let rect = CGRect(origin: origin, size: textSize).insetBy(dx: -4, dy: -3)
            guard !occupied.contains(where: { $0.intersects(rect) }) else { continue }
            occupied.append(rect)
            // A whisper of ground so labels stay legible over edge lines.
            context.fill(Path(roundedRect: rect, cornerRadius: 4), with: .color(Night.paper.opacity(0.55)))
            context.draw(resolved, in: CGRect(origin: origin, size: textSize))
        }
    }

    /// Legibility scrims over the graph's top and bottom margins, where the text chrome sits.
    private var scrims: some View {
        VStack(spacing: 0) {
            LinearGradient(
                colors: [Night.paper.opacity(0.92), Night.paper.opacity(0)],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: 168)
            Spacer(minLength: 0)
            LinearGradient(
                colors: [Night.paper.opacity(0), Night.paper.opacity(0.94)],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: 168)
        }
    }

    /// "Your world orbits: X · Y · Z" — the top god-node labels, in the serif archive voice.
    /// Falls back gracefully while the graph is still young.
    private var headline: Text {
        let base = Font.system(size: 34, weight: .semibold, design: .serif)
        guard !model.orbitLabels.isEmpty else {
            return Text("Your knowledge, mapped.").font(base).foregroundColor(Night.ink)
        }
        var line = Text("Your world orbits: ").font(base).foregroundColor(Night.ink)
        for (index, label) in model.orbitLabels.enumerated() {
            if index > 0 {
                line = line + Text(" · ").font(base).foregroundColor(Night.inkFaint)
            }
            line = line + Text(label).font(base).foregroundColor(Night.ink)
        }
        return line
    }

    private var chrome: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(model.stampLine)
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .kerning(2.4)
                .foregroundColor(Night.inkFaint)
            headline
                .lineLimit(1)
                .minimumScaleFactor(0.6)
                .padding(.top, 14)
            Spacer(minLength: 0)
            HStack(alignment: .lastTextBaseline, spacing: 44) {
                ForEach(model.stats, id: \.label) { stat in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(stat.value)
                            .font(.system(size: 40, weight: .semibold, design: .serif))
                            .monospacedDigit()
                            .foregroundColor(Night.ink)
                        Text(stat.label)
                            .font(.system(size: 12, weight: .medium, design: .monospaced))
                            .kerning(1.8)
                            .foregroundColor(Night.inkFaint)
                    }
                }
                Spacer(minLength: 0)
                // The branding: a wax-red seal dot and the wordmark, nothing louder.
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Circle()
                        .fill(Night.accent)
                        .frame(width: 7, height: 7)
                    Text("Mapped by")
                        .font(.system(size: 15, weight: .regular))
                        .foregroundColor(Night.inkSecondary)
                    Text("Cortex")
                        .font(.system(size: 22, weight: .semibold, design: .serif))
                        .foregroundColor(Night.ink)
                }
            }
        }
        .padding(.horizontal, 52)
        .padding(.top, 46)
        .padding(.bottom, 44)
    }
}

/// A weak handle to the AppKit view planted under the Share button, so the sharing picker's
/// popover can anchor to the button that summoned it.
private final class ShareAnchor {
    weak var view: NSView?
}

private struct ShareAnchorView: NSViewRepresentable {
    let anchor: ShareAnchor

    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        anchor.view = view
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        anchor.view = nsView
    }
}

/// The preview sheet behind the map's share button: renders the card ONCE (2× via ImageRenderer),
/// shows exactly the pixels that would leave the machine, and offers the three exits — the system
/// share picker (the one primary action), copy, and save-as-PNG.
private struct ConstellationShareSheet: View {
    let nodes: [GraphNode]
    let edges: [GraphEdge]
    let analysis: GraphAnalysis?
    let memoriesCount: Int?

    @Environment(\.dismiss) private var dismiss

    /// The rendered card (preview + share-picker item) and its PNG bytes (copy + save).
    @State private var cardImage: NSImage?
    @State private var cardPNG: Data?
    @State private var copied = false
    /// Held so the picker isn't deallocated out from under its own popover.
    @State private var activePicker: NSSharingServicePicker?
    @State private var shareAnchor = ShareAnchor()

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            HStack(alignment: .firstTextBaseline, spacing: CortexDesign.Space.sm) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Share your constellation")
                        .font(CortexDesign.Typography.title)
                        .foregroundColor(CortexDesign.ink)
                    Text("A snapshot of your real memory graph. Node names are visible — share it on purpose.")
                        .font(CortexDesign.Typography.caption)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
                Spacer(minLength: 0)
                CortexIconButton(systemImage: "xmark", role: .ghost, size: .small, help: "Close") {
                    dismiss()
                }
                .accessibilityLabel("Close share preview")
            }

            preview

            HStack(spacing: CortexDesign.Space.sm) {
                CortexButton(title: copied ? "Copied" : "Copy", systemImage: "doc.on.doc", role: .secondary) {
                    copyPNG()
                }
                .disabled(cardPNG == nil)
                CortexButton(title: "Save PNG", systemImage: "square.and.arrow.down", role: .secondary) {
                    savePNG()
                }
                .disabled(cardPNG == nil)
                Spacer(minLength: 0)
                CortexButton(title: "Share…", systemImage: "square.and.arrow.up", role: .primary) {
                    presentSharePicker()
                }
                .disabled(cardImage == nil)
                .background(ShareAnchorView(anchor: shareAnchor))
            }
        }
        .padding(CortexDesign.Space.lg)
        .frame(minWidth: 684, minHeight: 470)
        .background(CortexDesign.appBackground)
        .task { renderCard() }
    }

    @ViewBuilder
    private var preview: some View {
        ZStack {
            if let cardImage {
                Image(nsImage: cardImage)
                    .resizable()
                    .scaledToFit()
                    .accessibilityLabel("Preview of your constellation share card")
            } else {
                // The render lands in one beat; this ground only shows on the largest graphs.
                Rectangle()
                    .fill(CortexDesign.quietBackground)
                ProgressView()
                    .controlSize(.small)
            }
        }
        .frame(width: 640, height: 336)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
    }

    /// Build the model, lay the capped graph out at card scale, and rasterize at 2× — all of it
    /// deferred to sheet-open, so the live map never pays a cost for the card's existence.
    @MainActor
    private func renderCard() {
        guard cardImage == nil else { return }
        let model = ConstellationShareCardModel.build(
            nodes: nodes,
            edges: edges,
            analysis: analysis,
            memoriesCount: memoriesCount,
            graphSize: ConstellationShareCard.graphSize
        )
        let renderer = ImageRenderer(content: ConstellationShareCard(model: model))
        renderer.scale = 2
        guard let cgImage = renderer.cgImage else { return }
        let rep = NSBitmapImageRep(cgImage: cgImage)
        // Point size stays 1200×630 while the pixel grid is 2400×1260 — crisp on retina, correct
        // dimensions everywhere else.
        rep.size = NSSize(width: ConstellationShareCard.size.width, height: ConstellationShareCard.size.height)
        cardPNG = rep.representation(using: .png, properties: [:])
        let image = NSImage(size: rep.size)
        image.addRepresentation(rep)
        cardImage = image
    }

    /// PNG straight onto the general pasteboard (plus TIFF for older paste targets).
    private func copyPNG() {
        guard let data = cardPNG else { return }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.declareTypes([.png, .tiff], owner: nil)
        pasteboard.setData(data, forType: .png)
        if let tiff = cardImage?.tiffRepresentation {
            pasteboard.setData(tiff, forType: .tiff)
        }
        copied = true
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            copied = false
        }
    }

    private func savePNG() {
        guard let data = cardPNG else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.png]
        panel.nameFieldStringValue = "my-constellation.png"
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        if panel.runModal() == .OK, let url = panel.url {
            try? data.write(to: url)
        }
    }

    /// NSSharingServicePicker needs a real AppKit anchor; `ShareAnchorView` plants one under the
    /// Share button so the popover points at the control that summoned it.
    private func presentSharePicker() {
        guard let image = cardImage, let anchorView = shareAnchor.view else { return }
        let picker = NSSharingServicePicker(items: [image])
        activePicker = picker
        picker.show(relativeTo: anchorView.bounds, of: anchorView, preferredEdge: .minY)
    }
}
