const GOVERNMENT_WARNING_TEXT = 'GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS.';
const ABV_TOLERANCE = 0.1;

function collapseWhitespace(value = '') {
  return value.replace(/\s+/g, ' ').trim();
}

function normalizeLooseText(value = '') {
  return value
    .normalize('NFKD')
    .replace(/[\u2018\u2019]/g, "'")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, '');
}

function normalizeExactText(value = '') {
  return collapseWhitespace(
    value
      .normalize('NFKD')
      .replace(/[\u2018\u2019]/g, "'")
  );
}

function containsLooseText(labelText, expectedValue) {
  const expected = normalizeLooseText(expectedValue);
  if (!expected) {
    return null;
  }

  return normalizeLooseText(labelText).includes(expected);
}

function compareLooseField(label, labelText, expectedValue) {
  if (!collapseWhitespace(expectedValue)) {
    return {
      field: label,
      status: 'review',
      reason: 'No application value was provided for this field.',
    };
  }

  const isMatch = containsLooseText(labelText, expectedValue);

  return {
    field: label,
    status: isMatch ? 'match' : 'mismatch',
    expected: collapseWhitespace(expectedValue),
    reason: isMatch
      ? 'The application value appears in the extracted label text after case/punctuation normalization.'
      : 'The application value was not found in the extracted label text.',
  };
}

function parsePercentAbv(value = '') {
  const match = value.match(/(\d{1,3}(?:\.\d+)?)\s*%/i);
  return match ? Number.parseFloat(match[1]) : null;
}

function extractPercentCandidates(labelText = '') {
  return Array.from(labelText.matchAll(/(\d{1,3}(?:\.\d+)?)\s*%\s*(?:ALC\.?\s*\/\s*VOL\.?)?/gi)).map((match) =>
    Number.parseFloat(match[1])
  );
}

function normalizeMeasure(value = '') {
  const candidates = extractNetContentCandidates(value);

  if (!candidates.length) {
    return null;
  }

  return candidates[0];
}

function extractNetContentCandidates(labelText = '') {
  const tokens = labelText
    .toUpperCase()
    .replace(/[^A-Z0-9.\s]/g, ' ')
    .split(/\s+/)
    .filter(Boolean);
  const candidates = [];

  for (let index = 0; index < tokens.length; index += 1) {
    const numberToken = tokens[index];

    if (!/^\d+(?:\.\d+)?$/.test(numberToken)) {
      continue;
    }

    const unitToken = tokens[index + 1];
    const nextUnitToken = tokens[index + 2];

    if (unitToken === 'ML' || unitToken === 'L' || unitToken === 'OZ') {
      candidates.push(`${numberToken} ${unitToken}`);
      continue;
    }

    if (unitToken === 'FL' && nextUnitToken === 'OZ') {
      candidates.push(`${numberToken} FL OZ`);
    }
  }

  return candidates;
}

function compareAlcoholContent(labelText, expectedValue) {
  if (!collapseWhitespace(expectedValue)) {
    return {
      field: 'Alcohol content',
      status: 'review',
      reason: 'No application value was provided for this field.',
    };
  }

  const expectedAbv = parsePercentAbv(expectedValue);
  const extractedAbvValues = extractPercentCandidates(labelText);

  if (expectedAbv !== null && extractedAbvValues.length > 0) {
    const isMatch = extractedAbvValues.some((value) => Math.abs(value - expectedAbv) < ABV_TOLERANCE);
    return {
      field: 'Alcohol content',
      status: isMatch ? 'match' : 'mismatch',
      expected: collapseWhitespace(expectedValue),
      found: extractedAbvValues.join(', ') || undefined,
      reason: isMatch
        ? 'A matching ABV percentage was found on the label.'
        : 'No matching ABV percentage was found on the label.',
    };
  }

  return compareLooseField('Alcohol content', labelText, expectedValue);
}

function compareNetContents(labelText, expectedValue) {
  if (!collapseWhitespace(expectedValue)) {
    return {
      field: 'Net contents',
      status: 'review',
      reason: 'No application value was provided for this field.',
    };
  }

  const expectedMeasure = normalizeMeasure(expectedValue);
  const extractedMeasures = extractNetContentCandidates(labelText);

  if (expectedMeasure && extractedMeasures.length > 0) {
    const isMatch = extractedMeasures.includes(expectedMeasure);

    return {
      field: 'Net contents',
      status: isMatch ? 'match' : 'mismatch',
      expected: collapseWhitespace(expectedValue),
      found: extractedMeasures.join(', ') || undefined,
      reason: isMatch
        ? 'A matching net contents value was found on the label.'
        : 'No matching net contents value was found on the label.',
    };
  }

  return compareLooseField('Net contents', labelText, expectedValue);
}

function evaluateGovernmentWarning(labelText) {
  const exactWarning = normalizeExactText(GOVERNMENT_WARNING_TEXT);
  const normalizedLabel = normalizeExactText(labelText);

  if (normalizedLabel.includes(exactWarning)) {
    return {
      field: 'Government warning',
      status: 'review',
      expected: GOVERNMENT_WARNING_TEXT,
      reason: 'Exact warning text and uppercase wording were found, but bold styling still requires visual review.',
    };
  }

  const looseWarning = normalizeLooseText(GOVERNMENT_WARNING_TEXT);

  if (normalizeLooseText(labelText).includes(looseWarning)) {
    return {
      field: 'Government warning',
      status: 'mismatch',
      expected: GOVERNMENT_WARNING_TEXT,
      reason: 'The warning wording was found, but capitalization or punctuation did not exactly match the required statement.',
    };
  }

  return {
    field: 'Government warning',
    status: 'mismatch',
    expected: GOVERNMENT_WARNING_TEXT,
    reason: 'The required government warning statement was not found in the extracted label text.',
  };
}

function summarizeChecks(checks) {
  return checks.reduce(
    (summary, check) => {
      summary.total += 1;
      summary[check.status] += 1;
      return summary;
    },
    { total: 0, match: 0, mismatch: 0, review: 0 }
  );
}

function reviewLabel(application, labelText) {
  const checks = [
    compareLooseField('Brand name', labelText, application.brandName),
    compareLooseField('Class / type', labelText, application.classType),
    compareAlcoholContent(labelText, application.alcoholContent),
    compareNetContents(labelText, application.netContents),
    compareLooseField('Bottler / producer', labelText, application.producer),
    compareLooseField('Country of origin', labelText, application.countryOfOrigin),
    evaluateGovernmentWarning(labelText),
  ];

  return {
    extractedText: collapseWhitespace(labelText),
    checks,
    summary: summarizeChecks(checks),
  };
}

module.exports = {
  GOVERNMENT_WARNING_TEXT,
  collapseWhitespace,
  normalizeLooseText,
  normalizeExactText,
  reviewLabel,
  evaluateGovernmentWarning,
};
