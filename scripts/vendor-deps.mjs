import { createHash } from 'node:crypto';
import { readFileSync, copyFileSync, existsSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const SRC = join(ROOT, 'node_modules');
const DEST = join(ROOT, 'static');

const CHECK = process.argv.includes('--check');

const FILE_MAP = [
  ['leaflet/dist/leaflet.css', 'vendor/leaflet/leaflet.css'],
  ['leaflet/dist/leaflet.js', 'vendor/leaflet/leaflet.js'],
  ['leaflet/LICENSE', 'vendor/leaflet/LICENSE'],
  ['flatpickr/dist/flatpickr.min.css', 'vendor/flatpickr/flatpickr.min.css'],
  ['flatpickr/dist/flatpickr.min.js', 'vendor/flatpickr/flatpickr.min.js'],
  ['flatpickr/LICENSE.md', 'vendor/flatpickr/LICENSE.md'],
  ['@fortawesome/fontawesome-free/css/all.min.css', 'vendor/font-awesome/css/all.min.css'],
  ['@fortawesome/fontawesome-free/webfonts', 'vendor/font-awesome/webfonts'],
  ['@fortawesome/fontawesome-free/LICENSE.txt', 'vendor/font-awesome/LICENSE.txt'],
];

function sha256(filePath) {
  return createHash('sha256').update(readFileSync(filePath)).digest('hex');
}

function listFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...listFiles(full));
    } else {
      out.push(full);
    }
  }
  return out;
}

function ensureDir(filePath) {
  mkdirSync(dirname(filePath), { recursive: true });
}

function checkFile(srcPath, destPath) {
  if (!existsSync(destPath) || sha256(srcPath) !== sha256(destPath)) {
    console.error(`out of sync: ${relative(DEST, destPath)}`);
    return true;
  }
  return false;
}

function copyTree(srcDir, destDir) {
  for (const srcFile of listFiles(srcDir)) {
    const destFile = join(destDir, relative(srcDir, srcFile));
    if (CHECK) {
      return checkFile(srcFile, destFile);
    }
    ensureDir(destFile);
    copyFileSync(srcFile, destFile);
  }
  return false;
}

let mismatch = false;

if (CHECK) {
  console.log(`Verifying ${FILE_MAP.length} vendored entries against node_modules...`);
}

for (const [srcRel, destRel] of FILE_MAP) {
  const srcPath = join(SRC, srcRel);
  const destPath = join(DEST, destRel);

  if (!existsSync(srcPath)) {
    throw new Error(`Vendor source missing: ${srcRel} (run npm install)`);
  }

  if (statSync(srcPath).isDirectory()) {
    mismatch = copyTree(srcPath, destPath) || mismatch;
    continue;
  }

  if (CHECK) {
    mismatch = checkFile(srcPath, destPath) || mismatch;
  } else {
    ensureDir(destPath);
    copyFileSync(srcPath, destPath);
  }
}

if (CHECK && mismatch) {
  console.error('static/vendor is out of sync - run make vendor');
  process.exit(1);
} else if (CHECK) {
  console.log('static/vendor is in sync with node_modules');
} else {
  console.log('Vendored dependencies copied to static/vendor');
}