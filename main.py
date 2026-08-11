import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from dashscope import Generation, MultiModalConversation
from dotenv import load_dotenv

from knowledge.retrieval import RuleLoadError, get_retriever

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
API_KEY = os.getenv("ALIYUN_API_KEY")
VL_MODEL = "qwen3-vl-plus"
TEXT_MODEL = "qwen3.7-max"

MAX_RETRIES = 2
RETRY_BASE_DELAY_SEC = 1
API_TIMEOUT_SEC = 45
TARGET_SECONDS = 15
MAX_IMAGE_SIZE_MB = 10
MAX_IMAGE_PIXELS = 1280
MIN_IMAGE_SIZE_BYTES = 100
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
RAG_TOP_K_CULTURE = 5
RAG_TOP_K_HARD = 5
TEXT_MAX_TOKENS = 512
CULTURE_MAX_TOKENS = 512
API_TEMPERATURE = 0.2
PERFORMANCE_LOG = Path(__file__).parent / "performance.log"

VALID_MARKETS = {
    "middle_east": "中东（沙特/阿联酋）：检测女性着装、宗教符号、酒精猪肉、绿色",
    "japan": "日本：检测白黑包装、数字4/9、樱花图案",
    "us": "美国：检测绝对化用语Best/#1/100%、FDA/FCC认证",
    "eu": "欧盟：检测CE认证、环保标识",
    "southeast_asia": "东南亚：检测佛像、脚部朝向、宗教符号",
}

CULTURE_QUERY_HINTS = {
    "middle_east": "人物着装 宗教符号 饮食禁忌 颜色禁忌 手势 酒精 猪肉 绿色 伊斯兰",
    "japan": "颜色禁忌 数字4 数字9 白黑包装 樱花 文化习俗 丧事",
    "us": "广告法 绝对化用语 认证要求 Best FDA FCC",
    "eu": "CE认证 环保标识 GPSR 认证要求 标签",
    "southeast_asia": "宗教符号 佛像 手势 OK手势 脚部朝向 文化习俗",
}

HARD_QUERY_HINTS = {
    "middle_east": "广告法 标签 认证 Halal 阿拉伯语",
    "japan": "广告法 标签 认证 药机法 特定商取引法",
    "us": "广告法 绝对化用语 FDA FCC CPSIA FTC 认证",
    "eu": "CE GPSR 广告法 环保 标签 认证",
    "southeast_asia": "广告法 标签 认证 FDA 清真",
}

RETRYABLE_API_CODES = {
    "Timeout",
    "Throttling",
    "Throttling.RateQuota",
    "ServiceUnavailable",
    "InternalError",
    "RequestTimeOut",
    "SystemError",
}

_print_lock = threading.Lock()
_log_file_lock = threading.Lock()
_perf_tracker: "PerformanceTracker | None" = None
_validated_image_cache: dict[str, Path] = {}

STAGE_ORDER = [
    "startup.validate_market",
    "startup.validate_image",
    "startup.ensure_api_key",
    "startup.preload_rules",
    "rag.prefetch",
    "culture.rag",
    "culture.ai",
    "culture.agent_total",
    "hard.rag",
    "hard.ai",
    "hard.agent_total",
    "parallel.wall_time",
    "report.output",
    "api.retry_wait",
]


def _log(message: str) -> None:
    with _print_lock:
        print(message)


