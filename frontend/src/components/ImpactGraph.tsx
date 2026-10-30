export interface GraphNode {
  id: string;
  label: string;
  depth: number | null;
  severity: "critical" | "high" | "medium" | "low" | null;
  records: number;
  impacts: number;
}
export interface GraphEdge { source: string; target: string; relation: string; active: boolean }

const WIDTH = 790;
const HEIGHT = 470;
const CENTER = { x: WIDTH / 2, y: HEIGHT / 2 };
const NODE = { width: 132, height: 48 };
const RING = { first: 105, step: 60, xScale: 1.42, yScale: 0.92, idle: 190, twist: 0.7 };

function positions(nodes: GraphNode[], idle?: boolean) {
  const deepest = Math.max(1, ...nodes.map((n) => n.depth ?? 0));
  const ringOf = (n: GraphNode) => n.depth ?? (nodes.some((m) => m.depth !== null && m.depth > 0) ? Math.min(deepest + 1, 3) : 1);
  const rings = new Map<number, GraphNode[]>();
  for (const node of nodes) rings.set(ringOf(node), [...(rings.get(ringOf(node)) ?? []), node]);
  const placed = new Map<string, { x: number; y: number; ring: number }>();
  for (const [ring, members] of rings) {
    members.forEach((node, i) => {
      if (ring === 0) {
        placed.set(node.id, { ...CENTER, ring });
        return;
      }
      const radius = idle ? RING.idle : RING.first + (ring - 1) * RING.step;
      const offset = -Math.PI / 2 + (ring - 1) * RING.twist; // Rotating each ring stops nodes lining up behind one another.
      const angle = offset + (i * 2 * Math.PI) / members.length;
      placed.set(node.id, { x: CENTER.x + Math.cos(angle) * radius * RING.xScale, y: CENTER.y + Math.sin(angle) * radius * RING.yScale, ring });
    });
  }
  return { placed, rings: Math.max(...[...placed.values()].map((p) => p.ring), 1) };
}

/** Where a line from a node's centre leaves its box, so arrowheads stop at the edge. */
function edgePoint(from: { x: number; y: number }, to: { x: number; y: number }) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const scale = Math.min((NODE.width / 2 + 4) / Math.abs(dx || 1), (NODE.height / 2 + 4) / Math.abs(dy || 1));
  return { x: from.x + dx * scale, y: from.y + dy * scale };
}

const TONE_LABEL: Record<string, string> = { source: "Change", critical: "Critical", high: "High", medium: "Medium", low: "Low" };

/** The same consequences as the graph, as a vertical chain that stays readable on a phone. */
export function ImpactChain({ nodes, edges, selected, onSelect }: { nodes: GraphNode[]; edges: GraphEdge[]; selected: string | null; onSelect: (id: string | null) => void }) {
  const steps = nodes.filter((n) => n.depth !== null).sort((a, b) => a.depth! - b.depth!);
  const names = Object.fromEntries(nodes.map((n) => [n.id, n.label]));
  const depths = Object.fromEntries(nodes.map((n) => [n.id, n.depth]));
  const untouched = nodes.length - steps.length;
  return (
    <div className="impact-chain">
      <ol>
        {steps.map((n) => {
          const via = edges.find((e) => e.active && e.target === n.id && (depths[e.source] ?? 0) < n.depth!);
          const tone = n.depth === 0 ? "source" : n.severity ?? "low";
          return (
            <li key={n.id} className={`chain-step chain-${tone}`}>
              {via && <p className="chain-why">{names[via.source]} {via.relation} {n.label}</p>}
              <button className={selected === n.id ? "active" : ""} aria-pressed={selected === n.id} onClick={() => onSelect(selected === n.id ? null : n.id)}>
                <strong>{n.label}</strong>
                <small>{TONE_LABEL[tone]} · {n.depth === 0 ? "Change starts here" : `${n.records.toLocaleString("en-IN")} ${n.records === 1 ? "record" : "records"}`}</small>
              </button>
            </li>
          );
        })}
      </ol>
      {untouched > 0 && <p className="muted small">{untouched} {untouched === 1 ? "module is" : "modules are"} not affected.</p>}
    </div>
  );
}

export function ImpactGraph({ nodes, edges, selected, onSelect, idle }: {
  nodes: GraphNode[]; edges: GraphEdge[]; selected?: string | null; onSelect?: (id: string | null) => void; idle?: boolean;
}) {
  const { placed, rings } = positions(nodes, idle);
  const reached = nodes.filter((n) => n.depth !== null).length;
  return (
    <svg className={`impact-graph ${idle ? "idle" : ""}`} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="group"
         aria-label={idle ? "The connected festival modules" : `The change reaches ${reached} of ${nodes.length} modules`}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0L10 5L0 10z" fill="var(--cyan)" />
        </marker>
      </defs>
      {Array.from({ length: rings }, (_, i) => {
        const radius = idle ? RING.idle : RING.first + i * RING.step;
        return <ellipse key={i} className="ring" style={{ animationDelay: `${i * 160}ms` }} cx={CENTER.x} cy={CENTER.y} rx={radius * RING.xScale} ry={radius * RING.yScale} />;
      })}
      {edges.filter((e) => idle || e.active).map((e) => {
        const a = placed.get(e.source);
        const b = placed.get(e.target);
        if (!a || !b) return null;
        const start = edgePoint(a, b);
        const end = edgePoint(b, a);
        const delay = (nodes.find((n) => n.id === e.source)?.depth ?? 0) * 220;
        const midX = (start.x + end.x) / 2 + (end.y - start.y) * 0.1;
        const midY = (start.y + end.y) / 2 - (end.x - start.x) * 0.1;
        return (
          <path key={`${e.source}-${e.target}`} className={idle ? "link quiet" : "link"} style={{ animationDelay: `${delay}ms` }} pathLength={1}
                d={`M${start.x} ${start.y}Q${midX} ${midY} ${end.x} ${end.y}`} markerEnd={idle ? undefined : "url(#arrow)"}>
            <title>{`${e.source} ${e.relation} ${e.target}`}</title>
          </path>
        );
      })}
      {nodes.map((n) => {
        const p = placed.get(n.id)!;
        const tone = n.depth === null ? "off" : n.depth === 0 ? "source" : n.severity ?? "low";
        const active = selected === n.id;
        const label = idle ? "" : n.depth === null ? "Not affected" : n.depth === 0 ? "Change starts here" : `${n.records.toLocaleString("en-IN")} ${n.records === 1 ? "record" : "records"}`;
        return (
          <g key={n.id} className={`node node-${tone} ${active ? "active" : ""}`} style={{ animationDelay: `${(n.depth ?? 2) * 200}ms` }}
             transform={`translate(${p.x - NODE.width / 2} ${p.y - NODE.height / 2})`}
             role={onSelect && n.depth !== null ? "button" : undefined} tabIndex={onSelect && n.depth !== null ? 0 : undefined}
             aria-label={`${n.label}: ${label}`} aria-pressed={onSelect && n.depth !== null ? active : undefined}
             onClick={() => onSelect && n.depth !== null && onSelect(active ? null : n.id)}
             onKeyDown={(e) => { if (onSelect && n.depth !== null && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onSelect(active ? null : n.id); } }}>
            <rect width={NODE.width} height={NODE.height} rx={12} />
            <text x={NODE.width / 2} y={label ? 20 : 29} className="node-label">{n.label}</text>
            {label && <text x={NODE.width / 2} y={36} className="node-meta">{label}</text>}
          </g>
        );
      })}
    </svg>
  );
}
