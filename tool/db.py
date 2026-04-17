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
                CREATE TABLE IF NOT EXISTS classes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_name TEXT NOT NULL UNIQUE,
                    module TEXT DEFAULT 'default',
                    header_path TEXT,
                    impl_path TEXT,
                    method_count INTEGER DEFAULT 0,
                    member_count INTEGER DEFAULT 0,
                    line_count INTEGER DEFAULT 0,
                    is_orchestrator INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS dependencies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_class TEXT NOT NULL,
                    target_class TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    level_name TEXT NOT NULL,
                    source_evidence TEXT,
                    target_is_external INTEGER DEFAULT 0,
                    UNIQUE(source_class, target_class)
                );

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
            """)
            conn.commit()
        finally:
            conn.close()

    # ─── Class Registration ──────────────────────────────────

    def register_class(self, class_name, header_path=None, impl_path=None,
                       module='default', method_count=0, member_count=0,
                       line_count=0, is_orchestrator=False):
        """Register a class (upsert: update if exists, insert if not)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            existing = cur.execute(
                "SELECT id FROM classes WHERE class_name = ?",
                (class_name,)
            ).fetchone()

            if existing:
                cur.execute("""
                    UPDATE classes
                    SET header_path = COALESCE(?, header_path),
                        impl_path = COALESCE(?, impl_path),
                        module = ?,
                        method_count = ?,
                        member_count = ?,
                        line_count = ?,
                        is_orchestrator = ?
                    WHERE class_name = ?
                """, (str(header_path) if header_path else None,
                      str(impl_path) if impl_path else None,
                      module, method_count, member_count, line_count,
                      1 if is_orchestrator else 0,
                      class_name))
                conn.commit()
                return existing['id']

            cur.execute("""
                INSERT INTO classes
                    (class_name, header_path, impl_path, module,
                     method_count, member_count, line_count, is_orchestrator)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (class_name,
                  str(header_path) if header_path else None,
                  str(impl_path) if impl_path else None,
                  module, method_count, member_count, line_count,
                  1 if is_orchestrator else 0))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def save_classes_batch(self, class_list):
        """Batch write classes table. class_list = [dict, ...].
        Uses INSERT OR REPLACE for upsert semantics. Single connection + commit."""
        if not class_list:
            return
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executemany("""
                INSERT OR REPLACE INTO classes
                    (class_name, module, header_path, impl_path,
                     method_count, member_count, line_count, is_orchestrator)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [(c['class_name'], c.get('module', 'default'),
                   str(c['header_path']) if c.get('header_path') else None,
                   str(c['impl_path']) if c.get('impl_path') else None,
                   c.get('method_count', 0), c.get('member_count', 0),
                   c.get('line_count', 0), c.get('is_orchestrator', 0))
                  for c in class_list])
            conn.commit()
        finally:
            conn.close()

    # ─── Dependencies ────────────────────────────────────────

    def save_dependencies(self, deps_list):
        """Save a list of dependency records using executemany (batch). Each item: dict with
        source_class, target_class, level, level_name, source_evidence, target_is_external."""
        if not deps_list:
            return
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executemany("""
                INSERT OR REPLACE INTO dependencies
                    (source_class, target_class, level, level_name,
                     source_evidence, target_is_external)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [(dep['source_class'], dep['target_class'],
                   dep['level'], dep['level_name'],
                   dep.get('source_evidence', ''),
                   1 if dep.get('target_is_external') else 0)
                  for dep in deps_list])
            conn.commit()
        finally:
            conn.close()

    def get_dependencies(self, source_class=None):
        """Get dependencies. If source_class given, filter by it."""
        if source_class:
            return self._query_all(
                "SELECT * FROM dependencies WHERE source_class = ? ORDER BY level",
                (source_class,)
            )
        return self._query_all("SELECT * FROM dependencies ORDER BY source_class, level")

    def get_dependents_of_class(self, class_name):
        """Get classes that depend ON this class (incoming dependencies)."""
        return self._query_all(
            "SELECT * FROM dependencies WHERE target_class = ? ORDER BY level",
            (class_name,)
        )

    # ─── Module Info ─────────────────────────────────────────

    def save_module_info(self, module_name, directory=None, orchestrator=None,
                         file_count=None, class_count=None):
        """Save module scan info."""
        self._execute("""
            INSERT INTO module_info
                (module_name, directory, orchestrator, file_count, class_count)
            VALUES (?, ?, ?, ?, ?)
        """, (module_name, str(directory) if directory else None,
              orchestrator, file_count, class_count))

    def get_module_info(self, module_name=None):
        """Get module info. Returns latest entry (for specific module or overall)."""
        if module_name:
            return self._query_one(
                "SELECT * FROM module_info WHERE module_name = ? ORDER BY id DESC LIMIT 1",
                (module_name,)
            )
        return self._query_one("SELECT * FROM module_info ORDER BY id DESC LIMIT 1")

    # ─── Query ───────────────────────────────────────────────

    def get_class_by_name(self, class_name):
        """Get a single class record by name."""
        return self._query_one(
            "SELECT * FROM classes WHERE class_name = ?",
            (class_name,)
        )

    # Alias for backward compatibility (callers use get_task)
    get_task = get_class_by_name

    def get_all_tasks(self):
        """Get all registered classes."""
        return self._query_all("SELECT * FROM classes ORDER BY id")

    def get_stats(self):
        """Get pipeline statistics."""
        return self._query_one("""
            SELECT
                COUNT(*) as total,
                (SELECT COUNT(DISTINCT class_name)
                 FROM responsibility_analysis) as analyzed,
                COUNT(*) - (SELECT COUNT(DISTINCT ra.class_name)
                            FROM responsibility_analysis ra
                            INNER JOIN classes c ON ra.class_name = c.class_name
                           ) as pending
            FROM classes
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
        """Delete all scan data (classes + dependencies + module_info)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM dependencies")
            cur.execute("DELETE FROM module_info")
            cur.execute("DELETE FROM classes")
            conn.commit()
        finally:
            conn.close()

    def delete_architecture(self):
        """Delete architecture-level data (responsibilities + design proposals).
        Kept for backward compatibility with pipeline --from=arch."""
        self.delete_responsibilities()
