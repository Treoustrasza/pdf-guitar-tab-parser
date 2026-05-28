const fs = require('fs');
let s = fs.readFileSync('node_modules/@coderline/alphatab/dist/alphaTab.min.js', 'utf8');

const OLD = 'case"hammer-on":case"pull-off":e&&(e.isHammerPullOrigin=!0);break;';
const NEW = 'case"hammer-on":case"pull-off":e&&"stop"!==n.getAttribute("type")&&(e.isHammerPullOrigin=!0);break;';

if (!s.includes(OLD)) {
    console.error('Pattern NOT found! Cannot patch.');
    process.exit(1);
}

s = s.replace(OLD, NEW);
fs.writeFileSync('node_modules/@coderline/alphatab/dist/alphaTab.min.js', s, 'utf8');
console.log('Patched alphaTab.min.js successfully.');
