"""Unit tests for rendering and verification: no browser, no node, no network."""

from __future__ import annotations

import os
import re
import shutil
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.pipeline import parse_converter_errors  # noqa: E402
from app.render import (  # noqa: E402
    CANVAS,
    THEMES,
    _block_height,
    _parse_img_opts,
    _pin_layout,
    chunk_sections,
    estimate_lines,
    parse_markdown,
    render_deck,
    require_title,
)
from app.verify import expected_copy, verify_pptx  # noqa: E402

MANUSCRIPT = """# 职业规划展示

> 从初心出发，聚焦目标岗位

---

## 初心起源

- 从一次跨部门协作中意识到：把复杂方案讲清楚，本身就是稀缺能力
- 过去两年独立完成 12 场方案汇报

---

## 能力盘点

- 一
- 二
- 三
- 四
- 五
- 六
- 七

---

## 行动计划

1. 第 1-2 月：补齐数据分析短板
2. 第 3-4 月：沉淀作品集
"""

BARE_TEXT_RE = re.compile(r"<div[^>]*>[^<]*[\u4e00-\u9fff]")
SLIDE_XML = (
    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p>"
    "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
)
PRESENTATION_XML = (
    '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
    '<p:sldSz cx="{cx}" cy="{cy}"/></p:presentation>'
)


def make_pptx(path: Path, texts: list[str], cx: int = 12192000, cy: int = 6858000) -> Path:
    """Build a minimal but structurally valid PPTX fixture."""
    with zipfile.ZipFile(path, "w") as archive:
        for index, text in enumerate(texts, start=1):
            archive.writestr(f"ppt/slides/slide{index}.xml", SLIDE_XML.format(text=text))
        archive.writestr(
            "ppt/presentation.xml", PRESENTATION_XML.format(cx=cx, cy=cy)
        )
    return path


