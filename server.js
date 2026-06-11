const path = require('path');
const express = require('express');
const multer = require('multer');
const { createWorker } = require('tesseract.js');
const englishData = require('@tesseract.js-data/eng');
const { reviewLabel, collapseWhitespace } = require('./lib/compliance');

const app = express();
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 10 * 1024 * 1024 },
});

let workerPromise;

function getWorker() {
  if (!workerPromise) {
    workerPromise = createWorker(englishData.code, 1, {
      langPath: englishData.langPath,
      gzip: englishData.gzip,
      cachePath: path.join(__dirname, '.cache', 'tesseract', englishData.code),
    });
  }

  return workerPromise;
}

app.use(express.static(path.join(__dirname, 'public')));

app.get('/api/health', (_req, res) => {
  res.json({ ok: true });
});

app.post('/api/review', upload.single('labelImage'), async (req, res, next) => {
  try {
    const labelTextOverride = collapseWhitespace(req.body.labelTextOverride || '');

    if (!labelTextOverride && !req.file) {
      return res.status(400).json({
        error: 'Attach a label image or provide a label text override.',
      });
    }

    let extractedText = labelTextOverride;

    if (!extractedText && req.file) {
      const worker = await getWorker();
      const result = await worker.recognize(req.file.buffer);
      extractedText = result.data.text || '';
    }

    if (!collapseWhitespace(extractedText)) {
      return res.status(422).json({
        error: 'OCR did not return readable text. Provide a clearer image or a label text override.',
      });
    }

    const review = reviewLabel(
      {
        brandName: req.body.brandName || '',
        classType: req.body.classType || '',
        alcoholContent: req.body.alcoholContent || '',
        netContents: req.body.netContents || '',
        producer: req.body.producer || '',
        countryOfOrigin: req.body.countryOfOrigin || '',
      },
      extractedText
    );

    return res.json(review);
  } catch (error) {
    return next(error);
  }
});

app.use((error, _req, res, _next) => {
  res.status(500).json({
    error: error.message || 'Unexpected server error.',
  });
});

const port = process.env.PORT || 3000;
let server;

async function terminateWorker() {
  if (workerPromise) {
    const worker = await workerPromise;
    await worker.terminate();
    workerPromise = null;
  }
}

async function shutdown(exitCode = 0) {
  await terminateWorker();

  if (server) {
    server.close(() => process.exit(exitCode));
    return;
  }

  process.exit(exitCode);
}

if (require.main === module) {
  server = app.listen(port, () => {
    console.log(`TTB label compliance review tool running at http://localhost:${port}`);
  });
}

process.once('SIGINT', () => {
  shutdown(0);
});

process.once('SIGTERM', () => {
  shutdown(0);
});

module.exports = app;
