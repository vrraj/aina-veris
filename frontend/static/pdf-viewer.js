// Standalone PDF source viewer.
//
// URL params:
//   doc=<document_id>   served via /docling-document/{document_id}
//   src=<http(s) url>   fetched directly (subject to remote CORS)
//   page=<n>            1-based page to open
//   bbox=x0,y0,x1,y1    normalized (0-1, top-left origin) region to highlight
import * as pdfjsLib from '/static/vendor/pdfjs/pdf.min.mjs';

pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.mjs';

const params = new URLSearchParams(window.location.search);
const statusEl = document.getElementById('status');
const wrapEl = document.getElementById('page-wrap');
const canvas = document.getElementById('pdf-canvas');
const overlay = document.getElementById('overlay');
const pageIndicator = document.getElementById('page_indicator');
const prevBtn = document.getElementById('prev_page');
const nextBtn = document.getElementById('next_page');
const titleEl = document.getElementById('doc_title');
const rawLink = document.getElementById('raw_link');

const docId = (params.get('doc') || '').trim();
const srcParam = (params.get('src') || '').trim();
const initialPage = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);
const bboxParam = (params.get('bbox') || '')
  .split(',')
  .map((v) => parseFloat(v))
  .filter((v) => Number.isFinite(v));
const highlightBox = bboxParam.length === 4 ? bboxParam : null;

const sourceUrl = docId
  ? `/docling-document/${encodeURIComponent(docId)}`
  : srcParam;

let pdfDoc = null;
let pageNum = initialPage;
let renderToken = 0;

function setStatus(msg, isError) {
  statusEl.textContent = msg;
  statusEl.style.display = 'block';
  statusEl.style.color = isError ? '#b91c1c' : '';
  wrapEl.style.display = 'none';
}

function drawHighlight(cssWidth, cssHeight, showBox) {
  overlay.innerHTML = '';
  overlay.style.width = `${cssWidth}px`;
  overlay.style.height = `${cssHeight}px`;
  if (!highlightBox || !showBox) return;
  const [x0, y0, x1, y1] = highlightBox;
  const box = document.createElement('div');
  box.className = 'region-box';
  box.style.left = `${x0 * cssWidth}px`;
  box.style.top = `${y0 * cssHeight}px`;
  box.style.width = `${(x1 - x0) * cssWidth}px`;
  box.style.height = `${(y1 - y0) * cssHeight}px`;
  overlay.appendChild(box);
}

async function renderPage(num) {
  if (!pdfDoc) return;
  const token = ++renderToken;
  const page = await pdfDoc.getPage(num);
  const unscaled = page.getViewport({ scale: 1 });
  const maxWidth = Math.min(window.innerWidth - 32, 1100);
  const scale = maxWidth / unscaled.width;
  const viewport = page.getViewport({ scale });

  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(viewport.width * dpr);
  canvas.height = Math.floor(viewport.height * dpr);
  canvas.style.width = `${Math.floor(viewport.width)}px`;
  canvas.style.height = `${Math.floor(viewport.height)}px`;

  const ctx = canvas.getContext('2d');
  await page.render({
    canvasContext: ctx,
    viewport,
    transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
  }).promise;
  if (token !== renderToken) return; // superseded render

  statusEl.style.display = 'none';
  wrapEl.style.display = 'block';
  drawHighlight(viewport.width, viewport.height, num === initialPage);
  pageIndicator.textContent = `Page ${num} / ${pdfDoc.numPages}`;
  prevBtn.disabled = num <= 1;
  nextBtn.disabled = num >= pdfDoc.numPages;
  if (highlightBox && num === initialPage) overlay.scrollIntoView({ block: 'center' });
}

async function load() {
  if (!sourceUrl) {
    setStatus('No document specified. Use ?doc=<document_id> or ?src=<url>.', true);
    return;
  }
  titleEl.textContent = docId || srcParam;
  rawLink.href = sourceUrl;
  try {
    pdfDoc = await pdfjsLib.getDocument(sourceUrl).promise;
  } catch (err) {
    setStatus(`Could not load the PDF (${err && err.message ? err.message : err}).`, true);
    return;
  }
  pageNum = Math.min(Math.max(1, pageNum), pdfDoc.numPages);
  await renderPage(pageNum);
}

prevBtn.addEventListener('click', () => { if (pageNum > 1) renderPage(--pageNum); });
nextBtn.addEventListener('click', () => { if (pdfDoc && pageNum < pdfDoc.numPages) renderPage(++pageNum); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft' && pageNum > 1) renderPage(--pageNum);
  if (e.key === 'ArrowRight' && pdfDoc && pageNum < pdfDoc.numPages) renderPage(++pageNum);
});
window.addEventListener('resize', () => { if (pdfDoc) renderPage(pageNum); });

load();
