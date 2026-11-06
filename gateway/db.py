"""
数据库操作模块
负责与SQLite数据库交互，执行记忆查询等操作
"""
import sqlite3
import random
import logging
import json
import math
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Any
from config import config
from portrait import PORTRAIT_SECTIONS, PORTRAIT_BY_KEY

logger = logging.getLogger(__name__)


class Database:
    """数据库操作类"""

    def __init__(self, db_path: str = None):
        """
        初始化数据库连接

        Args:
            db_path: 数据库文件路径，默认为config中的配置
        """
        self.db_path = db_path or config.DATABASE_PATH
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_column(self, cursor: sqlite3.Cursor, table: str, column: str, definition: str):
        """确保旧表存在指定字段；已有字段时不做任何事。"""
        cursor.execute(f"PRAGMA table_info({table})")
        existing_columns = {row["name"] for row in cursor.fetchall()}
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _ensure_schema(self):
        """确保数据库表结构完整"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                # 创建 daily_summaries 表（如果不存在）
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_summaries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_archive_jobs (
                        date TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        retry_after REAL NOT NULL DEFAULT 0
                    )
                """)
                # 创建 date 字段索引
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_daily_summaries_date
                    ON daily_summaries(date)
                """)

                # 全局即时摘要只保留一个最新版；不同对话窗口共享同一条时间线。
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS instant_summary_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        summary TEXT NOT NULL DEFAULT '',
                        rounds_since_summary INTEGER NOT NULL DEFAULT 0,
                        last_summary_time REAL NOT NULL DEFAULT 0,
                        summary_failure_count INTEGER NOT NULL DEFAULT 0,
                        summary_retry_after REAL NOT NULL DEFAULT 0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                self._ensure_column(cursor, "instant_summary_state", "summary_failure_count", "INTEGER NOT NULL DEFAULT 0")
                self._ensure_column(cursor, "instant_summary_state", "summary_retry_after", "REAL NOT NULL DEFAULT 0")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS app_identity (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        assistant_name TEXT NOT NULL DEFAULT 'Mav',
                        user_name TEXT NOT NULL DEFAULT '岚',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    INSERT OR IGNORE INTO app_identity (id, assistant_name, user_name)
                    VALUES (1, 'Mav', '岚')
                """)

                # 创建 system_core 表（Mav核心设定）
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS system_core (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # 创建 notes 表（Mav备忘）
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        author TEXT NOT NULL DEFAULT 'Mav',
                        content TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # 创建 profiles 表（档案/近况快照）
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS profiles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT DEFAULT 'general',
                        key TEXT NOT NULL UNIQUE,
                        value TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_key
                    ON profiles(key)
                """)

                # 岚的画像：固定栏目正文与不含正文的更新日志分开保存。
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS lan_portrait (
                        section_key TEXT PRIMARY KEY,
                        section_name TEXT NOT NULL,
                        section_group TEXT NOT NULL,
                        sort_order INTEGER NOT NULL,
                        content TEXT NOT NULL DEFAULT '',
                        char_limit INTEGER NOT NULL DEFAULT 150,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS lan_portrait_update_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        section_key TEXT NOT NULL,
                        section_name TEXT NOT NULL,
                        action TEXT NOT NULL,
                        char_count INTEGER NOT NULL DEFAULT 0,
                        over_limit INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (section_key) REFERENCES lan_portrait(section_key)
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_lan_portrait_logs_created_at
                    ON lan_portrait_update_logs(created_at DESC)
                """)
                for section in PORTRAIT_SECTIONS:
                    cursor.execute("""
                        INSERT OR IGNORE INTO lan_portrait (
                            section_key, section_name, section_group, sort_order, content, char_limit
                        ) VALUES (?, ?, ?, ?, '', 150)
                    """, (
                        section["key"], section["name"], section["group"], section["order"]
                    ))

                # 创建 memories 表（长期记忆）
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        room TEXT DEFAULT 'living_room',
                        title TEXT DEFAULT '',
                        content TEXT,
                        recall_text TEXT,
                        keywords TEXT,
                        emotion_weight INTEGER DEFAULT 3,
                        event_date TEXT,
                        context TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS memory_threads (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_key TEXT NOT NULL UNIQUE,
                        title TEXT DEFAULT '',
                        summary_short TEXT DEFAULT '',
                        summary_full TEXT DEFAULT '',
                        current_state TEXT DEFAULT '',
                        mention_count INTEGER DEFAULT 0,
                        last_mentioned_at TEXT,
                        source TEXT DEFAULT 'system',
                        embedding TEXT DEFAULT '',
                        embedding_model TEXT DEFAULT '',
                        embedding_updated_at TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                self._ensure_column(cursor, "memory_threads", "embedding", "TEXT DEFAULT ''")
                self._ensure_column(cursor, "memory_threads", "embedding_model", "TEXT DEFAULT ''")
                self._ensure_column(cursor, "memory_threads", "embedding_updated_at", "TEXT")
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_memory_threads_event_key
                    ON memory_threads(event_key)
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS memory_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_key TEXT NOT NULL,
                        occurred_at TEXT,
                        summary TEXT NOT NULL,
                        importance INTEGER DEFAULT 3,
                        keywords TEXT DEFAULT '',
                        emotion_tags TEXT DEFAULT '',
                        source_memory_id INTEGER,
                        source_memory_ids TEXT DEFAULT '',
                        source_conversation_id TEXT,
                        source_message_ids TEXT DEFAULT '',
                        status TEXT DEFAULT 'active',
                        embedding TEXT DEFAULT '',
                        embedding_model TEXT DEFAULT '',
                        embedding_updated_at TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (event_key) REFERENCES memory_threads(event_key) ON DELETE CASCADE
                    )
                """)
                self._ensure_column(cursor, "memory_events", "source_memory_ids", "TEXT DEFAULT ''")
                self._ensure_column(cursor, "memory_events", "source_message_ids", "TEXT DEFAULT ''")
                self._ensure_column(cursor, "memory_events", "embedding", "TEXT DEFAULT ''")
                self._ensure_column(cursor, "memory_events", "embedding_model", "TEXT DEFAULT ''")
                self._ensure_column(cursor, "memory_events", "embedding_updated_at", "TEXT")
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_memory_events_event_key
                    ON memory_events(event_key)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_memory_events_occurred_at
                    ON memory_events(occurred_at)
                """)

                # 创建 conversations 表（对话表）
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id TEXT PRIMARY KEY,
                        title TEXT DEFAULT '新对话',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_folded INTEGER NOT NULL DEFAULT 1
                    )
                """)
                self._ensure_column(cursor, "conversations", "is_folded", "INTEGER NOT NULL DEFAULT 1")
                # 创建 updated_at 索引
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_conversations_updated
                    ON conversations(updated_at DESC)
                """)

                # 创建 messages 表（消息表）
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        thinking TEXT,
                        model TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                    )
                """)
                # 创建 conversation_id 索引
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, created_at ASC)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_messages_archive_assistant
                    ON messages(created_at) WHERE role = 'assistant'
                """)
                self._ensure_column(cursor, "messages", "image_preview", "TEXT")
                self._ensure_column(cursor, "messages", "file_name", "TEXT")
                self._ensure_column(cursor, "messages", "input_tokens", "INTEGER")
                self._ensure_column(cursor, "messages", "output_tokens", "INTEGER")
                self._ensure_column(cursor, "messages", "total_tokens", "INTEGER")
                self._ensure_column(cursor, "messages", "cache_creation_tokens", "INTEGER")
                self._ensure_column(cursor, "messages", "cache_read_tokens", "INTEGER")
                self._ensure_column(cursor, "messages", "cache_creation_1h_tokens", "INTEGER")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS message_images (
                        id TEXT PRIMARY KEY,
                        message_id TEXT NOT NULL,
                        sort_order INTEGER DEFAULT 0,
                        file_path TEXT NOT NULL,
                        thumb_path TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        width INTEGER,
                        height INTEGER,
                        vision_type TEXT DEFAULT '',
                        vision_summary TEXT DEFAULT '',
                        vision_detail TEXT DEFAULT '',
                        vision_status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_message_images_message
                    ON message_images(message_id, sort_order ASC)
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS art_jobs (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        user_message_id TEXT NOT NULL,
                        assistant_message_id TEXT NOT NULL,
                        model TEXT DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'queued',
                        attempt INTEGER NOT NULL DEFAULT 0,
                        request_json TEXT NOT NULL,
                        current_prompt TEXT NOT NULL DEFAULT '',
                        decision TEXT DEFAULT '',
                        selected_candidate_id TEXT,
                        caption TEXT DEFAULT '',
                        error TEXT DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                        FOREIGN KEY (user_message_id) REFERENCES messages(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_art_jobs_assistant
                    ON art_jobs(assistant_message_id)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_art_jobs_status
                    ON art_jobs(status, updated_at)
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS art_candidates (
                        id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        attempt INTEGER NOT NULL,
                        candidate_index INTEGER NOT NULL,
                        file_path TEXT NOT NULL,
                        thumb_path TEXT NOT NULL,
                        mime_type TEXT NOT NULL DEFAULT 'image/jpeg',
                        width INTEGER,
                        height INTEGER,
                        prompt TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'candidate',
                        review_summary TEXT DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (job_id) REFERENCES art_jobs(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_art_candidates_job
                    ON art_candidates(job_id, attempt, candidate_index)
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS token_usage_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL,
                        model TEXT,
                        input_tokens INTEGER DEFAULT 0,
                        output_tokens INTEGER DEFAULT 0,
                        total_tokens INTEGER DEFAULT 0,
                        conversation_id TEXT,
                        message_id TEXT,
                        metadata TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_token_usage_created
                    ON token_usage_events(created_at DESC)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_token_usage_message
                    ON token_usage_events(message_id)
                """)

                # books表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS books (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        author TEXT,
                        cover_image BLOB,
                        cover_mime TEXT,
                        total_chapters INTEGER DEFAULT 0,
                        processed_chapters INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'unprocessed',
                        last_chapter INTEGER DEFAULT 0,
                        last_position INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS book_chapters (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        book_id INTEGER NOT NULL,
                        chapter_index INTEGER NOT NULL,
                        title TEXT,
                        content_text TEXT,
                        content_images TEXT,
                        summary TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chapters_book
                    ON book_chapters(book_id, chapter_index)
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS annotations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        book_id INTEGER NOT NULL,
                        chapter_index INTEGER NOT NULL,
                        start_char INTEGER,
                        end_char INTEGER,
                        quote_text TEXT,
                        author TEXT NOT NULL,
                        content TEXT NOT NULL,
                        parent_id INTEGER DEFAULT NULL,
                        root_id INTEGER DEFAULT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_annotations_chapter
                    ON annotations(book_id, chapter_index)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_annotations_root
                    ON annotations(root_id)
                """)

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS image_notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        body TEXT DEFAULT '',
                        tags TEXT DEFAULT '[]',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_image_notes_created
                    ON image_notes(created_at DESC)
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS image_note_images (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        note_id INTEGER NOT NULL,
                        file_path TEXT NOT NULL,
                        thumb_path TEXT NOT NULL,
                        original_filename TEXT,
                        mime_type TEXT,
                        width INTEGER,
                        height INTEGER,
                        sort_order INTEGER DEFAULT 0,
                        ocr_text TEXT DEFAULT '',
                        vision_text TEXT DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (note_id) REFERENCES image_notes(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_image_note_images_note
                    ON image_note_images(note_id, sort_order)
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS image_note_comments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        note_id INTEGER NOT NULL,
                        author TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (note_id) REFERENCES image_notes(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_image_note_comments_note
                    ON image_note_comments(note_id, created_at ASC)
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS image_note_comment_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL UNIQUE,
                        note_id INTEGER NOT NULL,
                        mode TEXT NOT NULL,
                        user_reply TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'pending',
                        draft_text TEXT NOT NULL DEFAULT '',
                        comment_id INTEGER,
                        error TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (note_id) REFERENCES image_notes(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_image_note_comment_jobs_note
                    ON image_note_comment_jobs(note_id, id DESC)
                """)
                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_image_note_comment_jobs_active
                    ON image_note_comment_jobs(note_id)
                    WHERE status IN ('pending', 'running')
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS image_note_updates (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        note_id INTEGER NOT NULL,
                        action TEXT NOT NULL,
                        summary TEXT DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (note_id) REFERENCES image_notes(id) ON DELETE CASCADE
                    )
                """)

                conn.commit()
        except Exception as e:
            logger.error(f"确保数据库结构失败: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def get_app_identity(self) -> Dict[str, str]:
        with closing(self._get_connection()) as conn:
            row = conn.execute(
                "SELECT assistant_name, user_name FROM app_identity WHERE id = 1"
            ).fetchone()
        return {
            "assistant_name": row["assistant_name"] if row else "Mav",
            "user_name": row["user_name"] if row else "岚",
        }

    def update_app_identity(self, assistant_name: str, user_name: str) -> Dict[str, str]:
        with closing(self._get_connection()) as conn:
            conn.execute("""
                INSERT INTO app_identity (id, assistant_name, user_name, updated_at)
                VALUES (1, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    assistant_name = excluded.assistant_name,
                    user_name = excluded.user_name,
                    updated_at = CURRENT_TIMESTAMP
            """, (assistant_name, user_name))
            conn.commit()
        return {"assistant_name": assistant_name, "user_name": user_name}

    def get_system_core(self) -> Optional[str]:
        """
        获取最新的system core内容

        Returns:
            str: system core内容，如果没有则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT content FROM system_core ORDER BY id DESC LIMIT 1"
            )
            result = cursor.fetchone()
            conn.close()
            return result['content'] if result else None
        except Exception as e:
            logger.error(f"获取system_core失败: {e}")
            return None

    def get_mav_notes(self, limit: int = None) -> List[Dict[str, Any]]:
        """
        获取Mav的备忘记录

        Args:
            limit: 返回记录数量限制，默认为config中的MAV_NOTES_LIMIT

        Returns:
            List[Dict]: 备忘记录列表
        """
        if limit is None:
            limit = config.MAV_NOTES_LIMIT

        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, author, content, created_at
                   FROM notes
                   WHERE author = 'Mav'
                   ORDER BY id DESC
                   LIMIT ?""",
                (limit,)
            )
            results = cursor.fetchall()
            conn.close()
            return [dict(row) for row in results]
        except Exception as e:
            logger.error(f"获取Mav notes失败: {e}")
            return []

    def get_short_term_memories(self, days: int = 15) -> List[Dict[str, Any]]:
        """
        获取短期记忆（从daily_summaries表读取最近N天的每日总结）

        Args:
            days: 天数，默认为15天

        Returns:
            List[Dict]: 记忆列表
        """
        try:
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, date, summary, created_at
                    FROM daily_summaries
                    WHERE date >= ?
                    ORDER BY date DESC
                """, (cutoff_date,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"读取短期记忆失败: {e}")
            return []

    def get_long_term_memories(
        self,
        keywords: List[str],
        top_n: int = None,
        random_n: int = None
    ) -> List[Dict[str, Any]]:
        """
        获取长期记忆（根据关键词匹配）

        Args:
            keywords: 关键词列表
            top_n: 取top匹配数量，默认为config中的MEMORY_TOP_N
            random_n: 随机取数量，默认为config中的MEMORY_RANDOM_N

        Returns:
            List[Dict]: 记忆列表
        """
        if top_n is None:
            top_n = config.MEMORY_TOP_N
        if random_n is None:
            random_n = config.MEMORY_RANDOM_N

        if not keywords:
            return []

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 构建搜索条件（主查询，不用子查询）
            search_conditions = " OR ".join(["keywords LIKE ?" for _ in keywords])
            search_params = [f"%{kw}%" for kw in keywords]

            cursor.execute(
                f"""SELECT id, event_date, content, recall_text, keywords, created_at
                   FROM memories
                   WHERE keywords IS NOT NULL AND ({search_conditions})
                   ORDER BY emotion_weight DESC, id DESC
                   LIMIT 20""",
                search_params
            )
            all_matches = [dict(row) for row in cursor.fetchall()]

            # 在Python里计算匹配得分
            def count_matches(row_keywords, kws):
                if not row_keywords:
                    return 0
                rk = row_keywords.lower()
                return sum(1 for kw in kws if kw.lower() in rk)

            for row in all_matches:
                row['match_count'] = count_matches(row.get('keywords', ''), keywords)

            # 按匹配数量排序，取top_n条得分最高的
            sorted_matches = sorted(all_matches, key=lambda x: x['match_count'], reverse=True)
            top_matches = sorted_matches[:top_n]

            # 从剩余的里随机取random_n条
            remainder = sorted_matches[top_n:]
            if random_n > 0 and remainder:
                random_picks = random.sample(remainder, min(random_n, len(remainder)))
                top_matches = top_matches + random_picks

            # 去重：过滤recall_text高度相似的条目
            def similarity(a: str, b: str) -> float:
                if not a or not b:
                    return 0.0
                a_words = set(a.lower().split())
                b_words = set(b.lower().split())
                if not a_words or not b_words:
                    return 0.0
                intersection = a_words & b_words
                return len(intersection) / max(len(a_words), len(b_words))

            deduped = []
            for candidate in top_matches:
                text = candidate.get('recall_text', '')
                is_duplicate = any(
                    similarity(text, existing.get('recall_text', '')) > 0.6
                    for existing in deduped
                )
                if not is_duplicate:
                    deduped.append(candidate)

            conn.close()
            return deduped

        except Exception as e:
            logger.error(f"获取长期记忆失败: {e}")
            return []

    def list_memories(
        self,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        列出记忆

        Args:
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            List[Dict]: 记忆列表
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM memories
                   ORDER BY id DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset)
            )
            results = cursor.fetchall()
            conn.close()
            return [dict(row) for row in results]
        except Exception as e:
            logger.error(f"列出记忆失败: {e}")
            return []

    def delete_memory(self, memory_id: int) -> bool:
        """
        删除记忆

        Args:
            memory_id: 记忆ID

        Returns:
            bool: 是否删除成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
            conn.close()
            return deleted
        except Exception as e:
            logger.error(f"删除记忆失败: {e}")
            return False

    def add_memory(self, content: str = None, recall_text: str = None, keywords: str = None,
               room: str = "living_room", title: str = "", emotion_weight: int = 3,
               event_date: str = None, context: str = None) -> bool:
        """写入长期记忆"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO memories (room, title, content, recall_text, keywords, emotion_weight, event_date, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (room, title, content or recall_text, recall_text, keywords, emotion_weight, event_date, context))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"写入记忆失败: {e}")
            return False

    def count_memories(self) -> int:
        """Return the number of legacy long-term memories."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) AS count FROM memories")
                row = cursor.fetchone()
                return int(row["count"]) if row else 0
        except Exception as e:
            logger.error(f"Count memories failed: {e}")
            return 0

    def upsert_memory_thread(
        self,
        event_key: str,
        title: str = "",
        summary_short: str = "",
        summary_full: str = "",
        current_state: str = "",
        mention_count_delta: int = 0,
        last_mentioned_at: str = None,
        source: str = "system"
    ) -> bool:
        """Create or update a long-running memory thread."""
        if not event_key:
            return False
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO memory_threads (
                        event_key, title, summary_short, summary_full,
                        current_state, mention_count, last_mentioned_at, source
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_key) DO UPDATE SET
                        title = CASE WHEN excluded.title != '' THEN excluded.title ELSE title END,
                        summary_short = CASE WHEN excluded.summary_short != '' THEN excluded.summary_short ELSE summary_short END,
                        summary_full = CASE WHEN excluded.summary_full != '' THEN excluded.summary_full ELSE summary_full END,
                        current_state = CASE WHEN excluded.current_state != '' THEN excluded.current_state ELSE current_state END,
                        mention_count = mention_count + excluded.mention_count,
                        last_mentioned_at = COALESCE(excluded.last_mentioned_at, last_mentioned_at),
                        source = excluded.source,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    event_key,
                    title or "",
                    summary_short or "",
                    summary_full or "",
                    current_state or "",
                    int(mention_count_delta or 0),
                    last_mentioned_at,
                    source
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Upsert memory thread failed: {e}")
            return False

    def add_memory_event(
        self,
        event_key: str,
        summary: str,
        occurred_at: str = None,
        importance: int = 3,
        keywords: str = "",
        emotion_tags: str = "",
        source_memory_id: int = None,
        source_memory_ids: str = "",
        source_conversation_id: str = None,
        source_message_ids: str = "",
        status: str = "active"
    ) -> Optional[int]:
        """Add one timestamped event under a memory thread."""
        if not event_key or not summary:
            return None
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO memory_events (
                        event_key, occurred_at, summary, importance, keywords,
                        emotion_tags, source_memory_id, source_memory_ids,
                        source_conversation_id, source_message_ids, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event_key,
                    occurred_at,
                    summary,
                    int(importance or 3),
                    keywords or "",
                    emotion_tags or "",
                    source_memory_id,
                    source_memory_ids or "",
                    source_conversation_id,
                    source_message_ids or "",
                    status or "active"
                ))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Add memory event failed: {e}")
            return None

    def update_memory_event(
        self,
        event_id: int,
        summary: str = "",
        occurred_at: str = None,
        importance: int = None,
        keywords: str = "",
        emotion_tags: str = "",
        status: str = None
    ) -> bool:
        """Update one memory event when the newest conversation refines it."""
        if not event_id:
            return False
        try:
            fields = []
            params = []
            if summary:
                fields.append("summary = ?")
                params.append(summary)
            if occurred_at:
                fields.append("occurred_at = ?")
                params.append(occurred_at)
            if importance is not None:
                fields.append("importance = ?")
                params.append(int(importance or 3))
            if keywords:
                fields.append("keywords = ?")
                params.append(keywords)
            if emotion_tags:
                fields.append("emotion_tags = ?")
                params.append(emotion_tags)
            if status:
                fields.append("status = ?")
                params.append(status)

            if not fields:
                return False

            params.append(event_id)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE memory_events SET {', '.join(fields)} WHERE id = ?",
                    params
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Update memory event failed: {e}")
            return False

    def get_memory_event(self, event_id: int) -> Optional[Dict[str, Any]]:
        """Fetch one memory event by id."""
        if not event_id:
            return None
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM memory_events WHERE id = ?", (event_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Get memory event failed: {e}")
            return None

    def clear_memory_v2(self) -> bool:
        """Clear migrated Thread/Event memory tables without touching legacy memories."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM memory_events")
                cursor.execute("DELETE FROM memory_threads")
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Clear memory v2 failed: {e}")
            return False

    def delete_memory_event_v2(self, event_id: int) -> bool:
        """Delete one MemoryEvent from the v2 memory table."""
        if not event_id:
            return False
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM memory_events WHERE id = ?", (event_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Delete memory event v2 failed: {e}")
            return False

    def delete_memory_thread_v2(self, event_key: str) -> bool:
        """Delete one MemoryThread and all Events under the same event_key."""
        if not event_key:
            return False
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM memory_events WHERE event_key = ?", (event_key,))
                cursor.execute("DELETE FROM memory_threads WHERE event_key = ?", (event_key,))
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted
        except Exception as e:
            logger.error(f"Delete memory thread v2 failed: {e}")
            return False

    def get_memory_thread(self, event_key: str) -> Optional[Dict[str, Any]]:
        """Fetch one memory thread by event_key."""
        if not event_key:
            return None
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM memory_threads WHERE event_key = ?", (event_key,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Get memory thread failed: {e}")
            return None

    def build_thread_embedding_text(self, thread: Dict[str, Any]) -> str:
        """Build stable text used to embed a MemoryThread."""
        return "\n".join([
            f"event_key: {thread.get('event_key', '')}",
            f"title: {thread.get('title', '')}",
            f"summary_short: {thread.get('summary_short', '')}",
            f"summary_full: {thread.get('summary_full', '')}",
            f"current_state: {thread.get('current_state', '')}"
        ]).strip()

    def build_event_embedding_text(self, event: Dict[str, Any]) -> str:
        """Build stable text used to embed a MemoryEvent."""
        return "\n".join([
            f"event_key: {event.get('event_key', '')}",
            f"occurred_at: {event.get('occurred_at', '')}",
            f"summary: {event.get('summary', '')}",
            f"keywords: {event.get('keywords', '')}",
            f"emotion_tags: {event.get('emotion_tags', '')}"
        ]).strip()

    def update_thread_embedding(
        self,
        event_key: str,
        embedding: List[float],
        model: str
    ) -> bool:
        """Persist embedding vector for one MemoryThread."""
        if not event_key or not embedding:
            return False
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE memory_threads
                    SET embedding = ?,
                        embedding_model = ?,
                        embedding_updated_at = CURRENT_TIMESTAMP
                    WHERE event_key = ?
                """, (json.dumps(embedding), model, event_key))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Update thread embedding failed: {e}")
            return False

    def update_event_embedding(
        self,
        event_id: int,
        embedding: List[float],
        model: str
    ) -> bool:
        """Persist embedding vector for one MemoryEvent."""
        if not event_id or not embedding:
            return False
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE memory_events
                    SET embedding = ?,
                        embedding_model = ?,
                        embedding_updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (json.dumps(embedding), model, event_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Update event embedding failed: {e}")
            return False

    def count_missing_embeddings(self, model: str = None) -> Dict[str, int]:
        """Count Thread/Event rows without current embeddings."""
        model = model or config.EMBEDDING_MODEL
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*) AS count
                    FROM memory_threads
                    WHERE embedding = '' OR embedding_model != ?
                """, (model,))
                threads = int(cursor.fetchone()["count"])
                cursor.execute("""
                    SELECT COUNT(*) AS count
                    FROM memory_events
                    WHERE status = 'active' AND (embedding = '' OR embedding_model != ?)
                """, (model,))
                events = int(cursor.fetchone()["count"])
                return {"threads": threads, "events": events}
        except Exception as e:
            logger.error(f"Count missing embeddings failed: {e}")
            return {"threads": 0, "events": 0}

    def list_items_for_embedding(
        self,
        limit: int = 20,
        model: str = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """List rows that need embedding backfill."""
        model = model or config.EMBEDDING_MODEL
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT *
                    FROM memory_threads
                    WHERE embedding = '' OR embedding_model != ?
                    ORDER BY mention_count DESC, updated_at DESC, id DESC
                    LIMIT ?
                """, (model, limit))
                threads = [dict(row) for row in cursor.fetchall()]

                remaining = max(0, limit - len(threads))
                events = []
                if remaining:
                    cursor.execute("""
                        SELECT *
                        FROM memory_events
                        WHERE status = 'active'
                          AND (embedding = '' OR embedding_model != ?)
                        ORDER BY importance DESC, occurred_at DESC, id DESC
                        LIMIT ?
                    """, (model, remaining))
                    events = [dict(row) for row in cursor.fetchall()]

                return {"threads": threads, "events": events}
        except Exception as e:
            logger.error(f"List items for embedding failed: {e}")
            return {"threads": [], "events": []}

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Cosine similarity for JSON-stored vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if not norm_a or not norm_b:
            return 0.0
        return dot / (norm_a * norm_b)

    def _parse_embedding(self, value: str) -> List[float]:
        """Parse an embedding JSON string."""
        if not value:
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [float(x) for x in parsed]
        except Exception:
            return []
        return []

    def count_memory_threads(self, q: str = None) -> int:
        """Count memory threads, optionally filtered by a loose admin search."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if q:
                    like = f"%{q.lower()}%"
                    cursor.execute("""
                        SELECT COUNT(*) AS count FROM memory_threads
                        WHERE LOWER(event_key) LIKE ?
                           OR LOWER(title) LIKE ?
                           OR LOWER(summary_short) LIKE ?
                           OR LOWER(summary_full) LIKE ?
                           OR LOWER(current_state) LIKE ?
                    """, (like, like, like, like, like))
                else:
                    cursor.execute("SELECT COUNT(*) AS count FROM memory_threads")
                row = cursor.fetchone()
                return int(row["count"]) if row else 0
        except Exception as e:
            logger.error(f"Count memory threads failed: {e}")
            return 0

    def list_memory_threads(
        self,
        limit: int = 50,
        offset: int = 0,
        q: str = None
    ) -> List[Dict[str, Any]]:
        """List memory threads for admin and migration checks."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if q:
                    like = f"%{q.lower()}%"
                    cursor.execute("""
                        SELECT * FROM memory_threads
                        WHERE LOWER(event_key) LIKE ?
                           OR LOWER(title) LIKE ?
                           OR LOWER(summary_short) LIKE ?
                           OR LOWER(summary_full) LIKE ?
                           OR LOWER(current_state) LIKE ?
                        ORDER BY mention_count DESC, last_mentioned_at DESC, updated_at DESC, id DESC
                        LIMIT ? OFFSET ?
                    """, (like, like, like, like, like, limit, offset))
                else:
                    cursor.execute("""
                        SELECT * FROM memory_threads
                        ORDER BY mention_count DESC, last_mentioned_at DESC, updated_at DESC, id DESC
                        LIMIT ? OFFSET ?
                    """, (limit, offset))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"List memory threads failed: {e}")
            return []

    def count_memory_events(self, event_key: str = None, q: str = None) -> int:
        """Count memory events for admin pagination."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                clauses = []
                params = []
                if event_key:
                    clauses.append("event_key = ?")
                    params.append(event_key)
                if q:
                    like = f"%{q.lower()}%"
                    clauses.append("""(
                        LOWER(summary) LIKE ?
                        OR LOWER(keywords) LIKE ?
                        OR LOWER(emotion_tags) LIKE ?
                    )""")
                    params.extend([like, like, like])
                where = "WHERE " + " AND ".join(clauses) if clauses else ""
                cursor.execute(f"SELECT COUNT(*) AS count FROM memory_events {where}", params)
                row = cursor.fetchone()
                return int(row["count"]) if row else 0
        except Exception as e:
            logger.error(f"Count memory events failed: {e}")
            return 0

    def list_memory_events(
        self,
        event_key: str = None,
        limit: int = 50,
        offset: int = 0,
        q: str = None,
        status: str = None,
    ) -> List[Dict[str, Any]]:
        """List memory events, optionally filtered by event_key."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                clauses = []
                params = []
                if event_key:
                    clauses.append("event_key = ?")
                    params.append(event_key)
                if status:
                    clauses.append("status = ?")
                    params.append(status)
                if q:
                    like = f"%{q.lower()}%"
                    clauses.append("""(
                        LOWER(summary) LIKE ?
                        OR LOWER(keywords) LIKE ?
                        OR LOWER(emotion_tags) LIKE ?
                    )""")
                    params.extend([like, like, like])
                where = "WHERE " + " AND ".join(clauses) if clauses else ""
                cursor.execute(f"""
                    SELECT * FROM memory_events
                    {where}
                    ORDER BY occurred_at DESC, importance DESC, id DESC
                    LIMIT ? OFFSET ?
                """, (*params, limit, offset))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"List memory events failed: {e}")
            return []

    def get_long_term_memory_v2(
        self,
        keywords: List[str],
        query_embedding: List[float] = None,
        thread_top_n: int = None,
        event_top_n: int = None,
        sibling_n: int = None,
        primary_keywords: List[str] = None,
        expanded_keywords: List[str] = None,
        previous_keywords: List[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Retrieve Thread/Event memories for prompt injection."""
        if thread_top_n is None:
            thread_top_n = config.MEMORY_V2_THREAD_TOP_N
        if event_top_n is None:
            event_top_n = config.MEMORY_V2_EVENT_TOP_N
        if sibling_n is None:
            sibling_n = config.MEMORY_V2_SIBLING_N
        primary_keywords = (keywords or []) if primary_keywords is None else primary_keywords
        expanded_keywords = expanded_keywords or []
        previous_keywords = previous_keywords or []
        keywords = list(dict.fromkeys((keywords or []) + primary_keywords + expanded_keywords + previous_keywords))
        if not keywords and not query_embedding:
            return {"threads": [], "events": [], "sibling_events": []}

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                def fetch_event_candidates(terms: List[str], limit: int) -> List[Dict[str, Any]]:
                    if not terms:
                        return []
                    like_clauses = " OR ".join([
                        "LOWER(e.event_key) LIKE ?",
                        "LOWER(e.summary) LIKE ?",
                        "LOWER(e.keywords) LIKE ?",
                        "LOWER(e.emotion_tags) LIKE ?",
                        "LOWER(t.title) LIKE ?",
                        "LOWER(t.summary_short) LIKE ?",
                        "LOWER(t.summary_full) LIKE ?",
                        "LOWER(t.current_state) LIKE ?"
                    ] * len(terms))
                    params = []
                    for kw in terms:
                        like = f"%{kw.lower()}%"
                        params.extend([like, like, like, like, like, like, like, like])

                    cursor.execute(f"""
                        SELECT
                            e.*,
                            t.title AS thread_title,
                            t.summary_short AS thread_summary_short,
                            t.summary_full AS thread_summary_full,
                            t.current_state AS thread_current_state
                        FROM memory_events e
                        LEFT JOIN memory_threads t ON t.event_key = e.event_key
                        WHERE e.status = 'active' AND ({like_clauses})
                        ORDER BY e.importance DESC, e.occurred_at DESC, e.id DESC
                        LIMIT ?
                    """, (*params, limit))
                    return [dict(row) for row in cursor.fetchall()]

                # Preserve the existing candidate pool before adding the previous turn.
                current_terms = list(dict.fromkeys(primary_keywords + expanded_keywords))
                event_matches = fetch_event_candidates(current_terms, 80)
                previous_matches = fetch_event_candidates(previous_keywords, 80)
                candidate_ids = {row["id"] for row in event_matches}
                event_matches.extend(row for row in previous_matches if row["id"] not in candidate_ids)

                def match_stats(row: Dict[str, Any]) -> Dict[str, Any]:
                    haystack = " ".join([
                        str(row.get("event_key") or ""),
                        str(row.get("summary") or ""),
                        str(row.get("keywords") or ""),
                        str(row.get("emotion_tags") or ""),
                        str(row.get("thread_title") or ""),
                        str(row.get("thread_summary_short") or ""),
                        str(row.get("thread_summary_full") or ""),
                        str(row.get("thread_current_state") or "")
                    ]).lower()
                    primary_hits = [
                        kw for kw in primary_keywords
                        if kw and kw.lower() in haystack
                    ]
                    expanded_hits = [
                        kw for kw in expanded_keywords
                        if kw and kw.lower() in haystack
                    ]
                    previous_hits = [
                        kw for kw in previous_keywords
                        if kw and kw.lower() in haystack
                    ]
                    return {
                        "primary_match_count": len(primary_hits),
                        "expanded_match_count": len(expanded_hits),
                        "previous_match_count": len(previous_hits),
                        "match_count": len(primary_hits) + len(expanded_hits) + len(previous_hits),
                        # A previous turn can help recall, but cannot outweigh one current term alone.
                        "keyword_score": (len(primary_hits) * 3) + len(expanded_hits) + min(len(previous_hits), 2),
                        "primary_keyword_hits": primary_hits[:8],
                        "expanded_keyword_hits": expanded_hits[:8],
                        "previous_keyword_hits": previous_hits[:8]
                    }

                for row in event_matches:
                    row.update(match_stats(row))
                    row["embedding_score"] = 0.0
                    row["match_source"] = (
                        "primary_keyword"
                        if row.get("primary_match_count", 0) > 0
                        else "previous_keyword"
                        if row.get("previous_match_count", 0) > 0
                        else "expanded_keyword"
                    )

                if query_embedding:
                    cursor.execute("""
                        SELECT
                            e.*,
                            t.title AS thread_title,
                            t.summary_short AS thread_summary_short,
                            t.summary_full AS thread_summary_full,
                            t.current_state AS thread_current_state
                        FROM memory_events e
                        LEFT JOIN memory_threads t ON t.event_key = e.event_key
                        WHERE e.status = 'active'
                          AND e.embedding != ''
                          AND e.embedding_model = ?
                    """, (config.EMBEDDING_MODEL,))
                    embedding_hits = []
                    for row in cursor.fetchall():
                        item = dict(row)
                        vector = self._parse_embedding(item.get("embedding", ""))
                        score = self._cosine_similarity(query_embedding, vector)
                        if score >= config.EMBEDDING_MIN_SCORE:
                            item.update(match_stats(item))
                            item["embedding_score"] = score
                            item["match_source"] = "embedding"
                            embedding_hits.append(item)
                    embedding_hits.sort(
                        key=lambda r: (
                            r.get("embedding_score", 0.0),
                            int(r.get("importance") or 0),
                            r.get("occurred_at") or ""
                        ),
                        reverse=True
                    )

                    event_by_id = {
                        int(row["id"]): row
                        for row in event_matches
                        if row.get("id") is not None
                    }
                    for hit in embedding_hits[:config.EMBEDDING_EVENT_TOP_N]:
                        hit_id = int(hit["id"])
                        if hit_id in event_by_id:
                            existing = event_by_id[hit_id]
                            existing["embedding_score"] = max(
                                existing.get("embedding_score", 0.0),
                                hit.get("embedding_score", 0.0)
                            )
                            existing["match_source"] = (
                                "primary_mixed"
                                if existing.get("primary_match_count", 0) > 0
                                else "previous_mixed"
                                if existing.get("previous_match_count", 0) > 0
                                else "expanded_mixed"
                            )
                        else:
                            event_by_id[hit_id] = hit
                    event_matches = list(event_by_id.values())

                def event_rank(row: Dict[str, Any]):
                    return (
                        row.get("keyword_score", 0),
                        row.get("embedding_score", 0.0),
                        int(row.get("importance") or 0),
                        row.get("occurred_at") or "",
                        int(row.get("id") or 0)
                    )

                event_matches.sort(key=event_rank, reverse=True)

                selected_events = []
                selected_event_ids = set()
                pure_embedding_count = 0
                embedding_min = max(0, config.MEMORY_V2_EMBEDDING_EVENT_MIN)
                embedding_max = max(embedding_min, config.MEMORY_V2_EMBEDDING_EVENT_MAX)
                embedding_max = min(embedding_max, event_top_n)

                def add_event(row: Dict[str, Any]) -> bool:
                    if len(selected_events) >= event_top_n:
                        return False
                    nonlocal pure_embedding_count
                    is_pure_embedding = row.get("match_source") == "embedding"
                    if is_pure_embedding and pure_embedding_count >= embedding_max:
                        return False
                    event_id = row.get("id")
                    if event_id is not None:
                        event_id = int(event_id)
                        if event_id in selected_event_ids:
                            return False
                        selected_event_ids.add(event_id)
                    if is_pure_embedding:
                        pure_embedding_count += 1
                    selected_events.append(row)
                    return True

                # Mixed hits are both keyword-relevant and semantically close, so keep them first.
                for row in event_matches:
                    if row.get("match_source") in {"primary_mixed", "previous_mixed", "expanded_mixed"}:
                        add_event(row)

                # Reserve a small number of pure embedding seats so semantic recall is not squeezed out.
                embedding_quota = min(
                    embedding_min,
                    event_top_n
                )
                if query_embedding and embedding_quota:
                    for row in event_matches:
                        if row.get("match_source") != "embedding":
                            continue
                        add_event(row)
                        if pure_embedding_count >= embedding_quota:
                            break

                # Fill the remaining seats by the overall ranking.
                for row in event_matches:
                    if len(selected_events) >= event_top_n:
                        break
                    add_event(row)

                thread_keys = []
                for event in selected_events:
                    key = event.get("event_key")
                    if key and key not in thread_keys:
                        thread_keys.append(key)

                if len(thread_keys) < thread_top_n and keywords:
                    thread_clauses = " OR ".join([
                        "LOWER(event_key) LIKE ?",
                        "LOWER(title) LIKE ?",
                        "LOWER(summary_short) LIKE ?",
                        "LOWER(summary_full) LIKE ?",
                        "LOWER(current_state) LIKE ?"
                    ] * len(keywords))
                    thread_params = []
                    for kw in keywords:
                        like = f"%{kw.lower()}%"
                        thread_params.extend([like, like, like, like, like])
                    cursor.execute(f"""
                        SELECT *
                        FROM memory_threads
                        WHERE {thread_clauses}
                        ORDER BY mention_count DESC, last_mentioned_at DESC, id DESC
                        LIMIT ?
                    """, (*thread_params, thread_top_n * 2))
                    for row in cursor.fetchall():
                        key = row["event_key"]
                        if key and key not in thread_keys:
                            thread_keys.append(key)
                            # Thread source is reconstructed after fetch below.
                        if len(thread_keys) >= thread_top_n:
                            break

                embedding_thread_scores = {}
                if query_embedding and len(thread_keys) < thread_top_n:
                    cursor.execute("""
                        SELECT *
                        FROM memory_threads
                        WHERE embedding != '' AND embedding_model = ?
                    """, (config.EMBEDDING_MODEL,))
                    thread_hits = []
                    for row in cursor.fetchall():
                        item = dict(row)
                        vector = self._parse_embedding(item.get("embedding", ""))
                        score = self._cosine_similarity(query_embedding, vector)
                        if score >= config.EMBEDDING_MIN_SCORE:
                            item["embedding_score"] = score
                            embedding_thread_scores[item["event_key"]] = score
                            thread_hits.append(item)
                    thread_hits.sort(
                        key=lambda r: (
                            r.get("embedding_score", 0.0),
                            int(r.get("mention_count") or 0),
                            r.get("last_mentioned_at") or ""
                        ),
                        reverse=True
                    )
                    for row in thread_hits[:config.EMBEDDING_THREAD_TOP_N]:
                        key = row["event_key"]
                        if key and key not in thread_keys:
                            thread_keys.append(key)
                            embedding_thread_scores[key] = row.get("embedding_score", 0.0)
                        if len(thread_keys) >= thread_top_n:
                            break

                selected_thread_keys = thread_keys[:thread_top_n]
                threads = []
                if selected_thread_keys:
                    placeholders = ",".join(["?"] * len(selected_thread_keys))
                    cursor.execute(f"""
                        SELECT *
                        FROM memory_threads
                        WHERE event_key IN ({placeholders})
                    """, selected_thread_keys)
                    thread_by_key = {row["event_key"]: dict(row) for row in cursor.fetchall()}
                    for key in selected_thread_keys:
                        if key not in thread_by_key:
                            continue
                        thread = thread_by_key[key]
                        thread["embedding_score"] = embedding_thread_scores.get(key, 0.0)
                        thread["match_source"] = (
                            "embedding"
                            if key in embedding_thread_scores
                            else "keyword"
                        )
                        threads.append(thread)

                selected_event_ids = {
                    int(event["id"])
                    for event in selected_events
                    if event.get("id") is not None
                }
                sibling_events = []
                for key in selected_thread_keys:
                    if len(sibling_events) >= config.MEMORY_V2_SIBLING_TOTAL_N:
                        break
                    cursor.execute("""
                        SELECT *
                        FROM memory_events
                        WHERE event_key = ? AND status = 'active'
                        ORDER BY importance DESC, occurred_at DESC, id DESC
                        LIMIT ?
                    """, (key, sibling_n + len(selected_event_ids)))
                    added = 0
                    for row in cursor.fetchall():
                        event = dict(row)
                        event_id = int(event["id"])
                        if event_id in selected_event_ids:
                            continue
                        sibling_events.append(event)
                        added += 1
                        if (
                            added >= sibling_n
                            or len(sibling_events) >= config.MEMORY_V2_SIBLING_TOTAL_N
                        ):
                            break

                return {
                    "threads": threads,
                    "events": selected_events,
                    "sibling_events": sibling_events
                }

        except Exception as e:
            logger.error(f"Get long term memory v2 failed: {e}")
            return {"threads": [], "events": [], "sibling_events": []}

    def get_random_memory_event_v2(
        self,
        exclude_event_ids: List[int] = None,
        exclude_event_keys: List[str] = None,
        min_importance: int = 3
    ) -> Optional[Dict[str, Any]]:
        """Pick one active MemoryEvent for gentle resurfacing."""
        exclude_event_ids = exclude_event_ids or []
        exclude_event_keys = exclude_event_keys or []

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                clauses = ["e.status = 'active'", "COALESCE(e.importance, 3) >= ?"]
                params: List[Any] = [int(min_importance or 3)]

                if exclude_event_ids:
                    placeholders = ",".join(["?"] * len(exclude_event_ids))
                    clauses.append(f"e.id NOT IN ({placeholders})")
                    params.extend([int(event_id) for event_id in exclude_event_ids])

                if exclude_event_keys:
                    placeholders = ",".join(["?"] * len(exclude_event_keys))
                    clauses.append(f"e.event_key NOT IN ({placeholders})")
                    params.extend(exclude_event_keys)

                where = " AND ".join(clauses)
                cursor.execute(f"""
                    SELECT
                        e.*,
                        t.title AS thread_title,
                        t.summary_short AS thread_summary_short
                    FROM memory_events e
                    LEFT JOIN memory_threads t ON t.event_key = e.event_key
                    WHERE {where}
                    ORDER BY RANDOM()
                    LIMIT 1
                """, params)
                row = cursor.fetchone()
                if not row:
                    return None
                item = dict(row)
                item["match_source"] = "random_resurface"
                item["match_count"] = 0
                item["embedding_score"] = 0.0
                item["injection_strength"] = "weak"
                return item
        except Exception as e:
            logger.error(f"Get random memory v2 failed: {e}")
            return None

    def add_daily_summary(self, date: str, summary: str) -> Optional[int]:
        """
        写入每日对话总结到daily_summaries表

        Args:
            date: 日期，格式为YYYY-MM-DD
            summary: 每日总结内容

        Returns:
            int: 插入的记录ID，失败返回None
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO daily_summaries (date, summary)
                    VALUES (?, ?)
                """, (date, summary))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"写入每日总结失败: {e}")
            return None

    def list_daily_archive_rounds(self, day: Optional[date] = None) -> List[Dict[str, Any]]:
        """按服务器本地日归档，SQLite 消息时间是 UTC。跨午夜时按助手消息归属。"""
        def utc_midnight(local_day: date) -> str:
            return datetime.combine(local_day, time.min).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        day = day or date.today()
        with closing(self._get_connection()) as conn:
            rows = conn.execute("""
                SELECT u.content AS user, a.content AS assistant
                FROM messages AS a
                JOIN messages AS u ON u.rowid = (
                    SELECT p.rowid FROM messages AS p
                    WHERE p.conversation_id = a.conversation_id
                      AND p.role IN ('user', 'assistant')
                      AND p.created_at <= a.created_at
                      AND (p.created_at < a.created_at OR p.rowid < a.rowid)
                    ORDER BY p.created_at DESC, p.rowid DESC LIMIT 1
                )
                WHERE a.role = 'assistant' AND u.role = 'user'
                  AND a.created_at >= ? AND a.created_at < ?
                ORDER BY a.created_at, a.rowid
            """, (utc_midnight(day), utc_midnight(day + timedelta(days=1)))).fetchall()
        return [dict(row) for row in rows]

    def list_daily_archive_jobs(self, start: str, end: str) -> Dict[str, Dict[str, Any]]:
        with closing(self._get_connection()) as conn:
            rows = conn.execute("""
                SELECT date, status, retry_after FROM daily_archive_jobs
                WHERE (date >= ? AND date < ?) OR status = 'snapshot'
                UNION ALL
                SELECT DISTINCT s.date, 'legacy', 0 FROM daily_summaries AS s
                WHERE s.date >= ? AND s.date < ?
                  AND NOT EXISTS (SELECT 1 FROM daily_archive_jobs AS j WHERE j.date = s.date)
            """, (start, end, start, end)).fetchall()
        return {row["date"]: dict(row) for row in rows}

    def save_daily_archive_job(self, day: str, status: str, retry_after: float = 0) -> None:
        with closing(self._get_connection()) as conn, conn:
            conn.execute("""
                INSERT INTO daily_archive_jobs (date, status, retry_after) VALUES (?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET status = excluded.status, retry_after = excluded.retry_after
                WHERE daily_archive_jobs.status = 'retry'
            """, (day, status, retry_after))

    def complete_daily_archive(self, day: str, summary: str) -> bool:
        with closing(self._get_connection()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM daily_summaries WHERE date = ?", (day,)).fetchone():
                return False
            conn.execute("INSERT INTO daily_summaries (date, summary) VALUES (?, ?)", (day, summary))
            conn.execute("""
                INSERT INTO daily_archive_jobs (date, status) VALUES (?, 'snapshot')
                ON CONFLICT(date) DO UPDATE SET status = 'snapshot', retry_after = 0
            """, (day,))
        return True

    def update_daily_snapshot(self, days: List[str], snapshot: Optional[str], retry_after: float = 0) -> None:
        with closing(self._get_connection()) as conn, conn:
            if snapshot:
                conn.execute("""
                    INSERT INTO profiles (category, key, value) VALUES ('memory', 'short_term_snapshot', ?)
                    ON CONFLICT(key) DO UPDATE SET category = excluded.category,
                        value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """, (snapshot,))
            conn.executemany("""
                UPDATE daily_archive_jobs SET status = ?, retry_after = ?
                WHERE date = ? AND status = 'snapshot'
            """, [("done" if snapshot else "snapshot", retry_after, day) for day in days])

    def list_daily_summaries(self, limit: int = 30) -> List[Dict[str, Any]]:
        """列出每日总结"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, date, summary, created_at
                    FROM daily_summaries
                    ORDER BY date DESC
                    LIMIT ?
                """, (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"列出每日总结失败: {e}")
            return []

    def update_system_core(self, content: str) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # 清空旧记录，插入新的
                cursor.execute("DELETE FROM system_core")
                cursor.execute(
                    "INSERT INTO system_core (content) VALUES (?)", (content,)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"更新system core失败: {e}")
            return False

    def get_all_profiles(self) -> List[Dict[str, Any]]:
        """获取所有profiles"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT key, value FROM profiles ORDER BY key")
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取profiles失败: {e}")
            return []

    def get_profile(self, key: str) -> Optional[str]:
        """按key获取单条profile的value"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM profiles WHERE key = ?", (key,))
                row = cursor.fetchone()
                return row['value'] if row else None
        except Exception as e:
            logger.error(f"获取profile失败: {e}")
            return None

    def upsert_profile(self, key: str, value: str, category: str = 'general') -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO profiles (category, key, value) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        category = excluded.category,
                        value = excluded.value,
                        updated_at = CURRENT_TIMESTAMP
                """, (category, key, value))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"更新profile失败: {e}")
            return False

    def delete_profile(self, key: str) -> bool:
        """删除profile"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM profiles WHERE key = ?", (key,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除profile失败: {e}")
            return False

    def search_profiles(self, keywords: List[str], limit: int = 3) -> List[Dict[str, Any]]:
        """
        只根据关键词模糊搜索 profile key。
        value 是召回后的注入内容，不参与检索，避免正文偶然命中造成噪音。
        short_term_snapshot 由近期状态链路单独注入，不作为动态 profile 返回。
        """
        if not keywords:
            return []

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                results = []

                for kw in keywords:
                    kw_lower = kw.lower()

                    # Profile 的 key 才是检索标签；value 不参与召回。
                    cursor.execute(
                        """
                        SELECT key, value
                        FROM profiles
                        WHERE key != 'short_term_snapshot'
                          AND LOWER(key) LIKE ?
                        ORDER BY key ASC
                        """,
                        (f"%{kw_lower}%",)
                    )
                    for row in cursor.fetchall():
                        results.append({
                            "key": row["key"],
                            "value": row["value"],
                            "match_type": "key"
                        })

                # 同一个 key 可能命中多个关键词，只保留第一次出现的位置。
                seen = set()
                deduped = []
                for r in results:
                    if r["key"] not in seen:
                        seen.add(r["key"])
                        deduped.append(r)

                # 返回前 limit 条
                return deduped[:limit]

        except Exception as e:
            logger.error(f"搜索profiles失败: {e}")
            return []

    # ========== 岚的画像 ==========

    def get_portrait_sections(
        self,
        raise_on_error: bool = False
    ) -> List[Dict[str, Any]]:
        """按固定顺序读取完整画像；注入链路可选择感知读取失败。"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT section_key, section_name, section_group, sort_order,
                           content, char_limit, updated_at
                    FROM lan_portrait
                    ORDER BY sort_order ASC
                """)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"读取岚的画像失败: {e}")
            if raise_on_error:
                raise
            return []

    def replace_portrait_sections(self, sections: Dict[str, str]) -> bool:
        """Admin 手动导入或修正画像，不写入 Mav 自主更新日志。"""
        if not isinstance(sections, dict):
            return False
        invalid_keys = set(sections) - set(PORTRAIT_BY_KEY)
        if invalid_keys:
            logger.error(f"画像手动保存包含非法section: {sorted(invalid_keys)}")
            return False

        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                for section in PORTRAIT_SECTIONS:
                    key = section["key"]
                    if key not in sections:
                        continue
                    content = str(sections[key] or "").strip()
                    cursor.execute("""
                        UPDATE lan_portrait
                        SET content = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE section_key = ?
                    """, (content, key))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"手动保存岚的画像失败: {e}")
            return False

    def apply_portrait_update(self, section_key: str, action: str, content: str) -> Dict[str, Any]:
        """原子应用一条 Mav 画像更新，并仅记录非正文元数据。"""
        if section_key not in PORTRAIT_BY_KEY or action not in {"revise", "append"}:
            return {"updated": False, "reason": "invalid_request"}

        content = str(content or "").strip()
        if not content:
            return {"updated": False, "reason": "empty_content"}

        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute("""
                    SELECT section_name, content, char_limit
                    FROM lan_portrait
                    WHERE section_key = ?
                """, (section_key,))
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return {"updated": False, "reason": "missing_section"}

                old_content = str(row["content"] or "").strip()
                if action == "revise":
                    new_content = content
                elif old_content and (old_content == content or old_content.endswith("\n" + content)):
                    conn.rollback()
                    return {"updated": False, "reason": "duplicate"}
                else:
                    new_content = f"{old_content}\n{content}" if old_content else content

                if new_content == old_content:
                    conn.rollback()
                    return {"updated": False, "reason": "unchanged"}

                char_count = len(new_content)
                over_limit = char_count > int(row["char_limit"] or 150)
                cursor.execute("""
                    UPDATE lan_portrait
                    SET content = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE section_key = ?
                """, (new_content, section_key))
                cursor.execute("""
                    INSERT INTO lan_portrait_update_logs (
                        section_key, section_name, action, char_count, over_limit
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    section_key, row["section_name"], action, char_count, int(over_limit)
                ))
                conn.commit()
                return {
                    "updated": True,
                    "section_key": section_key,
                    "section_name": row["section_name"],
                    "action": action,
                    "char_count": char_count,
                    "over_limit": over_limit
                }
        except Exception as e:
            logger.error(f"应用Mav画像更新失败 section={section_key} action={action}: {e}")
            return {"updated": False, "reason": "database_error"}

    def list_portrait_update_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """读取画像更新元数据；日志不包含画像正文。"""
        try:
            safe_limit = max(1, min(int(limit), 500))
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, section_key, section_name, action,
                           char_count, over_limit, created_at
                    FROM lan_portrait_update_logs
                    ORDER BY id DESC
                    LIMIT ?
                """, (safe_limit,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"读取画像更新日志失败: {e}")
            return []

    def get_portrait_status(self) -> Dict[str, Any]:
        """返回不含正文的画像运行状态。"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*) AS section_count,
                           SUM(CASE WHEN TRIM(content) != '' THEN 1 ELSE 0 END) AS populated_count,
                           MAX(updated_at) AS last_content_update
                    FROM lan_portrait
                """)
                status = dict(cursor.fetchone())
                cursor.execute("SELECT MAX(created_at) AS last_mav_update FROM lan_portrait_update_logs")
                status.update(dict(cursor.fetchone()))
                return status
        except Exception as e:
            logger.error(f"读取画像状态失败: {e}")
            return {"section_count": 0, "populated_count": 0}

    def add_note(self, content: str) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO notes (author, content) VALUES ('Mav', ?)", (content,)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"添加备忘失败: {e}")
            return False

    def delete_note(self, note_id: int) -> bool:
        """删除Mav备忘"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM notes WHERE id = ?", (note_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除备忘失败: {e}")
            return False

    # ========== 对话 CRUD ==========

    def get_instant_summary_state(self) -> Dict[str, Any]:
        """读取全局即时摘要检查点；表中始终至多一行。"""
        try:
            with self._get_connection() as conn:
                row = conn.execute("""
                    SELECT summary, rounds_since_summary, last_summary_time,
                           summary_failure_count, summary_retry_after, updated_at
                    FROM instant_summary_state
                    WHERE id = 1
                """).fetchone()
                return dict(row) if row else {}
        except Exception as e:
            logger.error(f"读取即时摘要状态失败: {e}")
            return {}

    def save_instant_summary_state(
        self,
        summary: str,
        rounds_since_summary: int,
        last_summary_time: float,
        summary_failure_count: int = 0,
        summary_retry_after: float = 0,
    ) -> bool:
        """覆盖保存全局即时摘要及其计数器，不保留历史版本。"""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO instant_summary_state (
                        id, summary, rounds_since_summary, last_summary_time,
                        summary_failure_count, summary_retry_after, updated_at
                    ) VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        summary = excluded.summary,
                        rounds_since_summary = excluded.rounds_since_summary,
                        last_summary_time = excluded.last_summary_time,
                        summary_failure_count = excluded.summary_failure_count,
                        summary_retry_after = excluded.summary_retry_after,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    summary or "",
                    max(0, int(rounds_since_summary or 0)),
                    float(last_summary_time or 0),
                    max(0, int(summary_failure_count or 0)),
                    max(0, float(summary_retry_after or 0)),
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"保存即时摘要状态失败: {e}")
            return False

    def clear_instant_summary_state(self) -> bool:
        """显式重置服务状态时清除持久化的即时摘要。"""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM instant_summary_state WHERE id = 1")
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"清除即时摘要状态失败: {e}")
            return False

    def list_recent_completed_conversation_rounds(self, limit: int = 30) -> List[Dict[str, Any]]:
        """按全局时间线恢复最近的完整用户/助手轮次，忽略尚未回复的尾部用户消息。"""
        try:
            scan_limit = max(20, int(limit or 30) * 2 + 20)
            with self._get_connection() as conn:
                rows = conn.execute("""
                    SELECT id, conversation_id, role, content, created_at, rowid
                    FROM messages
                    WHERE role IN ('user', 'assistant')
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                """, (scan_limit,)).fetchall()

            pending_users: Dict[str, Dict[str, Any]] = {}
            completed = []
            for row in reversed(rows):
                item = dict(row)
                conversation_id = str(item.get("conversation_id") or "")
                if item.get("role") == "user":
                    pending_users[conversation_id] = item
                    continue
                user = pending_users.pop(conversation_id, None)
                if not user:
                    continue
                completed.append({
                    "user": user.get("content") or "",
                    "assistant": item.get("content") or "",
                    "timestamp": item.get("created_at") or "",
                })
            return completed[-max(1, int(limit or 30)):]
        except Exception as e:
            logger.error(f"恢复即时摘要近期轮次失败: {e}")
            return []

    def list_conversations(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """
        列出所有对话

        Args:
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            dict: 包含 conversations 列表和 total 总数
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 获取总数
                cursor.execute("SELECT COUNT(*) as count FROM conversations")
                total = cursor.fetchone()['count']

                # 获取对话列表
                cursor.execute("""
                    SELECT id, title, created_at, updated_at, is_folded
                    FROM conversations
                    ORDER BY updated_at DESC, created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                """, (limit, offset))
                conversations = [dict(row) for row in cursor.fetchall()]

                # 获取每条对话的最后一条消息预览
                for conv in conversations:
                    cursor.execute("""
                        SELECT content FROM messages
                        WHERE conversation_id = ?
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (conv['id'],))
                    last_msg = cursor.fetchone()
                    if last_msg:
                        conv['last_message_preview'] = last_msg['content'][:80]
                    else:
                        conv['last_message_preview'] = ''

                return {'conversations': conversations, 'total': total}
        except Exception as e:
            logger.error(f"列出对话失败: {e}")
            return {'conversations': [], 'total': 0}

    def create_conversation(self, id: str, title: str = '新对话') -> bool:
        """
        创建对话

        Args:
            id: 对话ID（前端生成的UUID）
            title: 对话标题

        Returns:
            bool: 是否创建成功
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO conversations (id, title)
                    VALUES (?, ?)
                """, (id, title))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"创建对话失败: {e}")
            return False

    def update_conversation(self, id: str, title: str) -> bool:
        """
        更新对话标题

        Args:
            id: 对话ID
            title: 新标题

        Returns:
            bool: 是否更新成功
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE conversations
                    SET title = ?
                    WHERE id = ?
                """, (title, id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"更新对话失败: {e}")
            return False

    def set_conversation_folded(self, id: str, folded: bool) -> bool:
        """保存历史折叠归属，不改变对话活跃时间。"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE conversations SET is_folded = ? WHERE id = ?", (int(folded), id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"更新对话折叠状态失败: {e}")
            return False

    def delete_conversation(self, id: str) -> bool:
        """
        删除对话（级联删除消息）

        Args:
            id: 对话ID

        Returns:
            bool: 是否删除成功
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # 先删除消息（虽然有外键级联，但显式删除更清晰）
                cursor.execute("DELETE FROM messages WHERE conversation_id = ?", (id,))
                # 再删除对话
                cursor.execute("DELETE FROM conversations WHERE id = ?", (id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除对话失败: {e}")
            return False

    def get_conversation(self, id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个对话

        Args:
            id: 对话ID

        Returns:
            dict: 对话数据，不存在返回None
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, title, created_at, updated_at, is_folded
                    FROM conversations
                    WHERE id = ?
                """, (id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"获取对话失败: {e}")
            return None

    # ========== 消息 CRUD ==========

    def list_messages(
        self,
        conversation_id: str,
        limit: int = 50,
        offset: int = 0,
        order: str = "asc"
    ) -> Dict[str, Any]:
        """
        获取对话的所有消息

        Args:
            conversation_id: 对话ID
            limit: 返回数量限制
            offset: 偏移量
            order: 排序方向（asc/desc）

        Returns:
            dict: 包含 messages 列表和 total 总数
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 获取总数
                cursor.execute(
                    "SELECT COUNT(*) as count FROM messages WHERE conversation_id = ?",
                    (conversation_id,)
                )
                total = cursor.fetchone()['count']

                # 排序方向只允许由这里生成，避免把外部参数直接拼进 SQL。
                direction = "DESC" if str(order).strip().lower().replace("_", " ").endswith("desc") else "ASC"

                # rowid 作为同秒消息的稳定次级排序，确保分页不会重复或漏项。
                cursor.execute(f"""
                    SELECT id, role, content, thinking, model, created_at, image_preview, file_name,
                           input_tokens, output_tokens, total_tokens, cache_creation_tokens,
                           cache_read_tokens, cache_creation_1h_tokens
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at {direction}, rowid {direction}
                    LIMIT ? OFFSET ?
                """, (conversation_id, limit, offset))
                messages = [dict(row) for row in cursor.fetchall()]

                return {'messages': messages, 'total': total}
        except Exception as e:
            logger.error(f"列出消息失败: {e}")
            return {'messages': [], 'total': 0}

    def list_message_map(self, conversation_id: str) -> Dict[str, Any]:
        """返回对话地图所需的全量轻量消息，不附带图片与token统计。"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, role, content, thinking, created_at
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (conversation_id,),
                )
                messages = [dict(row) for row in cursor.fetchall()]
                return {"messages": messages, "total": len(messages)}
        except Exception as e:
            logger.error(f"加载对话地图失败: {e}")
            return {"messages": [], "total": 0}

    def add_message(
        self,
        id: str,
        conversation_id: str,
        role: str,
        content: str,
        thinking: str = None,
        model: str = None,
        image_preview: str = None,
        file_name: str = None,
        input_tokens: int = None,
        output_tokens: int = None,
        total_tokens: int = None,
        cache_creation_tokens: int = None,
        cache_read_tokens: int = None,
        cache_creation_1h_tokens: int = None,
        message_images: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """
        保存单条消息

        Args:
            id: 消息ID（前端生成的UUID）
            conversation_id: 对话ID
            role: 角色（user/assistant/system）
            content: 消息内容
            thinking: 思考过程（可选）
            model: 模型名称（可选）
            image_preview: 图片预览（可选）
            file_name: 文件名（可选）

        Returns:
            bool: 是否保存成功
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO messages (
                        id, conversation_id, role, content, thinking, model,
                        image_preview, file_name, input_tokens, output_tokens, total_tokens,
                        cache_creation_tokens, cache_read_tokens, cache_creation_1h_tokens
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    id, conversation_id, role, content, thinking, model,
                    image_preview, file_name, input_tokens, output_tokens, total_tokens,
                    cache_creation_tokens, cache_read_tokens, cache_creation_1h_tokens
                ))
                message_inserted = cursor.rowcount > 0

                # 图片记录和消息共用同一事务。其他设备只会看到“全部完成”或“完全没有”，
                # 不会读到消息已经出现、图片还未关联的中间状态。
                for image in message_images or []:
                    cursor.execute(
                        """
                        INSERT INTO message_images
                        (id, message_id, sort_order, file_path, thumb_path, mime_type, width, height)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            image["id"],
                            id,
                            image.get("sort_order", 0),
                            image["file_path"],
                            image["thumb_path"],
                            image.get("mime_type", "image/jpeg"),
                            image.get("width"),
                            image.get("height"),
                        ),
                    )

                if role == "assistant" and (total_tokens or input_tokens or output_tokens):
                    self.add_token_usage_event(
                        cursor=cursor,
                        source="chat",
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        conversation_id=conversation_id,
                        message_id=id,
                        metadata={
                            "cache_creation_tokens": int(cache_creation_tokens or 0),
                            "cache_read_tokens": int(cache_read_tokens or 0),
                            "cache_creation_1h_tokens": int(cache_creation_1h_tokens or 0)
                        }
                    )

                # 更新对话的 updated_at
                cursor.execute("""
                    UPDATE conversations
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (conversation_id,))

                conn.commit()
                return message_inserted
        except Exception as e:
            logger.error(f"保存消息失败: {e}")
            return False

    def add_token_usage_event(
        self,
        source: str,
        model: str = None,
        input_tokens: int = None,
        output_tokens: int = None,
        total_tokens: int = None,
        conversation_id: str = None,
        message_id: str = None,
        metadata: Dict[str, Any] = None,
        cursor: sqlite3.Cursor = None
    ) -> bool:
        """璁板綍涓€绗旀ā鍨媡oken娑堣€楁祦姘达紝渚涘悗缁璽oken bank浣跨敤銆?"""
        input_value = int(input_tokens or 0)
        output_value = int(output_tokens or 0)
        total_value = int(total_tokens or (input_value + output_value))
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

        try:
            if cursor is not None:
                cursor.execute("""
                    INSERT INTO token_usage_events (
                        source, model, input_tokens, output_tokens, total_tokens,
                        conversation_id, message_id, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    source, model, input_value, output_value, total_value,
                    conversation_id, message_id, metadata_json
                ))
                return True

            with self._get_connection() as conn:
                cur = conn.cursor()
                self.add_token_usage_event(
                    cursor=cur,
                    source=source,
                    model=model,
                    input_tokens=input_value,
                    output_tokens=output_value,
                    total_tokens=total_value,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    metadata=metadata
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"璁板綍token娑堣€楁祦姘村け璐? {e}")
            return False

    def add_messages_batch(
        self,
        conversation_id: str,
        messages: List[Dict[str, Any]]
    ) -> bool:
        """
        批量保存消息

        Args:
            conversation_id: 对话ID
            messages: 消息列表

        Returns:
            bool: 是否保存成功
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                for msg in messages:
                    cursor.execute("""
                        INSERT INTO messages (
                            id, conversation_id, role, content, thinking, model,
                            image_preview, file_name, input_tokens, output_tokens, total_tokens,
                            cache_creation_tokens, cache_read_tokens, cache_creation_1h_tokens
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        msg.get('id'),
                        conversation_id,
                        msg.get('role'),
                        msg.get('content'),
                        msg.get('thinking'),
                        msg.get('model'),
                        msg.get('imagePreview') or msg.get('image_preview'),
                        msg.get('fileName') or msg.get('file_name'),
                        msg.get('inputTokens') or msg.get('input_tokens'),
                        msg.get('outputTokens') or msg.get('output_tokens'),
                        msg.get('totalTokens') or msg.get('total_tokens'),
                        msg.get('cacheCreationTokens') or msg.get('cache_creation_tokens'),
                        msg.get('cacheReadTokens') or msg.get('cache_read_tokens'),
                        msg.get('cacheCreation1hTokens') or msg.get('cache_creation_1h_tokens')
                    ))

                # 更新对话的 updated_at
                cursor.execute("""
                    UPDATE conversations
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (conversation_id,))

                conn.commit()
                return True
        except Exception as e:
            logger.error(f"批量保存消息失败: {e}")
            return False

    def delete_message(self, conversation_id: str, message_id: str) -> bool:
        """
        删除消息

        Args:
            conversation_id: 对话ID
            message_id: 消息ID

        Returns:
            bool: 是否删除成功
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    DELETE FROM messages
                    WHERE id = ? AND conversation_id = ?
                """, (message_id, conversation_id))

                # 更新对话的 updated_at
                cursor.execute("""
                    UPDATE conversations
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (conversation_id,))

                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"删除消息失败: {e}")
            return False

    def delete_messages_after(self, conversation_id: str, message_id: str, include_anchor: bool = False) -> bool:
        """删除某条消息之后的所有消息；可选择是否同时删除这条锚点消息。"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT created_at FROM messages
                    WHERE id = ? AND conversation_id = ?
                """, (message_id, conversation_id))
                row = cursor.fetchone()
                if not row:
                    return False

                operator = ">=" if include_anchor else ">"
                cursor.execute(f"""
                    DELETE FROM messages
                    WHERE conversation_id = ? AND created_at {operator} ?
                """, (conversation_id, row["created_at"]))

                cursor.execute("""
                    UPDATE conversations
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (conversation_id,))

                conn.commit()
                return True
        except Exception as e:
            logger.error(f"删除后续消息失败: {e}")
            return False

    # ========== 阅读模块 CRUD ==========

    def add_book(self, title, author, cover_image, cover_mime, total_chapters) -> Optional[int]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO books (title, author, cover_image, cover_mime, total_chapters)
                    VALUES (?, ?, ?, ?, ?)
                """, (title, author, cover_image, cover_mime, total_chapters))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"添加书籍失败: {e}")
            return None

    def get_book(self, book_id: int) -> Optional[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM books WHERE id = ?", (book_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"获取书籍失败: {e}")
            return None

    def list_books(self) -> List[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM books ORDER BY created_at DESC")
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"列出书籍失败: {e}")
            return []

    def update_book_status(self, book_id: int, status: str, processed_chapters: int = None):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if processed_chapters is not None:
                    cursor.execute(
                        "UPDATE books SET status=?, processed_chapters=? WHERE id=?",
                        (status, processed_chapters, book_id)
                    )
                else:
                    cursor.execute(
                        "UPDATE books SET status=? WHERE id=?",
                        (status, book_id)
                    )
                conn.commit()
        except Exception as e:
            logger.error(f"更新书籍状态失败: {e}")

    def update_book_progress(self, book_id: int, last_chapter: int, last_position: int):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE books SET last_chapter=?, last_position=? WHERE id=?",
                    (last_chapter, last_position, book_id)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"更新阅读进度失败: {e}")

    def delete_book(self, book_id: int):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM books WHERE id=?", (book_id,))
                conn.commit()
        except Exception as e:
            logger.error(f"删除书籍失败: {e}")

    def add_chapter(self, book_id, chapter_index, title, content_text, content_images):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO book_chapters (book_id, chapter_index, title, content_text, content_images)
                    VALUES (?, ?, ?, ?, ?)
                """, (book_id, chapter_index, title, content_text, content_images))
                conn.commit()
        except Exception as e:
            logger.error(f"添加章节失败: {e}")

    def get_chapter(self, book_id: int, chapter_index: int) -> Optional[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM book_chapters WHERE book_id=? AND chapter_index=?",
                    (book_id, chapter_index)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"获取章节失败: {e}")
            return None

    def list_chapters(self, book_id: int) -> List[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM book_chapters WHERE book_id=? ORDER BY chapter_index ASC",
                    (book_id,)
                )
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"列出章节失败: {e}")
            return []

    def save_processed_reading_chapter(
        self, book_id: int, chapter_index: int, summary: str, annotations: List[Dict[str, Any]]
    ) -> None:
        """Commit the whole chapter and its checkpoint together so retrying cannot leave partial work."""
        with closing(self._get_connection()) as conn, conn:
            for annotation in annotations:
                cursor = conn.execute("""
                    INSERT INTO annotations
                    (book_id, chapter_index, start_char, end_char, quote_text, author, content)
                    VALUES (?, ?, ?, ?, ?, 'mav', ?)
                """, (book_id, chapter_index, annotation["start_char"], annotation["end_char"],
                      annotation["quote_text"], annotation["content"]))
                conn.execute("UPDATE annotations SET root_id=? WHERE id=?", (cursor.lastrowid, cursor.lastrowid))
            conn.execute("UPDATE book_chapters SET summary=? WHERE book_id=? AND chapter_index=?",
                         (summary, book_id, chapter_index))
            conn.execute("UPDATE books SET processed_chapters=MAX(COALESCE(processed_chapters, 0), ?) WHERE id=?",
                         (chapter_index + 1, book_id))

    def update_chapter_summary(self, book_id: int, chapter_index: int, summary: str):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE book_chapters SET summary=? WHERE book_id=? AND chapter_index=?",
                    (summary, book_id, chapter_index)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"更新章节摘要失败: {e}")

    def add_annotation(self, book_id, chapter_index, start_char, end_char,
                       quote_text, author, content, parent_id, root_id) -> Optional[int]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO annotations
                    (book_id, chapter_index, start_char, end_char, quote_text, author, content, parent_id, root_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (book_id, chapter_index, start_char, end_char,
                      quote_text, author, content, parent_id, root_id))
                conn.commit()
                new_id = cursor.lastrowid
                if root_id is None and parent_id is None:
                    cursor.execute("UPDATE annotations SET root_id=? WHERE id=?", (new_id, new_id))
                    conn.commit()
                return new_id
        except Exception as e:
            logger.error(f"添加批注失败: {e}")
            return None

    def get_annotation(self, annotation_id: int) -> Optional[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"获取批注失败: {e}")
            return None

    def get_annotations_for_chapter(self, book_id: int, chapter_index: int, author: str = None) -> List[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if author:
                    cursor.execute("""
                        SELECT * FROM annotations
                        WHERE book_id=? AND chapter_index=? AND author=? AND parent_id IS NULL
                        ORDER BY start_char ASC
                    """, (book_id, chapter_index, author))
                else:
                    cursor.execute("""
                        SELECT * FROM annotations
                        WHERE book_id=? AND chapter_index=?
                        ORDER BY created_at ASC
                    """, (book_id, chapter_index))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取章节批注失败: {e}")
            return []

    def get_annotation_thread(self, root_id: int) -> List[Dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM annotations WHERE root_id=? ORDER BY created_at ASC",
                    (root_id,)
                )
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取批注线程失败: {e}")
            return []


# 全局数据库实例
db = Database()
