"""Report sections: Overview."""


def _section_overview(stats):
    if not stats or not stats['total']:
        return '<section><h2>Overview</h2><p>No data yet.</p></section>'

    total = stats['total'] or 0
    analyzed = stats['analyzed'] or 0
    pending = stats['pending'] or 0

    return f"""\
<section>
  <h2>Overview</h2>
  <div class="stat-grid">
    <div class="stat-box"><div class="num">{total}</div><div class="label">Total Classes</div></div>
    <div class="stat-box"><div class="num">{analyzed}</div><div class="label">Analyzed</div></div>
    <div class="stat-box"><div class="num">{pending}</div><div class="label">Pending</div></div>
  </div>
</section>"""
