/**
 * MARKET MAP — Pull immutable Pine artifacts from the Dashboard private repo.
 * Install as a NEW FILE (PineArchive.gs) in existing MARKET MAP Apps Script.
 * It does not change the current SPX raw backup or its trigger.
 *
 * Script Properties:
 * PINE_GITHUB_TOKEN   Fine-grained PAT for GrespoSan/Serafini-SPX-Dashboard,
 *                     repository Actions: Read-only
 * PINE_FOLDER_ID      ID of OAuth-created private "SPX Pine Daily" Drive folder
 */
const PINE_CONFIG = Object.freeze({
  repo: 'GrespoSan/Serafini-SPX-Dashboard',
  artifactPrefix: 'mm-pine-daily-',
  maxArtifactsPerRun: 8,
  maxPages: 3,
});

function pineCheckConnection() {
  const p = PropertiesService.getScriptProperties();
  const token = p.getProperty('PINE_GITHUB_TOKEN');
  const folderId = p.getProperty('PINE_FOLDER_ID');
  if (!token || !folderId) throw Error('Missing PINE_GITHUB_TOKEN / PINE_FOLDER_ID');
  const root = DriveApp.getFolderById(folderId);
  if (root.isTrashed()) throw Error('Pine folder is trashed');
  const repo = pineApi_(token, '');
  if (repo.full_name !== PINE_CONFIG.repo || !repo.private)
    throw Error('Unexpected GitHub repo or visibility');
  Logger.log('MARKET MAP Pine: token/read access and private Drive folder verified');
}

function pineApi_(token, endpoint) {
  const response = UrlFetchApp.fetch('https://api.github.com/repos/' +
      PINE_CONFIG.repo + (endpoint ? '/' + endpoint : ''), {
    method: 'get',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'market-map-pine-backup/1.0',
    },
    muteHttpExceptions: true,
  });
  if (response.getResponseCode() !== 200)
    throw Error('GitHub API HTTP ' + response.getResponseCode());
  return JSON.parse(response.getContentText('UTF-8'));
}

function pineDownloadZip_(token, id) {
  const response = UrlFetchApp.fetch('https://api.github.com/repos/' +
      PINE_CONFIG.repo + '/actions/artifacts/' + id + '/zip', {
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'market-map-pine-backup/1.0',
    },
    followRedirects: false, muteHttpExceptions: true,
  });
  if (![301, 302, 307].includes(response.getResponseCode()))
    throw Error('GitHub artifact redirect HTTP ' + response.getResponseCode());
  const h = response.getAllHeaders();
  const location = h.Location || h.location;
  if (!location || !/^https:\/\//.test(location))
    throw Error('Artifact download has no HTTPS redirect');
  // Never forward GitHub credentials to the signed artifact URL.
  const data = UrlFetchApp.fetch(location, {
    followRedirects: true, muteHttpExceptions: true,
  });
  if (data.getResponseCode() !== 200)
    throw Error('Artifact ZIP HTTP ' + data.getResponseCode());
  return data.getBlob().getBytes();
}

function pineU16_(b, p) {
  return (b[p] & 255) + ((b[p + 1] & 255) << 8);
}
function pineU32_(b, p) {
  return (b[p] & 255) + ((b[p+1] & 255) * 256) +
    ((b[p+2] & 255) * 65536) + ((b[p+3] & 255) * 16777216);
}
function pineSig_(b, p, arr) {
  return p >= 0 && p + arr.length <= b.length &&
    arr.every((x,i) => (b[p+i] & 255) === x);
}
function pineSha_(bytes) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes)
    .map(x => ('0' + (x & 255).toString(16)).slice(-2)).join('');
}

/** Read STORED (method 0) ZIP entries, preserving their exact original bytes.
 * The producing GitHub workflow explicitly uses compression-level: 0.
 * Never quietly decompress and re-encode the archived Pine scripts.
 */
function pineEntries_(bytes) {
  let eocd = -1;
  for (let i=bytes.length-22; i >= Math.max(0, bytes.length-65557); --i) {
    if (pineSig_(bytes, i, [80,75,5,6])) { eocd=i; break; }
  }
  if (eocd < 0) throw Error('Invalid artifact ZIP end directory');
  const count = pineU16_(bytes, eocd + 10);
  const offset = pineU32_(bytes, eocd + 16);
  if (count === 65535 || offset === 4294967295)
    throw Error('ZIP64 not supported; abort without writing');
  let pos = offset;
  const files = {};
  for (let i=0; i<count; i++) {
    if (!pineSig_(bytes, pos, [80,75,1,2]))
      throw Error('Malformed artifact ZIP central directory');
    const method = pineU16_(bytes, pos+10);
    const packed = pineU32_(bytes, pos+20);
    const unpacked = pineU32_(bytes, pos+24);
    const nameSize = pineU16_(bytes, pos+28);
    const extraSize = pineU16_(bytes, pos+30);
    const commentSize = pineU16_(bytes, pos+32);
    const localPos = pineU32_(bytes, pos+42);
    const name = Utilities.newBlob(
      bytes.slice(pos+46, pos+46+nameSize)).getDataAsString('UTF-8');
    pos += 46 + nameSize + extraSize + commentSize;
    if (name.endsWith('/')) continue;
    if (method !== 0 || packed !== unpacked)
      throw Error('Unexpected ZIP compression, refusing altered Pine bytes');
    if (!pineSig_(bytes, localPos, [80,75,3,4]))
      throw Error('Bad ZIP local header');
    const start = localPos + 30 + pineU16_(bytes,localPos+26) +
      pineU16_(bytes,localPos+28);
    if (start + packed > bytes.length)
      throw Error('ZIP entry exceeds file boundaries');
    const shortName = name.split('/').pop();
    if (files[shortName]) throw Error('Duplicate ZIP member: ' + shortName);
    files[shortName] = bytes.slice(start, start+packed);
  }
  return files;
}

