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

// Copy a file only when it is missing or its content differs, leaving the
// mtimes of unchanged files untouched so incremental deploys (rsync, etc.)
// don't re-upload every vendored asset on every build (#186).
function copyIfChanged(srcPath, destPath) {
  if (existsSync(destPath) && sha256(srcPath) === sha256(destPath)) {
    return false;
  }
  ensureDir(destPath);
  copyFileSync(srcPath, destPath);
  return true;
}

// Returns the number of files that were changed (check mode: out of sync;
// copy mode: written).
function copyTree(srcDir, destDir) {
  let changed = 0;
  for (const srcFile of listFiles(srcDir)) {
    const destFile = join(destDir, relative(srcDir, srcFile));
    if (CHECK) {
      if (checkFile(srcFile, destFile)) changed += 1;
    } else if (copyIfChanged(srcFile, destFile)) {
      changed += 1;
    }
  }
  return changed;
}

function totalFiles() {
  return FILE_MAP.reduce((total, [srcRel]) => {
    const srcPath = join(SRC, srcRel);
    return total + (statSync(srcPath).isDirectory() ? listFiles(srcPath).length : 1);
  }, 0);
}

let mismatch = 0;
let copied = 0;
let total = 0;

if (CHECK) {
  console.log(`Verifying ${FILE_MAP.length} vendored entries against node_modules...`);
} else {
  total = totalFiles();
}

for (const [srcRel, destRel] of FILE_MAP) {
  const srcPath = join(SRC, srcRel);
  const destPath = join(DEST, destRel);

  if (!existsSync(srcPath)) {
    throw new Error(`Vendor source missing: ${srcRel} (run npm install)`);
  }

  if (statSync(srcPath).isDirectory()) {
    if (CHECK) {
      mismatch += copyTree(srcPath, destPath);
    } else {
      copied += copyTree(srcPath, destPath);
    }
    continue;
  }

  if (CHECK) {
    if (checkFile(srcPath, destPath)) mismatch += 1;
  } else if (copyIfChanged(srcPath, destPath)) {
    copied += 1;
  }
}

if (CHECK && mismatch > 0) {
  console.error('static/vendor is out of sync - run make vendor');
  process.exit(1);
} else if (CHECK) {
  console.log('static/vendor is in sync with node_modules');
} else {
  console.log(
    `Vendored dependencies copied to static/vendor (${copied} written, ${total - copied} unchanged)`
  );
}