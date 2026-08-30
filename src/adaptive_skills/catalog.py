from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .config import Settings
from .database import Database, json_value, path_is_within, utc_now
from .errors import ConflictError, NotFoundError, ValidationError
from .scanner import CatalogScanner, decorate_audit_findings


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
            rows = connection.execute(
                f"""
                SELECT s.*, src.name AS source_name, src.url AS source_url,
                       src.local_path AS source_path, src.tracked_ref, src.head_sha,
                       src.status AS source_status, src.github_stars AS source_stars,
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
            if rows:
                selected = dict(
                    next((item for item in rows if item["id"] == skill_id), rows[0])
                )
                reviews = self._review_map(connection, [selected["id"]]).get(
                    selected["id"], {}
                )
            else:
                selected = None
                reviews = {}
        if not rows or selected is None:
            raise NotFoundError(f"Unknown skill: {skill_id}")
        if len(rows) > 1 and all(item["id"] != skill_id for item in rows):
            raise ValidationError(
                f"Skill name is ambiguous; use its stable ID: {skill_id}"
            )
        return self._decode(selected, reviews)

    def list_skills(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        clause = "" if include_inactive else "WHERE s.active = 1"
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT s.*, src.name AS source_name, src.url AS source_url,
                       src.local_path AS source_path, src.tracked_ref, src.head_sha,
                       src.status AS source_status, src.github_stars AS source_stars,
                       a.category_l1, a.category_l2, a.problem, a.use_case,
                       a.score, a.score_source, a.notes, a.tags_json, a.review_status,
                       a.content_hash AS annotation_content_hash
                FROM skills s JOIN sources src ON src.id = s.source_id
                LEFT JOIN annotations a ON a.skill_id = s.id
                {clause}
                ORDER BY s.name, src.name, s.rel_path
                """
            ).fetchall()
            reviews = self._review_map(connection, [row["id"] for row in rows])
        return [
            self._decode(dict(row), reviews.get(row["id"], {})) for row in rows
        ]

    def search(
        self,
        requirement: str,
        *,
        limit: int = 10,
        include_invalid: bool = False,
        allow_risk: bool = False,
        scope_root: str | Path | None = None,
        unique_names: bool = False,
        preferred_rel_prefixes: tuple[str, ...] = (),
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
        resolved_scope = (
            Path(scope_root).expanduser().resolve() if scope_root is not None else None
        )
        for skill in candidates:
            if not include_invalid and not skill["valid"]:
                continue
            if not allow_risk and skill["audit_severity"] in {"high", "critical"}:
                continue
            if resolved_scope is not None:
                source_root = Path(skill["source_path"])
                skill_root = source_root / skill["rel_path"]
                if not path_is_within(source_root, resolved_scope) or not path_is_within(
                    skill_root, resolved_scope
                ):
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
                    "source_name": skill["source_name"],
                    "source_url": skill.get("source_url"),
                    "source_stars": skill.get("source_stars"),
                    "rel_path": skill["rel_path"],
                    "valid": skill["valid"],
                    "audit_severity": skill["audit_severity"],
                    "format_issue_count": skill["format_issue_count"],
                    "capability_hint_count": skill["capability_hint_count"],
                    "unreviewed_risk_count": skill["unreviewed_risk_count"],
                    "confirmed_risk_count": skill["confirmed_risk_count"],
                    "false_positive_count": skill["false_positive_count"],
                    "score": round(score, 2),
                    "annotation_score": annotation_score,
                    "reason": matches,
                }
            )
        results.sort(key=lambda item: (-item["score"], item["name"], item["id"]))
        if unique_names:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for item in results:
                grouped.setdefault(item["name"].casefold(), []).append(item)
            results = []
            for variants in grouped.values():
                winner = min(
                    variants,
                    key=lambda item: (
                        self._rel_path_preference(
                            item["rel_path"], preferred_rel_prefixes
                        ),
                        -item["score"],
                        -(item["annotation_score"] or 0.0),
                        item["rel_path"].casefold(),
                        item["id"],
                    ),
                ).copy()
                winner["variant_count"] = len(variants)
                results.append(winner)
            results.sort(
                key=lambda item: (-item["score"], item["name"], item["id"])
            )
        return results[:limit]

    @staticmethod
    def _rel_path_preference(
        rel_path: str, preferred_rel_prefixes: tuple[str, ...]
    ) -> int:
        normalized = rel_path.strip("/").casefold()
        for index, raw_prefix in enumerate(preferred_rel_prefixes):
            prefix = raw_prefix.strip("/").casefold()
            if normalized == prefix or normalized.startswith(f"{prefix}/"):
                return index
        return len(preferred_rel_prefixes)

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

    def review_audit_finding(
        self,
        skill_id: str,
        finding_id: str,
        *,
        status: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"reviewed_false_positive", "confirmed_risk"}:
            raise ValidationError(
                "Audit review status must be reviewed_false_positive or confirmed_risk"
            )
        if note is not None and len(note) > 2_000:
            raise ValidationError("Audit review note must not exceed 2000 characters")
        skill = self.get_skill(skill_id)
        finding = next(
            (
                item
                for item in skill["audit"]
                if item.get("finding_id") == finding_id
            ),
            None,
        )
        if finding is None:
            raise NotFoundError(f"Unknown current audit finding: {finding_id}")
        if finding.get("classification") != "risk":
            raise ValidationError("Capability hints do not require a risk review")

        now = utc_now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT tree_hash FROM skills WHERE id = ? AND active = 1",
                (skill["id"],),
            ).fetchone()
            if current is None or current["tree_hash"] != skill["tree_hash"]:
                raise ConflictError(
                    "Skill source changed while the finding was being reviewed; reload and review again"
                )
            connection.execute(
                """
                INSERT INTO audit_reviews(
                    skill_id, finding_id, finding_digest, skill_tree_hash,
                    status, content_summary, note, reviewed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_id, finding_id) DO UPDATE SET
                    finding_digest=excluded.finding_digest,
                    skill_tree_hash=excluded.skill_tree_hash,
                    status=excluded.status,
                    content_summary=excluded.content_summary,
                    note=excluded.note,
                    reviewed_at=excluded.reviewed_at,
                    updated_at=excluded.updated_at
                """,
                (
                    skill["id"],
                    finding_id,
                    finding["content_digest"],
                    skill["tree_hash"],
                    status,
                    finding["content_summary"],
                    note.strip() if note and note.strip() else None,
                    now,
                    now,
                ),
            )
            reviews = self._review_map(connection, [skill["id"]]).get(
                skill["id"], {}
            )
            _, effective_severity = decorate_audit_findings(
                skill["audit"], skill["tree_hash"], reviews
            )
            connection.execute(
                "UPDATE skills SET audit_severity = ?, updated_at = ? WHERE id = ?",
                (effective_severity, now, skill["id"]),
            )
        return self.get_skill(skill["id"])

    @staticmethod
    def _review_map(
        connection: sqlite3.Connection, skill_ids: list[str]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        if not skill_ids:
            return {}
        placeholders = ",".join("?" for _ in skill_ids)
        rows = connection.execute(
            f"SELECT * FROM audit_reviews WHERE skill_id IN ({placeholders})",
            skill_ids,
        ).fetchall()
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            result.setdefault(row["skill_id"], {})[row["finding_id"]] = dict(row)
        return result

    @staticmethod
    def _decode(
        skill: dict[str, Any], reviews: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        skill["valid"] = bool(skill.get("valid"))
        skill["active"] = bool(skill.get("active"))
        skill["metadata"] = json_value(skill.pop("metadata_json", None), {})
        skill["validation"] = json_value(skill.pop("validation_json", None), [])
        audit, effective_severity = decorate_audit_findings(
            json_value(skill.pop("audit_json", None), []),
            str(skill.get("tree_hash") or ""),
            reviews,
        )
        skill["audit"] = audit
        skill["audit_severity"] = effective_severity
        skill["format_issue_count"] = len(skill["validation"])
        skill["capability_hint_count"] = sum(
            item["classification"] == "capability_hint" for item in audit
        )
        skill["unreviewed_risk_count"] = sum(
            item["classification"] == "risk" and item["status"] == "unreviewed"
            for item in audit
        )
        skill["confirmed_risk_count"] = sum(
            item["status"] == "confirmed_risk" for item in audit
        )
        skill["false_positive_count"] = sum(
            item["status"] == "reviewed_false_positive" for item in audit
        )
        skill["tags"] = json_value(skill.pop("tags_json", None), [])
        return skill
