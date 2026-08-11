import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from main import ComplianceChecker, ComplianceConfig, SailMirrorError

ROOT_DIR = Path(__file__).parent
TEMP_UPLOAD = ROOT_DIR / "temp_upload.jpg"
RULES_FILE = ROOT_DIR / "knowledge" / "culture_rules_v1.json"

app = Flask(__name__)
CORS(app)


@app.route("/")
def index():
    return send_from_directory(ROOT_DIR, "index.html")


@app.route("/detect", methods=["POST"])
def detect():
    temp_saved = False
    try:
        if "image" not in request.files:
            return jsonify({"error": "缺少 image 文件"}), 400

        image = request.files["image"]
        if not image.filename:
            return jsonify({"error": "image 文件为空"}), 400

        market = (request.form.get("market") or "").strip()
        if not market:
            return jsonify({"error": "缺少 market 参数"}), 400

        listing = (request.form.get("listing") or "").strip()

        image.save(TEMP_UPLOAD)
        temp_saved = True

        checker = ComplianceChecker(ComplianceConfig())
        result = checker.check(str(TEMP_UPLOAD), market, listing or None)
        return jsonify(result)

    except SailMirrorError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if temp_saved and TEMP_UPLOAD.exists():
            TEMP_UPLOAD.unlink(missing_ok=True)


@app.route("/knowledge/culture_rules_v1.json")
def culture_rules():
    if not RULES_FILE.exists():
        return jsonify({"error": f"规则库文件不存在: {RULES_FILE.name}"}), 404
    return send_from_directory(RULES_FILE.parent, RULES_FILE.name)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