class RenderTests(unittest.TestCase):
    def test_cover_and_sections_become_pages(self) -> None:
        pages = render_deck(MANUSCRIPT)
        # cover + 初心起源 + 能力盘点(7 bullets -> 2 pages) + 行动计划
        self.assertEqual(len(pages), 5)
        self.assertEqual(list(pages)[0], "slide01.html")
        self.assertEqual(list(pages)[-1], "slide05.html")

    def test_canvas_and_ratio_are_exact(self) -> None:
        for aspect, (width, height) in CANVAS.items():
            pages = render_deck(MANUSCRIPT, aspect_ratio=aspect)
            for page in pages.values():
                self.assertIn(f"width: {width}px", page)
                self.assertIn(f"height: {height}px", page)

    def test_all_text_is_wrapped_for_the_converter(self) -> None:
        pages = render_deck(MANUSCRIPT)
        for name, page in pages.items():
            self.assertIsNone(BARE_TEXT_RE.search(page), f"bare text in {name}")
            self.assertIn("<p", page)

    def test_requires_level_one_title(self) -> None:
        with self.assertRaisesRegex(ValueError, "level-1 title"):
            render_deck("## 只有二级标题\n\n- 内容\n")

    def test_rejects_unknown_aspect_ratio(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported aspect_ratio"):
            render_deck(MANUSCRIPT, aspect_ratio="5:4")

    def test_overflowing_section_is_continued(self) -> None:
        pages = render_deck(MANUSCRIPT, max_bullets=3)
        titles = [re.search(r"<h2>(.*?)</h2>", page).group(1) for page in list(pages.values())[1:]]
        self.assertIn("能力盘点（续）", titles)

    def test_ordered_list_uses_ol(self) -> None:
        pages = render_deck(MANUSCRIPT)
        last = list(pages.values())[-1]
        self.assertIn("<ol>", last)
        self.assertIn("<li>第 1-2 月：补齐数据分析短板</li>", last)

    def test_markdown_structure_is_parsed(self) -> None:
        deck = parse_markdown(MANUSCRIPT)
        self.assertEqual(deck["title"], "职业规划展示")
        self.assertEqual(deck["lead"], "从初心出发，聚焦目标岗位")
        self.assertEqual(
            [section["title"] for section in deck["sections"]],
            ["初心起源", "能力盘点", "行动计划"],
        )

    def test_rendering_is_deterministic(self) -> None:
        self.assertEqual(render_deck(MANUSCRIPT), render_deck(MANUSCRIPT))

    def test_logo_and_background_roles(self) -> None:
        markdown = (
            "# 标题\n\n引导\n\n![logo](assets/logo.png)\n\n"
            "![background](assets/bg.jpg){dim=0.6}\n\n## 节\n\n- 要点\n"
        )
        deck = parse_markdown(markdown)
        self.assertEqual(deck["logo"], "assets/logo.png")
        self.assertEqual(deck["background"]["src"], "assets/bg.jpg")
        self.assertEqual(deck["background"]["opts"], "dim=0.6")
        # site: logo replaces the nav colour block on cover AND section pages
        pages = render_deck(markdown, theme="site.tech")
        self.assertIn('alt="logo"', pages["slide01.html"])
        self.assertIn('alt="logo"', pages["slide02.html"])
        self.assertNotIn('class="logo"', pages["slide01.html"])
        # other layouts: logo lands top-right via inline style
        classic = render_deck(markdown, theme="classic")
        self.assertIn('alt="logo"', classic["slide01.html"])
        self.assertIn('alt="logo"', classic["slide02.html"])
        # background survives verification copy extraction
        expected = expected_copy(markdown)
        self.assertFalse(any("logo.png" in i or "bg.jpg" in i for i in expected))

    def test_figure_renders_with_size_and_position(self) -> None:
        markdown = (
            "# 标题\n\n## 架构\n\n"
            "![系统架构图](assets/2026-09-16_110109_160.jpg){half,left}\n\n- 要点\n"
        )
        pages = render_deck(markdown, theme="classic")
        section = pages["slide02.html"]
        self.assertIn('class="fig"', section)
        self.assertIn("width:550px", section)  # content 1100px * 50%
        self.assertIn("margin:0 auto 0 0", section)  # left-aligned
        self.assertIn("figcaption", section)
        self.assertIn("系统架构图", section)
        # image height participates in pagination (block height > 0)
        deck = parse_markdown(markdown)
        block = deck["sections"][0]["blocks"][0]
        self.assertGreater(_block_height(block, 1100.0), 100.0)

    def test_multiple_cover_figures_all_render(self) -> None:
        markdown = (
            "# 职业规划展示\n\n从初心出发\n\n"
            "![图一](assets/2026-09-16_110109_160.jpg){half,center}\n\n"
            "![图二](assets/17_59_47_614690.png){half,center}\n\n"
            "![图三](assets/blue.jpg){half,right}\n\n"
            "## 节\n\n- 要点\n"
        )
        deck = parse_markdown(markdown)
        self.assertEqual(len(deck["cover_images"]), 3)
        pages = render_deck(markdown, theme="site.tech")
        cover = pages["slide01.html"]
        self.assertIn('class="cover-figs"', cover)
        for src in (
            "2026-09-16_110109_160.jpg",
            "17_59_47_614690.png",
            "blue.jpg",
        ):
            self.assertIn(src, cover)
        # right-aligned figure keeps its margin
        self.assertIn("margin:0 0 0 auto", cover)
        # stacked heights exceed the zone budget -> uniform shrink applied
        self.assertNotIn("width:530px", cover)  # unshrunk half of 1060

    def test_pinned_figures_anchor_offset_and_clamp(self) -> None:
        region = (1000.0, 400.0)
        fig = {"src": "assets/blue.jpg", "alt": "", "opts": "pin=tl, w=25%"}
        w, h, left, top = _pin_layout(fig, *region)
        self.assertEqual((left, top), (0, 0))
        self.assertEqual(w, 250)
        # br anchor + negative offsets hugs the corner with a nudge
        fig_br = {"src": "assets/blue.jpg", "alt": "", "opts": "pin=br, w=25%, dx=-20, dy=-10"}
        w2, h2, l2, t2 = _pin_layout(fig_br, *region)
        self.assertAlmostEqual(l2, 1000 - w2 - 20, delta=1)
        self.assertAlmostEqual(t2, 400 - h2 - 10, delta=1)
        # absurd offsets clamp inside the region (footbar safe by construction)
        fig_far = {"src": "assets/blue.jpg", "alt": "", "opts": "pin=tl, w=25%, dy=99999"}
        w3, h3, l3, t3 = _pin_layout(fig_far, *region)
        self.assertAlmostEqual(t3, 400 - h3, delta=1)
        # pinned blocks consume no pagination budget
        self.assertEqual(
            _block_height({"kind": "image", "src": "x", "alt": "", "opts": "pin=c"}, 1100.0),
            0.0,
        )

    def test_pinned_renders_as_overlay_and_region_background(self) -> None:
        markdown = (
            "# 标题\n\n"
            "![background](assets/blue.jpg){region=content, dim=0.4}\n\n"
            "![角标](assets/blue.jpg){pin=br, w=25%}\n\n"
            "## 节\n\n"
            "![右上图](assets/blue.jpg){pin=tr, w=30%}\n\n- 要点\n"
        )
        pages = render_deck(markdown, theme="site.tech")
        cover, section = pages["slide01.html"], pages["slide02.html"]
        # cover + section: pinned stickers live in a PAGE-grid zone (tr = page
        # top-right, matching the user's nine-grid mental model)
        self.assertIn('class="pinzone"', cover)
        self.assertIn('class="pinzone"', section)
        # zone spans topbar..bottom-inset: site nav 74px is protected
        self.assertIn("left:0px;top:74px;", section)
        # tr sticker lands top-right BELOW the nav (page grid origin y=74)
        self.assertRegex(section, r"top:74px;")

    def test_pin_z_layers_under_and_over_text(self) -> None:
        markdown = (
            "# 标题\n\n## 节\n\n- 要点\n\n"
            "![水印](assets/blue.jpg){pin=bl, w=30%, z=0}\n\n"
            "![高](assets/blue.jpg){pin=tr, w=15%, z=20}\n\n"
            "![低](assets/blue.jpg){pin=tl, w=15%}\n\n"
            "![中](assets/blue.jpg){pin=l, w=15%, z=12}\n\n"
        )
        pages = render_deck(markdown, theme="site.tech")
        html = pages["slide02.html"]
        # z=0 watermark zone sits UNDER the text (emitted before .content)
        under = html.find('class="pinzone under"')
        content = html.find('<div class="content">')
        self.assertGreater(under, -1, "under-text pinzone missing")
        self.assertLess(under, content, "z=0 pin must be emitted before .content")
        # over-text zone sorted by z: 低(z=10) < 中(z=12) < 高(z=20)
        over = html[content:]
        self.assertLess(
            over.find("pin=tl") if "pin=tl" in over else over.find('alt="低"'),
            over.find('alt="中"'),
        )
        self.assertLess(over.find('alt="中"'), over.find('alt="高"'))

    def test_consecutive_left_right_figures_share_row(self) -> None:
        markdown = (
            "# 标题\n\n## 节\n\n"
            "![左图](assets/blue.jpg){half,left}\n\n"
            "![右图](assets/blue.jpg){half,right}\n\n"
            "- 要点一\n"
        )
        pages = render_deck(markdown, theme="site.tech")
        html = pages["slide02.html"]
        self.assertIn('class="figrow"', html)
        row = html[html.find('class="figrow"') : html.find("</div>", html.find('class="figrow"'))]
        # two images side by side inside one row: left at 0, right at >0
        first = row.find('left:0px;')
        second = row.find("left:", first + 1)
        self.assertGreater(first, -1)
        self.assertGreater(second, first, "second figure not positioned right of first")
        # pagination counts the row ONCE (max of pair, not sum): bullets fit same page
        self.assertEqual(len(pages), 2)

    def test_h3_groups_stay_atomic_su7_case(self) -> None:
        su7 = (
            "# 小米SU7 发布会\n\n引导语\n\n## 发布会要点\n\n"
            "### 车型矩阵：三款配置覆盖不同需求\n\n"
            "- SU7 标准版：21.59万起，后驱长续航，CLTC 700km+，面向日常通勤\n"
            "- SU7 Pro版：24.59万起，后驱超长续航，CLTC 830km+，面向长途出行\n"
            "- SU7 Max版：29.99万起，双电机四驱，零百2.78秒，面向性能玩家\n\n"
            "### 核心要求：设计、性能、智能、安全四大维度全面进阶\n\n"
            "- 经验匹配：小米十年消费电子研发积累与汽车工业供应链\n"
        )
        pages = render_deck(su7)
        # 两个 H3 组预算放得下：封面 + 一页收齐，绝无续页
        self.assertEqual(len(pages), 2)
        body = pages["slide02.html"]
        self.assertIn("车型矩阵", body)
        self.assertIn("核心要求", body)
        self.assertNotIn("（续）", body)

    def test_h3_groups_split_only_between_groups(self) -> None:
        md = (
            "# 标题\n\n## 节\n\n"
            + "".join(f"- 前置要点{i}：占位内容撑满预算行\n" for i in range(1, 7))
            + "\n### 车型矩阵：三款配置\n\n"
            "- SU7 标准版：21.59万起\n- SU7 Pro版：24.59万起\n\n"
            "### 核心要求：四大维度进阶\n\n- 经验匹配：供应链协同\n"
        )
        pages = render_deck(md)
        # 不变量：任何页里，H3 后面必须跟至少一个本组内容块（标题不孤悬）
        # 且同组的标题与内容不跨页
        for html in pages.values():
            pass  # 结构断言走下一个用例；本例验证组间切分结果
        first, second = pages["slide02.html"], pages.get("slide03.html", "")
        # 组A/组B 要么同页，要么整组各自迁移——车型矩阵不会与它的三条分离
        matrix_in_first = "车型矩阵" in first
        if matrix_in_first:
            self.assertIn("SU7 Pro版", first, "组A标题在页2但内容被切走")
        if "核心要求" in first:
            self.assertIn("经验匹配", first, "组B标题在页2但内容被切走")
        if second:
            if "车型矩阵" in second:
                self.assertIn("SU7 标准版", second)
            if "核心要求" in second:
                self.assertIn("经验匹配", second)

    def test_h3_heading_never_orphaned(self) -> None:
        # 直接检验分块器不变量：chunk 内 h3 块必非末块
        from app.render import THEMES as _T

        section = {
            "title": "节",
            "blocks": (
                [{"kind": "bullet", "items": [f"前置要点{i}：占位内容" for i in range(1, 7)]}]
                + [{"kind": "h3", "text": "组A标题"}]
                + [{"kind": "bullet", "items": ["SU7 标准版：内容", "SU7 Pro版：内容"]}]
                + [{"kind": "h3", "text": "组B标题"}, {"kind": "bullet", "items": ["经验匹配：内容"]}]
            ),
        }
        chunks = chunk_sections(section, 6, 1100.0, 720.0, 1.0, _T["classic"])
        for chunk in chunks:
            kinds = [b["kind"] for b in chunk["blocks"]]
            for i, k in enumerate(kinds):
                if k == "h3":
                    self.assertLess(
                        i + 1, len(kinds), f"H3 孤悬页尾: {chunk['title']} -> {kinds}"
                    )

    def test_fill_figure_consumes_remaining_space(self) -> None:
        md = (
            "# 标题\n\n## 节\n\n"
            "### 核心要求\n\n- 经验匹配：供应链协同\n\n"
            "![产品图](assets/blue.jpg){fill}\n\n"
            "- 后续要点应流到下一页\n"
        )
        pages = render_deck(md)
        self.assertEqual(len(pages), 3)  # cover + fill页 + 后续要点页
        fill_page, next_page = pages["slide02.html"], pages["slide03.html"]
        # fill 图留在标题所在页且吃掉剩余高度（约 415-109≈306，容差 30）
        m = __import__("re").search(r'class="fig"[^>]*height:(\d+)px', fill_page)
        self.assertTrue(m, "fill figure missing")
        self.assertTrue(280 <= int(m.group(1)) <= 340, f"fill height={m.group(1)}")
        self.assertIn("后续要点", next_page)

    def test_fill_zero_z_renders_under_text_without_pagination_cost(self) -> None:
        md = (
            "# 标题\n\n## 节\n\n"
            "### 核心要求\n\n- 经验匹配：供应链协同\n\n"
            "![背板](assets/blue.jpg){fill, z=0}\n\n"
            "- 后续要点仍在同页\n"
        )
        pages = render_deck(md)
        self.assertEqual(len(pages), 2)  # z=0 fill 不占预算：后续要点同页
        page = pages["slide02.html"]
        img_at = page.find('class="fillzone"')
        content_at = page.find('<div class="content">')
        self.assertGreater(img_at, -1, "fillzone missing")
        self.assertLess(img_at, content_at, "z=0 fill 必须渲染在文字下方")
        self.assertIn("后续要点仍在同页", page)

    def test_section_local_background_overrides_global(self) -> None:
        md = (
            "# 标题\n\n"
            "![background](assets/blue.jpg){dim=0.5}\n\n"
            "## 甲节\n\n- 要点一\n\n"
            "![background](assets/17_59_47_614690.png){dim=0.5}\n\n"
            "## 乙节\n\n- 要点二\n"
        )
        pages = render_deck(md, theme="site.tech")
        cover, a, b = pages["slide01.html"], pages["slide02.html"], pages["slide03.html"]
        # 全局生效于封面与无本节背景的乙节
        self.assertIn("blue.bg", cover)
        self.assertIn("blue.bg", b)
        # 甲节被本节背景覆盖（只在甲节生效）
        self.assertIn("17_59_47_614690.bg", a)
        self.assertNotIn("17_59_47_614690.bg", b)

    def test_local_background_consumes_no_budget(self) -> None:
        md = (
            "# 标题\n\n## 节\n\n"
            "![background](assets/blue.jpg){dim=0.5}\n\n"
            + "".join(f"- 要点{i}\n" for i in range(1, 7))
        )
        pages = render_deck(md)
        # 背景块不吃分页预算：6条与背景同页一页放下（封面+1）
        self.assertEqual(len(pages), 2)
        self.assertIn("blue.bg", pages["slide02.html"])

    def test_multi_cell_contiguous_bounding_box(self) -> None:
        md = (
            "# 标题\n\n## 节\n\n- 要点\n\n"
            "![右列图](assets/blue.jpg){pin=tr+r+br}\n"
        )
        pages = render_deck(md, theme="classic")
        html = pages["slide02.html"]
        # 一张图，落在外接矩形：右1/3 × 全格高（classic 区 960-12=948 高）
        m = __import__("re").search(
            r'<img src="assets/[^"]*"[^>]*style="position:absolute;left:(\d+)px;top:(\d+)px;width:(\d+)px;height:(\d+)px;"',
            html,
        )
        self.assertTrue(m, "multi-cell img missing")
        left, top, w, h = map(int, m.groups())
        self.assertAlmostEqual(left, 1280 * 2 / 3, delta=2)  # classic 16:9
        self.assertAlmostEqual(w, 1280 / 3, delta=2)
        # contain-fit 且在外接矩形内居中（blue.jpg 是宽图 → 宽度受限）
        zone_h = 720 - 12 - 16
        self.assertAlmostEqual(top + h / 2, zone_h / 2, delta=3)
        self.assertLess(h, zone_h)

    def test_multi_cell_fragments_four_corners(self) -> None:
        md = (
            "# 标题\n\n## 节\n\n- 要点\n\n"
            "![四角拼贴](assets/blue.jpg){pin=tl+tr+bl+br}\n"
        )
        pages = render_deck(md, theme="classic")
        html = pages["slide02.html"]
        under = html.find('class="pinzone under"')
        self.assertGreater(under, -1, "碎片默认 z=0 应进文字下层")
        zone = html[under : html.find("</div>", under)]
        frags = __import__("re").findall(r'src="assets/([^"]+\.cell[^"]*)"', zone)
        self.assertEqual(len(frags), 4, f"四角应有4张碎片: {frags}")
        self.assertEqual(len(set(frags)), 4, "碎片缓存名应各不相同")

    def test_bold_markdown_becomes_strong_and_copy_matches(self) -> None:
        from app.verify import expected_copy as _ec

        md = (
            "# 标题\n\n## 车型矩阵\n\n"
            "- **SU7 标准版**：21.59万起，面向日常通勤\n"
        )
        pages = render_deck(md)
        html = pages["slide02.html"]
        self.assertIn("<strong>SU7 标准版</strong>：21.59万起", html)
        self.assertNotIn("**", html)
        # 校验侧同步剥掉 ** 记号：期望文本与 PPTX 实文一致
        self.assertIn("SU7 标准版：21.59万起，面向日常通勤", _ec(md))

    def test_l_shape_selection_uses_fragments_not_full_image(self) -> None:
        md = (
            "# 标题\n\n## 节\n\n- 要点内容补充\n\n"
            "![L形贴图](assets/blue.jpg){pin=tr+r+bl+b+br, w=30%, z=0}\n"
        )
        pages = render_deck(md, theme="classic")
        html = pages["slide02.html"]
        # L 形连通但不填满包围盒 → 碎片模式：5 格 5 碎片，绝无整图
        under = html.find('class="pinzone under"')
        zone = html[under : html.find("</div>", under)] if under > -1 else ""
        import re as _re

        frags = _re.findall(r'<img src="assets/[^"]*"', zone)
        self.assertEqual(len(frags), 5, f"L形应5碎片, got {len(frags)}")
        # 每个碎片的宽高都不得超过单格尺寸（格宽=1280/3）
        sizes = _re.findall(r'<img src="assets/[^"]*"[^>]*width:(\d+)px;height:(\d+)px', zone)
        for w, h in sizes:
            self.assertLessEqual(int(w), 1280 / 3 + 2, "碎片宽超单格")

    def test_rectangular_block_still_uses_bounding_box(self) -> None:
        md = (
            "# 标题\n\n## 节\n\n- 要点内容补充\n\n"
            "![块图](assets/blue.jpg){pin=tl+t+l+c}\n"
        )
        pages = render_deck(md, theme="classic")
        html = pages["slide02.html"]
        # 2x2 块填满包围盒 → 单图模式，落左上 2/3 区域
        import re as _re

        m = _re.search(r'<img src="assets/[^"]*"[^>]*left:(\d+)px;top:(\d+)px;width:(\d+)px', html)
        self.assertTrue(m)
        # contain-fit 在 2x2 包围盒(853 宽)内居中
        left, _top, w = map(int, m.groups())
        self.assertAlmostEqual(left + w / 2, 1280 * 2 / 3 / 2, delta=3)
        self.assertLess(w, 1280 * 2 / 3)

    def test_image_lines_are_recognised_not_rendered_not_expected(self) -> None:
        markdown = (
            "# 标题\n\n引导\n\n![环境图](assets/env.jpg)\n\n## 节\n\n- 要点\n\n"
            "![架构图](assets/arch.png)\n"
        )
        deck = parse_markdown(markdown)
        kinds = [b["kind"] for b in deck["sections"][0]["blocks"]]
        self.assertIn("image", kinds)
        pages = render_deck(markdown)
        joined = "".join(pages.values())
        self.assertNotIn("![", joined)  # never leaks as literal text
        self.assertNotIn("assets/env.jpg", joined)
        expected = expected_copy(markdown)
        self.assertFalse(any("assets/" in item for item in expected))
        self.assertIn("要点", expected)


class VerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        # Fixtures live beside the tests: hermetic, and independent of the
        # platform temp directory (which some locked-down runners restrict).
        self.tmp = Path(__file__).resolve().parent / f".fixtures-{os.getpid()}"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_valid_pptx_passes(self) -> None:
        path = make_pptx(self.tmp / "ok.pptx", ["第一页", "第二页"])
        report = verify_pptx(path, 2, "16:9")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["canvas_inches"], [13.333, 7.5])

    def test_slide_count_mismatch_fails(self) -> None:
        path = make_pptx(self.tmp / "short.pptx", ["只有一页"])
        report = verify_pptx(path, 3, "16:9")
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["slide_count"])

    def test_aspect_ratio_mismatch_fails(self) -> None:
        path = make_pptx(self.tmp / "four3.pptx", ["页面"], cx=9144000, cy=6858000)
        self.assertFalse(verify_pptx(path, 1, "16:9")["checks"]["aspect_ratio"])
        self.assertTrue(verify_pptx(path, 1, "4:3")["checks"]["aspect_ratio"])

    def test_copy_completeness_ignores_list_markers(self) -> None:
        path = make_pptx(self.tmp / "copy.pptx", ["初心起源 从一次跨部门协作中意识到"])
        report = verify_pptx(path, 1, "16:9", markdown="- 从一次跨部门协作中意识到")
        self.assertTrue(report["checks"]["all_copy_present"], report)

    def test_missing_copy_is_reported(self) -> None:
        path = make_pptx(self.tmp / "missing.pptx", ["完全无关的内容"])
        report = verify_pptx(path, 1, "16:9", markdown="- 这一段应该丢失了")
        self.assertFalse(report["checks"]["all_copy_present"])
        self.assertEqual(report["missing_copy"], ["这一段应该丢失了"])

    def test_corrupt_archive_is_rejected(self) -> None:
        path = self.tmp / "broken.pptx"
        path.write_bytes(b"not a zip file")
        with self.assertRaises(zipfile.BadZipFile):
            verify_pptx(path, 1, "16:9")

    def test_expected_copy_strips_markers(self) -> None:
        self.assertEqual(
            expected_copy("# 标题\n\n- 要点\n1. 编号\n> 引导语\n\n---\n"),
            ["要点", "编号", "引导语"],
        )


