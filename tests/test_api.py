"""End-to-end API tests. Require node plus Chromium.

Run with:  PPTSVC_E2E=1 python -m pytest tests/test_api.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

MANUSCRIPT = """# 职业规划展示

> 从初心出发，聚焦目标岗位

---

## 初心起源

- 从一次跨部门协作中意识到：把复杂方案讲清楚，本身就是稀缺能力
- 过去两年独立完成 12 场方案汇报

---

## 行动计划

1. 第 1-2 月：补齐数据分析短板
2. 第 3-4 月：沉淀作品集
"""

TALL_MANUSCRIPT = """# 转型计划展示

## 转型计划

- 锁定 AI 产品经理 / AI 应用工程师方向，系统研究目标公司的产品线与岗位要求，明确能力差距与补齐路径，形成定向投递清单并按周滚动更新
- 梳理过去三年的项目经验，提炼可迁移的方法论案例，形成三个不同复杂度的故事线，分别对应从零到一、规模化与降本增效三类典型场景
- 每周完成一次行业动态整理，覆盖大模型应用、智能体编排、多模态交互三个方向，沉淀为简报并在组内分享，同时维护术语表
- 参加两场线下行业交流活动，积累一线从业者视角的真实反馈与常见误区清单，扩展至少五位可深聊的同行关系
- 准备 AI 技术面试与场景设计面试，针对典型场景完成五套完整的方案演练，覆盖需求澄清、方案取舍与指标设计
- 复盘每轮投递与面试反馈，滚动修订定位、简历与作品集，确保材料与目标岗位的匹配度持续提升，最终完成岗位转型
"""


@unittest.skipUnless(
    os.environ.get("PPTSVC_E2E") == "1", "set PPTSVC_E2E=1 to run the browser test"
)
class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = self.enterContext(TestClient(app))

    def test_healthz_reports_dependencies(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["checks"]["node_available"], body)
        self.assertTrue(body["checks"]["converter_present"], body)
        self.assertTrue(body["checks"]["templates_present"], body)

    def test_index_serves_html_form(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("/v1/decks", response.text)

    def test_generate_download_and_reread(self) -> None:
        response = self.client.post(
            "/v1/decks", json={"markdown": MANUSCRIPT, "aspect_ratio": "16:9"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["checks"]["ok"], body["checks"])
        # cover + 初心起源 + 行动计划
        self.assertEqual(body["slides"], 3)

        download = self.client.get(body["download_url"])
        self.assertEqual(download.status_code, 200)
        self.assertGreater(len(download.content), 10_000)

        again = self.client.get(f"/v1/decks/{body['task_id']}")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["task_id"], body["task_id"])

    def test_dry_run_validates_without_artifact(self) -> None:
        response = self.client.post(
            "/v1/decks", json={"markdown": MANUSCRIPT, "dry_run": True}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["dry_run"])
        self.assertIsNone(body["artifact"])
        self.assertNotIn("download_url", body)
        self.assertEqual(body.get("height_scale"), 1.0)

    def test_overflowing_slide_is_rejected_with_422(self) -> None:
        huge = "很长的要点内容" * 400
        manuscript = f"# 溢出测试\n\n---\n\n## 单页\n\n- {huge}\n"
        response = self.client.post(
            "/v1/decks", json={"markdown": manuscript, "max_bullets": 1}
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("errors", response.json()["detail"])

    def test_unknown_task_returns_404(self) -> None:
        self.assertEqual(self.client.get("/v1/decks/20240101-000000-abcdef").status_code, 404)
        self.assertEqual(self.client.get("/v1/decks/not-a-task-id").status_code, 404)

    def test_bad_aspect_ratio_returns_400(self) -> None:
        response = self.client.post(
            "/v1/decks", json={"markdown": MANUSCRIPT, "aspect_ratio": "5:4"}
        )
        self.assertEqual(response.status_code, 400)

    def test_glued_heading_markers_return_400_not_500(self) -> None:
        mangled = "#职业规划展示\n\n##初心起源\n\n-\n要点一\n"
        response = self.client.post("/v1/decks", json={"markdown": mangled})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("space", response.json()["detail"])

    def test_theme_variants_generate_valid_decks(self) -> None:
        for theme in ("swiss", "midnight"):
            response = self.client.post(
                "/v1/decks", json={"markdown": MANUSCRIPT, "theme": theme}
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["checks"]["ok"], theme)

    def test_unknown_theme_returns_400(self) -> None:
        response = self.client.post(
            "/v1/decks", json={"markdown": MANUSCRIPT, "theme": "nope"}
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_preview_returns_real_html_pages(self) -> None:
        response = self.client.post(
            "/v1/preview", json={"markdown": MANUSCRIPT, "theme": "swiss"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["canvas"], [1280, 720])
        self.assertEqual(body["slides"], 3)
        self.assertEqual(len(body["pages"]), 3)
        self.assertEqual(body["pages"][0]["kind"], "cover")
        self.assertEqual(body["pages"][1]["kind"], "section")
        self.assertIn("#0369a1", body["pages"][1]["html"])
        self.assertNotIn("{{", body["pages"][0]["html"])

    def test_preview_rejects_bad_theme(self) -> None:
        response = self.client.post(
            "/v1/preview", json={"markdown": MANUSCRIPT, "theme": "nope"}
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_preview_returns_all_pages_not_a_cap(self) -> None:
        sections = "\n\n---\n\n".join(f"## 第{i}节\n\n- 要点" for i in range(1, 9))
        markdown = "# 全览\n\n" + sections + "\n"
        response = self.client.post("/v1/preview", json={"markdown": markdown})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["slides"], 9)
        self.assertEqual(len(body["pages"]), 9)

    def test_generated_industry_themes_generate_valid_decks(self) -> None:
        for theme in (
            "cards.tech",
            "cards.finance",
            "cards.edu",
            "cards.energy",
            "cards.gov",
            "cards.med",
            "site.tech",
            "site.gov",
        ):
            response = self.client.post(
                "/v1/decks", json={"markdown": MANUSCRIPT, "theme": theme}
            )
            self.assertEqual(response.status_code, 200, theme)
            self.assertTrue(response.json()["checks"]["ok"], theme)

    def test_tall_bullets_paginate_by_height(self) -> None:
        response = self.client.post("/v1/decks", json={"markdown": TALL_MANUSCRIPT})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["slides"], 3, response.text)
        self.assertTrue(body["checks"]["ok"], body["checks"])

    def test_single_oversized_bullet_reports_422_with_hint(self) -> None:
        manuscript = "# 溢出\n\n---\n\n## 单页\n\n- " + "超长内容" * 250 + "\n"
        response = self.client.post("/v1/decks", json={"markdown": manuscript})
        self.assertEqual(response.status_code, 422, response.text)
        errors = response.json()["detail"]["errors"]
        self.assertTrue(any("single bullet" in err for err in errors), errors)


if __name__ == "__main__":
    unittest.main()