function pineSingle_(dir, filename) {
  const matches = dir.getFilesByName(filename);
  if (!matches.hasNext()) return null;
  const file = matches.next();
  if (matches.hasNext()) throw Error('Duplicate Pine Drive file ' + filename);
  return file;
}

function pineSave_(dir, name, bytes, mime) {
  const expected = pineSha_(bytes);
  let file = pineSingle_(dir, name);
  if (!file) file = dir.createFile(Utilities.newBlob(bytes, mime, name));
  if (file.getSize() !== bytes.length ||
      pineSha_(file.getBlob().getBytes()) !== expected)
    throw Error('Pine archive byte-for-byte mismatch: ' + name);
}

function pineDayFolder_(root, day) {
  const iterator = root.getFoldersByName(day);
  if (!iterator.hasNext()) return root.createFolder(day);
  const folder = iterator.next();
  if (iterator.hasNext()) throw Error('Duplicate date folders: ' + day);
  return folder;
}

function pinePreserve_(root, id, bytes) {
  const files = pineEntries_(bytes);
  if (!files['manifest.json']) throw Error('Missing Pine run manifest');
  const manifest = JSON.parse(
    Utilities.newBlob(files['manifest.json']).getDataAsString('UTF-8'));
  const day = manifest.day;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day))
    throw Error('Invalid source date in Pine artifact');
  const expected = manifest.produced || [];
  const destination = pineDayFolder_(root, day);
  for (const item of expected) {
    if (!['07:30','13:30','15:50','20:30'].includes(item.slot) ||
        !/^Serafini_NET_GAMMA_\d{4}-\d{2}-\d{2}_\d{4}_[0-9a-f]{12}\.pine$/.test(item.file) ||
        item.file.indexOf(day) < 0) throw Error('Unexpected Pine member name');
    const code = files[item.file];
    if (!code || pineSha_(code) !== item.sha256)
      throw Error('Source Pine SHA256 mismatch: ' + item.file);
    const metaName = item.file.replace(/\.pine$/, '.metadata.json');
    const metadata = files[metaName];
    if (!metadata) throw Error('Missing Pine metadata for ' + item.file);
    const m = JSON.parse(Utilities.newBlob(metadata).getDataAsString('UTF-8'));
    if (m.sha256 !== item.sha256 || m.slot !== item.slot ||
        m.date_rome !== day) throw Error('Pine metadata/source mismatch');
    pineSave_(destination, item.file, code, 'text/plain');
    pineSave_(destination, metaName, metadata, 'application/json');
  }
  const marker = 'pine_artifact_' + id + '.done.json';
  const ack = {
    schema:'market_map_pine_drive_ack_v1', github_artifact_id:id,
    source_day:day, files:expected.length, archived_at_utc:new Date().toISOString(),
  };
  pineSave_(root, marker, Utilities.newBlob(
    JSON.stringify(ack)).getBytes(), 'application/json');
  Logger.log('VERIFIED Pine day=' + day + ' scripts=' + expected.length +
             ' GitHub artifact=' + id);
}

function pineBackupArtifacts() {
  pineCheckConnection();
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('PINE_GITHUB_TOKEN');
  const root = DriveApp.getFolderById(props.getProperty('PINE_FOLDER_ID'));
  let reviewed=0, stored=0;
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) throw Error('Another Pine archive run is active');
  try {
    for (let page=1; page<=PINE_CONFIG.maxPages; page++) {
      const payload = pineApi_(token,'actions/artifacts?per_page=100&page='+page);
      const matches = (payload.artifacts || [])
        .filter(a => !a.expired && a.name.startsWith(PINE_CONFIG.artifactPrefix));
      for (const a of matches) {
        const done = 'pine_artifact_' + a.id + '.done.json';
        if (pineSingle_(root, done)) continue;
        if (reviewed >= PINE_CONFIG.maxArtifactsPerRun) break;
        reviewed += 1;
        pinePreserve_(root, a.id, pineDownloadZip_(token,a.id));
        stored += 1;
      }
      if (reviewed >= PINE_CONFIG.maxArtifactsPerRun ||
          (payload.artifacts || []).length < 100) break;
    }
    Logger.log('MARKET MAP Pine archived artifacts=' + stored +
               ', verified runs=' + reviewed);
  } finally {
    lock.releaseLock();
  }
}
