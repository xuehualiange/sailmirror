import json
import os
import sys
from pathlib import Path

from dashscope import MultiModalConversation
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
API_KEY = os.getenv("ALIYUN_API_KEY")
MODEL = "qwen3-vl-plus"
SCRIPT_DIR = Path(__file__).parent

# 10张测试图配置
test_cases = [
    {"id": "TC-001", "file": "TC-001.jpg", "market": "中东（沙特/阿联酋）", "expected": "极高风险-女性着装"},
    {"id": "TC-002", "file": "TC-002.jpg", "market": "中东（沙特/阿联酋）", "expected": "极高风险-宗教符号"},
    {"id": "TC-003", "file": "TC-003.jpg", "market": "中东（沙特/阿联酋）", "expected": "中风险-绿色禁忌"},
    {"id": "TC-004", "file": "TC-004.jpg", "market": "美国（亚马逊）", "expected": "高风险-绝对化用语"},
    {"id": "TC-005", "file": "TC-005.jpg", "market": "日本", "expected": "高风险-颜色禁忌"},
    {"id": "TC-006", "file": "TC-006.jpg", "market": "中东（沙特/阿联酋）", "expected": "极高风险-佛像图案"},
    {"id": "TC-007", "file": "TC-007.jpg", "market": "中东（沙特/阿联酋）", "expected": "极高风险-酒类"},
    {"id": "TC-008", "file": "TC-008.jpg", "market": "中东（沙特/阿联酋）", "expected": "极高风险-猪肉食品"},
    {"id": "TC-009", "file": "TC-009.jpg", "market": "日本", "expected": "高风险-数字4"},
    {"id": "TC-010", "file": "TC-010.jpg", "market": "东南亚", "expected": "中风险-OK手势"},
]

TOTAL_CASES = len(test_cases)

def build_prompt(market: str) -> str:
    return f"""你是出海镜文化合规检测专家。目标市场：{market}。

严格检测这张商品图片。重点检查：
1. 人物：女性是否暴露肩膀、手臂、腿部、胸部？
2. 宗教：有无十字架、佛像、宗教文字用于商业？
3. 饮食禁忌：有无酒精、酒瓶、Wine字样、猪肉/猪肉干等清真禁忌食品？
4. 颜色：有无大面积绿色（宗教关联）？白色/黑色包装（日本丧事关联）？
5. 数字：有无 prominently 展示数字"4"（日本忌讳）？
6. 手势：有无 OK 手势（东南亚部分国家冒犯）？
7. 文字：有无"Best""#1""100%""FDA Approved"等绝对化用语或未经认证声明？

判定标准：
- 极高风险：必须修改，否则下架/抵制
- 高风险：建议修改
- 中风险：提示注意
- 低风险：基本安全

严格判定。宁可误判也不漏判。

输出JSON格式：
{{
  "risks": [{{"element": "具体元素", "severity": "极高/高/中/低", "reason": "原因"}}],
  "overall_risk": "高/中/低"
}}

只输出JSON，不要其他解释。"""


def parse_json_result(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())


def analyze_image(image_path: Path, market: str) -> str:
    response = MultiModalConversation.call(
        api_key=API_KEY,
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": f"file://{image_path.resolve().as_posix()}"},
                    {"text": build_prompt(market)},
                ],
            }
        ],
    )

    if response.status_code != 200:
        raise RuntimeError(f"{response.code} - {response.message}")

    content = response.output.choices[0].message.content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and "text" in item:
                return item["text"]
        return str(content)
    return str(content)


print("=" * 60)
print("🚀 出海镜 SailMirror - 批量文化合规检测")
print("=" * 60)

results = []

for case in test_cases:
    print(f"\n{'=' * 60}")
    print(f"📋 {case['id']} | 目标市场：{case['market']}")
    print(f"🎯 期望：{case['expected']}")
    print("-" * 60)

    image_path = SCRIPT_DIR / case["file"]
    if not image_path.exists():
        print(f"❌ 图片文件不存在：{case['file']}")
        results.append({"id": case["id"], "status": "跳过", "file_missing": True})
        continue

    try:
        result = analyze_image(image_path, case["market"])

        try:
            parsed = parse_json_result(result)
            risks = parsed.get("risks", [])
            overall = parsed.get("overall_risk", "未知")

            print(f"🔍 检测到 {len(risks)} 个风险项：")
            for risk in risks:
                severity_emoji = {"极高": "🔴", "高": "🟠", "中": "🟡", "低": "🟢"}.get(
                    risk.get("severity", ""), "⚪"
                )
                print(f"  {severity_emoji} {risk.get('element', '未知')} - {risk.get('severity', '未知')}")
                print(f"     原因：{risk.get('reason', '无')}")

            print(f"\n📊 综合风险：{overall}")
            results.append(
                {
                    "id": case["id"],
                    "status": "成功",
                    "overall_risk": overall,
                    "risk_count": len(risks),
                    "risks": risks,
                }
            )

        except json.JSONDecodeError:
            print(f"⚠️ 返回结果不是JSON格式：{result[:200]}")
            results.append({"id": case["id"], "status": "解析失败", "raw": result[:200]})

    except Exception as e:
        print(f"❌ 调用失败：{str(e)}")
        results.append({"id": case["id"], "status": "失败", "error": str(e)})

# 汇总报告
print(f"\n{'=' * 60}")
print("📊 批量检测汇总报告")
print("=" * 60)

success_count = sum(1 for r in results if r["status"] == "成功")
print(f"✅ 成功：{success_count}/{TOTAL_CASES}")
print(f"❌ 失败/跳过：{TOTAL_CASES - success_count}/{TOTAL_CASES}")

for r in results:
    emoji = {"成功": "✅", "跳过": "⏭️", "解析失败": "⚠️", "失败": "❌"}.get(r["status"], "❓")
    risk_info = f"综合风险：{r.get('overall_risk', 'N/A')}" if r["status"] == "成功" else r["status"]
    print(f"  {emoji} {r['id']} - {risk_info}")

print(f"\n{'=' * 60}")
