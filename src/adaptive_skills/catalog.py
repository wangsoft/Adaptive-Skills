from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .config import Settings
from .database import Database, json_value, utc_now
from .errors import NotFoundError, ValidationError
from .scanner import CatalogScanner


LATIN_TERM = re.compile(r"[a-z0-9][a-z0-9_+.#/-]{1,}", re.I)
CJK_SEQUENCE = re.compile(r"[\u3400-\u9fff]{2,}")
FIELD_WEIGHTS = {
    "name": 8.0,
    "description": 5.0,
    "problem": 6.0,
    "use_case": 6.0,
    "categories": 4.0,
    "tags": 4.0,
    "notes": 2.5,
    "body": 1.0,
}
RISK_PENALTY = {"none": 0.0, "low": 0.2, "medium": 1.5, "high": 5.0, "critical": 20.0}


def query_terms(query: str) -> list[str]:
    normalized = query.casefold().strip()
    terms = LATIN_TERM.findall(normalized)
    for sequence in CJK_SEQUENCE.findall(normalized):
        terms.append(sequence)
        if len(sequence) > 2:
            terms.extend(
                sequence[index : index + 2] for index in range(len(sequence) - 1)
            )
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))


class Catalog:
    def __init__(self, settings: Settings, database: Database | None = None):
        self.settings = settings
        self.database = database or Database(settings)

    def get_skill(self, skill_id: str, *, active_only: bool = True) -> dict[str, Any]:
        where = "s.active = 1 AND " if active_only else ""
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""
                SELECT s.*, src.name AS source_name, src.url AS source_url,
                       src.local_path AS source_path, src.tracked_ref, src.head_sha,
                       a.category_l1, a.category_l2, a.problem, a.use_case,
                       a.score, a.score_source, a.notes, a.tags_json, a.review_status,
                       a.content_hash AS annotation_content_hash
                FROM skills s
                JOIN sources src ON src.id = s.source_id
                LEFT JOIN annotations a ON a.skill_id = s.id
                WHERE {where}(s.id = ? OR (s.name = ? AND s.active = 1))
                ORDER BY s.id LIMIT 2
                """,
                (skill_id, skill_id),
            ).fetchall()
        if not row:
            raise NotFoundError(f"Unknown skill: {skill_id}")
        if len(row) > 1 and all(item["id"] != skill_id for item in row):
            raise ValidationError(
                f"Skill name is ambiguous; use its stable ID: {skill_id}"
            )
        result = dict(next((item for item in row if item["id"] == skill_id), row[0]))
        return self._decode(result)

    def list_skills(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        clause = "" if include_inactive else "WHERE s.active = 1"
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT s.*, src.name AS source_name, src.url AS source_url,
                       src.local_path AS source_path, src.tracked_ref, src.head_sha,
                       a.category_l1, a.category_l2, a.problem, a.use_case,
                       a.score, a.score_source, a.notes, a.tags_json, a.review_status,
                       a.content_hash AS annotation_content_hash
                FROM skills s JOIN sources src ON src.id = s.source_id
                LEFT JOIN annotations a ON a.skill_id = s.id
                {clause}
                ORDER BY s.name, src.name, s.rel_path
                """
            ).fetchall()
        return [self._decode(dict(row)) for row in rows]

    def search(
        self,
        requirement: str,
        *,
        limit: int = 10,
        include_invalid: bool = False,
        allow_risk: bool = False,
    ) -> list[dict[str, Any]]:
        if not requirement.strip():
            raise ValidationError("Search requirement cannot be empty")
        if limit < 1 or limit > 100:
            raise ValidationError("Search limit must be between 1 and 100")
        terms = query_terms(requirement)
        if not terms:
            raise ValidationError(
                "Search requirement must contain words or Chinese text"
            )

        fts_ranks = self._fts_ranks(requirement, terms)
        candidates = self.list_skills()
        results: list[dict[str, Any]] = []
        full_query = requirement.casefold().strip()
        for skill in candidates:
            if not include_invalid and not skill["valid"]:
                continue
            if not allow_risk and skill["audit_severity"] in {"high", "critical"}:
                continue
            fields = {
                "name": skill["name"],
                "description": skill["description"],
                "problem": skill.get("problem"),
                "use_case": skill.get("use_case"),
                "categories": " ".join(
                    filter(None, [skill.get("category_l1"), skill.get("category_l2")])
                ),
                "tags": " ".join(skill.get("tags", [])),
                "notes": skill.get("notes"),
                "body": skill.get("body"),
            }
            score = 0.0
            matches: list[dict[str, Any]] = []
            for field, raw_value in fields.items():
                value = str(raw_value or "").casefold()
                matched = [term for term in terms if term in value]
                if not matched:
                    continue
                weight = FIELD_WEIGHTS[field]
                contribution = weight * sum(
                    1.0 if len(term) > 2 else 0.55 for term in matched
                )
                if full_query and full_query in value:
                    contribution += weight * 2.0
                score += contribution
                matches.append(
                    {
                        "field": field,
                        "terms": matched[:8],
                        "contribution": round(contribution, 2),
                    }
                )
            if score <= 0:
                continue
            if skill["id"] in fts_ranks:
                fts_boost = round(1.0 / (1.0 + max(fts_ranks[skill["id"]], 0.0)), 2)
                score += fts_boost
                matches.append(
                    {"field": "fts5", "terms": [], "contribution": fts_boost}
                )
            annotation_score = skill.get("score")
            if annotation_score is not None:
                score += min(max(float(annotation_score), 0.0), 100.0) / 20.0
            score -= RISK_PENALTY.get(skill["audit_severity"], 5.0)
            results.append(
                {
                    "id": skill["id"],
                    "name": skill["name"],
                    "description": skill["description"],
                    "source": skill["source_name"],
                    "rel_path": skill["rel_path"],
                    "valid": skill["valid"],
                    "audit_severity": skill["audit_severity"],
                    "score": round(score, 2),
                    "annotation_score": annotation_score,
                    "reason": matches,
                }
            )
        results.sort(key=lambda item: (-item["score"], item["name"], item["id"]))
        return results[:limit]

    def _fts_ranks(self, requirement: str, terms: list[str]) -> dict[str, float]:
        full_cjk = CJK_SEQUENCE.findall(requirement.casefold())
        latin = [term for term in terms if not CJK_SEQUENCE.fullmatch(term)]
        fts_terms = list(dict.fromkeys(latin + full_cjk))[:24]
        if not fts_terms:
            return {}
        expression = " OR ".join(
            f'"{term.replace(chr(34), chr(34) * 2)}"' for term in fts_terms
        )
        try:
            with self.database.transaction() as connection:
                rows = connection.execute(
                    "SELECT skill_id, bm25(skill_fts) AS rank FROM skill_fts WHERE skill_fts MATCH ? LIMIT 500",
                    (expression,),
                ).fetchall()
        except sqlite3.OperationalError:
            # Lexical substring ranking remains deterministic if a local SQLite build
            # has reduced FTS query support.
            return {}
        return {row["skill_id"]: float(row["rank"]) for row in rows}

    def annotate(self, skill_id: str, **values: Any) -> dict[str, Any]:
        skill = self.get_skill(skill_id)
        allowed = {
            "category_l1",
            "category_l2",
            "problem",
            "use_case",
            "score",
            "score_source",
            "notes",
            "tags",
            "review_status",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValidationError(
                f"Unknown annotation fields: {', '.join(sorted(unknown))}"
            )
        current = {key: skill.get(key) for key in allowed}
        current.update(values)
        score = current.get("score")
        if score is not None:
            try:
                score = float(score)
            except (TypeError, ValueError) as exc:
                raise ValidationError("Score must be a number from 0 to 10") from exc
            if not 0.0 <= score <= 10.0:
                raise ValidationError("Score must be between 0 and 10")
            current["score"] = score
        tags = current.pop("tags", []) or []
        if isinstance(tags, str):
            tags = [part.strip() for part in tags.split(",") if part.strip()]
        if not isinstance(tags, list):
            raise ValidationError("tags must be a list or comma-delimited string")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO annotations(
                    skill_id, category_l1, category_l2, problem, use_case,
                    score, score_source, notes, tags_json, review_status,
                    content_hash, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_id) DO UPDATE SET
                    category_l1=excluded.category_l1,
                    category_l2=excluded.category_l2,
                    problem=excluded.problem,
                    use_case=excluded.use_case,
                    score=excluded.score,
                    score_source=excluded.score_source,
                    notes=excluded.notes,
                    tags_json=excluded.tags_json,
                    review_status=excluded.review_status,
                    content_hash=excluded.content_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    skill["id"],
                    current["category_l1"],
                    current["category_l2"],
                    current["problem"],
                    current["use_case"],
                    current["score"],
                    current["score_source"],
                    current["notes"],
                    json.dumps(tags, ensure_ascii=False),
                    current["review_status"],
                    skill["content_hash"],
                    now,
                ),
            )
            CatalogScanner._index_skill(connection, skill["id"])
        return self.get_skill(skill["id"])

    @staticmethod
    def _decode(skill: dict[str, Any]) -> dict[str, Any]:
        skill["valid"] = bool(skill.get("valid"))
        skill["active"] = bool(skill.get("active"))
        skill["metadata"] = json_value(skill.pop("metadata_json", None), {})
        skill["validation"] = json_value(skill.pop("validation_json", None), [])
        skill["audit"] = json_value(skill.pop("audit_json", None), [])
        skill["tags"] = json_value(skill.pop("tags_json", None), [])
        return skill
