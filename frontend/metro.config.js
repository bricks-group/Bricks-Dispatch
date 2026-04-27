// metro.config.js
const { getDefaultConfig } = require("expo/metro-config");
const path = require('path');
const { FileStore } = require('metro-cache');

const config = getDefaultConfig(__dirname);

// EXCLUSION ENGINE: Block macOS "ghost" metadata files from ever being seen by Metro
config.resolver.blockList = [
  /.*\/(\._|.__)[^\/]*$/, // Blocks files starting with ._ or .__
  /(.*)\/(__tests__|android|ios|.git|node_modules\/.*\/android|node_modules\/.*\/ios|node_modules\/.*\/windows|node_modules\/.*\/macos)(\/.*)?$/
];

// Use a stable on-disk store (shared across web/android)
const root = process.env.METRO_CACHE_ROOT || path.join(__dirname, '.metro-cache');
config.cacheStores = [
  new FileStore({ root: path.join(root, 'cache') }),
];

// Reduce the number of workers to decrease resource usage
config.maxWorkers = 2;

module.exports = config;
