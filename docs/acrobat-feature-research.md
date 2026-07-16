# Adobe Acrobat Premium Feature Research → CoverUP Feature Map

Research date: 2026-07-16. Compiled from Adobe's feature and version-comparison pages and
independent comparisons. Tiers: **R** = free Acrobat Reader, **S** = Acrobat Standard,
**P** = Acrobat Pro / mobile "Acrobat Premium".

Status legend for CoverUP: ✅ implemented · ⚠️ partial (documented limitation) · ❌ out of scope (reason given).

| # | Category | Feature | Tier | CoverUP status |
|---|----------|---------|------|----------------|
| 1 | View | View / print / zoom / page navigation / search | R | ✅ view, zoom, nav existed; text search with hit navigation added |
| 2 | Create | Create PDF from images | S | ✅ existed (PNG/JPG import); multi-image → one PDF added |
| 3 | Create | Create PDF from Office / web / scanner | S/P | ⚠️ images→PDF supported; Office/web capture out of scope (requires MS Office / a browser engine) |
| 4 | Combine | Merge multiple PDFs | S | ✅ lossless merge (pypdf) |
| 5 | Organize | Delete / reorder / rotate pages | S | ✅ |
| 6 | Organize | Split by page ranges | S | ✅ |
| 7 | Organize | Extract pages | S | ✅ |
| 8 | Organize | Insert pages (from PDF / blank) | S | ✅ |
| 9 | Organize | Crop pages | S | ✅ |
| 10 | Compress | Reduce file size / optimizer | S | ✅ raster mode (flatten + downsample) and lossless mode (image recompress + stream compression) |
| 11 | Edit | Add text (typewriter) | S | ✅ canvas annotation, burned in on export |
| 12 | Edit | Insert image onto page | S | ✅ |
| 13 | Edit | Edit existing text/images in place | S | ⚠️ not feasible losslessly with an open-source Python stack; whiteout + retype workflow provided |
| 14 | Edit | Watermark (text/image) | S | ✅ |
| 15 | Edit | Headers & footers | S | ✅ |
| 16 | Edit | Page numbering + Bates numbering | P | ✅ |
| 17 | Comment | Highlight / underline / strikethrough | R | ✅ |
| 18 | Comment | Freehand draw (pencil) | R | ✅ |
| 19 | Comment | Shapes: rectangle / ellipse / line / arrow | R | ✅ |
| 20 | Comment | Stamps (Approved / Draft / custom) | R | ✅ |
| 21 | Comment | Text boxes / notes | R | ✅ text annotations (burned in) |
| 22 | Convert | PDF → Word (.docx) | S | ✅ paragraph-level reconstruction (layout not preserved — stated in dialog) |
| 23 | Convert | PDF → text / HTML / images | S | ✅ |
| 24 | Convert | PDF → Excel / PowerPoint | S | ⚠️ out of scope (reliable table/slide reconstruction needs heavy proprietary-grade tooling) |
| 25 | Convert | Export → InDesign / Illustrator / AutoCAD | P | ❌ proprietary formats |
| 26 | OCR | Recognize text → searchable PDF | P | ✅ via Tesseract when installed (auto-detected; feature disabled with hint otherwise) |
| 27 | Protect | Open password (document encryption) | S | ✅ AES-256 |
| 28 | Protect | Permission restrictions (print/copy/modify) | S | ✅ owner password + permission flags |
| 29 | Protect | Remove security | S | ✅ with known password |
| 30 | Protect | Redaction (permanent removal) | P | ✅ the app's core feature (rasterize + burn-in) |
| 31 | Protect | Sanitize / remove hidden data | P | ✅ strips XMP/doc info/JavaScript/embedded files/OpenAction; raster export flattens everything else |
| 32 | Protect | Certificate encryption | S | ⚠️ out of scope (recipient-certificate encryption not supported by the OSS stack); AES-256 password encryption covers protection |
| 33 | Sign | Fill & Sign (type / draw / image signature) | R | ✅ canvas placement, burned in |
| 34 | Sign | Certificate-based digital signature | S | ✅ PKCS#12 via pyHanko |
| 35 | Sign | Validate signatures | R | ✅ integrity + chain report |
| 36 | Sign | Request e-signatures (Acrobat Sign) | S | ❌ SaaS service |
| 37 | Forms | Fill AcroForm fields | R | ✅ lossless (pypdf) |
| 38 | Forms | Create fillable forms | S | ⚠️ form authoring out of scope for v1; typewriter fill covers flat forms |
| 39 | Compare | Compare two PDFs with difference report | P | ✅ page-image diff + side-by-side report |
| 40 | Docs | Document properties viewer/editor | S | ✅ |
| 41 | Batch | Action Wizard (batch sequences) | P | ✅ minimal: batch-apply one tool over a folder |
| 42 | Accessibility | Checker / tags / read-aloud | P | ❌ tagged-PDF authoring beyond OSS stack |
| 43 | Prepress | Preflight (PDF/A, PDF/X, PDF/E, PDF/UA), output preview, ink manager | P | ❌ prepress niche |
| 44 | Media | Embed video/audio | P | ❌ out of scope |
| 45 | Measure | Measure distance/area | P | ✅ simple ruler tool (points → cm/in via page size) |
| 46 | AI | AI Assistant (summarize / chat with PDF) | P add-on | ❌ requires a cloud LLM; the app is offline by design |
| 47 | Cloud | PDF Spaces / cloud storage / mobile sync | P | ❌ SaaS |

**Net:** 31 features shipped (4 pre-existing), 6 partial with documented limitations,
10 out of scope for a local, offline, open-source product.

## Sources

- [Acrobat features | Adobe Acrobat](https://www.adobe.com/acrobat/features.html)
- [Adobe Acrobat Standard vs Pro: compare Acrobat versions | Adobe](https://www.adobe.com/acrobat/pricing/compare-versions.html)
- [What's new in Acrobat on desktop | Adobe](https://helpx.adobe.com/acrobat/desktop/whats-new/whats-new-acrobat-desktop.html)
- [Acrobat on mobile subscriptions | Adobe](https://helpx.adobe.com/acrobat/mobile/subscription-refunds/about-subscriptions.html)
- [Subscription features — Acrobat for iOS Help | Adobe](https://www.adobe.com/devnet-docs/acrobat/ios/en/classic-subscription-features.html)
- [Acrobat Standard vs Pro vs Reader: 2026 Comparison | Mapsoft](https://mapsoft.com/posts/acrobat-standard-vs-pro.html)
- [Adobe Acrobat Standard vs. Acrobat Pro | PCWorld](https://www.pcworld.com/article/397929/adobe-acrobat-standard-dc-vs-adobe-acrobat-pro-dc.html)
- [Adobe Acrobat Pro vs Standard | PDFgear](https://www.pdfgear.com/pdf-editor-reader/adobe-acrobat-pro-vs-standard.htm)
- [Guide to Adobe Acrobat: Reader vs Standard vs Pro vs Premium | Net Carper](https://netcarper.com/article/guide-to-adobe-acrobat-reader-vs-standard-vs-pro-vs-premium)
- [Is Adobe Acrobat Worth It in 2026? | pdf.net](https://pdf.net/blog/is-adobe-acrobat-worth-it)
