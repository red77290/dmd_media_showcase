/**
 * Dedicated ES Module Web Worker (`static/js/batch_worker.js`).
 *
 * Runs completely off the UI main thread and uses event-driven HTTP `ReadableStream` I/O
 * (zero `setTimeout` polling -> zero background-tab timer throttling!):
 * 1. Scans the user-selected `FileSystemDirectoryHandle` for existing `.gif` files (instant resume).
 * 2. Pre-creates the planned folder structure (`<system>_<genre>/` or `showcase/<system>/`).
 * 3. Streams every produced RGB888 `.gif` (and optional marquee `.png`) directly onto the user's
 *    hard drive / SD card, updates `index.txt` + `playlists.json`, and sends immediate `DELETE` ACK
 *    so the server unlinks its ephemeral file right away.
 */

const IS_DEDICATED_WORKER = (
  typeof self !== 'undefined' &&
  typeof window === 'undefined' &&
  typeof self.postMessage === 'function'
);

let stopRequested = false;
let activeAbortCtrl = null;

function buildAuthHeaders(apiKey) {
  const h = {};
  if (apiKey) h['X-Showcase-Key'] = apiKey;
  return h;
}

async function ensureNestedDir(rootHandle, relDir) {
  const clean = String(relDir || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
  if (!clean) return rootHandle;
  let cur = rootHandle;
  for (const seg of clean.split('/')) {
    if (!seg || seg === '.') continue;
    cur = await cur.getDirectoryHandle(seg, { create: true });
  }
  return cur;
}

async function writeBytesToRelpath(rootHandle, relpath, byteBuffer) {
  const norm = String(relpath || '').replace(/\\/g, '/').replace(/^\/+/, '');
  const slashIdx = norm.lastIndexOf('/');
  const relDir = slashIdx >= 0 ? norm.slice(0, slashIdx) : '';
  const filename = slashIdx >= 0 ? norm.slice(slashIdx + 1) : norm;
  const targetDir = await ensureNestedDir(rootHandle, relDir);
  const fileHandle = await targetDir.getFileHandle(filename, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(byteBuffer);
  await writable.close();
}

async function writeTextFile(dirHandle, filename, textContent) {
  const fh = await dirHandle.getFileHandle(filename, { create: true });
  const wr = await fh.createWritable();
  await wr.write(new TextEncoder().encode(textContent));
  await wr.close();
}

async function scanExistingGifsRecursive(dirHandle, prefix = '', maxDepth = 3, out = []) {
  if (maxDepth < 0 || out.length >= 45000) return out;
  for await (const [name, entry] of dirHandle.entries()) {
    if (!name || name.startsWith('.')) continue;
    const rel = prefix ? `${prefix}/${name}` : name;
    if (entry.kind === 'file') {
      if (name.toLowerCase().endsWith('.gif')) {
        out.push(rel);
      }
    } else if (entry.kind === 'directory' && maxDepth > 0) {
      await scanExistingGifsRecursive(entry, rel, maxDepth - 1, out);
    }
  }
  return out;
}

async function loadExistingFolderIndexSet(dirHandle) {
  const set = new Set();
  for await (const [name, entry] of dirHandle.entries()) {
    if (entry.kind === 'file' && !name.startsWith('._') && /\.(gif|png|raw)$/i.test(name)) {
      set.add(name);
    }
  }
  return set;
}

async function flushClockIndexes(rootHandle, touchedFoldersMap, isTate, wrapOrientationRoot) {
  const rootPrefix = isTate ? 'gifs_tate' : 'gifs';
  const manifestDirHandle = wrapOrientationRoot
    ? await ensureNestedDir(rootHandle, rootPrefix)
    : rootHandle;

  let existingManifest = {};
  try {
    const mfHandle = await manifestDirHandle.getFileHandle('playlists.json', { create: false });
    const mfFile = await mfHandle.getFile();
    existingManifest = JSON.parse(await mfFile.text()) || {};
  } catch (_) {
    existingManifest = {};
  }

  for (const [relDir, fileSet] of touchedFoldersMap.entries()) {
    const folderHandle = await ensureNestedDir(rootHandle, relDir);
    const sortedNames = Array.from(fileSet).sort();
    await writeTextFile(folderHandle, 'index.txt', `${sortedNames.join('\n')}\n`);
    const leaf = relDir.split('/').filter(Boolean).pop() || relDir;
    existingManifest[leaf] = {
      path: `/${rootPrefix}/${leaf}`,
      count: sortedNames.length
    };
  }

  await writeTextFile(
    manifestDirHandle,
    'playlists.json',
    `${JSON.stringify(existingManifest, null, 2)}\n`
  );
}

async function runContinuousStreamConsumer(cfg) {
  const {
    jobId,
    baseUrl,
    apiKey,
    dirHandle,
    plannedDirs,
    isClockMode,
    isTate,
    wrapOrientationRoot,
    alreadyAckedCount
  } = cfg;

  stopRequested = false;
  const headers = buildAuthHeaders(apiKey);
  const touchedClockFolders = new Map();
  const writtenSeqs = new Set();
  const baseAcked = Math.max(0, Number(alreadyAckedCount || 0));
  let cursorSeq = 0;
  let unindexedCount = 0;

  // Phase 0: Pre-create initial directory structure immediately
  for (const pDir of plannedDirs || []) {
    if (stopRequested) return;
    await ensureNestedDir(dirHandle, pDir);
  }
  self.postMessage({ type: 'DIRS_READY', count: (plannedDirs || []).length });

  while (!stopRequested) {
    activeAbortCtrl = new AbortController();
    let streamFinished = false;
    try {
      const sseUrl = `${baseUrl}/api/v1/batch-scraper/jobs/${encodeURIComponent(jobId)}/events?after_seq=${cursorSeq}`;
      const resp = await fetch(sseUrl, { headers, signal: activeAbortCtrl.signal });
      if (!resp.ok || !resp.body) {
        throw new Error(`SSE HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let sseBuf = '';

      while (!stopRequested) {
        const { value, done } = await reader.read();
        if (done) break;
        sseBuf += decoder.decode(value, { stream: true });
        let sepIdx;
        while ((sepIdx = sseBuf.indexOf('\n\n')) !== -1) {
          const rawEvent = sseBuf.slice(0, sepIdx);
          sseBuf = sseBuf.slice(sepIdx + 2);
          const dataLine = rawEvent
            .split('\n')
            .find(line => line.startsWith('data:'));
          if (!dataLine) continue;

          const payload = JSON.parse(dataLine.slice(5).trim());
          const snap = payload.snapshot || null;
          const readyList = Array.isArray(payload.ready) ? payload.ready : [];

          for (const item of readyList) {
            if (stopRequested) break;
            const seq = Number(item.seq);
            if (!seq || writtenSeqs.has(seq)) continue;

            const gifUrl = `${baseUrl}/api/v1/batch-scraper/jobs/${encodeURIComponent(jobId)}/artifacts/${seq}?kind=gif`;
            const gifResp = await fetch(gifUrl, { headers, signal: activeAbortCtrl.signal });
            if (gifResp.ok) {
              const gifBuf = await gifResp.arrayBuffer();
              await writeBytesToRelpath(dirHandle, item.relpath, gifBuf);

              if (item.companion_relpath) {
                const compUrl = `${baseUrl}/api/v1/batch-scraper/jobs/${encodeURIComponent(jobId)}/artifacts/${seq}?kind=companion`;
                const compResp = await fetch(compUrl, { headers, signal: activeAbortCtrl.signal });
                if (compResp.ok) {
                  const compBuf = await compResp.arrayBuffer();
                  await writeBytesToRelpath(dirHandle, item.companion_relpath, compBuf);
                }
              }

              if (isClockMode && item.rel_dir) {
                if (!touchedClockFolders.has(item.rel_dir)) {
                  const folderHandle = await ensureNestedDir(dirHandle, item.rel_dir);
                  touchedClockFolders.set(item.rel_dir, await loadExistingFolderIndexSet(folderHandle));
                }
                touchedClockFolders.get(item.rel_dir).add(item.filename);
                unindexedCount += 1;
                if (unindexedCount >= 5) {
                  await flushClockIndexes(dirHandle, touchedClockFolders, isTate, wrapOrientationRoot);
                  unindexedCount = 0;
                }
              }

              // Immediately ACK so server unlinks its ephemeral file!
              await fetch(
                `${baseUrl}/api/v1/batch-scraper/jobs/${encodeURIComponent(jobId)}/artifacts/${seq}`,
                { method: 'DELETE', headers }
              );

              writtenSeqs.add(seq);
              cursorSeq = Math.max(cursorSeq, seq);
              self.postMessage({
                type: 'ITEM_WRITTEN',
                item,
                writtenTotal: baseAcked + writtenSeqs.size
              });
            }
          }

          if (snap) {
            self.postMessage({
              type: 'SNAPSHOT',
              snapshot: snap,
              writtenTotal: baseAcked + writtenSeqs.size
            });
            if (
              ['completed', 'cancelled', 'failed'].includes(snap.status) &&
              Number(snap.unacked_count || 0) === 0 &&
              readyList.length === 0
            ) {
              streamFinished = true;
              break;
            }
          }
        }
        if (streamFinished) break;
      }
    } catch (err) {
      if (stopRequested) break;
      self.postMessage({ type: 'TRANSIENT_WARN', message: String(err && err.message ? err.message : err) });
    }

    if (streamFinished || stopRequested) break;
  }

  if (isClockMode && touchedClockFolders.size > 0) {
    try {
      await flushClockIndexes(dirHandle, touchedClockFolders, isTate, wrapOrientationRoot);
    } catch (_) {}
  }

  self.postMessage({ type: 'STREAM_DONE', writtenTotal: baseAcked + writtenSeqs.size });
}

if (IS_DEDICATED_WORKER) {
  self.addEventListener('message', async evt => {
    const msg = evt.data || {};
    if (msg.type === 'STOP_STREAM') {
      stopRequested = true;
      if (activeAbortCtrl) {
        try { activeAbortCtrl.abort(); } catch (_) {}
      }
      return;
    }
    if (msg.type === 'SCAN_DIR') {
      try {
        const existing = await scanExistingGifsRecursive(msg.dirHandle, '', 3, []);
        self.postMessage({
          type: 'SCAN_RESULT',
          folderName: msg.dirHandle ? msg.dirHandle.name : '',
          existingRelpaths: existing
        });
      } catch (err) {
        self.postMessage({
          type: 'SCAN_ERROR',
          message: String(err && err.message ? err.message : err)
        });
      }
      return;
    }
    if (msg.type === 'START_STREAM') {
      await runContinuousStreamConsumer(msg);
    }
  });
}

export const BATCH_WORKER_VERSION = '1.0.0';
