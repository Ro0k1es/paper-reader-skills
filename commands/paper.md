# /paper command for Claude Code

Expected usage:

```text
/paper <PDF_FILE_PATH>
```

Instruction to Claude Code:

1. Treat the first argument as a local PDF path.
2. Find the `paper-reader` skill folder.
3. Extract PDF text:

```bash
python3 scripts/extract_pdf_text.py "$ARGUMENTS" --backend auto --out "${ARGUMENTS%.pdf}_extracted_text.md"
```

4. Build a strict figure-caption index:

```bash
python3 scripts/find_figures.py "$ARGUMENTS" --json --out "${ARGUMENTS%.pdf}_figure_index.json"
python3 scripts/find_figures.py "$ARGUMENTS" --out "${ARGUMENTS%.pdf}_figure_index.md"
```

Important: this index is meant to find actual caption anchors and recommended visual pages. Use `recommended_page`, not just `caption_page`. Do not use body-text mentions such as `Fig. 1a,b` as figure pages. If the result is confusing, diagnose with:

```bash
python3 scripts/find_figures.py "$ARGUMENTS" --include-references --out "${ARGUMENTS%.pdf}_figure_index_with_refs.md"
```

5. Extract complete composite figure images:

```bash
python3 scripts/extract_figures.py "$ARGUMENTS" --figures fig1,fig2,fig3 --names fig1,fig2,fig3 --crop --debug
```

If caption detection fails or the manifest reports `skipped`, inspect the PDF or rendered pages around the caption to locate the real visual figure page first, then use page numbers:

```bash
python3 scripts/extract_figures.py "$ARGUMENTS" --pages <real_figure_pages> --names fig1,fig2,... --crop --debug
```

If any crop is incomplete, rerun on the verified visual page with:

```bash
python3 scripts/extract_figures.py "$ARGUMENTS" --pages <real_figure_pages> --names fig1,fig2,... --full-page
```

6. When figures/tables are central, render the relevant pages for visual inspection:

```bash
python3 scripts/render_pdf_pages.py "$ARGUMENTS" --pages <page_numbers> --out-dir "${ARGUMENTS%.pdf}_page_images"
```

7. Generate a Chinese reading note following:
   - `references/reading_note_template.md`
   - `references/wechat_narrative_style.md`

8. Write in the original Paper Reader style: professional Chinese narrative, question-driven, figure-centered, evidence-bound, critical, and useful for the user's autoimmune disease epithelial-cell MP project. Use the uploaded WeChat article style only for structure and readability, not for overstatement. For “核心发现总结”, use discovery cards / evidence chains rather than a large table unless the user explicitly requests a table.

9. Embed figures using the actual relative path to `<PDF_STEM>_assets/`, for example:

```markdown
![Fig. 1](paper_assets/fig1.png)
```

10. Save the final note next to the PDF as:

```text
<PDF_STEM>_reading_note.md
```

11. Reply with the saved path and a 1-2 sentence Chinese summary.
