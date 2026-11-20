"""Serve the Jacana planner on a trusted local network."""
import argparse
import hmac
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile

DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(DIRECTORY.parents[1] / 'selfdrive/ui/mici/onroad'))
from route_cue import ROUTE_FILE, RouteCue

MAX_ROUTE_BYTES = 256000
ASSETS = {'': ('index.html', 'text/html'), 'map.js': ('map.js', 'application/javascript'),
          'route.js': ('route.js', 'application/javascript')}


def save_route(body, path=ROUTE_FILE):
  RouteCue(json.loads(body))
  with NamedTemporaryFile(dir=path.parent, delete=False) as output:
    temporary = Path(output.name)
    try:
      output.write(body)
      output.close()
      temporary.replace(path)
    finally:
      temporary.unlink(missing_ok=True)


class PlannerHandler(BaseHTTPRequestHandler):
  def authorized_path(self):
    _, token, resource = self.path.split('/', 2) if self.path.count('/') >= 2 else ('', '', '')
    return resource if hmac.compare_digest(token, self.server.token) else None

  def reply(self, status, body, media_type='text/plain'):
    self.send_response(status)
    self.send_header('Content-Type', media_type + '; charset=utf-8')
    self.send_header('Content-Length', str(len(body)))
    self.send_header('Cache-Control', 'no-store')
    self.send_header('Referrer-Policy', 'no-referrer')
    self.end_headers()
    self.wfile.write(body)

  def log_message(self, *_):
    pass  # The path contains the session token.

  def do_GET(self):
    resource = self.authorized_path()
    if resource not in ASSETS:
      return self.reply(404, b'Planner link not found')
    filename, media_type = ASSETS[resource]
    self.reply(200, (DIRECTORY / filename).read_bytes(), media_type)

  def do_POST(self):
    if self.authorized_path() != 'route':
      return self.reply(404, b'Planner link not found')
    if self.headers.get('Content-Type') != 'application/json':
      return self.reply(415, b'Expected a JSON route')
    try:
      length = int(self.headers.get('Content-Length', '0'))
      if not 0 < length <= MAX_ROUTE_BYTES:
        return self.reply(413, b'Route exceeds the size limit or is empty')
      self.connection.settimeout(5)
      save_route(self.rfile.read(length))
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
      return self.reply(400, b'Invalid route')
    except OSError:
      return self.reply(500, b'Could not save the route')
    self.reply(200, b'Route sent. Check the comma display.')


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('device_ip', help='Comma local IP address to bind to')
  args = parser.parse_args()
  with HTTPServer((args.device_ip, 8765), PlannerHandler) as server:
    server.token = secrets.token_urlsafe(24)
    print(f'Open http://{args.device_ip}:8765/{server.token}/ in Safari while parked.', flush=True)
    server.serve_forever()
