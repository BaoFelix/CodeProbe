"""
db.py — SQLite persistence: the pipeline's shared memory.

Agents never talk to each other directly — ScannerAgent writes the
graph here, DesignCriticAgent reads it and writes its analysis back,
the report reads everything. One process can crash and another can
resume because the state lives here, not in objects.

TABLES
  entities              every named thing (class/method/field/...)
                        keyed by qualified_name; parent_qname is a
                        STRING not a foreign key — bulk insert needs
                        no id lookups and debugging stays readable
  relationships         one row per evidence: the same class pair can
                        have multiple rows of different kinds (that
                        multi-edge richness is a core design feature)
  module_info           one row per scan: orchestrator, style, counts
  design_critic_subtree DesignCritic pass-1 results (one per subtree)
  design_critic_module  DesignCritic pass-2 synthesis
  parse_cache           per-file parse results keyed by
                        (mtime, size, PARSER_VERSION) — 22x re-scan
  llm_cache             prompt-hash → response; same question never
                        costs API money twice

Every method opens and closes its own connection — simple and
thread-safe (the LLM step used to run 3 threads in parallel).
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
                    style TEXT,          -- oop | mixed | crtp
                    style_note TEXT,     -- human-readable warning when not oop
                    scan_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                /* ── entity-relationship model ──────────────────────────
                   The whole graph lives here. The single source of
                   truth: every consumer (ScannerAgent, the report layer,
                   the pipeline) queries entities +
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
                         file_count=None, class_count=None,
                         style=None, style_note=None):
        """Record one scan's summary row."""
        self._execute("""
            INSERT INTO module_info
                (module_name, directory, orchestrator, file_count,
                 class_count, style, style_note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (module_name, str(directory) if directory else None,
              orchestrator, file_count, class_count, style, style_note))

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
        """total / analyzed / pending counts for the dashboard.
        'analyzed' counts subtrees covered by the latest DesignCritic run."""
        return self._query_one("""
            SELECT
                (SELECT COUNT(*) FROM entities
                  WHERE kind IN ('class','struct','interface')) AS total,
                (SELECT COUNT(DISTINCT subtree_root)
                  FROM design_critic_subtree) AS analyzed
        """)

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