class PerformanceTracker:
    """各环节耗时统计，每次运行结束写入 performance.log。"""

    def __init__(self):
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.metadata: dict[str, str] = {}
        self.stages: dict[str, float] = {}
        self.status = "running"
        self.error = ""
        self._lock = threading.Lock()
        self._active: dict[str, float] = {}
        self._run_started = time.perf_counter()
        self._written = False

    @contextmanager
    def measure(self, stage: str):
        self.start(stage)
        try:
            yield
        finally:
            self.stop(stage)

    def start(self, stage: str) -> None:
        with self._lock:
            self._active[stage] = time.perf_counter()

    def stop(self, stage: str) -> float:
        with self._lock:
            started = self._active.pop(stage, None)
            if started is None:
                return 0.0
            elapsed = time.perf_counter() - started
            self.stages[stage] = self.stages.get(stage, 0.0) + elapsed
            return elapsed

    def record(self, stage: str, elapsed: float) -> None:
        if elapsed <= 0:
            return
        with self._lock:
            self.stages[stage] = self.stages.get(stage, 0.0) + elapsed

    def set_metadata(self, **kwargs) -> None:
        with self._lock:
            self.metadata.update({key: str(value) for key, value in kwargs.items()})

    def mark_success(self) -> None:
        self.status = "success"

    def mark_failed(self, error: str) -> None:
        self.status = "failed"
        self.error = error.strip()

    def mark_interrupted(self) -> None:
        self.status = "interrupted"
        self.error = "用户中断"

    def total_elapsed(self) -> float:
        return time.perf_counter() - self._run_started

    def get_stage(self, stage: str) -> float:
        with self._lock:
            return self.stages.get(stage, 0.0)

    def agent_timings(self, prefix: str) -> dict[str, float]:
        rag = self.get_stage(f"{prefix}.rag")
        ai = self.get_stage(f"{prefix}.ai")
        total = self.get_stage(f"{prefix}.agent_total")
        if total <= 0:
            total = rag + ai
        return {"rag": rag, "ai": ai, "agent_total": total}

    def snapshot(self) -> dict:
        with self._lock:
            total = round(self.total_elapsed(), 4)
            stages = {key: round(value, 4) for key, value in self.stages.items()}
            return {
                "run_id": self.run_id,
                "status": self.status,
                "error": self.error,
                "metadata": dict(self.metadata),
                "stages": stages,
                "total_seconds": total,
            }

    def _format_stage_line(self, stage: str, elapsed: float, total: float) -> str:
        pct = (elapsed / total * 100) if total > 0 else 0.0
        return f"{stage:<30} {elapsed:>9.3f}s  ({pct:>5.1f}%)"

    def finalize_and_write(self) -> Path | None:
        """每次运行结束时写入 performance.log（幂等，仅写一次）。"""
        if self._written:
            return PERFORMANCE_LOG

        snapshot = self.snapshot()
        total = snapshot["total_seconds"] or 0.001
        lines = [
            "=" * 88,
            f"[{datetime.now().isoformat(timespec='seconds')}] Run ID: {snapshot['run_id']}",
            f"status={snapshot['status']}",
        ]

        if snapshot["metadata"]:
            meta = " | ".join(f"{key}={value}" for key, value in snapshot["metadata"].items())
            lines.append(meta)

        if snapshot["error"]:
            lines.append(f"error={snapshot['error']}")

        lines.append("-" * 88)

        printed: set[str] = set()
        for stage in STAGE_ORDER:
            if stage in snapshot["stages"]:
                lines.append(self._format_stage_line(stage, snapshot["stages"][stage], total))
                printed.add(stage)

        for stage in sorted(snapshot["stages"]):
            if stage not in printed:
                lines.append(self._format_stage_line(stage, snapshot["stages"][stage], total))

        lines.append("-" * 88)
        lines.append(f"{'run.total':<30} {total:>9.3f}s  (100.0%)")
        lines.append("=" * 88)

        json_record = json.dumps(snapshot, ensure_ascii=False)
        lines.extend(["JSON", json_record, ""])

        content = "\n".join(lines)
        PERFORMANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _log_file_lock:
            with PERFORMANCE_LOG.open("a", encoding="utf-8") as log_file:
                log_file.write(content)
                log_file.flush()

        self._written = True
        _log(f"📝 耗时日志已写入 {PERFORMANCE_LOG}（status={snapshot['status']}）")
        return PERFORMANCE_LOG


def get_perf_tracker() -> PerformanceTracker:
    global _perf_tracker
    if _perf_tracker is None:
        _perf_tracker = PerformanceTracker()
    return _perf_tracker


def reset_perf_tracker() -> PerformanceTracker:
    global _perf_tracker
    _perf_tracker = PerformanceTracker()
    return _perf_tracker


class SailMirrorError(Exception):
    """出海镜业务异常基类。"""


