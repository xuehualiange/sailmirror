import os
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from dashscope import MultiModalConversation

MODEL = "qwen3-vl-plus"
IMAGE_NAME = "test.jpg"

PROMPT = """你是出海镜文化合规检测专家。目标市场：中东（沙特/阿联酋）。

请严格检测这张商品图片。重点检查：
1. 人物：女性是否暴露肩膀、手臂、腿部、胸部？任何皮肤暴露在中东都是极高风险
2. 宗教：有无十字架、佛像、宗教文字用于商业？
3. 颜色：有无大面积绿色（宗教关联）？
4. 文字：有无"Best""#1""100%"等绝对化用语？

判定标准：
- 极高风险：必须修改，否则下架/抵制
- 高风险：建议修改
- 中风险：提示注意
- 低风险：基本安全

请严格判定。宁可误判也不漏判。

输出JSON格式：
{
  "risks": [{"element": "具体元素", "severity": "极高/高/中/低", "reason": "原因"}],
  "overall_risk": "高/中/低"
}

只输出JSON，不要其他解释。"""


def load_api_key() -> str:
    load_dotenv()
    api_key = os.getenv("ALIYUN_API_KEY")
    if not api_key or api_key == "sk-your-api-key":
        print("错误：请在 .env 文件中设置有效的 ALIYUN_API_KEY")
        sys.exit(1)
    return api_key


def get_image_path() -> str:
    image_path = Path(__file__).parent / IMAGE_NAME
    if not image_path.exists():
        print(f"错误：未找到图片文件 {image_path}")
        sys.exit(1)
    return f"file://{image_path.resolve().as_posix()}"


def analyze_image(api_key: str, image_path: str) -> str:
    messages = [
        {
            "role": "user",
            "content": [
                {"image": image_path},
                {"text": PROMPT},
            ],
        }
    ]

    response = MultiModalConversation.call(
        api_key=api_key,
        model=MODEL,
        messages=messages,
    )

    if response.status_code != 200:
        print(f"API 调用失败：{response.code} - {response.message}")
        sys.exit(1)

    content = response.output.choices[0].message.content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and "text" in item:
                return item["text"]
        return str(content)
    return str(content)


def main() -> None:
    api_key = load_api_key()
    image_path = get_image_path()

    print(f"正在使用 {MODEL} 分析图片 {IMAGE_NAME} ...\n")
    result = analyze_image(api_key, image_path)
    print("=" * 40)
    print("文化合规检测结果（中东严格版）")
    print("=" * 40)
    print(result)


if __name__ == "__main__":
    main()
