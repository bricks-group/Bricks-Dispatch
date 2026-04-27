const fs = require('fs');
const path = require('path');
const p = path.join(__dirname, '../node_modules/whatwg-fetch/package.json');
if (fs.existsSync(p)) {
  const pkg = JSON.parse(fs.readFileSync(p, 'utf8'));
  pkg.main = 'dist/fetch.js'; // Point to a file that actually exists
  fs.writeFileSync(p, JSON.stringify(pkg, null, 2));
  console.log('Patched whatwg-fetch package.json');
}
