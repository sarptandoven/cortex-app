import SwiftUI
import AppKit

/// "Your Constellation" — the interactive picture of who the user is: the people, projects,
/// topics and sources Cortex has learned about, and how they connect. Nodes are placed with a
/// deterministic radial layout seeded by node-id hash (stable across renders; importance pulls a
/// node toward the center and enlarges it). Edges are thin ink/gold hairlines scaled by weight.
/// Tapping hit-tests to the nearest node and selects it, revealing a quiet detail panel; tapping
/// empty space deselects. Reads only the existing @Published `state.graphNodes` / `state.graphEdges`
/// and refreshes via the existing `state.loadGraph()`.
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

    /// nil = no filter (everything bright). Otherwise the set of node ids whose label matches.
    private var matchedNodeIDs: Set<String>? {
        let query = searchText.trimmingCharacters(in: .whitespaces).lowercased()
        guard !query.isEmpty else { return nil }
        return Set(state.graphNodes.filter { $0.label.lowercased().contains(query) }.map(\.id))
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
                if state.graphNodes.count > 8 {
                    searchField
                }
                mapCanvas
                if let node = selectedNode {
                    NodeDetailPanel(
                        node: node,
                        neighborhood: neighborhood,
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

    private var mapCanvas: some View {
        GeometryReader { geo in
            let layout = MemoryMapLayout(
                nodes: state.graphNodes,
                edges: state.graphEdges,
                size: geo.size
            )
            let matched = matchedNodeIDs
            Canvas { context, size in
                drawEdges(in: &context, layout: layout, matched: matched)
                drawNodes(in: &context, size: size, layout: layout, matched: matched)
            }
            .contentShape(Rectangle())
            .onTapGesture { location in
                if let hit = layout.nearestNode(to: location) {
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
                    hoveredNodeID = layout.nearestNode(to: point)?.id
                case .ended:
                    hoveredNodeID = nil
                }
            }
        }
        .frame(height: canvasHeight)
        .background(CortexDesign.panelBackground)
        .clipShape(RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                .stroke(CortexDesign.hairline, lineWidth: 1)
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Memory map with \(state.graphNodes.count) points and \(state.graphEdges.count) connections")
    }

    private func drawEdges(in context: inout GraphicsContext, layout: MemoryMapLayout, matched: Set<String>?) {
        // Draw ordinary ties first, bridges (cross-community "surprising connections") last so they
        // sit on top: dashed gold, a little heavier.
        let ordered = state.graphEdges.sorted { ($0.is_bridge == true ? 1 : 0) < ($1.is_bridge == true ? 1 : 0) }
        for edge in ordered {
            guard let a = layout.position(of: edge.source_id),
                  let b = layout.position(of: edge.target_id) else { continue }
            var path = Path()
            path.move(to: a)
            path.addLine(to: b)
            // Dim an edge only when BOTH endpoints are filtered out, so a match keeps its context.
            let dimmed = matched != nil && !(matched!.contains(edge.source_id) || matched!.contains(edge.target_id))
            let dim: CGFloat = dimmed ? 0.28 : 1.0
            if edge.is_bridge == true {
                let style = StrokeStyle(lineWidth: 1.6, dash: [4, 3])
                context.stroke(path, with: .color(CortexDesign.gold.opacity(0.85 * dim)), style: style)
            } else {
                let weight = edge.weight ?? 0.5
                let lineWidth = 0.6 + CGFloat(max(0, min(1, weight))) * 1.4
                let color = weight >= 0.66 ? CortexDesign.gold : CortexDesign.ink
                let opacity = (0.14 + min(0.34, weight * 0.34)) * dim
                context.stroke(path, with: .color(color.opacity(opacity)), lineWidth: lineWidth)
            }
        }
    }

    private func drawNodes(in context: inout GraphicsContext, size: CGSize, layout: MemoryMapLayout, matched: Set<String>?) {
        // Label budget: on a dense graph (hundreds of nodes) labeling every "large"
        // node painted an unreadable wall of overlapping text. Only the most
        // prominent nodes carry a label, AND labels that would overlap an
        // already-placed label are skipped (hubs cluster at the center, so even
        // 12 labels collide without collision checks). Hover/selection always label.
        let labeledIDs = layout.topLabelNodeIDs
        var occupiedLabelRects: [CGRect] = []
        // Draw circles first so no label ever sits under a later circle.
        for node in state.graphNodes {
            guard let point = layout.position(of: node.id) else { continue }
            let radius = layout.radius(of: node)
            let isSelected = node.id == selectedNodeID
            let isHovered = node.id == hoveredNodeID
            let isDimmed = matched != nil && !matched!.contains(node.id)
            let fill = MemoryMapView.color(for: node)

            let rect = CGRect(
                x: point.x - radius,
                y: point.y - radius,
                width: radius * 2,
                height: radius * 2
            )
            let circle = Path(ellipseIn: rect)

            if isSelected || isHovered {
                let haloRadius = radius + (isSelected ? 6 : 3)
                let haloRect = CGRect(
                    x: point.x - haloRadius,
                    y: point.y - haloRadius,
                    width: haloRadius * 2,
                    height: haloRadius * 2
                )
                context.fill(Path(ellipseIn: haloRect), with: .color(fill.opacity(isSelected ? 0.22 : 0.14)))
            }

            let baseOpacity: Double = isSelected ? 1.0 : (isDimmed ? 0.18 : 0.85)
            context.fill(circle, with: .color(fill.opacity(baseOpacity)))
            context.stroke(circle, with: .color(CortexDesign.panelBackground), lineWidth: 1)
        }

        // Labels second, most prominent first, greedily skipping collisions.
        let labelCandidates = state.graphNodes
            .filter { node in
                let isSelected = node.id == selectedNodeID
                let isHovered = node.id == hoveredNodeID
                let isDimmed = matched != nil && !matched!.contains(node.id)
                return (labeledIDs.contains(node.id) || isSelected || isHovered) && !isDimmed
            }
            .sorted { lhs, rhs in
                // Selection/hover win outright, then bigger nodes first.
                let lhsPriority = (lhs.id == selectedNodeID || lhs.id == hoveredNodeID) ? CGFloat.greatestFiniteMagnitude : layout.radius(of: lhs)
                let rhsPriority = (rhs.id == selectedNodeID || rhs.id == hoveredNodeID) ? CGFloat.greatestFiniteMagnitude : layout.radius(of: rhs)
                return lhsPriority > rhsPriority
            }
        for node in labelCandidates {
            guard let point = layout.position(of: node.id) else { continue }
            let radius = layout.radius(of: node)
            let isSelected = node.id == selectedNodeID
            let text = Text(node.label)
                .font(CortexDesign.Typography.caption)
                .foregroundColor(isSelected ? CortexDesign.ink : CortexDesign.inkSecondary)
            let resolved = context.resolve(text)
            let textSize = resolved.measure(in: CGSize(width: 120, height: 40))
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

    private var legend: some View {
        HStack(spacing: CortexDesign.Space.md) {
            ForEach(MemoryMapView.legendEntries, id: \.label) { entry in
                HStack(spacing: CortexDesign.Space.xs) {
                    Circle()
                        .fill(entry.color)
                        .frame(width: 7, height: 7)
                    Text(entry.label)
                        .font(CortexDesign.Typography.stamp)
                        .kerning(0.8)
                        .foregroundColor(CortexDesign.inkFaint)
                }
            }
            Spacer(minLength: 0)
            Text("Tap a point to see the connection")
                .font(CortexDesign.Typography.caption)
                .foregroundColor(CortexDesign.inkFaint)
        }
        .accessibilityHidden(true)
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

    static func color(for node: GraphNode) -> Color {
        if let community = node.community {
            let count = communityPalette.count
            return communityPalette[((community % count) + count) % count]
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

/// The quiet detail card shown when a node is selected: its label, a human type word, and the
/// backend's detail text if present.
private struct NodeDetailPanel: View {
    let node: GraphNode
    var neighborhood: EntityNeighborhood? = nil
    var loading: Bool = false
    var onExplore: ((GraphNode) -> Void)? = nil
    /// Tapping a connection re-centers the drill on that entity.
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
        }
    }
}

/// Deterministic radial layout for the memory map. Node positions are seeded from a stable hash of
/// the node id, so the constellation looks the same every render; importance pulls a node toward
/// the center and grows its radius. Not a physics simulation — cheap, stable, and clutter-free.
private struct MemoryMapLayout {
    private var positions: [String: CGPoint] = [:]
    private var radii: [String: CGFloat] = [:]
    private let nodesByID: [String: GraphNode]

    init(nodes: [GraphNode], edges: [GraphEdge], size: CGSize) {
        var byID: [String: GraphNode] = [:]
        for node in nodes { byID[node.id] = node }
        self.nodesByID = byID

        guard !nodes.isEmpty, size.width > 0, size.height > 0 else { return }

        let center = CGPoint(x: size.width / 2, y: size.height / 2)
        let maxRadius = min(size.width, size.height) / 2 - 28

        let importances = nodes.map { CGFloat($0.importance ?? 1) }
        let maxImportance = max(1, importances.max() ?? 1)

        // Prefer graph centrality (the "god node" signal) for size + centre-pull; fall back to
        // importance (damped, so real hubs still dominate) for non-entity nodes that carry none.
        func prominence(_ node: GraphNode) -> CGFloat {
            if let c = node.centrality { return max(0, min(1, CGFloat(c))) }
            return max(0, min(1, (CGFloat(node.importance ?? 1) / maxImportance) * 0.6))
        }

        // Order matters for stable angular placement: sort by id so the sequence is deterministic
        // regardless of the order the backend returned nodes.
        let ordered = nodes.sorted { $0.id < $1.id }
        let count = max(1, ordered.count)

        for (index, node) in ordered.enumerated() {
            let seed = MemoryMapLayout.stableHash(node.id)
            let importance = prominence(node) // 0...1, centrality-first

            // Angle: evenly spread by index, jittered deterministically by the id hash so ties
            // don't overlap and the layout feels organic rather than perfectly wheel-like.
            let baseAngle = (CGFloat(index) / CGFloat(count)) * 2 * .pi
            let jitter = (CGFloat(seed % 1000) / 1000.0 - 0.5) * (2 * .pi / CGFloat(count))
            let angle = baseAngle + jitter

            // Distance: prominent nodes sit closer to the center; a deterministic radial jitter
            // keeps equal-prominence nodes off a single ring.
            let ringJitter = CGFloat((seed / 1000) % 1000) / 1000.0 // 0...1
            let normalizedDistance = (1 - importance) * 0.72 + ringJitter * 0.28
            let distance = maxRadius * (0.18 + normalizedDistance * 0.82)

            let point = CGPoint(
                x: center.x + cos(angle) * distance,
                y: center.y + sin(angle) * distance
            )
            positions[node.id] = point
            radii[node.id] = 5 + importance * 9
            if node.is_hub == true { radii[node.id] = max(radii[node.id] ?? 5, 13) }
        }

        // One deterministic clustering pass: nudge each node toward its community centroid so
        // clusters read as clusters. A single averaged step (not a physics sim) stays cheap and
        // stable across renders. Positions are clamped to the canvas so a pull can't push a node off.
        var centroids: [Int: (x: CGFloat, y: CGFloat, n: CGFloat)] = [:]
        for node in ordered {
            guard let community = node.community, let p = positions[node.id] else { continue }
            var acc = centroids[community] ?? (0, 0, 0)
            acc.x += p.x; acc.y += p.y; acc.n += 1
            centroids[community] = acc
        }
        for node in ordered {
            guard let community = node.community, let p = positions[node.id],
                  let acc = centroids[community], acc.n > 1 else { continue }
            let centroid = CGPoint(x: acc.x / acc.n, y: acc.y / acc.n)
            let pull: CGFloat = node.is_hub == true ? 0.15 : 0.35 // hubs anchor, satellites gather
            let nx = min(max(p.x + (centroid.x - p.x) * pull, 12), max(12, size.width - 12))
            let ny = min(max(p.y + (centroid.y - p.y) * pull, 12), max(12, size.height - 12))
            positions[node.id] = CGPoint(x: nx, y: ny)
        }
    }

    func position(of id: String) -> CGPoint? { positions[id] }

    /// The node ids that deserve an always-on label: the handful with the largest
    /// radii (hubs and top entities). 12 labels is the most a map this size can
    /// carry before neighbors start colliding.
    var topLabelNodeIDs: Set<String> {
        Set(radii.sorted { $0.value > $1.value }.prefix(12).map(\.key))
    }

    func radius(of node: GraphNode) -> CGFloat { radii[node.id] ?? 5 }

    /// Hit-tests a point to the nearest node within a generous touch radius; nil when the tap
    /// landed on empty space (so callers can deselect).
    func nearestNode(to point: CGPoint) -> GraphNode? {
        var best: (node: GraphNode, distance: CGFloat)?
        for (id, nodePoint) in positions {
            guard let node = nodesByID[id] else { continue }
            let dx = nodePoint.x - point.x
            let dy = nodePoint.y - point.y
            let distance = (dx * dx + dy * dy).squareRoot()
            let hitRadius = (radii[id] ?? 5) + 12
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
