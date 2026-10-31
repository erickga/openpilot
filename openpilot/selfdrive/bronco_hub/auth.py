"""Local pairing for the Hub. Use only on a trusted LAN or through an SSH tunnel."""
import os
import secrets
from pathlib import Path


def load_pairing_key() -> str:
  default = "/data/bronco_hub_key" if Path("/data").is_dir() else "/tmp/bronco_hub_key"
  path = Path(os.environ.get("BRONCO_HUB_KEY_PATH", default))
  path.parent.mkdir(parents=True, exist_ok=True)
  try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
  except FileExistsError:
    key = path.read_text().strip()
    if len(key) < 32:
      raise ValueError("Invalid Bronco Hub pairing key") from None
    path.chmod(0o600)
    return key
  key = secrets.token_urlsafe(32)
  with os.fdopen(fd, "w") as f:
    f.write(key + "\n")
  return key


PAIRING_HTML = """<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair Bronco Hub</title><body style="background:#101823;color:#fff;font:18px system-ui;max-width:420px;margin:60px auto;padding:20px">
<h1>Pair Bronco Hub</h1><p>Enter your device's pairing key to access the dashboard.</p>
<form id="pair"><input id="key" type="password" autocomplete="current-password" required aria-label="Pairing key"
style="width:100%;padding:12px;box-sizing:border-box"><button style="margin-top:16px;padding:12px">Connect</button></form>
<p id="error" role="alert"></p><script>
document.getElementById('pair').onsubmit = async e => {
  e.preventDefault();
  try {
    const r = await fetch('/api/pair', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({key:document.getElementById('key').value.trim()})});
    if (r.ok) location.replace('/');
    else document.getElementById('error').textContent = 'Pairing failed. Check the key and try again.';
  } catch (e) { document.getElementById('error').textContent = 'Unable to connect.'; }
};
</script></body></html>"""
