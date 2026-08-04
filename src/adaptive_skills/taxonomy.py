from __future__ import annotations

from typing import Any

from .database import Database
from .errors import ValidationError


TAXONOMY_VERSION = "core-zh-v1"
CORE_L1 = (
    "营销增长与社媒",
    "前端与设计",
    "元技能与知识管理",
    "抓取自动化与工具",
    "书籍与出版",
    "需求规划与决策",
    "交付部署与安全",
    "图像与视频",
    "演示与文档",
    "编码测试与调试",
    "写作与内容",
    "思维与决策",
    "阅读与学习",
    "领域与平台专用",
    "数据与数据库",
)


class Taxonomy:
    """Versioned core categories with a governed library-local L2 vocabulary."""

    def __init__(self, database: Database):
        self.database = database

    def snapshot(self, *, minimum_l2_uses: int = 2) -> dict[str, Any]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT category_l1, category_l2, count(*) AS total
                FROM annotations
                WHERE category_l1 IS NOT NULL AND trim(category_l1) != ''
                  AND category_l2 IS NOT NULL AND trim(category_l2) != ''
                GROUP BY category_l1, category_l2
                HAVING count(*) >= ?
                ORDER BY category_l1, total DESC, category_l2
                """,
                (minimum_l2_uses,),
            ).fetchall()
        by_l1: dict[str, list[str]] = {category: [] for category in CORE_L1}
        for row in rows:
            if row["category_l1"] in by_l1:
                by_l1[row["category_l1"]].append(row["category_l2"])
        return {
            "version": TAXONOMY_VERSION,
            "level_one": list(CORE_L1),
            "level_two": by_l1,
            "policy": {
                "level_one": "fixed-versioned",
                "level_two": "reuse-or-propose",
                "tags": "free-form",
                "minimum_l2_uses": minimum_l2_uses,
            },
        }

    def validate(
        self, category_l1: str, category_l2: str, *, category_candidate: bool
    ) -> None:
        if category_l1 not in CORE_L1:
            raise ValidationError(f"Unknown core category: {category_l1}")
        category_l2 = category_l2.strip()
        if not category_l2 or len(category_l2) > 40:
            raise ValidationError("Level-two category must contain 1-40 characters")
        existing = set(self.snapshot()["level_two"].get(category_l1, []))
        if category_l2 not in existing and not category_candidate:
            raise ValidationError(
                "A new level-two category must be marked as a taxonomy candidate"
            )