class ConfigError(SailMirrorError):
    """配置错误。"""


class ImageValidationError(SailMirrorError):
    """图片校验失败。"""


class APIError(SailMirrorError):
    """API 调用失败。"""


def ensure_api_key() -> None:
    if not API_KEY or API_KEY.strip() in {"", "sk-your-api-key"}:
        raise ConfigError(
            "未配置有效的 ALIYUN_API_KEY。请在项目根目录创建 .env 文件并设置：\n"
            "  ALIYUN_API_KEY=sk-xxxxxxxx"
        )


def validate_market(market: str) -> str:
    market = market.strip().lower()
    if market not in VALID_MARKETS:
        supported = " / ".join(VALID_MARKETS)
        raise SailMirrorError(
            f"不支持的目标市场：{market}\n"
            f"可选市场：{supported}"
        )
    return market


def _read_image_header(path: Path, length: int = 16) -> bytes:
    with path.open("rb") as f:
        return f.read(length)


def _detect_image_format(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if header.startswith(b"BM"):
        return "BMP"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "WEBP"
    return None


def validate_image(image_path: str) -> Path:
    """校验图片路径、格式与大小，返回规范化 Path。"""
    path = Path(image_path).expanduser()
    if not path.exists():
        raise ImageValidationError(f"图片不存在：{image_path}")
    if not path.is_file():
        raise ImageValidationError(f"路径不是文件：{image_path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_IMAGE_EXTENSIONS))
        raise ImageValidationError(
            f"不支持的图片格式：{suffix or '无扩展名'}\n"
            f"支持格式：{supported}"
        )

    size = path.stat().st_size
    if size < MIN_IMAGE_SIZE_BYTES:
        raise ImageValidationError(
            f"图片文件过小（{size} 字节），可能已损坏或不是有效图片"
        )

    max_bytes = MAX_IMAGE_SIZE_MB * 1024 * 1024
    if size > max_bytes:
        raise ImageValidationError(
            f"图片过大（{size / 1024 / 1024:.1f} MB），"
            f"最大允许 {MAX_IMAGE_SIZE_MB} MB"
        )

    try:
        header = _read_image_header(path)
    except OSError as exc:
        raise ImageValidationError(f"无法读取图片文件：{exc}") from exc

    image_format = _detect_image_format(header)
    if not image_format:
        raise ImageValidationError(
            f"文件扩展名为 {suffix}，但内容不是有效图片（文件头校验失败）"
        )

    return path.resolve()


def prepare_image_for_api(path: Path) -> Path:
    """压缩过大图片以加速视觉模型推理（需 Pillow）。"""
    try:
        from PIL import Image
    except ImportError:
        return path

    try:
        with Image.open(path) as img:
            width, height = img.size
            longest = max(width, height)
            if longest <= MAX_IMAGE_PIXELS:
                return path

            scale = MAX_IMAGE_PIXELS / longest
            resized = img.convert("RGB").resize(
                (int(width * scale), int(height * scale)),
                Image.Resampling.LANCZOS,
            )
            output = path.parent / f".{path.stem}_opt.jpg"
            resized.save(output, format="JPEG", quality=85, optimize=True)
            _log(f"🖼️ 图片已压缩：{width}x{height} → {resized.size[0]}x{resized.size[1]}")
            return output.resolve()
    except Exception as exc:
        _log(f"⚠️ 图片压缩跳过：{exc}")
        return path


def cache_validated_image(image_path: str) -> Path:
    """校验并缓存图片路径，避免重复校验。"""
    key = str(Path(image_path).expanduser().resolve())
    if key in _validated_image_cache:
        return _validated_image_cache[key]

    path = validate_image(image_path)
    path = prepare_image_for_api(path)
    _validated_image_cache[key] = path
    return path


def preload_rule_index() -> int:
    """启动时预加载规则库索引。"""
    return get_retriever().preload_index()


def parse_json_result(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    text = text.strip()
    if text.startswith("{") and "'" in text and '"' not in text:
        text = text.replace("'", '"')
    return json.loads(text)


def _extract_message_content(content) -> str:
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and "text" in item:
                return item["text"]
        return str(content)
    return str(content)


def _is_retryable_api_response(response) -> bool:
    code = getattr(response, "code", "") or ""
    message = getattr(response, "message", "") or ""
    if code in RETRYABLE_API_CODES:
        return True
    retry_keywords = ("timeout", "timed out", "throttl", "rate limit", "503", "502", "504")
    combined = f"{code} {message}".lower()
    return any(keyword in combined for keyword in retry_keywords)


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    message = str(exc).lower()
    retry_keywords = ("timeout", "timed out", "connection", "temporarily unavailable")
    return any(keyword in message for keyword in retry_keywords)


def _format_api_error(response) -> str:
    code = getattr(response, "code", "Unknown")
    message = getattr(response, "message", "未知错误")
    request_id = getattr(response, "request_id", "")
    detail = f"{code} - {message}"
    if request_id:
        detail += f"（request_id: {request_id}）"
    return detail


def call_with_retry(operation: str, call_fn):
    """带超时重试的 API 调用包装。"""
    last_error = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = call_fn()
            if response.status_code == 200:
                return response

            error_text = _format_api_error(response)
            if attempt < MAX_RETRIES and _is_retryable_api_response(response):
                delay = RETRY_BASE_DELAY_SEC * attempt
                _log(
                    f"⚠️ {operation} 失败（{attempt}/{MAX_RETRIES}）：{error_text}，"
                    f"{delay}s 后重试..."
                )
                time.sleep(delay)
                get_perf_tracker().record("api.retry_wait", delay)
                last_error = error_text
                continue

            raise APIError(f"{operation} 失败：{error_text}")

        except APIError:
            raise
        except Exception as exc:
            if attempt < MAX_RETRIES and _is_retryable_exception(exc):
                delay = RETRY_BASE_DELAY_SEC * attempt
                _log(
                    f"⚠️ {operation} 异常（{attempt}/{MAX_RETRIES}）：{exc}，"
                    f"{delay}s 后重试..."
                )
                time.sleep(delay)
                get_perf_tracker().record("api.retry_wait", delay)
                last_error = str(exc)
                continue
            raise APIError(f"{operation} 异常：{exc}") from exc

    raise APIError(f"{operation} 在 {MAX_RETRIES} 次尝试后仍失败：{last_error}")


def build_culture_query(market: str, listing_text: str = "") -> str:
    parts = [CULTURE_QUERY_HINTS.get(market, ""), listing_text.strip()]
    return " ".join(part for part in parts if part)


def build_hard_query(market: str, listing_text: str) -> str:
    parts = [listing_text.strip(), HARD_QUERY_HINTS.get(market, "")]
    return " ".join(part for part in parts if part)


def retrieve_culture_rules(market: str, listing_text: str = "") -> tuple[list[dict], str]:
    retriever = get_retriever()
    query = build_culture_query(market, listing_text)
    rules = retriever.retrieve(query, market, top_k=RAG_TOP_K_CULTURE)
    context = retriever.format_for_prompt(rules, compact=True)
    return rules, context


def retrieve_hard_rules(market: str, listing_text: str) -> tuple[list[dict], str]:
    retriever = get_retriever()
    query = build_hard_query(market, listing_text)
    rules = retriever.retrieve(query, market, top_k=RAG_TOP_K_HARD)
    context = retriever.format_for_prompt(rules, compact=True)
    return rules, context


def _api_kwargs(max_tokens: int, *, disable_thinking: bool = False) -> dict:
    kwargs = {
        "temperature": API_TEMPERATURE,
        "max_tokens": max_tokens,
    }
    if disable_thinking:
        kwargs["enable_thinking"] = False
    return kwargs


def call_text_api(messages: list) -> str:
    response = call_with_retry(
        "文本模型调用",
        lambda: Generation.call(
            api_key=API_KEY,
            model=TEXT_MODEL,
            messages=messages,
            result_format="message",
            timeout=API_TIMEOUT_SEC,
            **_api_kwargs(TEXT_MAX_TOKENS, disable_thinking=True),
        ),
    )
    return response.output.choices[0].message.content


def detect_culture(
    image_path: str,
    market: str,
    *,
    image_file: Path | None = None,
    rag_context: str = "",
    retrieved_rules: list[dict] | None = None,
) -> dict:
    """文化合规检测（RAG 增强）"""
    path = image_file or cache_validated_image(image_path)

    rule_section = rag_context or "（无匹配规则，按通用文化合规检测）"
    prompt = (
        f"目标市场：{VALID_MARKETS[market]}。\n"
        f"规则：\n{rule_section}\n"
        "检测图片文化禁忌（着装/宗教/饮食/颜色/手势）。"
        "违规项引用 rule_id。"
        '输出JSON：{"risks":[{"element":"","severity":"极高/高/中/低","reason":"","rule_id":""}],'
        '"overall_risk":""}。只输出JSON。'
    )

    try:
        response = call_with_retry(
            "文化合规视觉检测",
            lambda: MultiModalConversation.call(
                api_key=API_KEY,
                model=VL_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"image": f"file://{path.as_posix()}"},
                            {"text": prompt},
                        ],
                    }
                ],
                timeout=API_TIMEOUT_SEC,
                **_api_kwargs(CULTURE_MAX_TOKENS),
            ),
        )
    except APIError as exc:
        return {"error": str(exc)}

    content = _extract_message_content(response.output.choices[0].message.content)

    try:
        result = parse_json_result(content)
        if retrieved_rules is not None:
            result["retrieved_rules"] = [
                {"id": rule["id"], "category": rule["category"], "score": rule.get("score")}
                for rule in retrieved_rules
            ]
        return result
    except json.JSONDecodeError:
        return {
            "error": "模型返回结果不是有效 JSON，请稍后重试",
            "raw": content[:200],
        }


