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

    var body: some View {
        VStack(alignment: .leading, spacing: CortexDesign.Space.md) {
            if state.graphNodes.isEmpty {
                CortexEmptyState(
                    systemImage: "point.3.connected.trianglepath.dotted",
                    title: "Your Constellation",
                    message: "Your memory map appears as Cortex learns about you."
                )
            } else {
                mapCanvas
                if let node = selectedNode {
                    NodeDetailPanel(node: node, onExplore: onExplore)
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
            }
            hoveredNodeID = nil
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
            Canvas { context, size in
                drawEdges(in: &context, layout: layout)
                drawNodes(in: &context, size: size, layout: layout)
            }
            .contentShape(Rectangle())
            .onTapGesture { location in
                if let hit = layout.nearestNode(to: location) {
                    selectedNodeID = (selectedNodeID == hit.id) ? nil : hit.id
                } else {
                    selectedNodeID = nil
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

    private func drawEdges(in context: inout GraphicsContext, layout: MemoryMapLayout) {
        for edge in state.graphEdges {
            guard let a = layout.position(of: edge.source_id),
                  let b = layout.position(of: edge.target_id) else { continue }
            var path = Path()
            path.move(to: a)
            path.addLine(to: b)
            let weight = edge.weight ?? 0.5
            let lineWidth = 0.6 + CGFloat(max(0, min(1, weight))) * 1.4
            // Heavier ties lean gold, light ties are faint ink — both stay hairline-quiet.
            let color = weight >= 0.66 ? CortexDesign.gold : CortexDesign.ink
            let opacity = 0.14 + min(0.34, weight * 0.34)
            context.stroke(path, with: .color(color.opacity(opacity)), lineWidth: lineWidth)
        }
    }

    private func drawNodes(in context: inout GraphicsContext, size: CGSize, layout: MemoryMapLayout) {
        for node in state.graphNodes {
            guard let point = layout.position(of: node.id) else { continue }
            let radius = layout.radius(of: node)
            let isSelected = node.id == selectedNodeID
            let isHovered = node.id == hoveredNodeID
            let fill = MemoryMapView.color(for: node.type)

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

            context.fill(circle, with: .color(fill.opacity(isSelected ? 1.0 : 0.85)))
            context.stroke(circle, with: .color(CortexDesign.panelBackground), lineWidth: 1)

            // Label the larger / selected / hovered nodes so the map stays uncluttered.
            if radius >= 7 || isSelected || isHovered {
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
                context.draw(resolved, in: CGRect(origin: textPoint, size: textSize))
            }
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
    var onExplore: ((GraphNode) -> Void)? = nil

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
                    .fill(MemoryMapView.color(for: node.type))
                    .frame(width: 8, height: 8)
                Text(node.label)
                    .font(CortexDesign.Typography.title)
                    .foregroundColor(CortexDesign.ink)
                Spacer(minLength: 0)
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

        // Order matters for stable angular placement: sort by id so the sequence is deterministic
        // regardless of the order the backend returned nodes.
        let ordered = nodes.sorted { $0.id < $1.id }
        let count = max(1, ordered.count)

        for (index, node) in ordered.enumerated() {
            let seed = MemoryMapLayout.stableHash(node.id)
            let importance = CGFloat(node.importance ?? 1) / maxImportance // 0...1

            // Angle: evenly spread by index, jittered deterministically by the id hash so ties
            // don't overlap and the layout feels organic rather than perfectly wheel-like.
            let baseAngle = (CGFloat(index) / CGFloat(count)) * 2 * .pi
            let jitter = (CGFloat(seed % 1000) / 1000.0 - 0.5) * (2 * .pi / CGFloat(count))
            let angle = baseAngle + jitter

            // Distance: important nodes sit closer to the center; a deterministic radial jitter
            // keeps equal-importance nodes off a single ring.
            let ringJitter = CGFloat((seed / 1000) % 1000) / 1000.0 // 0...1
            let normalizedDistance = (1 - importance) * 0.72 + ringJitter * 0.28
            let distance = maxRadius * (0.18 + normalizedDistance * 0.82)

            let point = CGPoint(
                x: center.x + cos(angle) * distance,
                y: center.y + sin(angle) * distance
            )
            positions[node.id] = point
            radii[node.id] = 5 + importance * 9
        }
    }

    func position(of id: String) -> CGPoint? { positions[id] }

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
