import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const htmlPath = resolve('dist/index.html');
let html = readFileSync(htmlPath, 'utf8');

html = html.replace(
  'content="width=device-width, initial-scale=1, shrink-to-fit=no"',
  'content="width=device-width, initial-scale=1, viewport-fit=cover"',
);

if (!html.includes('bricks-mobile-native')) {
  const inject = `
    <meta name="theme-color" content="#141210" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <style id="bricks-mobile-native">
      html {
        height: 100%;
        height: 100dvh;
        -webkit-tap-highlight-color: transparent;
        touch-action: manipulation;
      }
      body {
        margin: 0;
        height: 100%;
        height: 100dvh;
        overflow: hidden;
        overscroll-behavior: none;
        background-color: #141210;
      }
      button, [role="button"], a {
        -webkit-user-select: none;
        user-select: none;
        -webkit-touch-callout: none;
      }
      input, textarea, select { font-size: 16px; }
    </style>
`;
  html = html.replace('</head>', `${inject}  </head>`);
}

writeFileSync(htmlPath, html);
writeFileSync(
  resolve('dist/vercel.json'),
  '{\n  "rewrites": [{ "source": "/(.*)", "destination": "/index.html" }]\n}\n',
);
console.log('patched dist/index.html + vercel.json');
