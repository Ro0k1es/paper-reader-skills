# Claude Code Paper Reader

A Claude Code-oriented `/paper` literature-reading skill for academic PDFs.

It generates Chinese Markdown reading notes with:

- rigorous narrative literature interpretation;
- figure-by-figure close reading;
- complete composite-figure extraction;
- evidence-strength judgments and constructive critique;
- reusable ideas for single-cell, spatial, mIF/IF, epithelial-state, immune-microenvironment, and autoimmune disease epithelial-cell meta-program projects.

## Key updates

1. **Stricter figure detection**
   - `find_figures.py` now distinguishes true caption anchors (`Fig. 1 | ...`, `Fig. 1. ...`) from body-text references (`Fig. 1a,b`).
   - `extract_figures.py --figures fig1,...` uses those caption anchors, reducing the chance of extracting a Results text page instead of the actual figure.
   - `--debug`, `--full-page`, and `figure_manifest.json` remain available for validation.

2. **Balanced writing style**
   - Preserves the original Paper Reader strengths: Figure-by-Figure interpretation, logic-chain reconstruction, criticism, and “对当前研究的可借鉴点”.
   - Borrows only the most useful WeChat-style structure: sharper opening, optional “省流版预告”, smoother transitions, and a final点评 feel.
   - Adds strict scientific boundaries: no unsupported causality, no sample-level overclaiming from cell/ROI/spot statistics, and no exaggerated analogy.

## Contents

```text
paper-reader/
├── SKILL.md
├── README.md
├── CLAUDE.md.example
├── agents/
│   └── openai.yaml
├── commands/
│   └── paper.md
├── references/
│   ├── reading_note_template.md
│   └── wechat_narrative_style.md
└── scripts/
    ├── extract_pdf_text.py
    ├── find_figures.py
    ├── extract_figures.py
    └── render_pdf_pages.py
```

## Dependencies

Minimum:

```bash
python3 -m pip install pypdf
```

Recommended:

```bash
python3 -m pip install pymupdf pypdf
```

`pymupdf` is required for figure location, page rendering, and smart cropping.

## Basic usage inside Claude Code

```text
/paper /absolute/path/to/paper.pdf
```

Typical workflow:

```bash
python3 scripts/extract_pdf_text.py "/absolute/path/to/paper.pdf" --backend auto --out "/absolute/path/to/paper_extracted_text.md"
python3 scripts/find_figures.py "/absolute/path/to/paper.pdf" --json --out "/absolute/path/to/paper_figure_index.json"
python3 scripts/find_figures.py "/absolute/path/to/paper.pdf" --out "/absolute/path/to/paper_figure_index.md"
python3 scripts/extract_figures.py "/absolute/path/to/paper.pdf" --figures fig1,fig2,fig3 --names fig1,fig2,fig3 --crop --debug
```

Figure assets are saved by default to:

```text
/absolute/path/to/paper_assets/
```

## Figure extraction examples

Locate true captions:

```bash
python3 scripts/find_figures.py paper.pdf
```

Diagnose body-text references versus captions:

```bash
python3 scripts/find_figures.py paper.pdf --include-references
```

Extract by figure label:

```bash
python3 scripts/extract_figures.py paper.pdf --figures fig1,fig2,fig3 --names fig1,fig2,fig3 --crop --debug
```

Extract by verified page numbers:

```bash
python3 scripts/extract_figures.py paper.pdf --pages 7,9,13 --names fig1,fig2,fig3 --crop --debug
```

Render full pages when a figure is split, unusually laid out, or cropped incorrectly:

```bash
python3 scripts/extract_figures.py paper.pdf --pages 7,9,13 --names fig1,fig2,fig3 --full-page
```

## Notes

- For scanned PDFs, OCR may still be required; this bundle does not include OCR by default.
- If a figure crop omits any panel, rerun with `--debug` and/or `--full-page` on the verified visual page.
- If the manifest says `skipped`, do not embed a text page. Inspect rendered pages around the caption and manually supply the correct page.
- Do not embed a partial or wrong figure just because it looks cleaner.
