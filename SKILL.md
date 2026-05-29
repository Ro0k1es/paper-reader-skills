---
name: paper-reader
description: Read, analyze, and summarize academic PDF papers into structured Chinese Markdown reading notes for Claude Code. Use when the user provides /paper with a PDF path, asks to deeply read a paper, locate and crop complete composite figures, extract figure-level evidence, summarize methods and innovations, critique evidence boundaries, or generate a same-directory *_reading_note.md file with a rigorous but readable Chinese literature-interpretation style.
---

# Paper Reader for Claude Code

## Core command

When the user types:

```bash
/paper <PDF_FILE_PATH>
```

read the local PDF and generate a Chinese Markdown note next to it:

```text
<PDF_STEM>_reading_note.md
```

Save figure assets in:

```text
<PDF_STEM>_assets/
```

## Execution workflow

1. Resolve the PDF path exactly as provided.
2. Verify that the file exists and has a `.pdf` extension.
3. Extract text:

```bash
python3 scripts/extract_pdf_text.py "/path/to/paper.pdf" --backend auto --out "/path/to/paper_extracted_text.md"
```

4. Build a strict figure-caption index with visual-page validation:

```bash
python3 scripts/find_figures.py "/path/to/paper.pdf" --json --out "/path/to/paper_figure_index.json"
python3 scripts/find_figures.py "/path/to/paper.pdf" --out "/path/to/paper_figure_index.md"
```

Important: use `recommended_page`, not merely `caption_page`. Rows with `warning:text-only-caption-page` mean the script found a caption-like line but did not find enough visual content; do not embed that page as a figure. Do not treat in-text mentions such as `Fig. 1a,b` in Results/Discussion as figure pages. To diagnose confusing PDFs, optionally run:

```bash
python3 scripts/find_figures.py "/path/to/paper.pdf" --include-references --out "/path/to/paper_figure_index_with_refs.md"
```

5. Extract complete composite figures from validated caption anchors:

```bash
python3 scripts/extract_figures.py "/path/to/paper.pdf" --figures fig1,fig2,fig3 --names fig1,fig2,fig3 --crop --debug
```

The extractor now skips suspicious text-only targets by default instead of saving them as figures. If a figure is skipped, inspect the PDF visually and rerun using verified page numbers.

If caption detection fails, manually identify the real figure page by inspecting the PDF/page render first, then use page numbers:

```bash
python3 scripts/extract_figures.py "/path/to/paper.pdf" --pages 7,9,13 --names fig1,fig2,fig3 --crop --debug
```

If any crop omits a panel, axis label, legend, scale bar, or panel letter, rerun with full-page rendering on the verified visual page:

```bash
python3 scripts/extract_figures.py "/path/to/paper.pdf" --pages 7,9,13 --names fig1,fig2,fig3 --full-page
```

6. Render important pages for visual inspection when figures, tables, microscopy images, spatial maps, or graphical abstracts are central:

```bash
python3 scripts/render_pdf_pages.py "/path/to/paper.pdf" --pages 7,9,13 --out-dir "/path/to/paper_page_images"
```

7. Read the extracted text, strict figure index, extracted figure PNGs, debug images when available, and rendered pages. Inspect visual content before writing Figure-by-Figure sections.
8. Generate the Chinese reading note using:
   - `references/reading_note_template.md`
   - `references/wechat_narrative_style.md`
9. Embed figures with relative Markdown paths, for example:

```markdown
![Fig. 1](paper_assets/fig1.png)
```

10. Save the final note as `<PDF_STEM>_reading_note.md` and reply with the saved path plus a 1-2 sentence Chinese summary.

Do not invent details. If metadata, p-values, sample sizes, software versions, author affiliations, or data/code availability are absent from the paper, write `文中未明确说明`.

## Figure extraction policy

Accurate figure capture is more important than tight cropping.

- Always run `find_figures.py --json` before figure extraction and read the `status` and `recommended_page` fields.
- The figure index is strict by default: it rejects body-text references like `Fig. 1a,b` and keeps caption anchors like `Fig. 1 | ...` or `Fig. 1. ...`.
- A page must contain sufficient non-text visual objects before it can be saved as a figure. Text-only or low-visual pages are skipped by default.
- Prefer `extract_figures.py --figures ... --crop --debug` for main figures.
- Treat each output PNG as a full figure unit containing all visible panels (`a,b,c,d...`) whenever possible.
- Inspect `*_debug_box.png` and `figure_manifest.json` before embedding figures. If `status` is `skipped`, do not embed that PNG placeholder; render pages around the caption and manually select the verified visual page.
- Use `--full-page` whenever automatic cropping is incomplete or suspicious, but only after verifying the page is a visual figure page.
- If a figure spans multiple PDF pages, save separate files such as `fig3_part1.png`, `fig3_part2.png`, and explain this in the note.
- If a publisher stores panels as vector objects or tiled images, do not rely on the largest embedded image. Use smart crop or full-page rendering.
- Do not embed a misleading partial figure just because it is visually cleaner.