def detect_hard(
    listing_text: str,
    market: str,
    *,
    rag_context: str = "",
    retrieved_rules: list[dict] | None = None,
) -> dict:
    """硬合规检测（RAG 增强）"""
    listing_text = listing_text.strip()
    if not listing_text:
        return {"error": "Listing 文案为空，跳过硬合规检测"}

    rule_section = rag_context or "（无匹配规则，按通用硬合规检测）"
    listing_trimmed = listing_text[:800]
    prompt = (
        f"目标市场：{market}。\n"
        f"规则：\n{rule_section}\n"
        f"文案：{listing_trimmed}\n"
        "检测敏感词/平台规则/认证要求，违规项引用 rule_id。"
        '输出JSON：{"violations":[{"type":"","content":"","severity":"高/中/低","suggestion":"","rule_id":""}],'
        '"certifications":[],"overall_status":"合规/警告/违规"}。只输出JSON。'
    )

    try:
        result = call_text_api(
            [
                {"role": "system", "content": "你是硬合规检测专家。简洁输出JSON。"},
                {"role": "user", "content": prompt},
            ]
        )
        parsed = parse_json_result(result)
        if retrieved_rules is not None:
            parsed["retrieved_rules"] = [
                {"id": rule["id"], "category": rule["category"], "score": rule.get("score")}
                for rule in retrieved_rules
            ]
        return parsed
    except APIError as exc:
        return {"error": str(exc)}
    except json.JSONDecodeError:
        raw = result if "result" in locals() else ""
        return {
            "error": "模型返回结果不是有效 JSON，请稍后重试",
            "raw": str(raw)[:200],
        }


