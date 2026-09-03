'use strict';

const assert = require('assert');
const { cleanForSpeech } = require('../voice-output-service');

const cases = [
  ['That actually worked 😂', 'That actually worked'],
  ['No way 🤣 😭', 'No way'],
  ['This is fire 🔥 👍', 'This is fire'],
  ['I love it ❤️ 😊', 'I love it'],
  ["Kenneth, I don't think version 2.5 works!", "Kenneth, I don't think version 2.5 works!"],
  ['Status: 100% — ready.', 'Status: 100% — ready.'],
];

for (const [input, expected] of cases) assert.strictEqual(cleanForSpeech(input), expected);

const allEmoji = '😂 🤣 😭 🔥 👍 ❤️ 😊';
const result = cleanForSpeech(`Bro... ${allEmoji} somehow it worked.`);
assert.strictEqual(result, 'Bro... somehow it worked.');
assert.doesNotMatch(result, /[😂🤣😭🔥👍❤❤️😊]/u);

console.log('Voice presentation emoji filtering: PASS');