## Writing style

Use the original Paper Reader style as the base: professional Chinese narrative, figure-centered, method-aware, critical, and useful for the user's research. The uploaded WeChat HTML examples should only contribute the best structural elements: a sharper opening, optional “省流版预告”, better transitions, and a more readable final点评. Do not fully imitate their tone.

Core rules:

1. Start from the scientific question, not the abstract.
2. Use a narrative opening: why this paper matters, what is surprising, and how it connects to the user's research interests.
3. Make the figure section the backbone of the note. Explain why each analysis was done before describing what it showed.
4. Weave methods into figure interpretation instead of isolating a dry methods section.
5. Use bilingual terms at first mention: 中文（English）.
6. Add judgment: evidence strength, logical gaps, alternative explanations, and what experiment/analysis would strengthen the claim.
7. Keep scientific boundaries strict: correlation is not causation; model/ROI/cell-level evidence cannot be overstated as patient-level causal proof.
8. Avoid translating the abstract, dumping data, writing “如图所示”, or producing panel-by-panel流水账.
9. Preserve the section “对当前研究的可借鉴点”, with special attention to the user's project on autoimmune disease epithelial-cell meta-programs (MPs).

## Output structure

Follow `references/reading_note_template.md` unless the user requests otherwise.

Required sections:

1. Title: `Journal Abbreviation | Chinese punchline`
2. 开场白
3. 省流版预告
4. 论文基本信息
5. 研究背景与核心科学问题
6. 研究设计速览
7. Figure-by-Figure 精读
8. 研究逻辑推理链
9. 核心发现总结（用发现卡片/证据链，不默认用表格）
10. 批判性分析 / 矛盾点
11. 对当前研究的可借鉴点
12. 核心结论

## Figure-by-Figure requirements

For every main figure:

- Write a subsection title that states the question answered by the figure.
- Embed the extracted figure image directly below the subsection title.
- Explain the figure's position in the whole paper: what came before, what this figure proves, and why the next figure follows.
- Cover all visible panels. For each panel, identify the analysis or experiment, comparison, sample/replicate unit when available, and main result.
- Record quantitative values, p-values, thresholds, software, statistical tests, and sample sizes when available.
- Distinguish descriptive, correlative, predictive, mechanistic, causal, and validation-level evidence.
- Flag likely overclaims or missing controls.

Use Extended Data or Supplementary figures only when they are essential to the main claim, clarify a contradiction, or provide reusable methods.

## Current-research relevance

In “对当前研究的可借鉴点”, always consider whether the paper offers something useful for:

- single-cell landscape construction across autoimmune target organs;
- epithelial-cell meta-program (MP) discovery;
- conserved versus disease-specific epithelial programs;
- epithelial-immune interaction or spatial niche analysis;
- marker selection for mIF/IF/ISH validation;
- figure design or analysis framing that can be reused in the user's autoimmune epithelial-cell MP project.

Do not force relevance. If the paper is unrelated, say so, then briefly state what still may be transferable at the level of methods, figure design, or writing.

## Quality checklist before saving

- [ ] Figure index used true caption anchors, not in-text references.
- [ ] `recommended_page` and visual-content status were checked; text-only pages were not embedded as figures.
- [ ] Figure files contain complete composite figures or clearly marked multi-part full pages.
- [ ] Figure crops were checked with `--debug` or full-page renders when the layout was complex.
- [ ] Title is an engaging Chinese punchline, not “Paper Title 阅读笔记”.
- [ ] 开场白 connects the paper to the reader's research context.
- [ ] Each main figure is analyzed by question, design, observation, interpretation, and evidence strength.
- [ ] Methods and parameters are woven into figure descriptions.
- [ ] Claims are tied to figure/table evidence.
- [ ] 批判性分析 identifies real evidence gaps and gives actionable “如果是我会怎么做” suggestions.
- [ ] 对当前研究的可借鉴点 includes autoimmune disease epithelial-cell MP relevance when appropriate.
- [ ] Missing information is marked as `文中未明确说明`.
- [ ] The note reads like a coherent scientific story, not a filled template.
- [ ] Markdown file is saved at `<PDF_STEM>_reading_note.md`.