def _log_retrieved_rules(agent_name: str, rules: list[dict]) -> None:
    _log(f"📚 [{agent_name}] 命中 {len(rules)} 条规则")
    for rule in rules[:3]:
        _log(
            f"   · [{rule['id']}] {rule['category']} "
            f"(score={rule.get('score', 0):.1f})"
        )
    if len(rules) > 3:
        _log(f"   · ... 另有 {len(rules) - 3} 条")


def prefetch_rag_context(
    market: str,
    listing_text: str = "",
) -> tuple[list[dict], str, list[dict], str]:
    """并行预取双轨 RAG 上下文。"""
    culture_rules: list[dict] = []
    culture_context = ""
    hard_rules: list[dict] = []
    hard_context = ""

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="rag") as executor:
        culture_future = executor.submit(retrieve_culture_rules, market, listing_text)
        futures = [("culture", culture_future)]
        if listing_text.strip():
            futures.append(("hard", executor.submit(retrieve_hard_rules, market, listing_text)))

        for name, future in futures:
            try:
                rules, context = future.result()
            except RuleLoadError as exc:
                _log(f"⚠️ [{name} RAG] 规则库加载失败：{exc}")
                rules, context = [], ""
            if name == "culture":
                culture_rules, culture_context = rules, context
            else:
                hard_rules, hard_context = rules, context

    return culture_rules, culture_context, hard_rules, hard_context


