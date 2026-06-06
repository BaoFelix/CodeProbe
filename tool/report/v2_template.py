"""v2_template.py — three stacked layers, nothing else.

Layer 1  高层工作流       — responsibility forest as a collapsible tree
Layer 2  详细关系图        — Cytoscape graph, pan/zoom/click
Layer 3  痛点与设计建议    — pain points + recommendations as a list

No side panels, no tabs, no search box, no layout switcher.
"""
import json


def render(payload):
    data_json = json.dumps(payload, ensure_ascii=False, default=str)
    return _HTML.replace('__PAYLOAD__', data_json)


_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CodeProbe</title>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.30.0/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
<style>
  :root {
    --bg:    #fafafa;
    --panel: #ffffff;
    --line:  #e2e2e2;
    --text:  #1f1f1f;
    --muted: #777;
    --link:  #2563eb;
    --orch:  #dc2626;
    --high:  #dc2626;
    --med:   #ca8a04;
    --low:   #94a3b8;
    --lv5: #dc2626; --lv4: #ea580c; --lv3: #f59e0b;
    --lv2: #6366f1; --lv1: #0891b2; --lv0: #94a3b8;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: ui-sans-serif, system-ui, sans-serif;
         color: var(--text); background: var(--bg);
         max-width: 1200px; margin: 0 auto; padding: 16px; }

  /* ── Header ────────────────────────────────────────── */
  header { padding: 8px 0 16px; border-bottom: 1px solid var(--line);
           margin-bottom: 16px; }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  header .meta { color: var(--muted); font-size: 12px; margin-top: 4px; }

  /* ── Section frame ─────────────────────────────────── */
  section { background: var(--panel); border: 1px solid var(--line);
            border-radius: 6px; margin-bottom: 16px; overflow: hidden; }
  section > h2 { margin: 0; padding: 12px 16px; font-size: 14px;
                 font-weight: 600; background: #f8fafc;
                 border-bottom: 1px solid var(--line); }
  section > .body { padding: 12px 16px; }

  /* ── Layer 1: forest ───────────────────────────────── */
  .forest { font-size: 13px; }
  .forest details { margin: 2px 0; }
  .forest summary { cursor: pointer; padding: 4px 6px; border-radius: 4px;
                    list-style: none; }
  .forest summary::-webkit-details-marker { display: none; }
  .forest summary::before { content: '▸'; display: inline-block;
                             width: 14px; color: var(--muted); }
  .forest details[open] > summary::before { content: '▾'; }
  .forest summary:hover { background: #f1f5f9; }
  .forest summary.orch { color: var(--orch); font-weight: 600; }
  .forest details details { margin-left: 18px; }
  .forest summary small { color: var(--muted); margin-left: 8px;
                          font-size: 11px; font-weight: normal; }
  .forest .leaf { padding: 4px 6px 4px 20px; cursor: pointer;
                  border-radius: 4px; }
  .forest .leaf:hover { background: #f1f5f9; }

  /* ── Layer 2: graph ────────────────────────────────── */
  #cy { height: 520px; background: var(--panel); }

  /* ── Layer 3: critic list ──────────────────────────── */
  .critic-list { font-size: 13px; }
  .critic-list h3 { margin: 16px 0 8px; font-size: 13px; font-weight: 600;
                    color: var(--muted); text-transform: uppercase;
                    letter-spacing: 0.05em; }
  .critic-list h3:first-child { margin-top: 0; }
  .card { padding: 10px 12px; border-radius: 4px; margin: 6px 0;
          border-left: 3px solid var(--low); background: #f8fafc; }
  .card.high { border-left-color: var(--high); background: #fef2f2; }
  .card.med  { border-left-color: var(--med);  background: #fef9c3; }
  .card.low  { border-left-color: var(--low);  background: #f8fafc; }
  .card .head { display: flex; align-items: baseline; gap: 8px;
                margin-bottom: 4px; }
  .card .tag { font-size: 10px; padding: 1px 6px; border-radius: 3px;
               color: white; background: var(--low); font-weight: 600; }
  .card.high .tag { background: var(--high); }
  .card.med  .tag { background: var(--med);  }
  .card .target { font-weight: 600; color: var(--text); }
  .card .where  { color: var(--muted); font-size: 11px;
                  font-family: ui-monospace, monospace; margin: 2px 0; }
  .card .body-text  { color: var(--text); margin: 6px 0; }
  .card .body-text strong { color: var(--text); }
  .card .body-text .label {
      color: var(--muted); font-size: 11px;
      text-transform: uppercase; letter-spacing: 0.05em;
      margin-right: 4px; }
  .empty { color: var(--muted); font-style: italic; padding: 8px; }
</style>
</head>
<body>

<header>
  <h1>CodeProbe <span id="proj"></span></h1>
  <div class="meta" id="meta"></div>
</header>

<section>
  <h2>1. 高层工作流</h2>
  <div class="body forest" id="forest"></div>
</section>

<section>
  <h2>2. 详细关系图</h2>
  <div id="cy"></div>
</section>

<section>
  <h2>3. 痛点与设计建议</h2>
  <div class="body critic-list" id="critic"></div>
</section>

<script>
const DATA = __PAYLOAD__;

// ── Header ───────────────────────────────────────────
document.getElementById('proj').textContent =
  DATA.summary && DATA.summary.directory ? '— ' + DATA.summary.directory : '';
document.getElementById('meta').textContent =
  `${DATA.summary.class_count || 0} classes · ${DATA.summary.file_count || 0} files`
  + (DATA.summary.orchestrator ? ` · orchestrator: ${DATA.summary.orchestrator}` : '')
  + (DATA.summary.style && DATA.summary.style !== 'oop' ? ` · style=${DATA.summary.style}` : '');

// ── Layer 1: responsibility forest ───────────────────
function renderForest() {
  const root = document.getElementById('forest');
  if (!DATA.forest || DATA.forest.length === 0) {
    root.innerHTML = '<div class="empty">No high-level workflow detected.</div>';
    return;
  }
  for (const tree of DATA.forest) {
    root.appendChild(buildTreeNode(tree, true));
  }
}
function buildTreeNode(node, open) {
  if (!node.children || node.children.length === 0) {
    const div = document.createElement('div');
    div.className = 'leaf';
    div.textContent = node.label.split('::').pop();
    const small = document.createElement('small');
    small.textContent = node.label;
    small.style.color = 'var(--muted)';
    small.style.marginLeft = '8px';
    div.appendChild(small);
    div.onclick = () => focusGraph(node.label);
    return div;
  }
  const det = document.createElement('details');
  if (open) det.open = true;
  const sum = document.createElement('summary');
  if (node.label === DATA.summary.orchestrator) sum.classList.add('orch');
  sum.appendChild(document.createTextNode(node.label.split('::').pop()));
  const small = document.createElement('small');
  small.textContent = `(${node.reach} below)`;
  sum.appendChild(small);
  sum.onclick = (e) => {
    if (e.target === sum || e.target.tagName === 'SMALL') {
      // let details toggle naturally; only focus on text click
      setTimeout(() => focusGraph(node.label), 0);
    }
  };
  det.appendChild(sum);
  for (const child of node.children) {
    det.appendChild(buildTreeNode(child, false));
  }
  return det;
}

// ── Layer 2: Cytoscape ───────────────────────────────
const LV = { 0:'var(--lv0)', 1:'var(--lv1)', 2:'var(--lv2)',
             3:'var(--lv3)', 4:'var(--lv4)', 5:'var(--lv5)' };
cytoscape.use(cytoscapeDagre);
const cy = cytoscape({
  container: document.getElementById('cy'),
  elements: { nodes: DATA.nodes, edges: DATA.edges },
  style: [
    { selector: 'node',
      style: {
        'label': 'data(label)',
        'background-color': '#3b82f6',
        'font-size': '11px',
        'text-valign': 'bottom',
        'text-margin-y': 4,
        'width': 'mapData(out_deg, 0, 10, 18, 38)',
        'height': 'mapData(out_deg, 0, 10, 18, 38)',
      } },
    { selector: 'node[is_orchestrator = 1]',
      style: { 'background-color': '#dc2626', 'border-width': 2,
               'border-color': '#7f1d1d', 'font-weight': 'bold' } },
    { selector: 'node[is_utility = 1]',
      style: { 'background-color': '#94a3b8', 'opacity': 0.7 } },
    { selector: 'node[kind = "interface"]',
      style: { 'shape': 'diamond' } },
    { selector: 'node[kind = "struct"]',
      style: { 'shape': 'rectangle' } },
    { selector: 'edge',
      style: {
        'curve-style': 'bezier',
        'target-arrow-shape': 'triangle',
        'width': 'mapData(level, 0, 5, 1, 4)',
        'line-color': ele => LV[ele.data('level')] || '#999',
        'target-arrow-color': ele => LV[ele.data('level')] || '#999',
        'opacity': 0.7,
      } },
    { selector: 'node.highlighted',
      style: { 'border-width': 3, 'border-color': '#2563eb' } },
    { selector: 'node.faded, edge.faded',
      style: { 'opacity': 0.15 } },
  ],
  layout: { name: 'dagre', rankDir: 'TB', nodeSep: 30, rankSep: 50 },
  wheelSensitivity: 0.2,
});
cy.on('tap', 'node', (evt) => focusGraph(evt.target.id()));
cy.on('tap', (evt) => { if (evt.target === cy) clearFocus(); });

function focusGraph(qname) {
  const node = cy.getElementById(qname);
  if (!node || node.empty()) return;
  cy.elements().removeClass('highlighted').addClass('faded');
  node.removeClass('faded').addClass('highlighted');
  node.neighborhood().removeClass('faded');
  cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1.2) },
             { duration: 300 });
}
function clearFocus() { cy.elements().removeClass('highlighted faded'); }

// ── Layer 3: pains + recommendations ─────────────────
function renderCritic() {
  const root = document.getElementById('critic');
  const critic = DATA.critic || {subtrees: [], module: null};
  let html = '';

  // pains, grouped by subtree
  const subtreesWithPains = (critic.subtrees || [])
    .filter(s => s.analysis && s.analysis.pains && s.analysis.pains.length);
  if (subtreesWithPains.length) {
    html += '<h3>痛点</h3>';
    for (const sub of subtreesWithPains) {
      for (const p of sub.analysis.pains) {
        html += `<div class="card">`;
        html += `<div class="head"><span class="target">${escapeHtml(sub.root.split('::').pop())}</span>`;
        if (p.category) html += ` <span class="tag" style="background:#64748b">${escapeHtml(p.category)}</span>`;
        html += `</div>`;
        if (p.where) html += `<div class="where">${escapeHtml(p.where)}</div>`;
        html += `<div class="body-text">${escapeHtml(p.what || '')}</div>`;
        html += `</div>`;
      }
    }
  }

  // recommendations
  const recs = (critic.module && critic.module.recommendations) || [];
  if (recs.length) {
    html += '<h3>设计建议</h3>';
    const order = {high:0, medium:1, low:2};
    const sorted = [...recs].sort((a,b) =>
      (order[a.priority]||3) - (order[b.priority]||3));
    for (const r of sorted) {
      const pri = r.priority || 'medium';
      const cls = pri === 'high' ? 'high' : pri === 'low' ? 'low' : 'med';
      html += `<div class="card ${cls}">`;
      html += `<div class="head"><span class="tag">${pri}</span>`;
      html += `<span class="target">${escapeHtml(r.target || '?')}</span></div>`;
      if (r.action)
        html += `<div class="body-text"><span class="label">action</span>${escapeHtml(r.action)}</div>`;
      if (r.expected_impact)
        html += `<div class="body-text"><span class="label">impact</span>${escapeHtml(r.expected_impact)}</div>`;
      if (r.evidence)
        html += `<div class="body-text"><span class="label">evidence</span>${escapeHtml(r.evidence)}</div>`;
      html += `</div>`;
    }
  }

  if (!html) html = '<div class="empty">No design analysis yet. Run <code>analyze</code> first.</div>';
  root.innerHTML = html;
}

function escapeHtml(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

renderForest();
renderCritic();
</script>
</body>
</html>
"""
