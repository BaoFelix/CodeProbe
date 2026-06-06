"""v2_template.py — the HTML shell. Single page, embedded CSS/JS,
loads Cytoscape.js + dagre layout from CDN.

The page is data-driven: we inject the JSON payload (from v2_data.py)
into a `window.__DATA__` global, and the JS below renders from it.
"""
import json


def render(payload):
    data_json = json.dumps(payload, ensure_ascii=False, default=str)
    return _HTML.replace('__PAYLOAD__', data_json)


# Triple-braced template: literal { become {{, and __PAYLOAD__ is the
# only swap point. Keep the HTML readable rather than f-stringing every
# line.

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CodeProbe — Architecture View</title>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.30.0/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
<style>
  :root {
    --bg:        #fafafa;
    --panel-bg:  #ffffff;
    --border:    #e2e2e2;
    --text:      #1f1f1f;
    --muted:     #777;
    --accent:    #2563eb;
    --orch:      #dc2626;
    --util:      #94a3b8;
    --lv5:       #dc2626;  /* inherits   — strongest */
    --lv4:       #ea580c;  /* composes */
    --lv3:       #f59e0b;  /* aggregates */
    --lv2:       #6366f1;  /* implements */
    --lv1:       #0891b2;  /* associates */
    --lv0:       #94a3b8;  /* depends    — weakest */
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
         color: var(--text); background: var(--bg); }
  header { padding: 8px 16px; border-bottom: 1px solid var(--border);
           background: var(--panel-bg); display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 14px; margin: 0; font-weight: 600; }
  header .meta { color: var(--muted); font-size: 12px; }
  header .controls { margin-left: auto; display: flex; gap: 8px; align-items: center; }
  header button { padding: 4px 10px; font-size: 12px; cursor: pointer;
                  border: 1px solid var(--border); background: white; border-radius: 4px; }
  header button.active { background: var(--accent); color: white; border-color: var(--accent); }
  header input[type="search"] { padding: 4px 8px; border: 1px solid var(--border);
                                 border-radius: 4px; font-size: 12px; width: 200px; }

  main { display: grid; grid-template-columns: 260px 1fr 320px;
         grid-template-rows: 1fr auto; height: calc(100vh - 41px); }
  .nav  { grid-column: 1; grid-row: 1 / 3; border-right: 1px solid var(--border);
          overflow-y: auto; padding: 12px; background: var(--panel-bg); }
  #cy   { grid-column: 2; grid-row: 1 / 3; }
  .side { grid-column: 3; grid-row: 1 / 3; border-left: 1px solid var(--border);
          overflow-y: auto; padding: 12px; background: var(--panel-bg); }
  .pains-strip { grid-column: 2; grid-row: 2; align-self: end;
                 border-top: 1px solid var(--border); padding: 8px 16px;
                 background: var(--panel-bg); max-height: 30vh; overflow-y: auto; }

  /* Left navigation: forest + utilities */
  .section-title { font-size: 11px; font-weight: 600; color: var(--muted);
                   text-transform: uppercase; letter-spacing: .05em;
                   margin: 12px 0 6px; }
  .tree details { margin-left: 0; }
  .tree summary { cursor: pointer; padding: 2px 4px; font-size: 13px;
                  border-radius: 3px; display: list-item; }
  .tree summary:hover { background: #f1f5f9; }
  .tree details details { margin-left: 16px; border-left: 1px solid #eee;
                          padding-left: 4px; }
  .tree .orch { color: var(--orch); font-weight: 600; }
  .tree .leaf { padding: 2px 4px 2px 22px; font-size: 13px; cursor: pointer;
                border-radius: 3px; }
  .tree .leaf:hover { background: #f1f5f9; }
  .tree small { color: var(--muted); margin-left: 4px; font-size: 11px; }
  .util-list { font-size: 13px; }
  .util-list .item { padding: 2px 4px; cursor: pointer; border-radius: 3px;
                     display: flex; justify-content: space-between; }
  .util-list .item:hover { background: #f1f5f9; }
  .util-list .item small { color: var(--muted); }

  /* Right side: selected node details */
  .side h2 { font-size: 13px; margin: 0 0 8px; padding-bottom: 6px;
             border-bottom: 1px solid var(--border); }
  .side .meta { font-size: 11px; color: var(--muted); margin-bottom: 12px; }
  .side .edges-section { margin-top: 16px; }
  .side .edges-section .section-title { margin-top: 8px; }
  .side .edge { padding: 6px 8px; border-radius: 4px; background: #f8fafc;
                margin-bottom: 6px; font-size: 12px; }
  .side .edge .kind { display: inline-block; padding: 1px 6px; border-radius: 3px;
                      color: white; font-size: 10px; font-weight: 600;
                      margin-right: 6px; }
  .side .edge .ev { color: var(--muted); font-family: ui-monospace, monospace;
                    font-size: 11px; display: block; margin-top: 4px;
                    word-break: break-all; }

  /* Pains strip */
  .critic-tabs { display: flex; gap: 4px; margin-bottom: 8px; }
  .ctab { padding: 3px 10px; font-size: 12px; cursor: pointer;
          border: 1px solid var(--border); background: white; border-radius: 4px; }
  .ctab.active { background: var(--accent); color: white; border-color: var(--accent); }
  .critic-panel { display: none; font-size: 12px; }
  .critic-panel.active { display: block; }
  .critic-panel details { margin-bottom: 4px; }
  .critic-panel summary { cursor: pointer; padding: 3px 0; font-weight: 600; }
  .critic-panel summary:hover { background: #f1f5f9; }
  .stage-card { padding: 6px 8px; background: #f0f9ff; border-left: 3px solid var(--accent);
                margin: 4px 0; border-radius: 2px; }
  .stage-card .stage-name { font-weight: 600; }
  .stage-card .stage-meta { color: var(--muted); font-size: 11px; }
  .component-line { padding: 2px 0 2px 12px; }
  .pain-card { padding: 6px 8px; background: #fef2f2; border-left: 3px solid var(--orch);
               margin: 4px 0; border-radius: 2px; }
  .pain-card .pain-where { color: var(--muted); font-size: 11px; font-family: ui-monospace, monospace; }
  .pain-card .pain-cat { display: inline-block; padding: 1px 5px; border-radius: 3px;
                         background: var(--orch); color: white; font-size: 10px; margin-right: 6px; }
  .rec-card { padding: 8px 10px; background: #fef9c3; border-left: 3px solid #ca8a04;
              margin: 6px 0; border-radius: 2px; }
  .rec-card.high { border-left-color: var(--orch); background: #fef2f2; }
  .rec-card.low  { border-left-color: var(--util);  background: #f8fafc; }
  .rec-card .rec-priority { font-size: 10px; padding: 1px 6px; border-radius: 3px;
                            color: white; background: #ca8a04; margin-right: 6px; }
  .rec-card.high .rec-priority { background: var(--orch); }
  .rec-card.low  .rec-priority { background: var(--util); }
  .rec-card .rec-target { font-weight: 600; }
  .rec-card .rec-impact { color: var(--muted); font-size: 11px; margin-top: 4px; }
  .empty-msg { color: var(--muted); font-style: italic; padding: 8px; }

  /* Legacy pain strip styles kept for any third-party skill that still uses them. */
  .pains-strip details { margin-bottom: 6px; }
  .pains-strip summary { cursor: pointer; font-size: 13px; font-weight: 600;
                          padding: 4px 0; }
  .pains-strip .pain-item { padding: 6px 8px; background: #fef2f2;
                            border-left: 3px solid var(--orch);
                            border-radius: 2px; margin: 4px 0;
                            font-size: 12px; }
  .pains-strip .pain-class { color: var(--accent); font-weight: 600;
                             cursor: pointer; }
  .pains-strip .pain-class:hover { text-decoration: underline; }

  /* Empty placeholder for right panel */
  .side .empty { color: var(--muted); font-style: italic; font-size: 12px; }
</style>
</head>
<body>

<header>
  <h1>CodeProbe</h1>
  <span class="meta" id="meta-line"></span>
  <span class="controls">
    <span>depth:</span>
    <button data-depth="1" class="depth-btn">1</button>
    <button data-depth="2" class="depth-btn">2</button>
    <button data-depth="3" class="depth-btn active">3</button>
    <button data-depth="0" class="depth-btn">all</button>
    <span style="margin-left: 12px;">layout:</span>
    <button data-layout="dagre" class="layout-btn active">tree</button>
    <button data-layout="cose" class="layout-btn">force</button>
    <input type="search" placeholder="search a class…" id="search">
  </span>
</header>

<main>
  <nav class="nav">
    <div class="section-title">Responsibility forest</div>
    <div id="forest" class="tree"></div>

    <div class="section-title">Utilities &amp; infrastructure</div>
    <div id="utilities" class="util-list"></div>
  </nav>

  <div id="cy"></div>

  <aside class="side">
    <div id="details"><div class="empty">Click a node to see its relationships.</div></div>
  </aside>

  <section class="pains-strip">
    <div class="critic-tabs">
      <button class="ctab active" data-tab="workflow">Ideal workflow</button>
      <button class="ctab" data-tab="pains">Pain points</button>
      <button class="ctab" data-tab="recs">Recommendations</button>
    </div>
    <div id="critic-workflow" class="critic-panel active"></div>
    <div id="critic-pains"    class="critic-panel"></div>
    <div id="critic-recs"     class="critic-panel"></div>
  </section>
</main>

<script>
const DATA = __PAYLOAD__;

// ── Header ────────────────────────────────────────────────────
const meta = DATA.summary;
document.getElementById('meta-line').textContent =
  `${meta.directory || ''} · ${meta.class_count || 0} classes · ${meta.file_count || 0} files`
  + (meta.style && meta.style !== 'oop' ? ` · style=${meta.style}` : '');

// ── Left: responsibility forest ───────────────────────────────
function renderForest() {
  const root = document.getElementById('forest');
  for (const tree of DATA.forest) {
    root.appendChild(renderTreeNode(tree, true));
  }
}
function renderTreeNode(node, open) {
  if (!node.children || node.children.length === 0) {
    const div = document.createElement('div');
    div.className = 'leaf';
    div.dataset.qname = node.label;
    div.textContent = node.label.split('::').pop();
    const small = document.createElement('small');
    small.textContent = node.label.split('::').slice(0, -1).join('::');
    if (small.textContent) div.appendChild(small);
    div.onclick = () => focusNode(node.label);
    return div;
  }
  const det = document.createElement('details');
  if (open) det.open = true;
  const sum = document.createElement('summary');
  const short = node.label.split('::').pop();
  sum.textContent = short + ` `;
  sum.dataset.qname = node.label;
  if (node.label === DATA.summary.orchestrator) sum.classList.add('orch');
  const reach = document.createElement('small');
  reach.textContent = `${node.reach} below`;
  sum.appendChild(reach);
  sum.onclick = (e) => { /* let details toggle */
    if (e.target === sum) focusNode(node.label);
  };
  det.appendChild(sum);
  for (const child of node.children) det.appendChild(renderTreeNode(child, false));
  return det;
}

// ── Left bottom: utility list ─────────────────────────────────
function renderUtilities() {
  const root = document.getElementById('utilities');
  if (DATA.utilities.length === 0) {
    root.innerHTML = '<div style="color: var(--muted); font-size: 12px;">none flagged</div>';
    return;
  }
  for (const u of DATA.utilities) {
    const div = document.createElement('div');
    div.className = 'item';
    div.dataset.qname = u.qname;
    div.innerHTML = `<span>${u.short}</span><small>in: ${u.in_deg}</small>`;
    div.onclick = () => focusNode(u.qname);
    root.appendChild(div);
  }
}

// ── Cytoscape main graph ──────────────────────────────────────
const LV_COLORS = { 0: 'var(--lv0)', 1: 'var(--lv1)', 2: 'var(--lv2)',
                    3: 'var(--lv3)', 4: 'var(--lv4)', 5: 'var(--lv5)' };

cytoscape.use(cytoscapeDagre);

const cy = cytoscape({
  container: document.getElementById('cy'),
  elements: { nodes: DATA.nodes, edges: DATA.edges },
  style: [
    { selector: 'node',
      style: {
        'label': 'data(label)',
        'background-color': '#3b82f6',
        'color': '#1f1f1f',
        'font-size': '11px',
        'text-valign': 'bottom',
        'text-margin-y': 4,
        'width': 'mapData(out_deg, 0, 10, 18, 40)',
        'height': 'mapData(out_deg, 0, 10, 18, 40)',
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
        'line-color': function(ele) { return LV_COLORS[ele.data('level')] || '#999'; },
        'target-arrow-color': function(ele) { return LV_COLORS[ele.data('level')] || '#999'; },
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

cy.on('tap', 'node', (evt) => focusNode(evt.target.id()));
cy.on('tap', (evt) => { if (evt.target === cy) clearFocus(); });

// ── Focus / highlight ────────────────────────────────────────
function focusNode(qname) {
  const node = cy.getElementById(qname);
  if (!node || node.empty()) return;
  cy.elements().removeClass('highlighted').addClass('faded');
  node.removeClass('faded').addClass('highlighted');
  node.neighborhood().removeClass('faded');
  cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1.2) },
             { duration: 300 });
  renderDetails(qname);
}
function clearFocus() {
  cy.elements().removeClass('highlighted faded');
  document.getElementById('details').innerHTML =
    '<div class="empty">Click a node to see its relationships.</div>';
}

// ── Right: selected-node details ────────────────────────────
function renderDetails(qname) {
  const node = cy.getElementById(qname);
  const d = node.data();
  const out = cy.edges(`[source = "${qname}"]`);
  const inc = cy.edges(`[target = "${qname}"]`);
  let html = `<h2>${d.label}</h2>`;
  html += `<div class="meta">${d.qname}<br>${d.kind} · ${d.file || ''}${d.start_line ? ':' + d.start_line : ''}</div>`;
  html += renderEdgeList('Outgoing (uses)', out, 'target');
  html += renderEdgeList('Incoming (used by)', inc, 'source');
  document.getElementById('details').innerHTML = html;
}
function renderEdgeList(title, edges, endField) {
  if (edges.length === 0) return '';
  let s = `<div class="edges-section"><div class="section-title">${title} (${edges.length})</div>`;
  edges.forEach(e => {
    const data = e.data();
    const other = data[endField];
    s += `<div class="edge">`;
    s += `<strong onclick="focusNode('${other}')" style="cursor:pointer; color: var(--accent);">${other.split('::').pop()}</strong>`;
    s += ` <small style="color: var(--muted);">${other}</small><br>`;
    data.evidence.forEach(ev => {
      s += `<span class="kind" style="background: ${LV_COLORS[ev.level]}">Lv-${ev.level} ${ev.kind}</span>`;
      if (ev.evidence_text) s += `<span class="ev">${escapeHtml(ev.evidence_text)}</span>`;
    });
    s += `</div>`;
  });
  return s + `</div>`;
}
function escapeHtml(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Bottom: pain points ─────────────────────────────────────
// ── Bottom: DesignCritic ────────────────────────────────────
function renderCritic() {
  const critic = DATA.critic || {subtrees: [], module: null};
  renderCriticWorkflow(critic);
  renderCriticPains(critic);
  renderCriticRecs(critic);

  // tab switching
  document.querySelectorAll('.ctab').forEach(b => {
    b.onclick = () => {
      document.querySelectorAll('.ctab').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.critic-panel').forEach(p => p.classList.remove('active'));
      b.classList.add('active');
      document.getElementById('critic-' + b.dataset.tab).classList.add('active');
    };
  });
}

function renderCriticWorkflow(critic) {
  const root = document.getElementById('critic-workflow');
  const mod = critic.module;
  if (!mod && critic.subtrees.length === 0) {
    root.innerHTML = '<div class="empty-msg">No design analysis yet. Run <code>analyze</code>.</div>';
    return;
  }

  let html = '';
  if (mod && mod.module_workflow && mod.module_workflow.length) {
    html += `<div style="margin-bottom:8px;"><strong>Module workflow</strong></div>`;
    for (const s of mod.module_workflow) {
      html += `<div class="stage-card">`;
      html += `<div class="stage-name">${escapeHtml(s.stage || '?')}</div>`;
      if (s.description) html += `<div>${escapeHtml(s.description)}</div>`;
      if (s.source_subtrees && s.source_subtrees.length)
        html += `<div class="stage-meta">from: ${(s.source_subtrees||[]).join(', ')}</div>`;
      html += `</div>`;
    }
  }
  for (const sub of critic.subtrees) {
    if (!sub.analysis) continue;
    const a = sub.analysis;
    const det = `<details><summary>${escapeHtml(sub.root)} — ${escapeHtml(a.essence || '')}</summary>`;
    let inner = '';
    for (const stage of (a.pipeline || [])) {
      inner += `<div class="stage-card">`;
      inner += `<div class="stage-name">${escapeHtml(stage.name || '?')}`;
      if (stage.altitude) inner += ` <span class="stage-meta">(${escapeHtml(stage.altitude)})</span>`;
      inner += `</div>`;
      if (stage.responsibility) inner += `<div>${escapeHtml(stage.responsibility)}</div>`;
      // Components in this stage
      const comps = (a.components || []).filter(c => c.stage === stage.name);
      for (const c of comps) {
        inner += `<div class="component-line">• ${escapeHtml(c.name || '?')}`;
        if (c.role) inner += ` <span class="stage-meta">— ${escapeHtml(c.role)}</span>`;
        if (c.multiple_impls) inner += ` <span class="stage-meta">[needs interface]</span>`;
        inner += `</div>`;
      }
      inner += `</div>`;
    }
    html += det + inner + '</details>';
  }
  root.innerHTML = html || '<div class="empty-msg">No workflow data.</div>';
}

function renderCriticPains(critic) {
  const root = document.getElementById('critic-pains');
  let html = '';
  let total = 0;
  for (const sub of critic.subtrees) {
    if (!sub.analysis || !sub.analysis.pains || sub.analysis.pains.length === 0) continue;
    html += `<details open><summary>${escapeHtml(sub.root)} (${sub.analysis.pains.length})</summary>`;
    for (const p of sub.analysis.pains) {
      total++;
      html += `<div class="pain-card">`;
      if (p.category) html += `<span class="pain-cat">${escapeHtml(p.category)}</span>`;
      html += `${escapeHtml(p.what || '')}`;
      if (p.where) html += `<div class="pain-where">${escapeHtml(p.where)}</div>`;
      html += `</div>`;
    }
    html += `</details>`;
  }
  root.innerHTML = total ? html : '<div class="empty-msg">No pain points reported.</div>';
}

function renderCriticRecs(critic) {
  const root = document.getElementById('critic-recs');
  const mod = critic.module;
  if (!mod || !mod.recommendations || mod.recommendations.length === 0) {
    // Also try to surface cross_observations as recs if no recs present.
    if (mod && mod.cross_observations && mod.cross_observations.length) {
      let html = '<div style="margin-bottom:8px;"><strong>Cross-subtree observations</strong></div>';
      for (const o of mod.cross_observations) {
        html += `<div class="rec-card"><div class="rec-target">${escapeHtml(o.pattern || '')}</div>`;
        if (o.suggestion) html += `<div class="rec-impact">${escapeHtml(o.suggestion)}</div>`;
        html += `</div>`;
      }
      root.innerHTML = html;
      return;
    }
    root.innerHTML = '<div class="empty-msg">No recommendations.</div>';
    return;
  }
  // Sort: high → medium → low
  const order = {high:0, medium:1, low:2};
  const recs = [...mod.recommendations].sort((a,b) =>
    (order[a.priority]||3) - (order[b.priority]||3));
  let html = '';
  for (const r of recs) {
    const pri = r.priority || 'medium';
    html += `<div class="rec-card ${pri}">`;
    html += `<span class="rec-priority">${pri}</span>`;
    html += `<span class="rec-target">${escapeHtml(r.target || '?')}</span>`;
    if (r.action) html += `<div>${escapeHtml(r.action)}</div>`;
    if (r.expected_impact) html += `<div class="rec-impact">${escapeHtml(r.expected_impact)}</div>`;
    if (r.evidence) html += `<div class="rec-impact" style="font-family:ui-monospace,monospace;">${escapeHtml(r.evidence)}</div>`;
    html += `</div>`;
  }
  // Append cross_observations + missing_abstractions at the end
  if (mod.cross_observations && mod.cross_observations.length) {
    html += `<div style="margin:12px 0 4px;"><strong>Cross-subtree observations</strong></div>`;
    for (const o of mod.cross_observations) {
      html += `<div class="rec-card"><div class="rec-target">${escapeHtml(o.pattern || '')}</div>`;
      if (o.suggestion) html += `<div class="rec-impact">${escapeHtml(o.suggestion)}</div>`;
      html += `</div>`;
    }
  }
  if (mod.missing_abstractions && mod.missing_abstractions.length) {
    html += `<div style="margin:12px 0 4px;"><strong>Missing abstractions</strong></div>`;
    for (const m of mod.missing_abstractions) {
      html += `<div class="rec-card"><div class="rec-target">${escapeHtml(m.role || '?')} → ${escapeHtml(m.suggested_interface || '?')}</div>`;
      if (m.current_implementations) html += `<div class="rec-impact">current: ${(m.current_implementations||[]).join(', ')}</div>`;
      html += `</div>`;
    }
  }
  root.innerHTML = html;
}

// ── Depth control: hide nodes deeper than k in the dom-forest ─
function nodeDepth(qname) {
  for (const tree of DATA.forest) {
    const d = findDepth(tree, qname, 0);
    if (d !== -1) return d;
  }
  return Infinity;
}
function findDepth(node, qname, depth) {
  if (node.label === qname) return depth;
  for (const c of (node.children || [])) {
    const d = findDepth(c, qname, depth + 1);
    if (d !== -1) return d;
  }
  return -1;
}
document.querySelectorAll('.depth-btn').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('.depth-btn').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    const k = parseInt(b.dataset.depth);
    cy.nodes().forEach(n => {
      const d = nodeDepth(n.id());
      if (k === 0 || d <= k) n.style('display', 'element');
      else n.style('display', 'none');
    });
  };
});

// ── Layout switcher ──────────────────────────────────────────
document.querySelectorAll('.layout-btn').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('.layout-btn').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    const name = b.dataset.layout;
    const opts = name === 'dagre'
      ? { name: 'dagre', rankDir: 'TB', nodeSep: 30, rankSep: 50, animate: true }
      : { name: 'cose', animate: true, idealEdgeLength: 80, nodeRepulsion: 4000 };
    cy.layout(opts).run();
  };
});

// ── Search box ───────────────────────────────────────────────
document.getElementById('search').oninput = (e) => {
  const q = e.target.value.toLowerCase().trim();
  if (!q) { cy.nodes().style('display', 'element'); return; }
  cy.nodes().forEach(n => {
    const match = n.id().toLowerCase().includes(q);
    n.style('display', match ? 'element' : 'none');
  });
};

// ── Boot ─────────────────────────────────────────────────────
renderForest();
renderUtilities();
renderCritic();
</script>
</body>
</html>
"""