def run_culture_agent(
    image_path: str,
    market: str,
    listing_text: str = "",
    *,
    image_file: Path | None = None,
    culture_rules: list[dict] | None = None,
    culture_context: str | None = None,
    rag_prefetched: bool = False,
) -> tuple[dict, list[dict], dict]:
    """文化合规 Agent：RAG 检索 + 视觉模型推理。"""
    tracker = get_perf_tracker()
    rules = culture_rules if culture_rules is not None else []
    context = culture_context if culture_context is not None else ""

    _log("🌍 [文化Agent] 启动...")
    with tracker.measure("culture.agent_total"):
        if not rag_prefetched:
            with tracker.measure("culture.rag"):
                try:
                    rules, context = retrieve_culture_rules(market, listing_text)
                except RuleLoadError as exc:
                    _log(f"⚠️ [文化Agent] 规则库加载失败：{exc}")
                    rules, context = [], ""
        _log_retrieved_rules("文化Agent", rules)

        _log("🌍 [文化Agent] AI 推理中...")
        with tracker.measure("culture.ai"):
            result = detect_culture(
                image_path,
                market,
                image_file=image_file,
                rag_context=context,
                retrieved_rules=rules,
            )

    timings = tracker.agent_timings("culture")
    _log(
        f"🌍 [文化Agent] 完成，耗时 {timings['agent_total']:.1f}s "
        f"(RAG {timings['rag']:.2f}s + AI {timings['ai']:.2f}s)"
    )
    return result, rules, timings


def run_hard_agent(
    listing_text: str,
    market: str,
    *,
    hard_rules: list[dict] | None = None,
    hard_context: str | None = None,
    rag_prefetched: bool = False,
) -> tuple[dict, list[dict], dict]:
    """硬合规 Agent：RAG 检索 + 文本模型推理。"""
    tracker = get_perf_tracker()
    listing_text = listing_text.strip()
    if not listing_text:
        return {}, [], {}

    rules = hard_rules if hard_rules is not None else []
    context = hard_context if hard_context is not None else ""

    _log("⚖️ [硬合规Agent] 启动...")
    with tracker.measure("hard.agent_total"):
        if not rag_prefetched:
            with tracker.measure("hard.rag"):
                try:
                    rules, context = retrieve_hard_rules(market, listing_text)
                except RuleLoadError as exc:
                    _log(f"⚠️ [硬合规Agent] 规则库加载失败：{exc}")
                    rules, context = [], ""
        _log_retrieved_rules("硬合规Agent", rules)

        _log("⚖️ [硬合规Agent] AI 推理中...")
        with tracker.measure("hard.ai"):
            result = detect_hard(
                listing_text,
                market,
                rag_context=context,
                retrieved_rules=rules,
            )

    timings = tracker.agent_timings("hard")
    _log(
        f"⚖️ [硬合规Agent] 完成，耗时 {timings['agent_total']:.1f}s "
        f"(RAG {timings['rag']:.2f}s + AI {timings['ai']:.2f}s)"
    )
    return result, rules, timings


