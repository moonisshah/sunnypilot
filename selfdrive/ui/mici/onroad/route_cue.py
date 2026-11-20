"""Read-only route display. Coordinates are local metres east/north."""
import json
import math
from time import monotonic
from bisect import bisect_left
from itertools import accumulate
from pathlib import Path

ROUTE_FILE = Path('/data/media/0/santa-fe-route.json')



DISPLAY_ACK = Path('/dev/shm/santa-fe-route-loaded')


def route_version(path):
  stat = path.stat()
  return f'{stat.st_ino}-{stat.st_mtime_ns}-{stat.st_size}'


def finite_pair(p):
  return isinstance(p, list) and len(p) == 2 and all(type(v) in (float, int) and math.isfinite(v) and abs(v) < 12000 for v in p)


class RouteCue:
  def __init__(self, route):
    if route.get('format') != 'local-route-2':
      raise ValueError('Unsupported route format')
    self.destination = route.get('destination', 'Saved route')
    if not isinstance(self.destination, str) or not self.destination.isprintable() or len(self.destination) > 120:
      raise ValueError('Invalid destination')
    origin = route['origin']
    if not finite_pair(origin) or abs(origin[0]) > 180 or abs(origin[1]) >= 90:
      raise ValueError('Invalid route origin')
    self.lon, self.lat = origin
    self.points = route['points']
    self.turns = route['turns']
    if not 2 <= len(self.points) <= 5000 or not all(map(finite_pair, self.points)):
      raise ValueError('Invalid route geometry')
    self.lengths = list(accumulate((math.dist(a, b) for a, b in zip(self.points, self.points[1:])), initial=0.0))
    if not 1 <= len(self.turns) <= 1000:
      raise ValueError('Invalid turn count')
    previous = -1
    for distance, cue in self.turns:
      if type(distance) not in (int, float) or not previous <= distance <= self.lengths[-1] + 1 or distance < 0:
        raise ValueError('Invalid turn position')
      if not isinstance(cue, str) or not cue.isprintable() or not 1 <= len(cue) <= 120:
        raise ValueError('Invalid turn text')
      previous = distance
    self.distances = [turn[0] for turn in self.turns]

  @classmethod
  def load(cls, path=ROUTE_FILE):
    try:
      with path.open('rb') as source:
        data = source.read(256001)
      if len(data) <= 256000:
        return cls(json.loads(data))
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError, OverflowError, RecursionError):
      pass
    return None

  def cue_at(self, lat, lon):
    p = ((lon - self.lon) * 111320 * math.cos(math.radians(self.lat)), (lat - self.lat) * 111320)
    if not all(math.isfinite(v) for v in p):
      return ''
    best_offset, along = float('inf'), 0.0
    for i, (a, b) in enumerate(zip(self.points, self.points[1:])):
      dx, dy = b[0] - a[0], b[1] - a[1]
      squared = dx * dx + dy * dy
      fraction = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / squared)) if squared else 0
      offset = math.hypot(p[0] - a[0] - fraction * dx, p[1] - a[1] - fraction * dy)
      if offset < best_offset:
        best_offset, along = offset, self.lengths[i] + fraction * math.sqrt(squared)
    if best_offset > 30:
      return 'Outside planned route'
    if self.lengths[-1] - along <= 10 and math.dist(p, self.points[-1]) <= 12:
      return 'At destination'
    index = min(bisect_left(self.distances, along), len(self.turns) - 1)
    distance, cue = self.turns[index]
    return f'{round(max(0, distance - along))} m  {cue}'


class RouteDisplay:
  def __init__(self, path=ROUTE_FILE):
    self.path = path
    self.route = None
    self.frame = None
    self.cue = ''
    self._file_version = None
    self._next_check = 0.0

  def _refresh_route(self, now):
    if now < self._next_check:
      return
    self._next_check = now + 1.0
    try:
      version = route_version(self.path)
    except OSError:
      version = None
    if version == self._file_version:
      return

    self.route = RouteCue.load(self.path) if version is not None else None
    self._file_version = version
    if self.path == ROUTE_FILE and self.route is not None:
      try:
        DISPLAY_ACK.write_text(version)
      except OSError:
        pass
    self.frame = None
    self.cue = ''

  def parked_preview(self):
    self._refresh_route(monotonic())
    if self.route is None:
      return ''
    return f'To {self.route.destination}'

  def current_text(self, sm, started_frame, now):
    self._refresh_route(now)
    if self.route is None:
      return ''
    service = 'gpsLocationExternal'
    frame = sm.recv_frame[service]
    if frame < started_frame or not sm.valid[service] or not sm.alive[service] or not 0 <= now - sm.recv_time[service] <= 2:
      return ''
    fix = sm[service]
    if not fix.hasFix or not 0 < fix.horizontalAccuracy <= 15:
      return ''
    if self.frame != frame:
      self.frame = frame
      self.cue = self.route.cue_at(fix.latitude, fix.longitude)
    return self.cue
