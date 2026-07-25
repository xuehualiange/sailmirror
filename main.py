import json
import os
import sys
from pathlib import Path

from dashscope import Generation, MultiModalConversation
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
API_KEY = os.getenv("ALIYUN_API_KEY")
VL_MODEL = "qwen3-vl-plus"
TEXT_MODEL = "qwen3.7-max"


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


def call_text_api(messages: list, temperature: float = 0.7) -> str:
    response = Generation.call(
        api_key=API_KEY,
        model=TEXT_MODEL,
        messages=messages,
        temperature=temperature,
        result_format="message",
    )
    if response.status_code != 200:
        raise RuntimeError(f"{response.code} - {response.message}")
    return response.output.choices[0].message.content


def detect_culture(image_path: str, market: str) -> dict:
    """文化合规检测"""
    path = Path(image_path)
    if not path.exists():
        return {"error": f"图片不存在: {image_path}"}

    market_desc = {
        "middle_east": "中东（沙特/阿联酋）：检测女性着装、宗教符号、酒精猪肉、绿色",
        "japan": "日本：检测白黑包装、数字4/9、樱花图案",
        "us": "美国：检测绝对化用语Best/#1/100%、FDA/FCC认证",
        "eu": "欧盟：检测CE认证、环保标识",
        "southeast_asia": "东南亚：检测佛像、脚部朝向、宗教符号",
    }.get(market, market)

    prompt = f"""你是出海镜文化合规检测专家。目标市场：{market_desc}。
严格检测图片中的文化禁忌元素（人物着装、宗教符号、饮食禁忌、颜色禁忌、手势）。
输出JSON：{{"risks":[{{"element":"","severity":"极高/高/中/低","reason":""}}],"overall_risk":""}}。只输出JSON。"""

    response = MultiModalConversation.call(
        api_key=API_KEY,
        model=VL_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": f"file://{path.resolve().as_posix()}"},
                    {"text": prompt},
                ],
            }
        ],
    )

    if response.status_code != 200:
        return {"error": f"{response.code} - {response.message}"}

    content = response.output.choices[0].message.content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and "text" in item:
                content = item["text"]
                break
        else:
            content = str(content)

    try:
        return parse_json_result(content)
    except json.JSONDecodeError:
        return {"error": "解析失败", "raw": str(content)[:200]}


def detect_hard(listing_text: str, market: str) -> dict:
    """硬合规检测"""
    prompt = f"""检测以下文案，目标市场：{market}。文案：{listing_text}。
检测敏感词（Best/#1/100%/FDA Approved等）、平台规则违反、认证要求。
输出JSON：{{"violations":[{{"type":"","content":"","severity":"高/中/低","suggestion":""}}],"certifications":[],"overall_status":"合规/警告/违规"}}。只输出JSON。"""

    try:
        result = call_text_api(
            [
                {"role": "system", "content": "你是出海镜硬合规检测专家。"},
                {"role": "user", "content": prompt},
            ]
        )
        return parse_json_result(result)
    except (json.JSONDecodeError, RuntimeError) as e:
        raw = result if "result" in locals() else str(e)
        return {"error": "解析失败", "raw": str(raw)[:200]}


def generate_report(image_path: str, market: str, listing_text: str = "") -> dict:
    """生成双轨报告"""
    print("🔍 文化合规检测...")
    culture = detect_culture(image_path, market)

    hard = {}
    if listing_text:
        print("⚠️ 硬合规检测...")
        hard = detect_hard(listing_text, market)

    report = {
        "image": image_path,
        "market": market,
        "listing": listing_text,
        "culture_compliance": culture,
        "hard_compliance": hard,
        "timestamp": "2024-07-24",
    }

    print("\n" + "=" * 50)
    print("🚀 出海镜 · 双轨合规检测报告")
    print("=" * 50)

    if culture.get("error"):
        print(f"\n🌍 文化合规：检测失败 ❌")
        print(f"     {culture.get('error')}")
        if culture.get("raw"):
            print(f"     {culture.get('raw')}")
    else:
        risks = culture.get("risks", [])
        if risks:
            print(f"\n🌍 文化合规：发现 {len(risks)} 个风险")
            for r in risks:
                emoji = {"极高": "🔴", "高": "🟠", "中": "🟡", "低": "🟢"}.get(
                    r.get("severity", ""), "⚪"
                )
                print(f"  {emoji} {r.get('element', '')} - {r.get('severity', '')}")
                print(f"     {r.get('reason', '')}")
        else:
            print("\n🌍 文化合规：无风险 ✅")

    if hard:
        if hard.get("error"):
            print(f"\n⚠️ 硬合规：检测失败 ❌")
            print(f"     {hard.get('error')}")
        else:
            vios = hard.get("violations", [])
            if vios:
                print(f"\n⚠️ 硬合规：发现 {len(vios)} 个问题")
                for v in vios:
                    print(f"  🟠 {v.get('content', '')} - {v.get('severity', '')}")
            else:
                print("\n⚠️ 硬合规：无问题 ✅")

    print("\n" + "=" * 50)
    return report


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法：python main.py <图片路径> <目标市场> [Listing文案]")
        print("示例：python main.py TC-001.jpg middle_east")
        print("市场代码：middle_east / japan / us / eu / southeast_asia")
        sys.exit(1)

    generate_report(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
