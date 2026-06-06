"""
db.py — SQLite database management
═══════════════════════════════════════
AI concept: Memory / State Persistence
Key insight:
  - SQLite instead of markdown — atomic INSERT/UPDATE won't corrupt other data
  - DB is the pipeline's "working memory": which classes analyzed, results, what's next
  - Each Agent reads input from DB, writes output back to DB
  - Every method manages its own connection — thread-safe by design
═══════════════════════════════════════
"""
import sqlite3
from pathlib import Path


class DBManager:
    """SQLite state manager — the pipeline's coordinator."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)

    # ─── Connection Helper ───────────────────────────────────

    def _connect(self):
        """Create a new connection (each call = own connection, thread-safe)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _execute(self, sql, params=None):
        """Execute a single write statement, auto-commit and close."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            conn.commit()
            return cur
        finally:
            conn.close()

    def _query_one(self, sql, params=None):
        """Execute a query and return one row (or None)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return cur.fetchone()
        finally:
            conn.close()

    def _query_all(self, sql, params=None):
        """Execute a query and return all rows."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return cur.fetchall()
        finally:
            conn.close()

    # ─── Schema Management ───────────────────────────────────

    def ensure_tables(self):
        """Create all tables (idempotent, safe to call repeatedly)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS module_info (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_name TEXT NOT NULL,
                    directory TEXT,
                    orchestrator TEXT,
                    file_count INTEGER,
                    class_count INTEGER,
                    scan_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS responsibility_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_name TEXT NOT NULL,
                    actual_responsibilities TEXT,
                    ideal_responsibility TEXT,
                    srp_violations TEXT,
                    extract_candidates TEXT,
                    responsibility_tags TEXT,
                    sin_diagnosis TEXT,
                    full_analysis TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS design_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phase_plan TEXT,
                    new_classes TEXT,
                    interfaces TEXT,
                    effort_total TEXT,
                    mermaid_diagram TEXT,
                    full_analysis TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                /* ── entity-relationship model ──────────────────────────
                   The whole graph lives here. The single source of
                   truth: every consumer (ScannerAgent, ResponsibilityAgent,
                   the report layer, the pipeline) queries entities +
                   relationships directly via get_entities /
                   get_relationships / get_classes / get_entity. */

                CREATE TABLE IF NOT EXISTS entities (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind            TEXT NOT NULL,           -- class|struct|interface|enum|method|field|namespace
                    name            TEXT NOT NULL,
                    qualified_name  TEXT NOT NULL,
                    parent_qname    TEXT,                    -- containing entity's qualified_name
                    file_path       TEXT,
                    start_line      INTEGER,
                    end_line        INTEGER,
                    signature       TEXT,                    -- method sig / field type / NULL
                    attrs           TEXT DEFAULT '{}',       -- JSON
                    UNIQUE(qualified_name, kind)
                );

                CREATE INDEX IF NOT EXISTS idx_entities_qname  ON entities(qualified_name);
                CREATE INDEX IF NOT EXISTS idx_entities_parent ON entities(parent_qname);
                CREATE INDEX IF NOT EXISTS idx_entities_kind   ON entities(kind);

                CREATE TABLE IF NOT EXISTS relationships (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_qname    TEXT NOT NULL,           -- always resolved (source is ours)
                    target_qname    TEXT,                    -- NULL when target is external
                    target_name     TEXT NOT NULL,           -- always populated
                    kind            TEXT NOT NULL,           -- depends|associates|implements|aggregates|composes|inherits
                    level           INTEGER NOT NULL,        -- 0..5
                    evidence_file   TEXT,
                    evidence_line   INTEGER,
                    evidence_text   TEXT,
                    attrs           TEXT DEFAULT '{}'        -- JSON
                );

                CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_qname);
                CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_qname);
                CREATE INDEX IF NOT EXISTS idx_rel_kind   ON relationships(kind);

                /* ── parse_cache: file-content fingerprint → parsed
                   data (entities + relationships + aliases). Lets us
                   skip tree-sitter on files whose mtime+size are
                   unchanged between scans. Bump `version` when the
                   parser logic changes so stale caches don't survive
                   a code update. */
                CREATE TABLE IF NOT EXISTS parse_cache (
                    file_path           TEXT PRIMARY KEY,
                    mtime               REAL NOT NULL,
                    size                INTEGER NOT NULL,
                    parser_version      INTEGER NOT NULL,
                    entities_json       TEXT NOT NULL,
                    relationships_json  TEXT NOT NULL,
                    aliases_json        TEXT NOT NULL,
                    cached_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                /* ── llm_cache: prompt hash → response. Skips API
                   calls when an identical prompt+model has already
                   been answered. Saves $ and time across re-runs of
                   `analyze` (especially while iterating prompts).
                   Set LLM_NO_CACHE=1 to force a fresh call. */
                CREATE TABLE IF NOT EXISTS llm_cache (
                    prompt_hash         TEXT NOT NULL,
                    model               TEXT NOT NULL,
                    response            TEXT NOT NULL,
                    cached_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (prompt_hash, model)
                );

                /* ── design_critic_subtree: per-subtree analysis raw
                   + parsed JSON. One row per subtree per scan. */
                CREATE TABLE IF NOT EXISTS design_critic_subtree (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    subtree_root    TEXT NOT NULL,
                    prompt          TEXT,
                    raw_response    TEXT,
                    parsed_json     TEXT,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                /* ── design_critic_module: synthesis pass output. */
                CREATE TABLE IF NOT EXISTS design_critic_module (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_name     TEXT NOT NULL,
                    prompt          TEXT,
                    raw_response    TEXT,
                    parsed_json     TEXT,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
        finally:
            conn.close()

    # ─── Module info ─────────────────────────────────────────

    def save_module_info(self, module_name, directory=None, orchestrator=None,
                         file_count=None, class_count=None):
        """Record one scan's summary row."""
        self._execute("""
            INSERT INTO module_info
                (module_name, directory, orchestrator, file_count, class_count)
            VALUES (?, ?, ?, ?, ?)
        """, (module_name, str(directory) if directory else None,
              orchestrator, file_count, class_count))

    def get_module_info(self, module_name=None):
        """Latest module_info row (for a specific module or overall)."""
        if module_name:
            return self._query_one(
                "SELECT * FROM module_info WHERE module_name = ? "
                "ORDER BY id DESC LIMIT 1",
                (module_name,))
        return self._query_one(
            "SELECT * FROM module_info ORDER BY id DESC LIMIT 1")

    # ─── Aggregates / dashboard ──────────────────────────────

    def get_stats(self):
        """total / analyzed / pending counts for the dashboard."""
        return self._query_one("""
            SELECT
                (SELECT COUNT(*) FROM entities
                  WHERE kind IN ('class','struct','interface')) AS total,
                (SELECT COUNT(DISTINCT class_name)
                  FROM responsibility_analysis) AS analyzed,
                (SELECT COUNT(*) FROM entities
                  WHERE kind IN ('class','struct','interface'))
                - (SELECT COUNT(DISTINCT class_name)
                    FROM responsibility_analysis) AS pending
        """)

    # ─── Responsibility Analysis CRUD ────────────────────────

    def save_responsibility(self, class_name, parsed, full_analysis, arch_id=None):
        """Save responsibility analysis results."""
        return self._execute("""
            INSERT INTO responsibility_analysis
                (class_name, actual_responsibilities,
                 ideal_responsibility, srp_violations, extract_candidates,
                 responsibility_tags, sin_diagnosis, full_analysis)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (class_name,
              parsed.get('actual_responsibilities', ''),
              parsed.get('ideal_responsibility', ''),
              parsed.get('srp_violations', ''),
              parsed.get('extract_candidates', ''),
              parsed.get('responsibility_tags', ''),
              parsed.get('sin_diagnosis', ''),
              full_analysis)).lastrowid

    def get_responsibility(self, class_name, arch_id=None):
        """Get responsibility analysis for a class."""
        return self._query_one("""
            SELECT * FROM responsibility_analysis
            WHERE class_name = ?
            ORDER BY id DESC LIMIT 1
        """, (class_name,))

    def get_all_responsibilities(self, arch_id=None):
        """Get all responsibility analyses."""
        return self._query_all("""
            SELECT * FROM responsibility_analysis
            ORDER BY class_name
        """)

    # ─── Design Proposals CRUD ───────────────────────────────

    def save_design_proposal(self, parsed, full_analysis, arch_id=None):
        """Save design proposal."""
        return self._execute("""
            INSERT INTO design_proposals
                (phase_plan, new_classes, interfaces,
                 effort_total, full_analysis)
            VALUES (?, ?, ?, ?, ?)
        """, (parsed.get('phase_plan', ''),
              parsed.get('new_classes', ''),
              parsed.get('interfaces', ''),
              parsed.get('effort_total', ''),
              full_analysis)).lastrowid

    def get_latest_design(self, arch_id=None):
        """Get latest design proposal."""
        return self._query_one("""
            SELECT * FROM design_proposals
            ORDER BY id DESC LIMIT 1
        """)

    # ─── Delete (for --from restart) ──────────────────────────

    def delete_design_proposals(self, arch_id=None):
        """Delete design proposals."""
        return self._execute("DELETE FROM design_proposals").rowcount

    def delete_responsibilities(self, arch_id=None):
        """Delete responsibility analyses + cascade delete design proposals."""
        self.delete_design_proposals()
        return self._execute("DELETE FROM responsibility_analysis").rowcount

    def delete_all_tasks(self):
        """Delete all scan data (graph + module_info)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM relationships")
            cur.execute("DELETE FROM entities")
            cur.execute("DELETE FROM module_info")
            conn.commit()
        finally:
            conn.close()

    def delete_architecture(self):
        """Delete architecture-level data (responsibilities + design proposals).
        Kept for backward compatibility with pipeline --from=arch."""
        self.delete_responsibilities()

    # ─── Entity / Relationship API (Phase 1) ──────────────────────

    def save_entities(self, entities):
        """Bulk upsert entities. `entities` is an iterable of Entity dataclass.

        Conflict on (qualified_name, kind) → row is replaced (idempotent
        re-scans). Returns the number of rows written.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            rows = [(e.kind, e.name, e.qualified_name, e.parent_qname,
                     e.file_path, e.start_line, e.end_line,
                     e.signature, e.attrs_json()) for e in entities]
            cur.executemany("""
                INSERT INTO entities (kind, name, qualified_name, parent_qname,
                                       file_path, start_line, end_line,
                                       signature, attrs)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(qualified_name, kind) DO UPDATE SET
                    name = excluded.name,
                    parent_qname = excluded.parent_qname,
                    file_path = excluded.file_path,
                    start_line = excluded.start_line,
                    end_line = excluded.end_line,
                    signature = excluded.signature,
                    attrs = excluded.attrs
            """, rows)
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    def save_relationships(self, rels):
        """Bulk insert relationships. Multiple edges between the same pair
        with different `kind` are allowed (each piece of evidence is its
        own row). Returns the number written."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            rows = [(r.source_qname, r.target_qname, r.target_name, r.kind,
                     r.level, r.evidence_file, r.evidence_line,
                     r.evidence_text, r.attrs_json()) for r in rels]
            cur.executemany("""
                INSERT INTO relationships (source_qname, target_qname, target_name,
                                            kind, level, evidence_file,
                                            evidence_line, evidence_text, attrs)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, rows)
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    def get_entities(self, kind=None, parent_qname=None):
        """Query entities, optionally filtered by kind and/or parent."""
        sql = "SELECT * FROM entities WHERE 1=1"
        params = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if parent_qname:
            sql += " AND parent_qname = ?"
            params.append(parent_qname)
        sql += " ORDER BY qualified_name"
        return self._query_all(sql, params)

    def get_relationships(self, source_qname=None, target_qname=None, kind=None):
        """Query relationships, optionally filtered. Returns rows in
        their natural shape (source_qname / target_qname / target_name /
        kind / level / evidence_text / attrs).
        """
        sql = "SELECT * FROM relationships WHERE 1=1"
        params = []
        if source_qname:
            sql += " AND source_qname = ?"
            params.append(source_qname)
        if target_qname:
            sql += " AND target_qname = ?"
            params.append(target_qname)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY level DESC"
        return self._query_all(sql, params)

    def get_entity(self, qualified_name, kind=None):
        """One entity by qualified name (and optional kind disambiguation
        — same qname can in theory exist as both class and namespace).
        """
        if kind:
            return self._query_one(
                "SELECT * FROM entities WHERE qualified_name = ? AND kind = ?",
                (qualified_name, kind))
        return self._query_one(
            "SELECT * FROM entities WHERE qualified_name = ?",
            (qualified_name,))

    def get_classes(self):
        """All class/struct/interface entities. Convenience wrapper since
        most downstream code wants 'the things that are classes' as a
        single list."""
        return self._query_all(
            "SELECT * FROM entities "
            "WHERE kind IN ('class','struct','interface') "
            "ORDER BY qualified_name")

    def clear_graph(self):
        """Wipe entities + relationships (called on re-scan).
        Parse cache survives — it's keyed on file fingerprint so it
        stays valid across re-scans of the same source tree.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM relationships")
            cur.execute("DELETE FROM entities")
            conn.commit()
        finally:
            conn.close()

    # ─── Parse cache (file fingerprint → parsed result) ─────

    def cache_get(self, file_path, mtime, size, parser_version):
        """Return cached parse output for this file if fingerprint
        matches, else None. Caller deserializes the JSON columns.
        """
        row = self._query_one(
            "SELECT entities_json, relationships_json, aliases_json "
            "FROM parse_cache "
            "WHERE file_path = ? AND mtime = ? AND size = ? "
            "  AND parser_version = ?",
            (str(file_path), mtime, size, parser_version))
        return row

    def cache_put(self, file_path, mtime, size, parser_version,
                  entities_json, relationships_json, aliases_json):
        """Upsert one file's cache entry."""
        self._execute("""
            INSERT INTO parse_cache (file_path, mtime, size,
                                      parser_version, entities_json,
                                      relationships_json, aliases_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                mtime = excluded.mtime,
                size = excluded.size,
                parser_version = excluded.parser_version,
                entities_json = excluded.entities_json,
                relationships_json = excluded.relationships_json,
                aliases_json = excluded.aliases_json,
                cached_at = CURRENT_TIMESTAMP
        """, (str(file_path), mtime, size, parser_version,
              entities_json, relationships_json, aliases_json))

    def cache_clear(self):
        """Wipe the parse cache (e.g. when parser logic changes)."""
        self._execute("DELETE FROM parse_cache")

    # ─── LLM response cache ─────────────────────────────────

    def llm_cache_get(self, prompt_hash, model):
        """Return the cached response string, or None on miss."""
        row = self._query_one(
            "SELECT response FROM llm_cache "
            "WHERE prompt_hash = ? AND model = ?",
            (prompt_hash, model))
        return row['response'] if row else None

    def llm_cache_put(self, prompt_hash, model, response):
        """Upsert one cached response."""
        self._execute("""
            INSERT INTO llm_cache (prompt_hash, model, response)
            VALUES (?, ?, ?)
            ON CONFLICT(prompt_hash, model) DO UPDATE SET
                response = excluded.response,
                cached_at = CURRENT_TIMESTAMP
        """, (prompt_hash, model, response))

    def llm_cache_clear(self):
        """Wipe the LLM response cache."""
        self._execute("DELETE FROM llm_cache")

    # ─── DesignCritic outputs ───────────────────────────────

    def save_design_subtree(self, subtree_root, prompt, raw_response,
                            parsed):
        import json as _json
        self._execute("""
            INSERT INTO design_critic_subtree
                (subtree_root, prompt, raw_response, parsed_json)
            VALUES (?, ?, ?, ?)
        """, (subtree_root, prompt, raw_response,
              _json.dumps(parsed) if parsed else None))

    def save_design_module(self, module_name, prompt, raw_response, parsed):
        import json as _json
        self._execute("""
            INSERT INTO design_critic_module
                (module_name, prompt, raw_response, parsed_json)
            VALUES (?, ?, ?, ?)
        """, (module_name, prompt, raw_response,
              _json.dumps(parsed) if parsed else None))

    def get_design_subtrees(self):
        """Latest analysis per subtree_root."""
        return self._query_all("""
            SELECT s.* FROM design_critic_subtree s
            INNER JOIN (
                SELECT subtree_root, MAX(id) AS max_id
                FROM design_critic_subtree GROUP BY subtree_root
            ) latest
            ON s.id = latest.max_id
            ORDER BY s.subtree_root
        """)

    def get_design_module(self, module_name='default'):
        return self._query_one(
            "SELECT * FROM design_critic_module "
            "WHERE module_name = ? ORDER BY id DESC LIMIT 1",
            (module_name,))

    def delete_design_critic(self):
        self._execute("DELETE FROM design_critic_subtree")
        self._execute("DELETE FROM design_critic_module")
