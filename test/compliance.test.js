const test = require('node:test');
const assert = require('node:assert/strict');
const { GOVERNMENT_WARNING_TEXT, evaluateGovernmentWarning, reviewLabel } = require('../lib/compliance');

test('brand matching tolerates punctuation and case differences', () => {
  const review = reviewLabel(
    {
      brandName: "Stone's Throw",
      classType: '',
      alcoholContent: '',
      netContents: '',
      producer: '',
      countryOfOrigin: '',
    },
    "STONE'S THROW SMALL BATCH BOURBON"
  );

  assert.equal(review.checks[0].status, 'match');
});

test('government warning exact text is marked for manual formatting review', () => {
  const result = evaluateGovernmentWarning(`Front label copy ${GOVERNMENT_WARNING_TEXT} Additional copy`);

  assert.equal(result.status, 'review');
});

test('government warning title case is treated as a mismatch', () => {
  const result = evaluateGovernmentWarning(
    'Government Warning: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems.'
  );

  assert.equal(result.status, 'mismatch');
});

test('alcohol content and net contents compare against extracted label text', () => {
  const review = reviewLabel(
    {
      brandName: 'Old Tom Distillery',
      classType: 'Kentucky Straight Bourbon Whiskey',
      alcoholContent: '45% Alc./Vol. (90 Proof)',
      netContents: '750 mL',
      producer: 'Old Tom Distillery, Louisville, KY',
      countryOfOrigin: 'United States',
    },
    `
      OLD TOM DISTILLERY
      KENTUCKY STRAIGHT BOURBON WHISKEY
      45% ALC./VOL. (90 PROOF)
      750 ML
      OLD TOM DISTILLERY, LOUISVILLE, KY
      PRODUCT OF THE UNITED STATES
      ${GOVERNMENT_WARNING_TEXT}
    `
  );

  const alcoholCheck = review.checks.find((check) => check.field === 'Alcohol content');
  const netContentsCheck = review.checks.find((check) => check.field === 'Net contents');

  assert.equal(alcoholCheck.status, 'match');
  assert.equal(netContentsCheck.status, 'match');
});