def run_parallel_detection(
    image_path: str,
    market: str,
    listing_text: str = "",
    *,
    image_file: Path | None = None,
) -> tuple[dict, dict, dict, float, dict]:
    """双 Agent 并行检测，返回 culture、hard、rag_meta、总耗时、分环节耗时。"""
    tracker = get_perf_tracker()
    rag_meta = {"culture_rules": [], "hard_rules": []}
    perf_detail = {"culture": {}, "hard": {}}
    has_listing = bool(listing_text.strip())

    _log("🚀 双 Agent 并行检测启动...")
    if has_listing:
        _log("   · 文化Agent（视觉） + 硬合规Agent（文本）同时运行")
    else:
        _log("   · 仅文化Agent（无 Listing 文案）")

    culture: dict = {}
    hard: dict = {}

    with tracker.measure("parallel.wall_time"):
        with tracker.measure("rag.prefetch"):
            culture_rules, culture_context, hard_rules, hard_context = prefetch_rag_context(
                market,
                listing_text,
            )

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent") as executor:
            futures = {
                executor.submit(
                    run_culture_agent,
                    image_path,
                    market,
                    listing_text,
                    image_file=image_file,
                    culture_rules=culture_rules,
                    culture_context=culture_context,
                    rag_prefetched=True,
                ): "culture",
            }
            if has_listing:
                futures[
                    executor.submit(
                        run_hard_agent,
                        listing_text,
                        market,
                        hard_rules=hard_rules,
                        hard_context=hard_context,
                        rag_prefetched=True,
                    )
                ] = "hard"

            for future in as_completed(futures):
                agent_type = futures[future]
                try:
                    result, rules, timings = future.result()
                except Exception as exc:
                    _log(f"❌ [{agent_type}Agent] 未捕获异常：{exc}")
                    result = {"error": f"Agent 执行异常：{exc}"}
                    rules = []
                    timings = {}

                perf_detail[agent_type] = timings
                if agent_type == "culture":
                    culture = result
                    rag_meta["culture_rules"] = rules
                else:
                    hard = result
                    rag_meta["hard_rules"] = rules

    elapsed = tracker.stages.get("parallel.wall_time", 0.0)
    budget_hint = "✅ 达标" if elapsed <= TARGET_SECONDS else f"⚠️ 超出目标 {TARGET_SECONDS}s"
    _log(f"✅ 双 Agent 并行检测完成，总耗时 {elapsed:.1f}s（目标 ≤{TARGET_SECONDS}s）{budget_hint}")
    return culture, hard, rag_meta, elapsed, perf_detail


