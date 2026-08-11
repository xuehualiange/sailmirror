"""规则库 RAG 检索模块。

从 knowledge 目录加载 JSON 规则文件，按目标市场与查询文本检索最相关规则，
供合规检测 Prompt 注入使用。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

KNOWLEDGE_DIR = Path(__file__).parent

SEVERITY_WEIGHT = {"极高": 4, "高": 3, "中": 2, "低": 1}
SOURCE_LEVEL_WEIGHT = {"A": 4, "B": 3, "C": 2, "D": 1}

RULE_FIELDS = (
    "id",
    "market",
    "category",
    "rule",
    "severity",
    "source",
    "source_level",
    "source_url",
)


@dataclass
class RuleRecord:
    id: str
    market: str
    category: str
    rule: str
    severity: str
    source: str
    source_level: str
    source_url: str
    source_file: str = field(default="", repr=False)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("source_file", None)
        return data

    def searchable_text(self) -> str:
        return " ".join(
            part for part in (self.category, self.rule, self.source) if part
        )


class RuleLoadError(Exception):
    """规则库加载失败。"""


class RuleRetriever:
    """文化/硬合规规则检索器。"""

    def __init__(self, knowledge_dir: Path | str | None = None):
        self.knowledge_dir = Path(knowledge_dir) if knowledge_dir else KNOWLEDGE_DIR
        self.rules: list[RuleRecord] = []
        self._market_index: dict[str, list[RuleRecord]] = {}
        self._token_cache: dict[str, set[str]] = {}
        self.reload()

    def reload(self) -> int:
        """重新加载 knowledge 目录下全部 JSON 规则文件。"""
        self.rules = self._load_all_rules()
        self._build_indexes()
        return len(self.rules)

    def preload_index(self) -> int:
        """预构建市场索引与分词缓存，加速检索。"""
        if not self.rules:
            self.reload()
        elif not self._market_index:
            self._build_indexes()
        return len(self.rules)

    def _build_indexes(self) -> None:
        self._market_index: dict[str, list[RuleRecord]] = {}
        self._token_cache: dict[str, set[str]] = {}
        for rule in self.rules:
            self._market_index.setdefault(rule.market, []).append(rule)
            self._token_cache[rule.id] = tokenize(rule.searchable_text())

    def _load_all_rules(self) -> list[RuleRecord]:
        if not self.knowledge_dir.is_dir():
            raise RuleLoadError(f"规则目录不存在：{self.knowledge_dir}")

        json_files = sorted(self.knowledge_dir.glob("*.json"))
        if not json_files:
            raise RuleLoadError(f"未在 {self.knowledge_dir} 找到 JSON 规则文件")

        records: list[RuleRecord] = []
        seen_ids: set[str] = set()

        for json_file in json_files:
            try:
                payload = json.loads(json_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuleLoadError(f"JSON 解析失败：{json_file.name}（{exc}）") from exc

            for raw in self._extract_rule_list(payload):
                record = self._normalize_rule(raw, json_file.name)
                if not record:
                    continue
                if record.id in seen_ids:
                    continue
                seen_ids.add(record.id)
                records.append(record)

        if not records:
            raise RuleLoadError(f"规则库为空：{self.knowledge_dir}")

        return records

    @staticmethod
    def _extract_rule_list(payload) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("rules", "data", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _normalize_rule(raw: dict, source_file: str) -> RuleRecord | None:
        rule_text = str(raw.get("rule", "")).strip()
        rule_id = str(raw.get("id", "")).strip()
        if not rule_id or not rule_text:
            return None

        return RuleRecord(
            id=rule_id,
            market=str(raw.get("market", "")).strip().lower(),
            category=str(raw.get("category", "")).strip(),
            rule=rule_text,
            severity=str(raw.get("severity", "")).strip(),
            source=str(raw.get("source", "")).strip(),
            source_level=str(raw.get("source_level", "")).strip().upper(),
            source_url=str(raw.get("source_url", "")).strip(),
            source_file=source_file,
        )

    def list_markets(self) -> list[str]:
        return sorted({rule.market for rule in self.rules if rule.market})

    def list_categories(self, market: str | None = None) -> list[str]:
        market = market.strip().lower() if market else None
        categories = {
            rule.category
            for rule in self.rules
            if rule.category and (not market or rule.market == market)
        }
        return sorted(categories)

    def get_rule_by_id(self, rule_id: str) -> RuleRecord | None:
        rule_id = rule_id.strip()
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        return None

    def filter_by_market(
        self,
        market: str,
        categories: list[str] | None = None,
    ) -> list[RuleRecord]:
        market = market.strip().lower()
        candidates = list(self._market_index.get(market, []))
        if not categories:
            return candidates

        category_set = {c.strip() for c in categories}
        return [rule for rule in candidates if rule.category in category_set]

    def retrieve(
        self,
        query: str,
        market: str,
        *,
        categories: list[str] | None = None,
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> list[dict]:
        """检索与 query 最相关的规则，返回带 score 的字典列表。"""
        market = market.strip().lower()
        candidates = self.filter_by_market(market, categories)
        if not candidates:
            return []

        query = query.strip()
        if not query:
            ranked = self._rank_without_query(candidates)
        else:
            ranked = self._rank_with_query(candidates, query)

        results = []
        for rule, score in ranked:
            if score < min_score:
                continue
            item = rule.to_dict()
            item["score"] = round(score, 4)
            item["source_file"] = rule.source_file
            results.append(item)
            if len(results) >= top_k:
                break
        return results

    def format_for_prompt(
        self,
        rules: list[dict],
        *,
        include_source: bool = True,
        compact: bool = False,
    ) -> str:
        """将检索结果格式化为可注入 LLM Prompt 的文本。"""
        if not rules:
            return "（未检索到相关规则）"

        if compact:
            lines = []
            for rule in rules:
                lines.append(
                    f"- [{rule.get('id', '')}] {rule.get('category', '')}/"
                    f"{rule.get('severity', '')}: {rule.get('rule', '')}"
                )
            return "\n".join(lines)

        lines = ["以下是与当前检测任务相关的规则条目："]
        for index, rule in enumerate(rules, start=1):
            header = (
                f"{index}. [{rule.get('id', '')}] "
                f"{rule.get('category', '')} / 风险：{rule.get('severity', '')}"
            )
            lines.append(header)
            lines.append(f"   规则：{rule.get('rule', '')}")
            if include_source:
                source = rule.get("source", "")
                level = rule.get("source_level", "")
                if source:
                    suffix = f"（{level}级）" if level else ""
                    lines.append(f"   来源：{source}{suffix}")
        return "\n".join(lines)

    def build_context(
        self,
        query: str,
        market: str,
        *,
        categories: list[str] | None = None,
        top_k: int = 10,
    ) -> str:
        """检索并直接返回 Prompt 上下文。"""
        rules = self.retrieve(query, market, categories=categories, top_k=top_k)
        return self.format_for_prompt(rules)

    def _rank_without_query(self, candidates: list[RuleRecord]) -> list[tuple[RuleRecord, float]]:
        ranked = [(rule, self._base_weight(rule)) for rule in candidates]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    def _rank_with_query(
        self,
        candidates: list[RuleRecord],
        query: str,
    ) -> list[tuple[RuleRecord, float]]:
        query_tokens = tokenize(query)
        query_lower = query.lower()

        ranked: list[tuple[RuleRecord, float]] = []
        for rule in candidates:
            score = self._score_rule(rule, query_tokens, query_lower)
            ranked.append((rule, score))

        ranked.sort(key=lambda item: (item[1], self._base_weight(item[0])), reverse=True)
        return ranked

    def _score_rule(
        self,
        rule: RuleRecord,
        query_tokens: set[str],
        query_lower: str,
    ) -> float:
        rule_tokens = self._token_cache.get(rule.id) or tokenize(rule.searchable_text())
        rule_lower = rule.searchable_text().lower()

        overlap = query_tokens & rule_tokens
        score = len(overlap) * 2.0

        for token in query_tokens:
            if len(token) >= 2 and token in rule_lower:
                score += 1.0

        if rule.category and rule.category.lower() in query_lower:
            score += 3.0

        score += self._base_weight(rule) * 0.25
        return score

    @staticmethod
    def _base_weight(rule: RuleRecord) -> float:
        severity = SEVERITY_WEIGHT.get(rule.severity, 0)
        source = SOURCE_LEVEL_WEIGHT.get(rule.source_level, 0)
        return severity * 2 + source


def tokenize(text: str) -> set[str]:
    """中英文混合分词（轻量版，无第三方依赖）。"""
    text = text.lower()
    tokens: set[str] = set()

    for word in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text):
        tokens.add(word)
        if re.fullmatch(r"[\u4e00-\u9fff]{2,}", word):
            for idx in range(len(word) - 1):
                tokens.add(word[idx : idx + 2])

    return tokens


_default_retriever: RuleRetriever | None = None


def get_retriever(knowledge_dir: Path | str | None = None) -> RuleRetriever:
    """获取全局单例检索器。"""
    global _default_retriever
    if knowledge_dir is not None:
        return RuleRetriever(knowledge_dir)
    if _default_retriever is None:
        _default_retriever = RuleRetriever()
    return _default_retriever


def retrieve_rules(
    query: str,
    market: str,
    *,
    top_k: int = 10,
    categories: list[str] | None = None,
) -> list[dict]:
    """便捷函数：检索规则列表。"""
    return get_retriever().retrieve(
        query,
        market,
        categories=categories,
        top_k=top_k,
    )


def build_rule_context(
    query: str,
    market: str,
    *,
    top_k: int = 10,
    categories: list[str] | None = None,
) -> str:
    """便捷函数：检索并格式化为 Prompt 上下文。"""
    return get_retriever().build_context(
        query,
        market,
        categories=categories,
        top_k=top_k,
    )


def _demo() -> None:
    retriever = RuleRetriever()
    print(f"已加载规则：{len(retriever.rules)} 条")
    print(f"覆盖市场：{', '.join(retriever.list_markets())}")

    query = "女性暴露着装 宗教符号 绿色"
    market = "middle_east"
    results = retriever.retrieve(query, market, top_k=5)

    print(f"\n检索示例：market={market!r}, query={query!r}")
    print("-" * 60)
    for item in results:
        print(
            f"[{item['score']:.2f}] {item['id']} | "
            f"{item['category']} | {item['severity']} | {item['rule'][:40]}..."
        )

    print("\nPrompt 上下文：")
    print(retriever.format_for_prompt(results))


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        _market = sys.argv[1]
        _query = " ".join(sys.argv[2:])
        print(build_rule_context(_query, _market))
    else:
        _demo()