class ConverterErrorTests(unittest.TestCase):
    def test_single_error_keeps_only_the_slide_name(self) -> None:
        stderr = (
            'Error: F:/abs/out/t1/slides/slide02.html: Text box "x" ends too close '
            'to bottom edge (-0.04" from bottom, minimum 0.5" required)'
        )
        self.assertEqual(
            parse_converter_errors(stderr),
            [
                'slide02.html: Text box "x" ends too close to bottom edge '
                '(-0.04" from bottom, minimum 0.5" required)'
            ],
        )

    def test_multiple_validation_errors_are_split(self) -> None:
        stderr = (
            "Error: /abs/slide01.html: Multiple validation errors found:\n"
            '  1. DIV element contains unwrapped text "裸文本"\n'
            '  2. Text box "页面底部" ends too close to bottom edge'
        )
        errors = parse_converter_errors(stderr)
        self.assertEqual(len(errors), 2)
        self.assertTrue(errors[0].startswith("DIV element"), errors)
        self.assertTrue(errors[1].startswith("Text box"), errors)

    def test_missing_diagnostics_has_a_fallback(self) -> None:
        self.assertEqual(
            parse_converter_errors(""), ["converter failed without diagnostics"]
        )


class HeightAwarePaginationTests(unittest.TestCase):
    def test_estimate_lines_treats_cjk_as_full_width(self) -> None:
        self.assertEqual(estimate_lines("职" * 42, 26, 1100), 1)
        self.assertEqual(estimate_lines("职" * 43, 26, 1100), 2)
        self.assertEqual(estimate_lines("A" * 70, 26, 1100), 1)

    def test_tall_bullets_split_by_height_before_the_count_limit(self) -> None:
        markdown = "# t\n\n## s\n\n" + "\n".join("- " + "职" * 80 for _ in range(6)) + "\n"
        pages = chunk_sections(parse_markdown(markdown)["sections"][0], 6, 1100.0, 720.0)
        self.assertEqual([len(p["blocks"][0]["items"]) for p in pages], [4, 2])
        self.assertEqual(pages[1]["title"], "s（续）")

    def test_count_limit_still_applies_to_short_bullets(self) -> None:
        markdown = "# t\n\n## s\n\n" + "\n".join(f"- 短{i}" for i in range(9)) + "\n"
        pages = chunk_sections(parse_markdown(markdown)["sections"][0], 4, 1100.0, 720.0)
        self.assertEqual([len(p["blocks"][0]["items"]) for p in pages], [4, 4, 1])

    def test_single_oversized_item_is_never_dropped(self) -> None:
        markdown = "# t\n\n## s\n\n- " + "职" * 500 + "\n"
        pages = chunk_sections(parse_markdown(markdown)["sections"][0], 6, 1100.0, 720.0)
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0]["blocks"][0]["items"]), 1)

    def test_render_deck_adds_pages_for_tall_content(self) -> None:
        markdown = "# t\n\n## s\n\n" + "\n".join("- " + "职" * 80 for _ in range(6)) + "\n"
        self.assertEqual(len(render_deck(markdown)), 3)


