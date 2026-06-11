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
    const message = document.createElement('p');
    message.textContent = 'No image uploaded yet.';
    preview.replaceChildren(message);
    return;
  }

  const imageUrl = URL.createObjectURL(file);
  const image = document.createElement('img');
  image.src = imageUrl;
  image.alt = 'Uploaded label preview';
  preview.replaceChildren(image);
}

function createStatusBadge(status) {
  const badge = document.createElement('span');
  badge.className = `status-badge ${status}`;
  badge.textContent = status;
  return badge;
}

function createLabeledParagraph(label, value) {
  const paragraph = document.createElement('p');
  const strong = document.createElement('strong');
  strong.textContent = `${label}:`;
  paragraph.append(strong, ` ${value}`);
  return paragraph;
}

function renderMessage(results, message) {
  const heading = document.createElement('h3');
  heading.textContent = 'Results';

  const paragraph = document.createElement('p');
  paragraph.className = 'muted';
  paragraph.textContent = message;

  results.replaceChildren(heading, paragraph);
}

function renderResults(item, payload) {
  const results = item.querySelector('[data-role="results"]');
  const badge = item.querySelector('[data-role="status"]');

  const overallStatus = payload.summary.mismatch > 0 ? 'mismatch' : payload.summary.review > 0 ? 'review' : 'match';

  item.dataset.status = overallStatus;
  badge.textContent =
    overallStatus === 'match' ? 'Ready to approve' : overallStatus === 'mismatch' ? 'Potential rejection' : 'Manual review needed';
  badge.className = `status-badge ${overallStatus}`;

  const heading = document.createElement('h3');
  heading.textContent = 'Results';

  const resultGrid = document.createElement('div');
  resultGrid.className = 'result-grid';

  const summaryCard = document.createElement('article');
  summaryCard.className = 'result-summary';
  [
    `${payload.summary.match} match(es)`,
    `${payload.summary.mismatch} mismatch(es)`,
    `${payload.summary.review} manual-review item(s)`,
  ].forEach((text) => {
    const paragraph = document.createElement('p');
    const strong = document.createElement('strong');
    const [count, ...rest] = text.split(' ');
    strong.textContent = count;
    paragraph.append(strong, ` ${rest.join(' ')}`);
    summaryCard.append(paragraph);
  });
  resultGrid.append(summaryCard);

  payload.checks.forEach((check) => {
    const card = document.createElement('article');
    card.className = 'result-card';

    const title = document.createElement('h4');
    title.textContent = check.field;

    const reason = document.createElement('p');
    reason.textContent = check.reason;

    card.append(createStatusBadge(check.status), title);

    if (check.expected) {
      card.append(createLabeledParagraph('Application', check.expected));
    }

    if (check.found) {
      card.append(createLabeledParagraph('Detected', check.found));
    }

    card.append(reason);
    resultGrid.append(card);
  });

  const ocrOutput = document.createElement('section');
  ocrOutput.className = 'ocr-output';
  const ocrHeading = document.createElement('h4');
  ocrHeading.textContent = 'Extracted label text';
  const pre = document.createElement('pre');
  pre.textContent = payload.extractedText;
  ocrOutput.append(ocrHeading, pre);

  results.replaceChildren(heading, resultGrid, ocrOutput);

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
    renderMessage(item.querySelector('[data-role="results"]'), 'Attach an image or paste label text before running analysis.');
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
    renderMessage(item.querySelector('[data-role="results"]'), payload.error);
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
