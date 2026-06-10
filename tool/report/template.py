"""template.py — three-section report.

1. 架构分析  — UML class diagram, STRUCTURAL relations only
              (inherits / implements / composes / aggregates).
2. 详细关系  — UML class diagram, ALL relations.
   Both: proper UML notation (triangles / diamonds / arrows), edge
   labels, dashed external nodes, and expand/collapse (default
   collapsed, reveal neighbors on click).
3. 设计审视  — nested collapsibles: high-level then class/function.
"""
import json


def render(payload):
    return _HTML.replace('__PAYLOAD__',
                         json.dumps(payload, ensure_ascii=False, default=str))


_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CodeProbe</title>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.30.0/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
<style>
  :root{
    --bg:#f6f7f9;--panel:#fff;--line:#e2e2e2;--text:#1f2329;--muted:#8a8f99;
    --orch:#dc2626;--util:#94a3b8;
    --high:#dc2626;--med:#ca8a04;--low:#64748b;--info:#0891b2;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:ui-sans-serif,system-ui,"Segoe UI",sans-serif;
       max-width:1100px;margin:0 auto;padding:20px;}
  header h1{margin:0;font-size:18px;}
  header .meta{color:var(--muted);font-size:12px;margin-top:4px;}
  section{background:var(--panel);border:1px solid var(--line);border-radius:8px;
          margin:18px 0;overflow:hidden;}
  section>h2{margin:0;padding:12px 16px;font-size:14px;font-weight:600;
             background:#f1f3f5;border-bottom:1px solid var(--line);}
  .hint{color:var(--muted);font-size:11px;font-weight:400;margin-left:8px;}
  .graph{height:500px;}
  .legend{display:flex;flex-wrap:wrap;gap:14px;padding:8px 16px;font-size:11px;
          color:var(--muted);border-bottom:1px solid var(--line);background:#fbfbfc;}
  .legend b{color:var(--text);font-weight:600;}

  /* Section 3 */
  .review{padding:8px 12px 14px;}
  .review h3{margin:14px 6px 6px;font-size:12px;font-weight:700;color:var(--muted);
             text-transform:uppercase;letter-spacing:.06em;}
  details.item{border:1px solid var(--line);border-radius:6px;margin:6px 0;background:#fcfcfd;}
  details.item>summary{cursor:pointer;padding:9px 12px;font-size:13px;font-weight:600;
                       list-style:none;display:flex;align-items:center;gap:8px;}
  details.item>summary::-webkit-details-marker{display:none;}
  details.item>summary::before{content:'▸';color:var(--muted);width:12px;display:inline-block;}
  details.item[open]>summary::before{content:'▾';}
  details.item>summary:hover{background:#f1f5f9;}
  .pill{font-size:10px;font-weight:700;color:#fff;border-radius:10px;padding:1px 8px;}
  .pill.high{background:var(--high);}.pill.med{background:var(--med);}
  .pill.low{background:var(--low);}.pill.info{background:var(--info);}.pill.cat{background:#64748b;}
  .item .content{padding:4px 14px 12px 30px;font-size:13px;line-height:1.5;}
  .kv{margin:6px 0;}
  .kv .label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-right:6px;}
  .kv .mono{font-family:ui-monospace,monospace;font-size:12px;color:#475569;}
  details.sub{margin:5px 0;border-left:2px solid var(--line);}
  details.sub>summary{cursor:pointer;padding:5px 10px;font-size:12px;font-weight:600;
                      list-style:none;display:flex;gap:8px;align-items:center;}
  details.sub>summary::-webkit-details-marker{display:none;}
  details.sub>summary::before{content:'▸';color:var(--muted);width:10px;display:inline-block;}
  details.sub[open]>summary::before{content:'▾';}
  details.sub>summary:hover{background:#f1f5f9;}
  details.sub .content{padding:2px 12px 8px 26px;}
  .empty{color:var(--muted);font-style:italic;padding:14px;font-size:13px;}
  .essence{color:var(--muted);font-weight:400;font-size:11px;}
</style>
</head>
<body>

<header>
  <h1>CodeProbe</h1>
  <div class="meta" id="meta"></div>
</header>

<section>
  <h2>1. Architecture <span class="hint">Workflow hierarchy (dominator tree) · click [+] nodes to expand, click again to collapse</span></h2>
  <div class="legend">
    <span><b>◆</b> composes</span><span><b>◇</b> aggregates</span><span><b>→</b> associates</span>
    <span><b>△</b> inherits</span><span><b>┄△</b> implements</span>
    <span style="color:#cbd5e1"><b>·····</b> dominates (no direct dependency)</span>
    <span style="color:var(--orch)"><b>■</b> orchestrator</span>
  </div>
  <div class="graph" id="cy-arch"></div>
</section>

<section>
  <h2>2. Relationships <span class="hint">All relationships · click a node to reveal what it depends on</span></h2>
  <div class="legend">
    <span><b>△</b> inherits</span><span><b>┄△</b> implements</span>
    <span><b>◆</b> composes</span><span><b>◇</b> aggregates</span>
    <span><b>→</b> associates</span><span><b>┄→</b> depends</span>
    <span style="color:var(--util)"><b>┄ ┄</b> external type</span>
  </div>
  <div class="graph" id="cy-rel"></div>
</section>

<section>
  <h2>3. Design Review <span class="hint">Collapsed by default · click a title to expand</span></h2>
  <div class="review" id="review"></div>
</section>

<script>
const DATA = __PAYLOAD__;
cytoscape.use(cytoscapeDagre);

document.getElementById('meta').textContent =
  [DATA.summary.directory,
   (DATA.summary.class_count||0)+' classes',
   (DATA.summary.file_count||0)+' files',
   DATA.summary.orchestrator ? 'orchestrator: '+DATA.summary.orchestrator : '']
  .filter(Boolean).join('  ·  ');

const STEREO = {interface:'«interface»', external:'«external»', struct:'«struct»'};

function umlStyle(){
  return [
    {selector:'node',style:{
      'label':'data(disp)','shape':'round-rectangle',
      'background-color':'#eaf1fb','border-width':1.5,'border-color':'#3b82f6',
      'color':'#1f2329','text-valign':'center','text-halign':'center',
      'font-size':'11px','text-wrap':'wrap','text-max-width':'150px',
      'padding':'8px','width':'label','height':'label'}},
    {selector:'node[is_orch = 1]',style:{'background-color':'#fde8e8','border-color':'#dc2626','border-width':2.5}},
    {selector:'node[is_util = 1]',style:{'background-color':'#f1f5f9','border-color':'#94a3b8'}},
    {selector:'node[kind = "interface"]',style:{'background-color':'#eef2ff','border-color':'#6366f1','border-style':'solid'}},
    {selector:'node[kind = "struct"]',style:{'background-color':'#ecfdf5','border-color':'#0d9488'}},
    {selector:'node[is_external = 1]',style:{
      'background-color':'#fff','color':'#64748b','border-color':'#cbd5e1','border-style':'dashed'}},

    /* UML edge notations */
    {selector:'edge',style:{
      'curve-style':'bezier','width':1.5,'line-color':'#94a3b8',
      'label':'data(elabel)','font-size':'9px','color':'#64748b',
      'text-background-color':'#fff','text-background-opacity':1,'text-background-padding':'2px',
      'arrow-scale':1.1}},
    {selector:'edge[primary="inherits"]',style:{
      'line-style':'solid','target-arrow-shape':'triangle','target-arrow-fill':'hollow',
      'target-arrow-color':'#475569','line-color':'#475569'}},
    {selector:'edge[primary="implements"]',style:{
      'line-style':'dashed','target-arrow-shape':'triangle','target-arrow-fill':'hollow',
      'target-arrow-color':'#6366f1','line-color':'#6366f1'}},
    {selector:'edge[primary="composes"]',style:{
      'line-style':'solid','source-arrow-shape':'diamond','source-arrow-fill':'filled',
      'source-arrow-color':'#ea580c','line-color':'#ea580c'}},
    {selector:'edge[primary="aggregates"]',style:{
      'line-style':'solid','source-arrow-shape':'diamond','source-arrow-fill':'hollow',
      'source-arrow-color':'#f59e0b','line-color':'#f59e0b'}},
    {selector:'edge[primary="associates"]',style:{
      'line-style':'solid','target-arrow-shape':'vee','target-arrow-color':'#0891b2','line-color':'#0891b2'}},
    {selector:'edge[primary="depends"]',style:{
      'line-style':'dashed','target-arrow-shape':'vee','target-arrow-color':'#94a3b8','line-color':'#94a3b8'}},
    {selector:'node.hl',style:{'border-width':3,'border-color':'#1d4ed8'}},
  ];
}

function disp(n, prefix){
  const st = STEREO[n.kind];
  return (st ? st+'\n' : '') + prefix + n.label;
}

/* Reusable UML graph with expand/collapse.
   edgePred: which edges to include. dir: dagre rankDir. */
function makeGraph(containerId, edgePred, dir){
  const G = DATA.graph;
  const edges = G.edges.filter(edgePred);
  const usedNodes = new Set();
  edges.forEach(e=>{usedNodes.add(e.source);usedNodes.add(e.target);});
  // keep root nodes even if they have no edges in this view
  G.roots.forEach(r=>usedNodes.add(r));
  const nodes = G.nodes.filter(n=>usedNodes.has(n.id));

  if(nodes.length===0){
    document.getElementById(containerId).innerHTML='<div class="empty">No relationships to display in this view.</div>';
    return;
  }

  const out = {};
  nodes.forEach(n=>out[n.id]=[]);
  edges.forEach(e=>{ if(out[e.source]) out[e.source].push(e.target); });
  const hasOut = id => (out[id]||[]).length>0;
  const expanded = new Set();
  let roots = G.roots.filter(r=>usedNodes.has(r));
  if(roots.length===0) roots = nodes.slice(0,12).map(n=>n.id);

  const cy = cytoscape({
    container: document.getElementById(containerId),
    elements:{
      nodes: nodes.map(n=>({data:{...n, disp:disp(n,'')}})),
      edges: edges.map(e=>({data:{id:e.id,source:e.source,target:e.target,
                                  primary:e.primary, elabel:(e.kinds||[]).join(', ')}})),
    },
    style: umlStyle(),
    wheelSensitivity:0.2,
  });

  function visibleSet(){
    const vis=new Set(roots);
    let changed=true;
    while(changed){
      changed=false;
      for(const id of [...vis]){
        if(expanded.has(id)){
          for(const t of (out[id]||[])){ if(!vis.has(t)){vis.add(t);changed=true;} }
        }
      }
    }
    return vis;
  }
  function apply(){
    const vis=visibleSet();
    cy.batch(()=>{
      cy.nodes().forEach(n=>{
        const id=n.id();
        n.style('display',vis.has(id)?'element':'none');
        const pfx = hasOut(id) ? (expanded.has(id)?'[−] ':'[+] ') : '';
        n.data('disp', disp(n.data(), pfx));
      });
      cy.edges().forEach(e=>{
        e.style('display',(vis.has(e.source().id())&&vis.has(e.target().id()))?'element':'none');
      });
    });
    cy.layout({name:'dagre',rankDir:dir,nodeSep:30,rankSep:60,
               fit:true,padding:24,animate:false}).run();
  }
  cy.on('tap','node',evt=>{
    const id=evt.target.id();
    cy.nodes().removeClass('hl'); evt.target.addClass('hl');
    if(hasOut(id)){
      if(expanded.has(id)) expanded.delete(id); else expanded.add(id);
      apply();
    }
  });
  apply();
}

/* ── Section 1: dominator-forest workflow tree ──────────────
   Edges are parent→child in the responsibility hierarchy. When a
   real dependency backs the edge we draw its UML notation; pure
   dominance (no direct edge) is a dotted grey line. */
(function(){
  const A = DATA.arch;
  if(!A || A.nodes.length===0){
    document.getElementById('cy-arch').innerHTML =
      '<div class="empty">No workflow hierarchy detected (no internal dependencies between classes).</div>';
    return;
  }
  const childrenOf = {};
  A.nodes.forEach(n=>childrenOf[n.id]=[]);
  A.edges.forEach(e=>{ (childrenOf[e.source]=childrenOf[e.source]||[]).push(e.target); });
  const rootIds = A.nodes.filter(n=>n.is_root).map(n=>n.id);
  const hasChild = id => (childrenOf[id]||[]).length>0;
  const collapsed = new Set(A.nodes.filter(n=>hasChild(n.id)).map(n=>n.id));

  function archDisp(n, pfx){
    let txt = pfx + n.label;
    if(n.impls && n.impls.length) txt += '\n(+' + n.impls.length + ' impls)';
    return txt;
  }

  const cy = cytoscape({
    container: document.getElementById('cy-arch'),
    elements:{
      nodes: A.nodes.map(n=>({data:{...n, disp:archDisp(n,'')}})),
      edges: A.edges.map((e,i)=>({data:{id:'a'+i,source:e.source,target:e.target,
                                        primary:e.kind, elabel:e.kind==='dominates'?'':e.kind}})),
    },
    style: umlStyle().concat([
      {selector:'edge[primary="dominates"]',style:{
        'line-style':'dotted','line-color':'#cbd5e1','width':1.2,
        'target-arrow-shape':'none','source-arrow-shape':'none'}},
    ]),
    wheelSensitivity:0.2,
  });

  function visibleSet(){
    const vis=new Set(rootIds);
    let changed=true;
    while(changed){
      changed=false;
      for(const id of [...vis]){
        if(!collapsed.has(id)){
          for(const c of (childrenOf[id]||[])){ if(!vis.has(c)){vis.add(c);changed=true;} }
        }
      }
    }
    return vis;
  }
  function apply(){
    const vis=visibleSet();
    cy.batch(()=>{
      cy.nodes().forEach(n=>{
        const id=n.id();
        n.style('display',vis.has(id)?'element':'none');
        const pfx = hasChild(id) ? (collapsed.has(id)?'[+] ':'[−] ') : '';
        n.data('disp', archDisp(n.data(), pfx));
      });
      cy.edges().forEach(e=>{
        e.style('display',(vis.has(e.source().id())&&vis.has(e.target().id()))?'element':'none');
      });
    });
    cy.layout({name:'dagre',rankDir:'TB',nodeSep:26,rankSep:56,
               fit:true,padding:24,animate:false}).run();
  }
  function collapseSubtree(id){
    collapsed.add(id);
    for(const c of (childrenOf[id]||[])) collapseSubtree(c);
  }
  cy.on('tap','node',evt=>{
    const id=evt.target.id();
    if(!hasChild(id)) return;
    if(collapsed.has(id)) collapsed.delete(id); else collapseSubtree(id);
    apply();
  });
  apply();
})();

/* Section 2: all relations, UML */
makeGraph('cy-rel', e=>true, 'LR');

/* Section 3: design review */
(function(){
  const root=document.getElementById('review');
  const R=DATA.review||{high_level:[],class_level:[]};
  let html='';

  html+='<h3>High-level design issues</h3>';
  if((R.high_level||[]).length){
    for(const p of R.high_level){
      const pri=p.priority==='high'?'high':p.priority==='medium'?'med':p.priority==='low'?'low':'info';
      html+=`<details class="item"><summary><span class="pill ${pri}">${esc(p.priority||'info')}</span>`;
      html+=`<span>${esc(p.title)}</span></summary><div class="content">${renderKV(p.details)}</div></details>`;
    }
  } else html+='<div class="empty">No high-level issues yet. Run <code>analyze</code> to generate.</div>';

  html+='<h3>Class- / function-level issues</h3>';
  if((R.class_level||[]).length){
    for(const c of R.class_level){
      html+=`<details class="item"><summary><span>${esc(c.short)}</span>`;
      html+=`<span class="essence">${esc(c.essence)}</span></summary><div class="content">`;
      for(const pain of c.pains){
        html+=`<details class="sub"><summary>`;
        if(pain.category) html+=`<span class="pill cat">${esc(pain.category)}</span>`;
        html+=`<span>${esc(pain.title)}</span></summary><div class="content">${renderKV(pain.details)}</div></details>`;
      }
      html+=`</div></details>`;
    }
  } else html+='<div class="empty">No class- or function-level issues found.</div>';

  root.innerHTML=html;
  function renderKV(details){
    let s='';
    for(const d of (details||[])){
      const mono=d.label==='where'||d.label==='evidence';
      s+=`<div class="kv"><span class="label">${esc(d.label)}</span><span class="${mono?'mono':''}">${esc(d.text)}</span></div>`;
    }
    return s;
  }
})();

function esc(s){return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
</script>
</body>
</html>
"""