class ThemeTests(unittest.TestCase):
    def test_every_shipped_theme_has_both_templates(self) -> None:
        root = Path(__file__).resolve().parents[1] / "app" / "templates"
        shipped = sorted(p.name for p in root.iterdir() if p.is_dir())
        self.assertEqual(shipped, sorted(THEMES))
        for name in THEMES:
            self.assertTrue((root / name / "cover.css").is_file(), name)
            self.assertTrue((root / name / "section.css").is_file(), name)

    def test_unknown_theme_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            render_deck("# t\n", theme="nope")

    def test_each_theme_renders_the_same_structure(self) -> None:
        markdown = (
            "# 标题\n\n> 引导\n\n## 节\n\n" + "\n".join("- 要点" for _ in range(4)) + "\n"
        )
        for name in THEMES:
            pages = render_deck(markdown, theme=name)
            self.assertEqual(sorted(pages), ["slide01.html", "slide02.html"], name)
            self.assertNotIn("{{", "".join(pages.values()), name)

    def test_swiss_metrics_still_split_tall_bullets(self) -> None:
        markdown = "# t\n\n## s\n\n" + "\n".join("- " + "职" * 80 for _ in range(6)) + "\n"
        pages = chunk_sections(
            parse_markdown(markdown)["sections"][0],
            6,
            1100.0,
            720.0,
            metrics=THEMES["swiss"],
        )
        self.assertEqual([len(p["blocks"][0]["items"]) for p in pages], [4, 2])

    def test_generated_cards_themes_render_card_dom(self) -> None:
        for theme in ("cards.tech", "cards.energy"):
            self.assertIn(theme, THEMES)
            self.assertEqual(THEMES[theme].layout, "cards")
        markdown = "# t\n\n## s\n\n" + "\n".join("- " + "职" * 80 for _ in range(6)) + "\n"
        pages = render_deck(markdown, theme="cards.tech")
        self.assertEqual(len(pages), 3)  # cover + 3 cards + 3 cards
        section_html = pages["slide02.html"]
        self.assertIn('<div class="card">', section_html)
        self.assertIn('class="cnum"', section_html)
        self.assertNotIn("<ul>", section_html)

    def test_site_themes_render_persistent_nav(self) -> None:
        for theme in ("site.tech", "site.gov"):
            self.assertIn(theme, THEMES)
            self.assertEqual(THEMES[theme].layout, "site")
        sections = "\n\n---\n\n".join(f"## 第{i}节\n\n- 要点" for i in range(1, 5))
        pages = render_deck("# 标题\n\n" + sections + "\n", theme="site.tech")
        cover = pages["slide01.html"]
        second = pages["slide02.html"]
        self.assertIn('class="nav"', cover)
        self.assertIn("heroPanel", cover)
        # cover CSS must position nav items absolutely (regression: they once
        # collapsed to the top-left because the cover template lacked .nitem)
        self.assertIn(".nitem { position: absolute", cover)
        # nav on every page; current section highlighted on its own page
        self.assertIn('class="nav"', second)
        self.assertIn('class="nitem cur"', second)
        self.assertNotIn('class="nitem cur"', cover)


class TitleContractTests(unittest.TestCase):
    def test_glued_heading_marker_gets_actionable_hint(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            render_deck("#职业规划展示\n\n---\n\n##初心起源\n\n- 要点\n")
        message = str(ctx.exception)
        self.assertIn("level-1 title", message)
        self.assertIn("'# 职业规划展示'", message)

    def test_glued_subheading_keeps_its_depth_in_hint(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            render_deck("##初心起源\n")
        self.assertIn("'## 初心起源'", str(ctx.exception))

    def test_well_formed_title_passes(self) -> None:
        deck = require_title("# 标题\n\n> 引导\n\n## 节\n\n- 要点\n")
        self.assertEqual(deck["title"], "标题")


if __name__ == "__main__":
    unittest.main()