def generate_report(
    image_path: str,
    market: str,
    listing_text: str = "",
    *,
    image_file: Path | None = None,
) -> dict:
    """生成双轨报告"""
    tracker = get_perf_tracker()
    tracker.set_metadata(
        image=Path(image_path).name,
        market=market,
        has_listing=bool(listing_text.strip()),
    )

    culture, hard, rag_meta, elapsed, perf_detail = run_parallel_detection(
        image_path,
        market,
        listing_text,
        image_file=image_file,
    )

    report = {
        "image": image_path,
        "market": market,
        "listing": listing_text,
        "rag": rag_meta,
        "parallel": True,
        "target_seconds": TARGET_SECONDS,
        "within_target": elapsed <= TARGET_SECONDS,
        "elapsed_seconds": round(elapsed, 2),
        "performance": {
            "wall_time": round(elapsed, 4),
            "agents": perf_detail,
            "stages": tracker.snapshot()["stages"],
            "status": tracker.status,
        },
        "culture_compliance": culture,
        "hard_compliance": hard,
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }

    with tracker.measure("report.output"):
        print("\n" + "=" * 50)
        print("🚀 出海镜 · 双轨合规检测报告")
        print(f"⏱️ 并行耗时：{elapsed:.1f}s / 目标 ≤{TARGET_SECONDS}s")
        if perf_detail.get("culture"):
            c = perf_detail["culture"]
            print(
                f"   · 文化Agent：{c.get('agent_total', 0):.2f}s "
                f"(RAG {c.get('rag', 0):.2f}s + AI {c.get('ai', 0):.2f}s)"
            )
        if perf_detail.get("hard"):
            h = perf_detail["hard"]
            print(
                f"   · 硬合规Agent：{h.get('agent_total', 0):.2f}s "
                f"(RAG {h.get('rag', 0):.2f}s + AI {h.get('ai', 0):.2f}s)"
            )
        print("=" * 50)

        if culture.get("error"):
            print("\n🌍 文化合规：检测失败 ❌")
            print(f"     {culture.get('error')}")
            if culture.get("raw"):
                print(f"     原始返回：{culture.get('raw')}")
        else:
            risks = culture.get("risks", [])
            if risks:
                print(f"\n🌍 文化合规：发现 {len(risks)} 个风险")
                for r in risks:
                    emoji = {"极高": "🔴", "高": "🟠", "中": "🟡", "低": "🟢"}.get(
                        r.get("severity", ""), "⚪"
                    )
                    rule_id = r.get("rule_id", "")
                    rule_hint = f" [{rule_id}]" if rule_id else ""
                    print(f"  {emoji} {r.get('element', '')} - {r.get('severity', '')}{rule_hint}")
                    print(f"     {r.get('reason', '')}")
            else:
                print("\n🌍 文化合规：无风险 ✅")

            retrieved = culture.get("retrieved_rules", [])
            if retrieved:
                ids = ", ".join(item["id"] for item in retrieved[:5])
                print(f"     参考规则：{ids}")

        if hard:
            if hard.get("error"):
                print("\n⚠️ 硬合规：检测失败 ❌")
                print(f"     {hard.get('error')}")
                if hard.get("raw"):
                    print(f"     原始返回：{hard.get('raw')}")
            else:
                vios = hard.get("violations", [])
                if vios:
                    print(f"\n⚠️ 硬合规：发现 {len(vios)} 个问题")
                    for v in vios:
                        rule_id = v.get("rule_id", "")
                        rule_hint = f" [{rule_id}]" if rule_id else ""
                        print(f"  🟠 {v.get('content', '')} - {v.get('severity', '')}{rule_hint}")
                        if v.get("suggestion"):
                            print(f"     建议：{v.get('suggestion')}")
                else:
                    print("\n⚠️ 硬合规：无问题 ✅")

            retrieved = hard.get("retrieved_rules", [])
            if retrieved:
                ids = ", ".join(item["id"] for item in retrieved[:5])
                print(f"     参考规则：{ids}")

        print("\n" + "=" * 50)

    tracker.mark_success()
    return report


def print_usage() -> None:
    print("用法：python main.py <图片路径> <目标市场> [Listing文案]")
    print("示例：python main.py TC-001.jpg middle_east \"Best product ever\"")
    print("市场代码：middle_east / japan / us / eu / southeast_asia")


def main() -> None:
    if len(sys.argv) < 3:
        print_usage()
        sys.exit(1)

    image_path = sys.argv[1]
    market = sys.argv[2]
    listing_text = sys.argv[3] if len(sys.argv) > 3 else ""

    tracker = reset_perf_tracker()
    tracker.set_metadata(
        image=Path(image_path).name,
        market=market,
        has_listing=bool(listing_text.strip()),
    )

    exit_code = 0
    image_file: Path | None = None
    try:
        with tracker.measure("startup.validation"):
            with tracker.measure("startup.validate_market"):
                market = validate_market(market)
            with tracker.measure("startup.validate_image"):
                image_file = cache_validated_image(image_path)
            with tracker.measure("startup.ensure_api_key"):
                ensure_api_key()
            with tracker.measure("startup.preload_rules"):
                rule_count = preload_rule_index()
                _log(f"📚 规则库已预加载：{rule_count} 条")
        generate_report(image_path, market, listing_text, image_file=image_file)
    except ConfigError as exc:
        tracker.mark_failed(str(exc))
        print(f"❌ 配置错误：{exc}")
        exit_code = 2
    except ImageValidationError as exc:
        tracker.mark_failed(str(exc))
        print(f"❌ 图片校验失败：{exc}")
        exit_code = 3
    except SailMirrorError as exc:
        tracker.mark_failed(str(exc))
        print(f"❌ 运行失败：{exc}")
        exit_code = 4
    except KeyboardInterrupt:
        tracker.mark_interrupted()
        print("\n⏹ 用户中断检测")
        exit_code = 130
    except Exception as exc:
        tracker.mark_failed(str(exc))
        print(f"❌ 未预期错误：{exc}")
        exit_code = 1
    finally:
        tracker.finalize_and_write()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
