"""template.py — three-section report shell.

1. 架构分析  — dominator forest as an expand/collapse box-and-line tree.
2. 详细关系  — relationship graph, same expand/collapse + level-colored edges.
3. 设计审视  — high-level problems then class/function problems, nested
              collapsibles (VSCode / markdown heading style).

Everything starts collapsed; the user expands level by level.
"""
import json


def render(payload):
    return _HTML.replace('__PAYLOAD__',
                         json.dumps(payload, ensure_ascii=False, default=str))


_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>CodeProbe</title>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.30.0/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
<style>
  :root{
    --bg:#f6f7f9; --panel:#fff; --line:#e2e2e2; --text:#1f2329; --muted:#8a8f99;
    --link:#2563eb; --orch:#dc2626; --util:#94a3b8;
    --high:#dc2626; --med:#ca8a04; --low:#64748b; --info:#0891b2;
    --lv5:#dc2626; --lv4:#ea580c; --lv3:#f59e0b; --lv2:#6366f1; --lv1:#0891b2; --lv0:#94a3b8;
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
  .graph{height:480px;}

  /* ── Section 3 collapsibles ───────────────────────────── */
  .review{padding:8px 12px 14px;}
  .review h3{margin:14px 6px 6px;font-size:12px;font-weight:700;color:var(--muted);
             text-transform:uppercase;letter-spacing:.06em;}
  details.item{border:1px solid var(--line);border-radius:6px;margin:6px 0;
               background:#fcfcfd;}
  details.item>summary{cursor:pointer;padding:9px 12px;font-size:13px;font-weight:600;
                       list-style:none;display:flex;align-items:center;gap:8px;}
  details.item>summary::-webkit-details-marker{display:none;}
  details.item>summary::before{content:'▸';color:var(--muted);width:12px;display:inline-block;}
  details.item[open]>summary::before{content:'▾';}
  details.item>summary:hover{background:#f1f5f9;}
  .pill{font-size:10px;font-weight:700;color:#fff;border-radius:10px;padding:1px 8px;}
  .pill.high{background:var(--high);} .pill.med{background:var(--med);}
  .pill.low{background:var(--low);} .pill.info{background:var(--info);}
  .pill.cat{background:#64748b;}
  .item .content{padding:4px 14px 12px 30px;font-size:13px;line-height:1.5;}
  .kv{margin:6px 0;}
  .kv .label{color:var(--muted);font-size:11px;text-transform:uppercase;
             letter-spacing:.05em;margin-right:6px;}
  .kv .mono{font-family:ui-monospace,monospace;font-size:12px;color:#475569;}
  details.sub{margin:5px 0 5px 0;border-left:2px solid var(--line);}
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
  <h2>1. 架构分析 <span class="hint">点击带 + 的节点展开下一层,再点收起</span></h2>
  <div class="graph" id="cy-arch"></div>
</section>

<section>
  <h2>2. 详细关系 <span class="hint">点击节点展开它依赖的类;边的颜色越暖耦合越强</span></h2>
  <div class="graph" id="cy-rel"></div>
</section>

<section>
  <h2>3. 设计审视 <span class="hint">默认收起,点击标题展开详情</span></h2>
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

const LV = {0:'var(--lv0)',1:'var(--lv1)',2:'var(--lv2)',3:'var(--lv3)',4:'var(--lv4)',5:'var(--lv5)'};

function nodeStyle(){
  return [
    {selector:'node',style:{
      'label':'data(disp)','shape':'round-rectangle',
      'background-color':'#3b82f6','color':'#fff',
      'text-valign':'center','text-halign':'center',
      'font-size':'11px','padding':'8px',
      'width':'label','height':'label',
      'text-wrap':'wrap','text-max-width':'160px'}},
    {selector:'node[is_orch = 1]',style:{'background-color':'#dc2626'}},
    {selector:'node[is_util = 1]',style:{'background-color':'#94a3b8'}},
    {selector:'node[kind = "interface"]',style:{'shape':'diamond'}},
    {selector:'node[kind = "struct"]',style:{'background-color':'#0d9488'}},
    {selector:'edge',style:{
      'curve-style':'bezier','target-arrow-shape':'triangle',
      'width':2,'line-color':'#bbb','target-arrow-color':'#bbb','opacity':0.8}},
    {selector:'node.hl',style:{'border-width':3,'border-color':'#1d4ed8'}},
  ];
}

/* ============ Section 1: dominator forest ============ */
(function(){
  const A = DATA.arch;
  const childrenOf = {};
  A.nodes.forEach(n => childrenOf[n.id] = []);
  A.edges.forEach(e => { (childrenOf[e.source]=childrenOf[e.source]||[]).push(e.target); });
  const rootIds = A.nodes.filter(n => n.is_root).map(n => n.id);
  const hasChild = id => (childrenOf[id]||[]).length > 0;
  const collapsed = new Set(A.nodes.filter(n => hasChild(n.id)).map(n => n.id)); // all collapsed

  const cy = cytoscape({
    container: document.getElementById('cy-arch'),
    elements: {
      nodes: A.nodes.map(n => ({data:{...n, disp:n.label}})),
      edges: A.edges.map(e => ({data:{source:e.source,target:e.target}})),
    },
    style: nodeStyle(),
    wheelSensitivity: 0.2,
  });

  function visibleSet(){
    const vis = new Set(rootIds);
    let changed = true;
    while(changed){
      changed = false;
      for(const id of [...vis]){
        if(!collapsed.has(id)){
          for(const c of (childrenOf[id]||[])){
            if(!vis.has(c)){ vis.add(c); changed = true; }
          }
        }
      }
    }
    return vis;
  }
  function apply(){
    const vis = visibleSet();
    cy.batch(()=>{
      cy.nodes().forEach(n=>{
        const id = n.id();
        n.style('display', vis.has(id) ? 'element':'none');
        const prefix = hasChild(id) ? (collapsed.has(id) ? '+ ' : '− ') : '';
        n.data('disp', prefix + n.data('label'));
      });
      cy.edges().forEach(e=>{
        e.style('display', (vis.has(e.source().id())&&vis.has(e.target().id()))?'element':'none');
      });
    });
    cy.layout({name:'dagre',rankDir:'TB',nodeSep:24,rankSep:48,
               fit:true,padding:20,animate:false}).run();
  }
  cy.on('tap','node',evt=>{
    const id = evt.target.id();
    if(!hasChild(id)) return;
    if(collapsed.has(id)) collapsed.delete(id); else collapseSubtree(id);
    apply();
  });
  function collapseSubtree(id){
    collapsed.add(id);
    for(const c of (childrenOf[id]||[])) collapseSubtree(c);
  }
  if(A.nodes.length===0){
    document.getElementById('cy-arch').innerHTML =
      '<div class="empty">没有检测到架构层级。</div>';
  } else { apply(); }
})();

/* ============ Section 2: relationship graph ============ */
(function(){
  const R = DATA.rel;
  if(R.nodes.length===0){
    document.getElementById('cy-rel').innerHTML='<div class="empty">没有关系数据。</div>';
    return;
  }
  const out = {};
  R.nodes.forEach(n=>out[n.id]=[]);
  R.edges.forEach(e=>{ (out[e.source]=out[e.source]||[]).push(e.target); });
  const hasOut = id => (out[id]||[]).length>0;
  const expanded = new Set();              // start: nothing expanded
  const roots = R.roots.length ? R.roots : R.nodes.slice(0,1).map(n=>n.id);

  const cy = cytoscape({
    container: document.getElementById('cy-rel'),
    elements:{
      nodes: R.nodes.map(n=>({data:{...n, disp:n.label}})),
      edges: R.edges.map(e=>({data:{id:e.id,source:e.source,target:e.target,
                                    level:e.level,kinds:(e.kinds||[]).join(',')}})),
    },
    style: nodeStyle().concat([
      {selector:'edge',style:{
        'curve-style':'bezier','target-arrow-shape':'triangle',
        'width':'mapData(level,0,5,1.5,5)',
        'line-color':ele=>LV[ele.data('level')]||'#bbb',
        'target-arrow-color':ele=>LV[ele.data('level')]||'#bbb','opacity':0.85}},
    ]),
    wheelSensitivity:0.2,
  });

  function visibleSet(){
    const vis = new Set(roots);
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
    const vis = visibleSet();
    cy.batch(()=>{
      cy.nodes().forEach(n=>{
        const id=n.id();
        n.style('display',vis.has(id)?'element':'none');
        const expandable = hasOut(id) && (out[id]||[]).some(t=>!vis.has(t) || !expanded.has(id));
        const prefix = hasOut(id) ? (expanded.has(id)?'− ':'+ ') : '';
        n.data('disp', prefix + n.data('label'));
      });
      cy.edges().forEach(e=>{
        e.style('display',(vis.has(e.source().id())&&vis.has(e.target().id()))?'element':'none');
      });
    });
    cy.layout({name:'dagre',rankDir:'LR',nodeSep:20,rankSep:60,
               fit:true,padding:20,animate:false}).run();
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
})();

/* ============ Section 3: design review ============ */
(function(){
  const root = document.getElementById('review');
  const R = DATA.review;
  let html = '';

  html += '<h3>高层设计问题</h3>';
  if((R.high_level||[]).length){
    for(const p of R.high_level){
      const pri = p.priority==='high'?'high':p.priority==='medium'?'med':
                  p.priority==='low'?'low':'info';
      html += `<details class="item"><summary>`;
      html += `<span class="pill ${pri}">${esc(p.priority||'info')}</span>`;
      html += `<span>${esc(p.title)}</span></summary><div class="content">`;
      html += renderKV(p.details);
      html += `</div></details>`;
    }
  } else {
    html += '<div class="empty">暂无高层设计问题(运行 analyze 生成)。</div>';
  }

  html += '<h3>类 / 函数级问题</h3>';
  if((R.class_level||[]).length){
    for(const c of R.class_level){
      html += `<details class="item"><summary>`;
      html += `<span>${esc(c.short)}</span>`;
      html += `<span class="essence">${esc(c.essence)}</span></summary><div class="content">`;
      for(const pain of c.pains){
        html += `<details class="sub"><summary>`;
        if(pain.category) html += `<span class="pill cat">${esc(pain.category)}</span>`;
        html += `<span>${esc(pain.title)}</span></summary><div class="content">`;
        html += renderKV(pain.details);
        html += `</div></details>`;
      }
      html += `</div></details>`;
    }
  } else {
    html += '<div class="empty">暂无类/函数级问题。</div>';
  }

  root.innerHTML = html;

  function renderKV(details){
    let s='';
    for(const d of (details||[])){
      const mono = d.label==='where'||d.label==='evidence';
      s += `<div class="kv"><span class="label">${esc(d.label)}</span>`;
      s += `<span class="${mono?'mono':''}">${esc(d.text)}</span></div>`;
    }
    return s;
  }
})();

function esc(s){return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
</script>
</body>
</html>
"""
