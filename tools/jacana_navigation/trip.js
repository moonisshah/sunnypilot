save.onclick = async () => {
  if (!result) return;
  save.disabled = true;
  try {
    const response = await fetch('route', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(result)
    });
    statusLine.textContent = await response.text();
  } catch {
    statusLine.textContent = 'Could not connect to the comma. Check the local connection and retry.';
  } finally {
    save.disabled = !result;
  }
};

const activeRoute = document.getElementById('activeRoute');
const cancelRoute = document.getElementById('cancelRoute');
let activeVersion;
async function refreshStatus() {
  try {
    const response = await fetch('status', {cache: 'no-store'});
    if (!response.ok) throw Error();
    const state = await response.json();
    activeVersion = state?.version;
    cancelRoute.hidden = !state;
    activeRoute.textContent = state ? (state.loaded ? 'Loaded by comma display · ' : 'Saved on comma · ') + state.destination : 'No active route';
  } catch {
    cancelRoute.hidden = true;
    activeRoute.textContent = 'Route status unavailable';
  }
}
cancelRoute.onclick = async () => {
  cancelRoute.disabled = true;
  try {
    const response = await fetch('route', {method:'DELETE', headers:{'If-Match':activeVersion}});
    if (response.ok) reset();
    statusLine.textContent = await response.text();
  } catch {
    statusLine.textContent = 'Could not confirm cancellation. Check the connection.';
  } finally {
    await refreshStatus();
    cancelRoute.disabled = false;
  }
};
async function pollStatus() {
  if (!document.hidden) await refreshStatus();
  setTimeout(pollStatus, 3000);
}
pollStatus();
