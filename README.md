# 🚀 出海镜 SailMirror

AI跨境合规检测系统 — 硬合规+文化合规双轨报告

## 产品定位

面向跨境卖家的AI上新合规检测器。上传商品图和Listing，
输出目标市场的"能不能卖（硬合规）+ 卖不卖得动（文化合规）"双轨风险报告。

## 核心功能

- 🌍 文化合规检测：宗教符号、禁忌色、人物着装、手势姿态
- ⚠️ 硬合规检测：敏感词、平台规则、认证要求
- 📊 双轨报告：一份报告，两个维度

## 技术架构

多Agent协同 + 阿里云百炼

- qwen3-vl-plus：视觉理解
- qwen3.7-max：推理与报告生成
- Streamlit：前端展示

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env  # 填写你的API Key
python test_vl.py     # 运行检测测试
```
