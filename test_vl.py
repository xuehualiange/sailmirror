import os
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from dashscope import MultiModalConversation

MODEL = "qwen3-vl-plus"
IMAGE_NAME = "test.jpg"

PROMPT = """请仔细分析这张图片，检测其中可能存在的文化禁忌元素，包括但不限于：
1. 宗教符号（如十字架、新月、法轮、佛像、宗教手势等）
2. 人物着装（是否暴露、是否涉及特定宗教/民族服饰的不当使用等）
3. 其他可能冒犯特定文化、宗教或民族群体的视觉元素

请按以下格式输出检测结果：
- 是否检测到文化禁忌元素：是/否
- 检测到的元素列表（若无则写"无"）
- 风险等级：低/中/高
- 详细说明与建议
"""


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
    print("文化禁忌检测结果")
    print("=" * 40)
    print(result)


if __name__ == "__main__":
    main()
