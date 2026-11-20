"""Serve the Jacana planner on a trusted local network."""
import argparse
import errno
import hmac
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic_ns, sleep

DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(DIRECTORY.parents[1] / 'selfdrive/ui/mici/onroad'))
from route_cue import DISPLAY_ACK, MAX_ROUTE_BYTES, ROUTE_FILE, RouteCue, parse_route, route_version


ASSETS = {'au_route.js': ('au_route.js', 'application/javascript'), '?area=au': ('australia.html', 'text/html'), 'trip.js': ('trip.js', 'application/javascript'),
          '': ('index.html', 'text/html'), 'map.js': ('map.js', 'application/javascript'),
          'route.js': ('route.js', 'application/javascript')}


def save_route(body, path=ROUTE_FILE):
  parse_route(json.loads(body))
  with NamedTemporaryFile(dir=path.parent, delete=False) as output:
    temporary = Path(output.name)
    try:
      output.write(body)
      output.close()
      temporary.replace(path)
    finally:
      temporary.unlink(missing_ok=True)


def current_position(event, now):
  if event is None or not event.valid or not 0 <= now - event.logMonoTime <= 2_000_000_000:
    return None
  fix = event.gpsLocationExternal
  if not (fix.hasFix and 0 < fix.horizontalAccuracy <= 15
          and -90 <= fix.latitude <= 90 and -180 <= fix.longitude <= 180):
    return None
  return {'latitude': fix.latitude, 'longitude': fix.longitude}


class PlannerHandler(BaseHTTPRequestHandler):
  def authorized_path(self):
    _, token, resource = self.path.split('/', 2) if self.path.count('/') >= 2 else ('', '', '')
    return resource if hmac.compare_digest(token, self.server.token) else None

  def reply(self, status, body, media_type='text/plain', encoding=None):
    self.send_response(status)
    self.send_header('Content-Type', media_type + '; charset=utf-8')
    if encoding:
      self.send_header('Content-Encoding', encoding)
    self.send_header('Content-Length', str(len(body)))
    self.send_header('Cache-Control', 'no-store')
    self.send_header('Referrer-Policy', 'no-referrer')
    self.end_headers()
    self.wfile.write(body)

  def log_message(self, *_):
    pass  # The path contains the session token.

  def do_GET(self):
    resource = self.authorized_path()
    if resource == 'status':
      try:
        version = route_version(ROUTE_FILE)
        if getattr(self.server, 'status_version', None) != version:
          route = RouteCue.load()
          if route is None:
            return self.reply(500, b'Could not read saved route')
          self.server.status_destination = route.destination
          self.server.status_version = version
        loaded = DISPLAY_ACK.exists() and DISPLAY_ACK.read_text() == version
        state = {'version': version, 'destination': self.server.status_destination, 'loaded': loaded}
      except FileNotFoundError:
        state = None
      except OSError:
        return self.reply(500, b'Could not read route status')
      return self.reply(200, json.dumps(state).encode(), 'application/json')
    if resource == 'position':
      event = self.server.read_gps()
      if event is not None:
        self.server.last_fix = event
      position = current_position(self.server.last_fix, monotonic_ns())
      return self.reply(200, json.dumps(position).encode(), 'application/json')
    areas = {'': ('Jacana', 'map.js'), '?area=pascoe': ('Pascoe Vale', 'pascoe.js'),
             '?area=corridor': ('Pascoe Vale–Jacana', 'corridor.js')}
    if resource in areas:
      page = (DIRECTORY / 'index.html').read_text()
      area, asset = areas[resource]
      page = page.replace('{{area}}', area).replace('{{map_file}}', asset)
      return self.reply(200, page.encode(), 'text/html')
    if resource == 'corridor.js':
      return self.reply(200, (DIRECTORY / 'corridor.js.gz').read_bytes(), 'application/javascript', 'gzip')
    if resource == 'pascoe.js':
      return self.reply(200, (DIRECTORY / resource).read_bytes(), 'application/javascript')
    if resource not in ASSETS:
      return self.reply(404, b'Planner link not found')
    filename, media_type = ASSETS[resource]
    self.reply(200, (DIRECTORY / filename).read_bytes(), media_type)

  def do_DELETE(self):
    if self.authorized_path() != 'route':
      return self.reply(404, b'Planner link not found')
    try:
      if self.headers.get('If-Match') != route_version(ROUTE_FILE):
        return self.reply(409, b'The route has changed. Check the current destination.')
      ROUTE_FILE.unlink()
    except FileNotFoundError:
      pass
    except OSError:
      return self.reply(500, b'Could not clear route')
    self.reply(200, b'Route cleared.')

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
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, OverflowError, RecursionError):
      return self.reply(400, b'Invalid route')
    except OSError:
      return self.reply(500, b'Could not save the route')
    self.reply(200, b'Route sent. Check the comma display.')


CONFIG = Path('/data/local-route-planner.json')


def configure(address, path=CONFIG):
  ip = IPv4Address(address)
  if not ip.is_private or ip.is_unspecified or ip.is_loopback or ip.is_link_local:
    raise ValueError('Use the local Wi-Fi address shown on the comma.')
  token = json.loads(path.read_text())['token'] if path.exists() else secrets.token_urlsafe(24)
  with NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
    temporary = Path(output.name)
    try:
      json.dump({'ip': str(ip), 'token': token}, output)
      output.close()
      temporary.replace(path)
    finally:
      temporary.unlink(missing_ok=True)
  return token


def serve(address, token, managed=False):
  while True:
    try:
      server = HTTPServer((address, 8765), PlannerHandler)
      break
    except OSError as error:
      if not managed or error.errno not in (errno.EADDRNOTAVAIL, errno.EADDRINUSE):
        raise
      sleep(5)
  with server:
    sys.path.insert(0, str(DIRECTORY.parents[1]))
    from cereal import messaging
    gps = messaging.sub_sock('gpsLocationExternal', conflate=True)
    server.read_gps = lambda: messaging.recv_one_or_none(gps)
    server.last_fix = None
    server.token = token
    if not managed:
      print(f'Open http://{address}:8765/{token}/ in Safari while parked.', flush=True)
    server.serve_forever()


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('device_ip', nargs='?', help='Comma local IP address to bind to')
  mode = parser.add_mutually_exclusive_group()
  mode.add_argument('--enable-autostart', action='store_true')
  mode.add_argument('--managed', action='store_true')
  args = parser.parse_args()
  if args.managed:
    config = json.loads(CONFIG.read_text())
    serve(config['ip'], config['token'], managed=True)
  elif not args.device_ip:
    parser.error('Enter the local IP address of the comma.')
  elif args.enable_autostart:
    token = configure(args.device_ip)
    print(f'After reboot, bookmark http://{args.device_ip}:8765/{token}/?area=pascoe in Safari.')
  else:
    serve(args.device_ip, secrets.token_urlsafe(24))
