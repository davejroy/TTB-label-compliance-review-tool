const queue = document.querySelector('#queue');
const template = document.querySelector('#queue-item-template');
const batchUpload = document.querySelector('#batch-upload');
const addItemButton = document.querySelector('#add-item');
const analyzeAllButton = document.querySelector('#analyze-all');

let itemSequence = 0;

function updateSummary() {
  const items = [...queue.querySelectorAll('.queue-item')];
  const statuses = items.map((item) => item.dataset.status || 'pending');
  const counts = statuses.reduce(
    (summary, status) => {
      if (summary[status] !== undefined) {
        summary[status] += 1;
      }

      return summary;
    },
    { match: 0, mismatch: 0, review: 0 }
  );

  document.querySelector('[data-summary="queued"]').textContent = String(items.length);
  document.querySelector('[data-summary="match"]').textContent = String(counts.match);
  document.querySelector('[data-summary="mismatch"]').textContent = String(counts.mismatch);
  document.querySelector('[data-summary="review"]').textContent = String(counts.review);
}

function setPreview(item, file) {
  const preview = item.querySelector('[data-role="preview"]');

  if (!file) {
    preview.innerHTML = '<p>No image uploaded yet.</p>';
    return;
  }

  const imageUrl = URL.createObjectURL(file);
  preview.innerHTML = `<img src="${imageUrl}" alt="Uploaded label preview" />`;
}

function renderResults(item, payload) {
  const results = item.querySelector('[data-role="results"]');
  const badge = item.querySelector('[data-role="status"]');

  const overallStatus = payload.summary.mismatch > 0 ? 'mismatch' : payload.summary.review > 0 ? 'review' : 'match';

  item.dataset.status = overallStatus;
  badge.textContent =
    overallStatus === 'match' ? 'Ready to approve' : overallStatus === 'mismatch' ? 'Potential rejection' : 'Manual review needed';
  badge.className = `status-badge ${overallStatus}`;

  const cards = payload.checks
    .map(
      (check) => `
        <article class="result-card">
          <span class="status-badge ${check.status}">${check.status}</span>
          <h4>${check.field}</h4>
          ${check.expected ? `<p><strong>Application:</strong> ${check.expected}</p>` : ''}
          ${check.found ? `<p><strong>Detected:</strong> ${check.found}</p>` : ''}
          <p>${check.reason}</p>
        </article>
      `
    )
    .join('');

  results.innerHTML = `
    <h3>Results</h3>
    <div class="result-grid">
      <article class="result-summary">
        <p><strong>${payload.summary.match}</strong> match(es)</p>
        <p><strong>${payload.summary.mismatch}</strong> mismatch(es)</p>
        <p><strong>${payload.summary.review}</strong> manual-review item(s)</p>
      </article>
      ${cards}
    </div>
    <section class="ocr-output">
      <h4>Extracted label text</h4>
      <pre>${payload.extractedText}</pre>
    </section>
  `;

  updateSummary();
}

function setPendingStatus(item, message = 'Not reviewed') {
  const badge = item.querySelector('[data-role="status"]');
  item.dataset.status = 'pending';
  badge.textContent = message;
  badge.className = 'status-badge';
  updateSummary();
}

async function analyzeItem(item) {
  const fields = Object.fromEntries(
    [...item.querySelectorAll('input[type="text"], textarea')].map((input) => [input.name, input.value.trim()])
  );
  const fileInput = item.querySelector('input[name="labelImage"]');
  const file = fileInput.files[0];

  if (!file && !fields.labelTextOverride) {
    setPendingStatus(item, 'Needs image or text');
    item.querySelector('[data-role="results"]').innerHTML =
      '<h3>Results</h3><p class="muted">Attach an image or paste label text before running analysis.</p>';
    return;
  }

  setPendingStatus(item, 'Analyzing…');

  const body = new FormData();
  Object.entries(fields).forEach(([key, value]) => body.append(key, value));

  if (file) {
    body.append('labelImage', file);
  }

  const response = await fetch('/api/review', {
    method: 'POST',
    body,
  });

  const payload = await response.json();

  if (!response.ok) {
    setPendingStatus(item, 'Needs attention');
    item.querySelector('[data-role="results"]').innerHTML = `<h3>Results</h3><p class="muted">${payload.error}</p>`;
    return;
  }

  renderResults(item, payload);
}

function createQueueItem(file) {
  const fragment = template.content.cloneNode(true);
  const item = fragment.querySelector('.queue-item');
  const title = item.querySelector('.queue-item__title');
  const fileInput = item.querySelector('input[name="labelImage"]');

  item.dataset.itemId = String(++itemSequence);
  item.dataset.status = 'pending';
  title.textContent = file ? file.name : `Label review ${itemSequence}`;

  if (file) {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    fileInput.files = transfer.files;
    setPreview(item, file);
  }

  fileInput.addEventListener('change', () => {
    const [selectedFile] = fileInput.files;
    if (selectedFile) {
      title.textContent = selectedFile.name;
    }

    setPreview(item, selectedFile);
    setPendingStatus(item);
  });

  item.querySelectorAll('input[type="text"], textarea').forEach((input) => {
    input.addEventListener('input', () => setPendingStatus(item));
  });

  item.querySelector('[data-action="remove"]').addEventListener('click', () => {
    item.remove();
    if (!queue.children.length) {
      createQueueItem();
    }

    updateSummary();
  });

  item.querySelector('[data-action="analyze"]').addEventListener('click', async () => {
    await analyzeItem(item);
  });

  queue.appendChild(item);
  updateSummary();
}

batchUpload.addEventListener('change', () => {
  const files = [...batchUpload.files];

  if (!files.length) {
    return;
  }

  files.forEach((file) => createQueueItem(file));
  batchUpload.value = '';
});

addItemButton.addEventListener('click', () => createQueueItem());

analyzeAllButton.addEventListener('click', async () => {
  const items = [...queue.querySelectorAll('.queue-item')];

  for (const item of items) {
    await analyzeItem(item);
  }
});

createQueueItem();
