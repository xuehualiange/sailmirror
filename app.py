import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st

from main import detect_culture, detect_hard

ROOT_DIR = Path(__file__).parent

MARKETS = {
    "middle_east": "中东（沙特/阿联酋）",
    "us": "美国（亚马逊）",
    "eu": "欧盟",
    "japan": "日本",
    "southeast_asia": "东南亚",
}

SEVERITY_EMOJI = {"极高": "🔴", "高": "🟠", "中": "🟡", "低": "🟢"}


def render_culture_result(result: dict) -> None:
    if result.get("error"):
        st.error(f"文化合规检测失败：{result['error']}")
        if result.get("raw"):
            st.code(result["raw"])
        return

    risks = result.get("risks", [])
    overall = result.get("overall_risk", "未知")
    st.metric("综合风险", overall)

    if not risks:
        st.success("未发现文化合规风险")
        return

    for risk in risks:
        emoji = SEVERITY_EMOJI.get(risk.get("severity", ""), "⚪")
        st.markdown(f"**{emoji} {risk.get('element', '未知')}** · {risk.get('severity', '未知')}")
        st.caption(risk.get("reason", ""))


def render_hard_result(result: dict) -> None:
    if result.get("error"):
        st.error(f"硬合规检测失败：{result['error']}")
        if result.get("raw"):
            st.code(result["raw"])
        return

    status = result.get("overall_status", "未知")
    st.metric("合规状态", status)

    violations = result.get("violations", [])
    if not violations:
        st.success("未发现硬合规问题")
        return

    for item in violations:
        st.markdown(f"**🟠 {item.get('content', '未知')}** · {item.get('severity', '未知')}")
        if item.get("suggestion"):
            st.caption(f"建议：{item['suggestion']}")
        elif item.get("type"):
            st.caption(f"类型：{item['type']}")


st.set_page_config(page_title="出海镜 SailMirror", page_icon="🚀", layout="wide")

st.title("🚀 出海镜 SailMirror")
st.caption("AI 跨境合规检测 · 硬合规 + 文化合规双轨报告")

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("📤 上传检测素材")
    uploaded = st.file_uploader("商品图片", type=["jpg", "jpeg", "png", "webp"])
    if uploaded:
        st.image(uploaded, caption="预览图", use_container_width=True)

    listing_text = st.text_area(
        "Listing 文案（可选）",
        placeholder='例如：Best Bluetooth Headphones #1 Quality',
        height=120,
    )

    market_code = st.selectbox(
        "目标市场",
        options=list(MARKETS.keys()),
        format_func=lambda code: MARKETS[code],
    )

    run = st.button("🔍 开始合规检测", type="primary", use_container_width=True)

with col_right:
    st.subheader("📊 双轨检测报告")

    if not run:
        st.info("上传图片或填写 Listing，选择目标市场后点击检测。")
    elif not uploaded and not listing_text.strip():
        st.warning("请至少上传商品图或填写 Listing 文案。")
    else:
        with st.spinner("AI 检测中，请稍候..."):
            culture_result = {}
            hard_result = {}

            if uploaded:
                suffix = Path(uploaded.name).suffix or ".jpg"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uploaded.getbuffer())
                    temp_path = tmp.name
                culture_result = detect_culture(temp_path, market_code)

            if listing_text.strip():
                hard_result = detect_hard(listing_text.strip(), market_code)

        st.caption(f"检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        st.caption(f"目标市场：{MARKETS[market_code]}")

        tab_culture, tab_hard = st.tabs(["🌍 文化合规", "⚠️ 硬合规"])

        with tab_culture:
            if uploaded:
                render_culture_result(culture_result)
            else:
                st.info("未上传图片，跳过文化合规检测。")

        with tab_hard:
            if listing_text.strip():
                render_hard_result(hard_result)
            else:
                st.info("未填写 Listing，跳过硬合规检测。")

st.divider()
st.markdown(
    "GitHub: [xuehualiange/sailmirror](https://github.com/xuehualiange/sailmirror) · "
    "在线 Demo: [Railway 部署版](https://sailmirror-production.up.railway.app/)"
)
